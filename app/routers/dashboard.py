from __future__ import annotations
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Request

logger = logging.getLogger("app.dashboard")

router = APIRouter()

@router.get("/dashboard")
@router.get("/api/dashboard")
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


@router.get("/api/markets")
async def markets(request: Request):
    payload = await dashboard(request)
    markets_data = payload.get("markets", [])
    for market in markets_data:
        market["status"] = "watching" if market.get("active") else "closed"
    return {
        "status": payload.get("status", "ok"),
        "timestamp": payload.get("timestamp"),
        "markets": markets_data,
        "markets_count": len(markets_data),
    }


@router.post("/api/mode")
async def set_mode(request: Request):
    body = await request.json()
    mode = str(body.get("mode", "")).strip().lower()
    token = request.headers.get("x-mode-token", "")
    expected = getattr(request.app.state.settings, "polymarket_secret_key", "")
    if expected and token != expected:
        raise HTTPException(status_code=401, detail="unauthorized")
    if mode not in {"dry", "live"}:
        raise HTTPException(status_code=400, detail="mode must be dry or live")

    settings = request.app.state.settings
    settings.dry_run = mode == "dry"
    settings.auto_execute = mode == "live"
    return {"status": "ok", "mode": "DRY" if settings.dry_run else "LIVE", "auto_execute": settings.auto_execute}
