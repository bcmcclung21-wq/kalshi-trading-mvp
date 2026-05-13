from __future__ import annotations
from datetime import datetime, timezone
from fastapi import APIRouter, Request

router = APIRouter()

@router.get("/dashboard")
async def dashboard(request: Request):
    try:
        from app.main import universe, engine, cashout
        from app.config import settings
    except Exception:
        universe = None
        engine = None
        cashout = None
        settings = None

    markets = []
    if universe is not None and hasattr(universe, "_markets"):
        raw = universe._markets
        items = raw if isinstance(raw, list) else []
        now = datetime.now(timezone.utc)
        for market in items:
            if not hasattr(market, "id"):
                continue
            markets.append({
                "id": market.id,
                "title": market.title,
                "category": market.category.value if hasattr(market.category, "value") else str(market.category),
                "confidence": round(market.confidence, 3),
                "liquidity": market.liquidity,
                "spread": round(market.spread, 4),
                "url": market.url,
                "active": market.ends_at > now if hasattr(market, "ends_at") else True,
            })
            if len(markets) >= 50:
                break

    trades = []
    daily_stats = {}
    if engine is not None and hasattr(engine, "daily_stats"):
        daily_stats = engine.daily_stats

    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "markets": markets,
        "markets_count": len(markets),
        "trades": trades,
        "daily_stats": daily_stats,
        "balance": None,
        "auto_execute": settings.auto_execute if settings else False,
        "allow_combos": settings.allow_combos if settings else False,
    }
