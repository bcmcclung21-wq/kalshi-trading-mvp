from __future__ import annotations
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, Request

logger = logging.getLogger("app.dashboard")

router = APIRouter()

@router.get("/dashboard")
async def dashboard(request: Request):
    universe = getattr(request.app.state, "universe", None)
    engine = getattr(request.app.state, "engine", None)
    settings = getattr(request.app.state, "settings", None)

    markets = []
    if universe is not None and hasattr(universe, "_markets"):
        items = universe._markets if isinstance(universe._markets, list) else []
        for market in items:
            if not hasattr(market, "id"):
                continue
            markets.append({
                "id": market.id,
                "title": market.title,
                "category": str(market.category.value if hasattr(market.category, "value") else market.category),
                "confidence": round(market.confidence, 2),
                "edge_bps": int(abs(market.confidence - market.last_price) * 10000) if market.last_price else 0,
                "spread": round(market.spread, 4),
                "liquidity": round(market.liquidity, 2),
                "last_price": round(market.last_price, 4),
                "ends_at": market.ends_at.isoformat() if market.ends_at else None,
                "url": market.url,
                "active": market.ends_at > datetime.now(timezone.utc) if market.ends_at else False,
            })
    logger.info("dashboard_markets_prepared count=%d", len(markets))

    trades = []
    if engine and hasattr(engine, "daily_stats"):
        trades = engine.daily_stats.get("last_trades", [])

    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "markets": markets,
        "markets_count": len(markets),
        "trades": trades,
        "balance": None,
        "auto_execute": getattr(settings, "auto_execute", False) if settings else False,
        "allow_combos": getattr(settings, "allow_combos", False) if settings else False,
        "trades_today": engine.daily_stats["trades_today"] if engine else 0,
        "daily_pnl": engine.daily_stats["daily_pnl"] if engine else 0.0,
        "brier_score": engine.daily_stats["brier_score"] if engine else 0.0,
        "win_rate": engine.daily_stats["win_rate"] if engine else 0.0,
    }
