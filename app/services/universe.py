"""
UniverseService — market discovery & filtering for Poly Trading MVP.
Categories: sports, politics, crypto, climate, economics.
"""

import os
import asyncio
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum
import httpx
from datetime import datetime, timedelta


class Category(Enum):
    SPORTS = "sports"
    POLITICS = "politics"
    CRYPTO = "crypto"
    CLIMATE = "climate"
    ECONOMICS = "economics"


@dataclass
class Market:
    id: str
    title: str
    category: Category
    confidence: float          # 0.0 - 1.0
    ev: Optional[float]        # expected value, optional
    liquidity: float
    spread: float              # bid-ask spread
    volume_24h: float
    ends_at: datetime
    url: str


class UniverseService:
    """
    Fetches, scores, and filters Polymarket US markets.
    Keeps singles-first, high-confidence, market-quality-first rules.
    """

    BANKROLL_PCT: Dict[int, float] = {
        1: 0.02,
        2: 0.01,
        3: 0.0075,
        4: 0.005,
    }

    def __init__(
        self,
        api_base: Optional[str] = None,
        min_confidence: float = 0.60,
        min_liquidity: float = 5000.0,
        max_spread: float = 0.05,
        allowed_categories: Optional[List[Category]] = None,
    ):
        self.api_base = api_base or os.getenv(
            "DASHBOARD_BASE_URL", "https://polymarket.com/api"
        )
        self.min_confidence = min_confidence
        self.min_liquidity = min_liquidity
        self.max_spread = max_spread
        self.allowed_categories = allowed_categories or list(Category)
        self._cache: List[Market] = []
        self._last_fetch: Optional[datetime] = None

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    async def refresh(self) -> List[Market]:
        """Fetch latest markets and cache them."""
        raw = await self._fetch_raw()
        scored = [self._score(m) for m in raw]
        filtered = [m for m in scored if self._passes_filters(m)]
        # Sort: confidence desc, then liquidity desc, then EV desc
        filtered.sort(key=lambda m: (m.confidence, m.liquidity, m.ev or 0), reverse=True)
        self._cache = filtered
        self._last_fetch = datetime.utcnow()
        return filtered

    async def get_candidates(
        self,
        category: Optional[Category] = None,
        min_confidence: Optional[float] = None,
        top_n: int = 20,
    ) -> List[Market]:
        """Return top-N trade candidates."""
        if self._stale():
            await self.refresh()
        markets = self._cache
        if category:
            markets = [m for m in markets if m.category == category]
        if min_confidence is not None:
            markets = [m for m in markets if m.confidence >= min_confidence]
        return markets[:top_n]

    async def get_active_markets(self) -> List[Dict[str, Any]]:
        """Return raw active markets from Polymarket gateway."""
        if self._stale():
            await self.refresh()
        # Return as dicts to match engine expectations
        return [
            {
                "ticker": m.id,
                "title": m.title,
                "category": m.category.value,
                "type": "single",
                "legs": 1,
                "close_time": m.ends_at.isoformat() if m.ends_at else "",
            }
            for m in self._cache
        ]

    def size_position(self, market: Market, bankroll: float, legs: int = 1) -> float:
        """Fixed bankroll % sizing. README: 1-leg = 2%, 2-leg = 1%, etc."""
        pct = self.BANKROLL_PCT.get(legs, 0.005)
        return round(bankroll * pct, 2)

    async def daily_audit(self, trades: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Run end-of-day audit to learn from prior trades."""
        now = datetime.utcnow()
        summary = {
            "date": now.isoformat(),
            "total_trades": len(trades),
            "wins": sum(1 for t in trades if t.get("pnl", 0) > 0),
            "losses": sum(1 for t in trades if t.get("pnl", 0) <= 0),
            "net_pnl": sum(t.get("pnl", 0) for t in trades),
            "avg_confidence": (
                sum(t.get("confidence", 0) for t in trades) / len(trades)
                if trades else 0
            ),
            "learnings": [],
        }
        for t in trades:
            if t.get("pnl", 0) < 0 and t.get("confidence", 0) > 0.8:
                summary["learnings"].append(
                    f"High-confidence loss on {t.get('market_id')}: review model."
                )
        return summary

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #

    async def _fetch_raw(self) -> List[Dict[str, Any]]:
        """Fetch from Polymarket gateway."""
        import os
        base = os.getenv("POLYMARKET_GATEWAY_BASE", "https://gateway.polymarket.us/v1")
        markets = []
        offset = 0
        async with httpx.AsyncClient(timeout=30) as client:
            while True:
                resp = await client.get(
                    f"{base}/markets",
                    params={
                        "limit": 100,
                        "offset": offset,
                        "active": "true",
                        "closed": "false",
                        "archived": "false",
                    }
                )
                resp.raise_for_status()
                data = resp.json().get("data", [])
                if not data:
                    break
                markets.extend(data)
                if len(data) < 100:
                    break
                offset += 100
        return markets

    def _score(self, raw: Dict[str, Any]) -> Market:
        """Convert raw API record to scored Market."""
        cat = self._infer_category(raw.get("tags", []), raw.get("title", ""))
        confidence = self._compute_confidence(raw)
        ends_at_str = raw.get("endDate") or raw.get("closeDate") or "2026-12-31T23:59:59+00:00"
        if ends_at_str:
            ends_at_str = ends_at_str.replace("Z", "+00:00")
        ends_at = datetime.fromisoformat(ends_at_str)
        return Market(
            id=raw.get("id") or raw.get("slug", "unknown"),
            title=raw.get("title", "Untitled"),
            category=cat,
            confidence=confidence,
            ev=raw.get("expected_value"),
            liquidity=raw.get("liquidity", 0),
            spread=raw.get("spread", 0.05),
            volume_24h=raw.get("volume24h", 0),
            ends_at=ends_at,
            url=raw.get("url", ""),
        )

    def _passes_filters(self, m: Market) -> bool:
        if m.category not in self.allowed_categories:
            return False
        if m.confidence < self.min_confidence:
            return False
        if m.liquidity < self.min_liquidity:
            return False
        if m.spread > self.max_spread:
            return False
        if m.ends_at < datetime.utcnow() + timedelta(hours=1):
            return False
        return True

    def _stale(self) -> bool:
        if self._last_fetch is None:
            return True
        return datetime.utcnow() - self._last_fetch > timedelta(minutes=5)

    @staticmethod
    def _infer_category(tags: List[str], title: str) -> Category:
        text = " ".join(tags + [title]).lower()
        if any(k in text for k in ("sport", "nba", "nfl", "soccer", "baseball")):
            return Category.SPORTS
        if any(k in text for k in ("election", "president", "senate", "vote", "poll")):
            return Category.POLITICS
        if any(k in text for k in ("bitcoin", "btc", "eth", "crypto", "ethereum", "blockchain")):
            return Category.CRYPTO
        if any(k in text for k in ("climate", "temperature", "carbon", "weather", "warming")):
            return Category.CLIMATE
        if any(k in text for k in ("gdp", "inflation", "fed", "rate", "unemployment", "cpi")):
            return Category.ECONOMICS
        return Category.POLITICS

    @staticmethod
    def _compute_confidence(raw: Dict[str, Any]) -> float:
        """Heuristic confidence score 0-1 based on market quality metrics."""
        liquidity = raw.get("liquidity", 0)
        volume = raw.get("volume24h", 0)
        spread = raw.get("spread", 0.05)
        liq_score = min(liquidity / 50_000, 1.0)
        vol_score = min(volume / 20_000, 1.0)
        spread_score = max(0, 1 - spread * 20)
        return round((liq_score * 0.4 + vol_score * 0.3 + spread_score * 0.3), 3)
