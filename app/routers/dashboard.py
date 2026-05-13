from __future__ import annotations
from datetime import datetime, timezone
from fastapi import APIRouter, Request
import logging

logger = logging.getLogger("app.routers.dashboard")
router = APIRouter()

_cache = {"ts": None, "payload": None}


@router.get("/dashboard")
async def dashboard(request: Request):
    now = datetime.now(timezone.utc)

    if _cache["ts"] and (now - _cache["ts"]).total_seconds() < 5:
        return _cache["payload"]

    universe = getattr(request.app.state, "universe", None)
    engine = getattr(request.app.state, "engine", None)
    settings = getattr(request.app.state, "settings", None)

    markets = []
    if universe is not None:
        market_list = getattr(universe, "_markets", [])
        logger.info("dashboard_universe_markets_count=%d", len(market_list))
        cutoff = now
        for i, market in enumerate(market_list[:50]):
            try:
                if isinstance(market, dict):
                    mid = market.get("id", "")
                    title = market.get("title", "Untitled")
                    cat = str(market.get("category", "other"))
                    confidence = float(market.get("confidence", 0))
                    liquidity = float(market.get("liquidity", 0))
                    spread = float(market.get("spread", 1))
                    url = market.get("url", "")
                    ends_at = market.get("ends_at")
                else:
                    mid = getattr(market, "id", "")
                    title = getattr(market, "title", "Untitled")
                    cat = (
                        market.category.value
                        if hasattr(market, "category") and hasattr(market.category, "value")
                        else str(getattr(market, "category", "other"))
                    )
                    confidence = float(getattr(market, "confidence", 0))
                    liquidity = float(getattr(market, "liquidity", 0))
                    spread = float(getattr(market, "spread", 1))
                    url = getattr(market, "url", "")
                    ends_at = getattr(market, "ends_at", None)

                if not mid:
                    continue

                active = True
                if ends_at:
                    try:
                        if isinstance(ends_at, str):
                            ends_at = datetime.fromisoformat(ends_at.replace("Z", "+00:00"))
                        active = ends_at > cutoff
                    except Exception:
                        active = True

                markets.append({
                    "id": mid,
                    "title": title,
                    "category": cat,
                    "confidence": round(confidence, 3),
                    "liquidity": liquidity,
                    "spread": round(spread, 4),
                    "url": url,
                    "active": active,
                })
            except Exception as e:
                logger.warning("dashboard_market_parse_error idx=%d: %s", i, e)
                continue
    else:
        logger.warning("dashboard_universe_is_none")

    logger.info("dashboard_parsed_markets=%d", len(markets))

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
