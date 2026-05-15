"""UniverseService with concurrent fetching, thread pool scoring, shared HTTP client."""
from __future__ import annotations

import asyncio
import heapq
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.http_client import get_client
from app.models import Category, Market

logger = logging.getLogger("app.services.universe")


class UniverseService:
    def __init__(self):
        self._markets: List[Any] = []
        self._orderbooks: Dict[str, Any] = {}
        self._score_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="scorer")
        self._refresh_lock = asyncio.Lock()
        self._latency_samples_ms: List[int] = []
        self._max_latency_samples = 5000
        self._client = None
        self._last_refresh: Optional[datetime] = None
        self._active_markets_gauge = 0
        self.gamma_base = os.getenv("POLYMARKET_GAMMA_BASE", "https://gamma-api.polymarket.com").rstrip("/")
        self.clob_base = os.getenv("POLYMARKET_CLOB_BASE", "https://clob.polymarket.com").rstrip("/")

    @property
    def last_refresh(self):
        return self._last_refresh

    @property
    def active_markets_gauge(self) -> int:
        return self._active_markets_gauge

    async def initialize(self):
        self._client = await get_client()

    async def get_active_markets(self):
        stale = self._last_refresh is None or (datetime.now(timezone.utc) - self._last_refresh) > timedelta(minutes=5)
        if stale or not self._markets:
            await self.refresh()
        return self._markets

    def get_orderbook(self, key: str):
        return self._orderbooks.get(key)

    async def refresh(self) -> None:
        if self._client is None:
            await self.initialize()
        async with self._refresh_lock:
            t0 = time.monotonic()
            raw = await self._fetch_all_markets()
            loop = asyncio.get_event_loop()
            scored = await loop.run_in_executor(self._score_executor, lambda: [self._score(r) for r in raw])
            active = [s for s in scored if self._is_active(s)]
            self._markets = active[:150]
            self._active_markets_gauge = len(self._markets)
            self._last_refresh = datetime.now(timezone.utc)
            await self._fetch_orderbooks_concurrent()
            latency_ms = int((time.monotonic() - t0) * 1000)
            self._latency_samples_ms.append(latency_ms)
            if len(self._latency_samples_ms) > self._max_latency_samples:
                self._latency_samples_ms = self._latency_samples_ms[-self._max_latency_samples :]
            logger.info("refresh_complete markets=%d orderbooks=%d latency_ms=%d", len(raw), len(self._orderbooks), latency_ms)

    async def _fetch_all_markets(self) -> List[dict]:
        resp = await self._client.get(f"{self.gamma_base}/markets", params={"limit": 100, "offset": 0, "closed": "false", "active": "true"})
        resp.raise_for_status()
        first_batch = resp.json()
        if len(first_batch) < 100:
            return first_batch
        pages = [first_batch]
        offsets = range(100, 1000, 100)

        async def _fetch_page(offset):
            r = await self._client.get(f"{self.gamma_base}/markets", params={"limit": 100, "offset": offset, "closed": "false", "active": "true"})
            r.raise_for_status()
            return r.json()

        remaining = await asyncio.gather(*[_fetch_page(o) for o in offsets])
        for batch in remaining:
            pages.append(batch)
            if len(batch) < 100:
                break
        return [m for page in pages for m in page]

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
                    resp = await self._client.get(f"{self.clob_base}/book", params={"token_id": token_id}, timeout=10)
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

    def _score(self, raw: dict) -> Market:
        return Market.from_gamma(raw)

    def _is_active(self, scored: Market) -> bool:
        now = datetime.now(timezone.utc)
        return scored.ends_at > now and scored.liquidity > 100 and scored.spread < 0.15

    def _get_yes_token_id(self, market: Any) -> Optional[str]:
        raw = getattr(market, "raw", None)
        if raw and isinstance(raw, dict):
            tokens = raw.get("clobTokenIds")
            if tokens:
                if isinstance(tokens, str):
                    import json
                    try:
                        tokens = json.loads(tokens)
                    except Exception:
                        return tokens
                if isinstance(tokens, list) and len(tokens) > 0:
                    return str(tokens[0])
            for key in ["tokenId", "token_id", "yes_token_id", "asset_id"]:
                if raw.get(key):
                    return str(raw[key])
        return None

    def _convert_clob_orderbook(self, data: dict, token_id: str, market: Any) -> Any:
        bids = data.get("bids", [])
        asks = data.get("asks", [])
        yes_bids = [{"price": float(b.get("price", 0)), "qty": float(b.get("size", 0))} for b in bids if float(b.get("price", 0)) > 0 and float(b.get("size", 0)) > 0]
        yes_asks = [{"price": float(a.get("price", 0)), "qty": float(a.get("size", 0))} for a in asks if float(a.get("price", 0)) > 0 and float(a.get("size", 0)) > 0]
        best_yes_bid = yes_bids[0]["price"] if yes_bids else None
        best_yes_ask = yes_asks[0]["price"] if yes_asks else None
        return {"token_id": token_id, "yes_bids": yes_bids, "yes_asks": yes_asks, "yes_bid": best_yes_bid, "yes_ask": best_yes_ask, "no_bid": (1 - best_yes_ask) if best_yes_ask else None, "no_ask": (1 - best_yes_bid) if best_yes_bid else None}
