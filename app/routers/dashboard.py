from __future__ import annotations
from datetime import datetime, timezone
from fastapi import APIRouter, Request

router = APIRouter()

# In-memory cache: 5-second TTL to protect from polling
_cache = {"ts": None, "payload": None}


@router.get("/dashboard")
async def dashboard(request: Request):
    now = datetime.now(timezone.utc)

    # Return cached payload if fresh
    if _cache["ts"] and (now - _cache["ts"]).total_seconds() < 5:
        return _cache["payload"]

    # Pull services from app.state (never import from main)
    universe = getattr(request.app.state, "universe", None)
    engine = getattr(request.app.state, "engine", None)
    settings = getattr(request.app.state, "settings", None)

    markets = []
    if universe is not None and hasattr(universe, "_markets"):
        cutoff = now
        for market in universe._markets[:50]:
            if not hasattr(market, "id"):
                continue
            markets.append(
                {
                    "id": market.id,
                    "title": market.title,
                    "category": (
                        market.category.value
                        if hasattr(market.category, "value")
                        else str(market.category)
                    ),
                    "confidence": round(market.confidence, 3),
                    "liquidity": market.liquidity,
                    "spread": round(market.spread, 4),
                    "url": market.url,
                    "active": (
                        market.ends_at > cutoff
                        if hasattr(market, "ends_at")
                        else True
                    ),
                }
            )

    trades = []
    daily_stats = {}
    brier_score = 0.0
    win_rate = 0.0
    learning_state = {}
    last_plan = {}

    if engine is not None and hasattr(engine, "daily_stats"):
        daily_stats = engine.daily_stats
        brier_score = daily_stats.get("brier_score", 0.0)
        win_rate = daily_stats.get("win_rate", 0.0)
        trades = daily_stats.get("last_trades", [])[:20]
        last_plan = daily_stats.get("last_plan", {})

    try:
        from app.strategy import TUNER
        learning_state = TUNER.learning.to_dict()
    except Exception:
        pass

    payload = {
        "status": "ok",
        "timestamp": now.isoformat(),
        "markets": markets,
        "markets_count": len(markets),
        "trades": trades,
        "daily_stats": daily_stats,
        "post_mortems": daily_stats.get("post_mortems", []),
        "balance": None,
        "auto_execute": settings.auto_execute if settings else False,
        "allow_combos": settings.allow_combos if settings else False,
        "brier_score": brier_score,
        "win_rate": win_rate,
        "learning": learning_state,
        "last_plan": last_plan,
    }

    _cache["ts"] = now
    _cache["payload"] = payload
    return payload
