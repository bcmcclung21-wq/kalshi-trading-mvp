from __future__ import annotations
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select

from app.db import session_scope
from app.models import OrderRecord

logger = logging.getLogger("app.dashboard")

router = APIRouter()


def _recent_trades(limit: int = 25) -> list[dict]:
    try:
        with session_scope() as session:
            stmt = select(OrderRecord).order_by(OrderRecord.created_at.desc()).limit(limit)
            rows = session.execute(stmt).scalars().all()
            return [
                {
                    "id": r.id,
                    "market_id": r.ticker,
                    "side": r.side,
                    "price": round((r.price_cents or 0) / 100.0, 4),
                    "size": r.count,
                    "status": r.status,
                    "timestamp": r.created_at.isoformat() if r.created_at else None,
                    "category": r.category,
                    "dry_run": r.dry_run,
                }
                for r in rows
            ]
    except Exception as exc:
        logger.warning("dashboard_recent_trades_failed err=%s", exc)
        return []

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

    trades = _recent_trades(limit=25)

    category_counts = {}
    for m in markets:
        cat = m.get("category", "other")
        category_counts[cat] = category_counts.get(cat, 0) + 1

    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "markets": markets,
        "markets_count": len(markets),
        "category_breakdown": category_counts,
        "trades": trades,
        "positions": engine.daily_stats.get("positions", []) if engine and hasattr(engine, "daily_stats") else [],
        "alerts": engine.daily_stats.get("alerts", []) if engine and hasattr(engine, "daily_stats") else [],
        "balance": None,
        "auto_execute": getattr(settings, "auto_execute", False) if settings else False,
        "dry_run": getattr(settings, "dry_run", True) if settings else True,
        "allow_combos": getattr(settings, "allow_combos", False) if settings else False,
        "cycle_interval_seconds": getattr(settings, "cycle_interval_seconds", None) if settings else None,
        "cache_ttl_seconds": getattr(settings, "cache_ttl_seconds", None) if settings else None,
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


@router.get("/api/mode")
async def get_mode(request: Request):
    settings = request.app.state.settings
    return {
        "status": "ok",
        "mode": "DRY" if settings.dry_run else "LIVE",
        "dry_run": settings.dry_run,
        "auto_execute": settings.auto_execute,
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
    return {"status": "ok", "mode": "DRY" if settings.dry_run else "LIVE", "dry_run": settings.dry_run, "auto_execute": settings.auto_execute}
