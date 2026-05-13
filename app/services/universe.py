from __future__ import annotations
import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
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
        stale = self._last_refresh is None or (datetime.now(timezone.utc) - self._last_refresh) > timedelta(minutes=5)
        if stale or not self._markets:
            await self.refresh()
        return self._markets

    async def refresh(self):
        async with self._refresh_lock:
            raw = await self._fetch_raw()
            scored = [self._score(r) for r in raw]
            now = datetime.now(timezone.utc)
            active = [m for m in scored if m.ends_at > now and m.liquidity > 500 and m.spread < 0.10]
            self._markets = active
            self._last_refresh = now
            logger.warning("universe_refresh_complete raw=%d active=%d", len(raw), len(active))
            return active

    async def _fetch_raw(self):
        url = f"{self.gamma_base}/markets"
        markets, offset, limit = [], 0, 100
        headers = {"User-Agent": "PolyTradingMVP/1.0"}
        async with httpx.AsyncClient(timeout=30) as client:
            while len(markets) < self.max_markets:
                try:
                    resp = await client.get(
                        url,
                        headers=headers,
                        params={"limit": limit, "offset": offset, "closed": "false", "active": "true"}
                    )
                    resp.raise_for_status()
                    payload = resp.json()

                    # FIX: handle both raw list and wrapped dict responses
                    if isinstance(payload, list):
                        data = payload
                    elif isinstance(payload, dict):
                        data = payload.get("markets") or payload.get("data") or payload.get("results") or []
                    else:
                        logger.warning("unexpected_api_response_type type=%s", type(payload).__name__)
                        break

                    if not data:
                        break

                    markets.extend(data)
                    if len(data) < limit:
                        break
                    offset += limit
                    await asyncio.sleep(0.15)

                except httpx.HTTPStatusError as e:
                    logger.warning("api_http_error status=%d", e.response.status_code)
                    if e.response.status_code == 429:
                        return markets
                    raise
                except Exception as e:
                    logger.warning("api_fetch_error: %s", e)
                    return markets

        logger.warning("fetch_raw_complete count=%d", len(markets))
        return markets

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

        spread = 0.05
        try:
            bid = float(raw.get("bestBid", 0))
            ask = float(raw.get("bestAsk", 1))
            if ask > 0:
                spread = (ask - bid) / ask
        except Exception:
            pass

        slug = raw.get("slug", "")
        mid = raw.get("id", "")
        url = f"https://polymarket.com/event/{slug}" if slug else f"https://polymarket.com/market/{mid}"

        return Market(
            id=mid or slug or "unknown",
            title=raw.get("question") or raw.get("title") or "Untitled",
            category=cat,
            confidence=confidence,
            ev=raw.get("expected_value"),
            liquidity=liquidity,
            spread=spread,
            volume_24h=volume_24h,
            ends_at=ends_at,
            url=url,
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
        if any(k in text for k in ("tech", "ai", "apple", "google", "microsoft", "tesla", "elon", "chip")):
            return Category.TECH
        return Category.OTHER

    @staticmethod
    def _compute_confidence(raw):
        score = 0.5
        try:
            vol = float(raw.get("volume24h") or raw.get("volume") or 0)
            if vol > 100000:
                score += 0.15
            elif vol > 50000:
                score += 0.10
            elif vol > 10000:
                score += 0.05
        except Exception:
            pass
        return min(1.0, score)
