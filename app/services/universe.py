"""UniverseService with concurrent fetching, thread pool scoring, shared HTTP client.

DEFENSIVE FIXES v2:
1. _fetch_all_markets now validates every page is a list before flattening.
   Prevents 'str' object has no attribute 'get' when Gamma returns malformed pages.
2. _score now skips non-dict items and handles already-parsed Market objects.
3. get_active_markets() restored as async method — engine.py awaits it.
4. refresh() wraps scoring in per-item try/except so one bad market doesn't crash the cycle.
5. _is_active safely rejects None values.
"""
import asyncio
import heapq
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
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
        self.last_refresh: Optional[datetime] = None
        self.active_markets_gauge = 0

    async def initialize(self):
        self._client = await get_client()

    async def get_active_markets(self) -> List[Any]:
        await self.refresh()
        return self._markets

    async def refresh(self) -> None:
        async with self._refresh_lock:
            t0 = time.monotonic()
            raw = await self._fetch_all_markets()
            loop = asyncio.get_event_loop()

            def _score_all():
                scored = []
                for idx, r in enumerate(raw):
                    try:
                        s = self._score(r)
                        if s is not None:
                            scored.append(s)
                    except Exception as e:
                        r_type = type(r).__name__
                        r_preview = str(r)[:60] if isinstance(r, str) else "non-str"
                        logger.warning("score_failed idx=%d type=%s preview=%s error=%s", idx, r_type, r_preview, e)
                return scored

            scored = await loop.run_in_executor(self._score_executor, _score_all)
            active = [s for s in scored if self._is_active(s)]
            self._markets = active[:150]
            self.active_markets_gauge = len(self._markets)
            self.last_refresh = datetime.now(timezone.utc)

            await self._fetch_orderbooks_concurrent()

            latency_ms = int((time.monotonic() - t0) * 1000)
            self._latency_samples_ms.append(latency_ms)
            if len(self._latency_samples_ms) > self._max_latency_samples:
                self._latency_samples_ms = self._latency_samples_ms[-self._max_latency_samples:]

            logger.info("refresh_complete markets=%d orderbooks=%d latency_ms=%d", len(raw), len(self._orderbooks), latency_ms)

    async def _fetch_all_markets(self) -> List[dict]:
        resp = await self._client.get(
            "https://gamma-api.polymarket.com/markets",
            params={"limit": 100, "offset": 0, "closed": "false", "active": "true"},
        )
        resp.raise_for_status()
        first_batch = resp.json()

        if not isinstance(first_batch, list):
            logger.error("gamma_first_batch_not_list type=%s body_preview=%s", type(first_batch).__name__, str(first_batch)[:200])
            return []

        if len(first_batch) < 100:
            return first_batch

        pages = [first_batch]
        offsets = range(100, 1000, 100)

        async def _fetch_page(offset):
            try:
                r = await self._client.get(
                    "https://gamma-api.polymarket.com/markets",
                    params={"limit": 100, "offset": offset, "closed": "false", "active": "true"},
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
            else:
                logger.warning("gamma_page_skipped_non_list type=%s", type(batch).__name__)

        result = []
        for page in pages:
            if isinstance(page, list):
                result.extend(page)
            else:
                logger.warning("gamma_flatten_skipped_non_list type=%s", type(page).__name__)
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
                        timeout=10,
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

    def get_orderbook(self, key: str):
        return self._orderbooks.get(key)

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
            logger.debug("_score_skipped type=%s preview=%s", type(raw).__name__, str(raw)[:60])
            return None

        from app.models import Market

        if hasattr(Market, 'model_validate'):
            return Market.model_validate(raw)
        if hasattr(Market, 'parse_obj'):
            return Market.parse_obj(raw)
        return Market(**raw)

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
