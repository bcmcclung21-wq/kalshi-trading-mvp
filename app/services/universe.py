"""UniverseService with concurrent fetching, thread pool scoring, shared HTTP client.

DEFENSIVE FIXES v3:
1. _score() now TRANSFORMS raw Gamma API dicts before Pydantic validation.
   Maps 'question' → 'title', provides defaults for missing fields.
   This fixes the 100% validation failure rate causing count=0 dashboard.
2. _fetch_all_markets validates every page is a list before flattening.
3. get_active_markets() is async — engine.py awaits it.
4. refresh() wraps scoring in per-item try/except + summary logging.
"""
import asyncio
import heapq
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, List, Dict, Any
import logging

from app.http_client import get_client
from app.models import ALLOWED_CATEGORIES, CATEGORY_MAP, Market

logger = logging.getLogger(__name__)


class UniverseService:
    def __init__(self):
        self._markets: List[Any] = []
        self._orderbooks: Dict[str, Any] = {}
        self._score_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="scorer")
        self._refresh_lock = asyncio.Lock()
        self._latency_samples_ms: List[int] = []
        self._max_latency_samples = 5000
        self._client = None
        self._zero_parsed_streak = 0

    async def initialize(self):
        self._client = await get_client()

    async def get_active_markets(self) -> List[Any]:
        """Async entrypoint used by engine.py."""
        await self.refresh()
        return self._markets

    async def refresh(self) -> None:
        async with self._refresh_lock:
            t0 = time.monotonic()

            raw = await self._fetch_all_markets()

            logger.info("refresh_start raw=%d", len(raw))
            if raw and isinstance(raw[0], dict):
                sample = raw[0]
                logger.info(
                    "raw_sample id=%s category=%s active=%s keys=%s",
                    sample.get("id"),
                    sample.get("category"),
                    sample.get("active"),
                    list(sample.keys())[:12],
                )

            loop = asyncio.get_event_loop()

            scored = await loop.run_in_executor(self._score_executor, self.score_summary, raw)

            active = [s for s in scored if self._is_active(s)]
            self._markets = active[:150]   # Cap to reduce CLOB load

            self._update_pipeline_health(len(raw), len(self._markets))
            await self._fetch_orderbooks_concurrent()

            latency_ms = int((time.monotonic() - t0) * 1000)
            self._latency_samples_ms.append(latency_ms)
            if len(self._latency_samples_ms) > self._max_latency_samples:
                self._latency_samples_ms = self._latency_samples_ms[-self._max_latency_samples:]

            logger.info("refresh_complete raw=%d parsed=%d orderbooks=%d latency_ms=%d",
                       len(raw), len(self._markets), len(self._orderbooks), latency_ms)

    def score_summary(self, raw_markets: list[dict]) -> list[Market]:
        markets: list[Market] = []
        dropped: dict[str, int] = {"parse_failed": 0, "disallowed_category": 0, "inactive": 0}
        for raw in raw_markets:
            try:
                transformed = self._transform_for_model(raw)
                market = Market.parse_obj(transformed)
                if not bool(transformed.get("active", True)):
                    dropped["inactive"] += 1
                    continue
                if market.category not in ALLOWED_CATEGORIES:
                    dropped["disallowed_category"] += 1
                    logger.debug(
                        "market_drop reason=disallowed_category id=%s category=%s liquidity=%s volume24h=%s",
                        transformed.get("id"),
                        market.category,
                        transformed.get("liquidity"),
                        transformed.get("volume24h"),
                    )
                    continue
                markets.append(market)
            except Exception as exc:
                dropped["parse_failed"] += 1
                logger.debug("score_parse_failed err=%s", exc)
                continue
        logger.info(
            "score_summary raw=%d parsed=%d dropped_parse=%d dropped_category=%d",
            len(raw_markets),
            len(markets),
            dropped["parse_failed"],
            dropped["disallowed_category"],
        )
        logger.info(
            "filter_status in=%d out=%d dropped_inactive=%d",
            len(raw_markets) - dropped["parse_failed"] - dropped["disallowed_category"],
            len(markets),
            dropped["inactive"],
        )
        return markets


    def _update_pipeline_health(self, raw_count: int, parsed_count: int) -> None:
        if raw_count > 0 and parsed_count == 0:
            self._zero_parsed_streak += 1
            logger.error(
                "pipeline_alert zero_parsed_streak=%d raw=%d parsed=%d",
                self._zero_parsed_streak,
                raw_count,
                parsed_count,
            )
        else:
            self._zero_parsed_streak = 0

    async def _fetch_all_markets(self) -> List[dict]:
        """Fetch all market pages concurrently with defensive validation."""
        resp = await self._client.get(
            "https://gamma-api.polymarket.com/markets",
            params={"limit": 100, "offset": 0, "closed": "false", "active": "true"}
        )
        resp.raise_for_status()
        first_batch = resp.json()

        if not isinstance(first_batch, list):
            logger.error("gamma_first_batch_not_list type=%s preview=%s",
                        type(first_batch).__name__, str(first_batch)[:200])
            return []

        if len(first_batch) < 100:
            return first_batch

        pages = [first_batch]
        offsets = range(100, 1000, 100)

        async def _fetch_page(offset):
            try:
                r = await self._client.get(
                    "https://gamma-api.polymarket.com/markets",
                    params={"limit": 100, "offset": offset, "closed": "false", "active": "true"}
                )
                r.raise_for_status()
                data = r.json()
                if not isinstance(data, list):
                    logger.warning("gamma_page_not_list offset=%d type=%s", offset, type(data).__name__)
                    return []
                return data
            except Exception as e:
                logger.warning("gamma_page_fetch_failed offset=%d error=%s", offset, e)
                return []

        remaining = await asyncio.gather(*[_fetch_page(o) for o in offsets])
        for batch in remaining:
            if isinstance(batch, list):
                pages.append(batch)
                if len(batch) < 100:
                    break

        result = []
        for page in pages:
            if isinstance(page, list):
                result.extend(page)
        return result

    async def _fetch_orderbooks_concurrent(self, max_concurrency: int = 20) -> None:
        self._orderbooks.clear()
        if not self._markets:
            logger.warning("orderbook_fetch_skipped reason=no_markets")
            return

        semaphore = asyncio.Semaphore(max_concurrency)
        stats = {"misses": 0, "failures": 0}

        async def _fetch_one(market) -> Optional[tuple]:
            async with semaphore:
                token_id = self._get_yes_token_id(market)
                if not token_id:
                    logger.debug("ob_fetch_skip market=%s reason=missing_token_id", getattr(market, 'id', 'unknown'))
                    return None
                try:
                    resp = await self._client.get(
                        "https://clob.polymarket.com/book",
                        params={"token_id": token_id},
                        timeout=10
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        ob = self._convert_clob_orderbook(data, token_id, market)
                        return (market.id, getattr(market, "slug", None), ob)
                except Exception as e:
                    stats["failures"] += 1
                    logger.warning("ob_fetch_fail market=%s token=%s error=%s", getattr(market, "id", "unknown"), str(token_id)[:16], e)
                return None

        results = await asyncio.gather(*[_fetch_one(m) for m in self._markets])
        for result in results:
            if result is None:
                stats["misses"] += 1
                continue
            mid, slug, ob = result
            self._orderbooks[mid] = ob
            if slug:
                self._orderbooks[slug] = ob
        logger.info(
            "orderbook_fetch_summary requested=%d fetched=%d misses=%d failures=%d",
            len(self._markets),
            len({m.id for m in self._markets if m.id in self._orderbooks}),
            stats["misses"],
            stats["failures"],
        )

    @property
    def processing_latency_p99_ms(self) -> int:
        if not self._latency_samples_ms:
            return 0
        n = len(self._latency_samples_ms)
        if n <= 100:
            return max(self._latency_samples_ms)
        return heapq.nlargest(max(1, n // 100), self._latency_samples_ms)[-1]

    async def aclose(self):
        self._score_executor.shutdown(wait=True)

    def _score(self, raw: Any) -> Optional[Any]:
        if hasattr(raw, 'id') or hasattr(raw, 'slug'):
            return raw

        if not isinstance(raw, dict):
            return None

        transformed = self._transform_for_model(raw)

        if hasattr(Market, 'model_validate'):
            return Market.model_validate(transformed)
        elif hasattr(Market, 'parse_obj'):
            return Market.parse_obj(transformed)
        else:
            return Market(**transformed)

    def _transform_for_model(self, raw: dict) -> dict:
        t = dict(raw)

        if 'question' in t and 'title' not in t:
            t['title'] = t.pop('question')

        category = str(t.get('category') or '').strip().lower()
        if not category:
            category = self._infer_category_from_tags(t.get('tags', []), t.get('title') or t.get('question') or '')
        t['category'] = CATEGORY_MAP.get(category, category) or None

        if 'ends_at' not in t:
            t['ends_at'] = t.get('endDate') or t.get('closeTime') or t.get('close_time')

        if 'raw' not in t:
            t['raw'] = raw

        if 'active' not in t:
            t['active'] = True

        return t


    def _infer_category_from_tags(self, tags: Any, text: str = "") -> str:
        values: list[str] = []
        if isinstance(tags, list):
            for tag in tags:
                if isinstance(tag, dict):
                    values.extend([str(tag.get("slug") or ""), str(tag.get("label") or ""), str(tag.get("name") or "")])
                else:
                    values.append(str(tag))
        tag_text = " ".join(v.lower() for v in values if v).strip()
        haystack = f"{tag_text} {str(text or '').lower()}"
        checks = {
            "sports": ["sport", "nba", "nfl", "mlb", "soccer", "football", "tennis"],
            "politics": ["politic", "election", "president", "senate", "congress", "government"],
            "crypto": ["crypto", "bitcoin", "ethereum", "solana", "token", "defi"],
            "climate": ["climate", "temperature", "weather", "hurricane", "emissions"],
            "economics": ["econom", "inflation", "gdp", "fed", "rates", "recession"],
            "tech": ["tech", "ai", "openai", "software", "chip", "semiconductor"],
        }
        for category, needles in checks.items():
            if any(n in haystack for n in needles):
                return category
        return "unknown"

    def _is_active(self, scored: Any) -> bool:
        if scored is None:
            return False
        return True

    def _get_yes_token_id(self, market: Any) -> Optional[str]:
        if market is None:
            return None

        tokens = getattr(market, 'tokens', None) or (market.raw.get('tokens') if getattr(market, 'raw', None) else None)
        if tokens:
            for token in tokens:
                outcome = (getattr(token, 'outcome', None) or (token.get('outcome') if isinstance(token, dict) else '') or '')
                if str(outcome).lower() in ('yes', 'yes token'):
                    return getattr(token, 'token_id', None) or getattr(token, 'id', None) or (token.get('token_id') if isinstance(token, dict) else None) or (token.get('id') if isinstance(token, dict) else None)

        return (
            getattr(market, 'yes_token_id', None)
            or getattr(market, 'token_id', None)
            or (market.raw.get('token_id') if getattr(market, 'raw', None) else None)
            or (market.raw.get('clobTokenIds', [None])[0] if isinstance(market.raw.get('clobTokenIds') if getattr(market, 'raw', None) else None, list) else None)
        )

    def _convert_clob_orderbook(self, data: dict, token_id: str, market: Any) -> Any:
        return data
