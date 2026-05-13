"""Universe service: fetches and scores active Polymarket markets."""
from __future__ import annotations
import asyncio, logging, os
from datetime import datetime, timedelta
from typing import List, Optional
import httpx
from app.models import Category, Market
logger = logging.getLogger("app.services.universe")

class UniverseService:
    def __init__(self):
        self._markets: List[Market] = []
        self._last_refresh: Optional[datetime] = None
        self._refresh_lock = asyncio.Lock()
        self.gamma_base = os.getenv("POLYMARKET_GAMMA_BASE", "https://gamma-api.polymarket.com").rstrip("/")
        self.max_markets = int(os.getenv("MAX_MARKETS_FETCH", "3000"))
    async def get_active_markets(self):
        stale = self._last_refresh is None or (datetime.utcnow() - self._last_refresh) > timedelta(minutes=5)
        if stale or not self._markets: await self.refresh()
        return self._markets
    async def refresh(self):
        async with self._refresh_lock:
            raw = await self._fetch_raw(); scored = [self._score(r) for r in raw]; now = datetime.utcnow()
            active = [m for m in scored if m.ends_at > now and m.liquidity > 500 and m.spread < 0.10]
            self._markets = active; self._last_refresh = now
            logger.info("universe_refresh_complete", extra={"raw_count": len(raw), "active_count": len(active)})
            return active
    async def _fetch_raw(self):
        url = f"{self.gamma_base}/markets"; markets, offset, limit = [], 0, 100
        async with httpx.AsyncClient(timeout=30) as client:
            while len(markets) < self.max_markets:
                try:
                    resp = await client.get(url, params={"limit": limit, "offset": offset, "closed": "false"}); resp.raise_for_status(); data = resp.json()
                    if not data or not isinstance(data, list): break
                    markets.extend(data)
                    if len(data) < limit: break
                    offset += limit; await asyncio.sleep(0.15)
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 429: return markets
                    raise
                except Exception:
                    return markets
        return markets
    def _score(self, raw):
        cat = self._infer_category(raw.get("tags", []), raw.get("question", "")); confidence = self._compute_confidence(raw)
        ends_at_str = (raw.get("endDate") or "2026-12-31T23:59:59Z").replace("Z", "+00:00")
        try: ends_at = datetime.fromisoformat(ends_at_str)
        except: ends_at = datetime(2026, 12, 31, 23, 59, 59)
        try: liquidity = float(raw.get("liquidity") or 0)
        except: liquidity = 0.0
        try: volume_24h = float(raw.get("volume24h") or raw.get("volume") or 0)
        except: volume_24h = 0.0
        spread = 0.05
        try:
            bid = float(raw.get("bestBid", 0)); ask = float(raw.get("bestAsk", 1))
            if ask > 0: spread = (ask - bid) / ask
        except: pass
        slug = raw.get("slug", ""); mid = raw.get("id", "")
        url = f"https://polymarket.com/event/{slug}" if slug else f"https://polymarket.com/market/{mid}"
        return Market(id=mid or slug or "unknown", title=raw.get("question") or raw.get("title") or "Untitled", category=cat, confidence=confidence, ev=raw.get("expected_value"), liquidity=liquidity, spread=spread, volume_24h=volume_24h, ends_at=ends_at, url=url)
    @staticmethod
    def _infer_category(tags, question):
        labels = [t.get("label", "") if isinstance(t, dict) else str(t) for t in tags]; text = " ".join(labels + [question]).lower()
        if any(k in text for k in ("crypto", "bitcoin", "ethereum", "btc", "eth", "token", "defi", "nft")): return Category.CRYPTO
        if any(k in text for k in ("election", "vote", "poll", "senate", "congress", "president", "governor")): return Category.POLITICS
        if any(k in text for k in ("sports", "nba", "nfl", "soccer", "baseball", "tennis", "game", "match")): return Category.SPORTS
        if any(k in text for k in ("economy", "gdp", "inflation", "unemployment", "fed", "interest rate", "recession")): return Category.ECONOMY
        if any(k in text for k in ("tech", "ai", "apple", "google", "microsoft", "tesla", "elon", "chip")): return Category.TECH
        return Category.OTHER
    @staticmethod
    def _compute_confidence(raw):
        score = 0.5
        try:
            vol = float(raw.get("volume24h") or raw.get("volume") or 0)
            if vol > 100000: score += 0.15
            elif vol > 50000: score += 0.10
            elif vol > 10000: score += 0.05
        except: pass
        return min(1.0, score)
