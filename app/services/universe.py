from __future__ import annotations
import asyncio
import logging
import os
import statistics
import time
from datetime import datetime, timedelta, timezone
from collections import deque
import httpx

from app.models import Category, Market

logger = logging.getLogger("app.services.universe")

class UniverseService:
    def __init__(self):
        self._markets = []
        self._last_refresh = None
        self._refresh_lock = asyncio.Lock()
        self.gamma_base = os.getenv("POLYMARKET_GAMMA_BASE", "https://gamma-api.polymarket.com").rstrip("/")
        self.max_markets = int(os.getenv("MAX_MARKETS_FETCH", "800"))
        self._client = httpx.AsyncClient(
            timeout=30,
            headers={"User-Agent": "PolyTradingMVP/1.2"},
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=10),
        )
        self._orderbooks = {}
        self._cycle_id = 0
        self._consecutive_orderbook_zero = 0
        self._orderbook_zero_warn_threshold = int(os.getenv("ORDERBOOK_ZERO_WARN_THRESHOLD", "3"))
        self._active_markets_gauge = 0
        self._latency_samples_ms = deque(maxlen=500)

    @property
    def last_refresh(self):
        return self._last_refresh

    @property
    def active_markets_gauge(self) -> int:
        return self._active_markets_gauge

    @property
    def processing_latency_p99_ms(self) -> int:
        if not self._latency_samples_ms:
            return 0
        if len(self._latency_samples_ms) == 1:
            return int(self._latency_samples_ms[0])
        return int(statistics.quantiles(self._latency_samples_ms, n=100, method="inclusive")[98])

    async def get_active_markets(self):
        stale = self._last_refresh is None or (datetime.now(timezone.utc) - self._last_refresh) > timedelta(minutes=5)
        if stale or not self._markets:
            await self.refresh()
        return self._markets

    async def refresh(self):
        async with self._refresh_lock:
            cycle_id = self._cycle_id
            self._cycle_id += 1
            error_flags = []
            refresh_started = time.perf_counter()

            fetch_started = time.perf_counter()
            raw = await self._fetch_raw()
            fetch_duration_ms = int((time.perf_counter() - fetch_started) * 1000)

            scored = [self._score(r) for r in raw]
            now = datetime.now(timezone.utc)
            active = [m for m in scored if m.ends_at > now and m.liquidity > 500 and m.spread < 0.10]
            self._markets = active
            self._active_markets_gauge = len(active)
            self._last_refresh = now
            await self._fetch_orderbooks_for_active()
            orderbook_count = len(self._orderbooks)
            if orderbook_count == 0:
                self._consecutive_orderbook_zero += 1
                error_flags.append("orderbooks_empty")
            else:
                self._consecutive_orderbook_zero = 0
            refresh_duration_ms = int((time.perf_counter() - refresh_started) * 1000)
            self._latency_samples_ms.append(refresh_duration_ms)

            logger.warning(
                "universe_refresh_complete",
                extra={
                    "cycle_id": cycle_id,
                    "fetch_duration_ms": fetch_duration_ms,
                    "refresh_duration_ms": refresh_duration_ms,
                    "raw_count": len(raw),
                    "active_count": len(active),
                    "orderbook_count": orderbook_count,
                    "error_flags": error_flags,
                    "active_markets": self._active_markets_gauge,
                    "processing_latency_p99": self.processing_latency_p99_ms,
                },
            )
            if self._consecutive_orderbook_zero >= self._orderbook_zero_warn_threshold:
                logger.warning(
                    "orderbooks_zero_consecutive",
                    extra={
                        "cycle_id": cycle_id,
                        "error_flags": ["orderbooks_empty_consecutive"],
                        "orderbook_count": orderbook_count,
                    },
                )
            return active

    async def _fetch_raw(self):
        url = f"{self.gamma_base}/markets"
        markets, offset, limit = [], 0, 100
        while len(markets) < self.max_markets:
            try:
                resp = await self._client.get(url, params={"limit": limit, "offset": offset, "closed": "false", "active": "true"})
                resp.raise_for_status()
                payload = resp.json()
                if isinstance(payload, list):
                    data = payload
                elif isinstance(payload, dict):
                    data = payload.get("markets") or payload.get("data") or payload.get("results") or []
                else:
                    break
                if not data:
                    break
                markets.extend(data)
                if len(data) < limit:
                    break
                offset += limit
                await asyncio.sleep(0.15)
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    return markets
                raise
            except Exception:
                return markets
        logger.warning("fetch_raw_complete count=%d", len(markets))
        return markets

    async def _fetch_orderbooks_for_active(self):
        top_markets = sorted(self._markets, key=lambda m: m.liquidity, reverse=True)[:50]
        for m in top_markets:
            try:
                slug = getattr(m, 'slug', '') or (getattr(m, 'url', '').split('/')[-1] if hasattr(m, 'url') else m.id)
                if not slug:
                    continue
                resp = await self._client.get(f"{self.gamma_base}/orderbook/{slug}", timeout=10)
                if resp.status_code == 200:
                    self._orderbooks[slug] = resp.json()
                else:
                    self._orderbooks.pop(slug, None)
                await asyncio.sleep(0.05)
            except Exception as e:
                logger.debug("orderbook_fetch_failed %s: %s", slug, e)

    def get_orderbook(self, market_id: str) -> dict:
        return self._orderbooks.get(market_id, {})

    def _score(self, raw):
        cat = self._infer_category(raw.get("tags", []), raw.get("question", ""))
        confidence = self._compute_confidence(raw)
        ends_at_str = (raw.get("endDate") or "2026-12-31T23:59:59Z").replace("Z", "+00:00")
        try:
            ends_at = datetime.fromisoformat(ends_at_str)
        except Exception:
            ends_at = datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
        try:
            liquidity = float(raw.get("liquidity") or 0)
        except Exception:
            liquidity = 0.0
        try:
            volume_24h = float(raw.get("volume24h") or raw.get("volume") or 0)
        except Exception:
            volume_24h = 0.0
        best_bid = float(raw.get("bestBid", 0))
        best_ask = float(raw.get("bestAsk", 1))
        last_price = float(raw.get("lastPrice", 0) or raw.get("price", 0) or 0)
        if last_price == 0 and (best_bid > 0 or best_ask < 1):
            last_price = (best_bid + best_ask) / 2.0
        spread = 0.05
        try:
            if best_ask > 0:
                spread = (best_ask - best_bid) / best_ask
        except Exception:
            pass
        slug = raw.get("slug", "")
        mid = raw.get("id", "")
        url = f"https://polymarket.com/event/{slug}" if slug else f"https://polymarket.com/market/{mid}"
        close_time = raw.get("close_time") or raw.get("expiration_time") or raw.get("endDate")
        return Market(
            id=mid or slug or "unknown",
            title=raw.get("question") or raw.get("title") or "Untitled",
            category=cat,
            confidence=confidence,
            ev=raw.get("expected_value"),
            liquidity=liquidity,
            spread=spread,
            volume_24h=volume_24h,
            last_price=max(0.0, min(1.0, last_price)),
            ends_at=ends_at,
            url=url,
            best_bid=best_bid,
            best_ask=best_ask,
            market_type=raw.get("market_type") or "single",
            close_time=str(close_time) if close_time is not None else None,
            tags=raw.get("tags", []),
            question=raw.get("question") or raw.get("title") or "",
            slug=slug,
        )

    @staticmethod
    def _infer_category(tags, question):
        labels = [t.get("label", "") if isinstance(t, dict) else str(t) for t in tags]
        text = " ".join(labels + [question]).lower()
        if any(k in text for k in ("crypto", "bitcoin", "ethereum", "btc", "eth", "token", "defi", "nft")):
            return Category.CRYPTO
        if any(k in text for k in ("election", "vote", "poll", "senate", "congress", "president", "governor")):
            return Category.POLITICS
        if any(k in text for k in ("sports", "nba", "nfl", "soccer", "baseball", "tennis", "game", "match")):
            return Category.SPORTS
        if any(k in text for k in ("economy", "gdp", "inflation", "unemployment", "fed", "interest rate", "recession")):
            return Category.ECONOMY
        if any(k in text for k in ("climate", "weather", "temperature", "carbon", "emission", "warming")):
            return Category.CLIMATE
        if any(k in text for k in ("tech", "ai", "apple", "google", "microsoft", "tesla", "elon", "chip")):
            return Category.TECH
        return Category.OTHER

    @staticmethod
    def _compute_confidence(raw):
        score = 0.5
        try:
            vol = float(raw.get("volume24h") or raw.get("volume") or 0)
            if vol > 100000: score += 0.15
            elif vol > 50000: score += 0.10
            elif vol > 10000: score += 0.05
        except Exception:
            pass
        return min(1.0, score)
