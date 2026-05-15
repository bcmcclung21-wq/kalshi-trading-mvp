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

            loop = asyncio.get_event_loop()

            def _score_all():
                scored = []
                fail_count = 0
                first_fail = None
                for idx, r in enumerate(raw):
                    try:
                        s = self._score(r)
                        if s is not None:
                            scored.append(s)
                    except Exception as e:
                        fail_count += 1
                        if first_fail is None:
                            first_fail = (idx, type(r).__name__, str(e)[:120])
                if fail_count > 0:
                    logger.warning("score_summary failed=%d/%d first_fail_idx=%d err=%s",
                                   fail_count, len(raw), first_fail[0], first_fail[2])
                else:
                    logger.info("score_summary all_passed=%d", len(scored))
                return scored

            scored = await loop.run_in_executor(self._score_executor, _score_all)

            active = [s for s in scored if self._is_active(s)]
            self._markets = active[:150]   # Cap to reduce CLOB load

            await self._fetch_orderbooks_concurrent()

            latency_ms = int((time.monotonic() - t0) * 1000)
            self._latency_samples_ms.append(latency_ms)
            if len(self._latency_samples_ms) > self._max_latency_samples:
                self._latency_samples_ms = self._latency_samples_ms[-self._max_latency_samples:]

            logger.info("refresh_complete markets=%d orderbooks=%d latency_ms=%d",
                       len(raw), len(self._orderbooks), latency_ms)

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
            return

        semaphore = asyncio.Semaphore(max_concurrency)

        async def _fetch_one(market) -> Optional[tuple]:
            async with semaphore:
                token_id = self._get_yes_token_id(market)
                if not token_id:
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
                    logger.debug("ob_fetch_fail token=%s... error=%s", str(token_id)[:16], e)
                return None

        results = await asyncio.gather(*[_fetch_one(m) for m in self._markets])
        for res in results:
            if res:
                mid, slug, ob = res
                self._orderbooks[mid] = ob
                if slug:
                    self._orderbooks[slug] = ob

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

        from app.models import Market

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

        if 'category' not in t:
            t['category'] = ''

        if 'ends_at' not in t:
            t['ends_at'] = None

        if 'active' not in t:
            t['active'] = True

        return t

    def _is_active(self, scored: Any) -> bool:
        if scored is None:
            return False
        return True

    def _get_yes_token_id(self, market: Any) -> Optional[str]:
        if market is None:
            return None

        tokens = getattr(market, 'tokens', None)
        if tokens:
            for token in tokens:
                outcome = getattr(token, 'outcome', '') or ''
                if outcome.lower() in ('yes', 'yes token'):
                    return getattr(token, 'token_id', None) or getattr(token, 'id', None)

        return getattr(market, 'yes_token_id', None) or getattr(market, 'token_id', None)

    def _convert_clob_orderbook(self, data: dict, token_id: str, market: Any) -> Any:
        return data
