import os
import logging
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
from contextlib import asynccontextmanager

from app.polymarket import PolyMarketAPI
from app.engine import TradingEngine
from app.services.universe import UniverseService
from app.strategy import TUNING
from app.calibration import compute_brier, latest_snapshot

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("app.main")

api = PolyMarketAPI()
universe = UniverseService()
engine = TradingEngine(api, universe)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("startup", extra={"auto_execute": os.getenv("AUTO_EXECUTE", "false")})
    ok = await api.health_check()
    if not ok:
        logger.error("startup_auth_failed")
    yield
    logger.info("shutdown")

app = FastAPI(title=os.getenv("APP_NAME", "Poly Trading MVP"), lifespan=lifespan)

@app.get("/")
async def root():
    return {
        "status": "ok",
        "auth_ok": api.auth_ok,
        "auto_execute": os.getenv("AUTO_EXECUTE", "false"),
        "allow_combos": os.getenv("ALLOW_COMBOS", "false"),
    }

@app.get("/health")
async def health():
    ok = await api.health_check()
    if not ok:
        raise HTTPException(status_code=503, detail="auth_failed")
    return {"status": "healthy", "auth_ok": True}

@app.post("/cycle")
async def run_cycle():
    if not api.auth_ok:
        raise HTTPException(status_code=503, detail="not_authenticated")
    result = await engine.run_cycle()
    return result

@app.get("/api/dashboard")
async def dashboard():
    cal = compute_brier(window_size=TUNING.calibration_window_size, threshold=TUNING.calibration_brier_threshold)
    snap = latest_snapshot()
    positions = await api.get_positions()
    balances = await api.get_balances()
    return {
        "markets": 0,
        "candidates": 0,
        "orders": 0,
        "submitted": 0,
        "wins": 0,
        "losses": 0,
        "settled": cal.get("trades_evaluated", 0),
        "realized_pnl": 0.0,
        "calibration": {
            "status": cal.get("status", "ok"),
            "brier": cal.get("brier_score", 0.0),
            "threshold": TUNING.calibration_brier_threshold,
            "trades_in_window": cal.get("trades_evaluated", 0),
            "window_size": TUNING.calibration_window_size,
            "computed_at": cal.get("raw", {}).get("computed_at"),
            "buckets": cal.get("bucket_breakdown", {}),
        },
        "system_health": {
            "stage": "running",
            "init_db_ok": True,
            "engine_started": True,
            "uptime": 0,
            "last_sync": datetime.now(timezone.utc).isoformat(),
            "last_cycle": datetime.now(timezone.utc).isoformat(),
            "last_reconcile": datetime.now(timezone.utc).isoformat(),
            "last_audit": None,
            "last_error": None,
        },
        "execution_posture": {
            "auth_ok": api.auth_ok,
            "auto_execute": os.getenv("AUTO_EXECUTE", "false").lower() == "true",
            "allow_combos": TUNING.allow_combos,
            "same_day_only": TUNING.same_day_only,
            "min_minutes_to_close": TUNING.min_minutes_to_close,
            "max_days_to_close": TUNING.max_days_to_close,
            "max_orders_per_cycle": TUNING.max_orders_per_cycle,
            "max_category_exposure_pct": TUNING.max_category_exposure_pct * 100,
        },
        "positions": positions,
        "balances": balances,
    }

@app.get("/positions")
async def positions():
    return await api.get_positions()

@app.get("/balances")
async def balances():
    return await api.get_balances()
