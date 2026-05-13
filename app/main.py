import os
import asyncio
import logging
from datetime import datetime, timezone
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from contextlib import asynccontextmanager
import pathlib

from app.polymarket import PolyMarketAPI
from app.engine import TradingEngine
from app.services.universe import UniverseService
from app.strategy import TUNING
from app.calibration import compute_brier, latest_snapshot, persist_snapshot

logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO")),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("app.main")

api = PolyMarketAPI()
universe = UniverseService()
engine = TradingEngine(api, universe)

_last_cycle_result = {
    "markets": 0,
    "candidates": 0,
    "orders": 0,
    "submitted": 0,
    "wins": 0,
    "losses": 0,
    "rejected": 0,
    "settled": 0,
    "realized_pnl": 0.0,
    "last_cycle_at": None,
    "error": None,
}

_last_calibration = {
    "brier_score": 0.0,
    "status": "ok",
    "trades_evaluated": 0,
}


async def _cycle_loop():
    while True:
        try:
            if not api.auth_ok:
                logger.warning("cycle_loop_skipped_not_authenticated")
                await asyncio.sleep(TUNING.check_interval_sec)
                continue

            cal = compute_brier(
                window_size=TUNING.calibration_window_size,
                threshold=TUNING.calibration_brier_threshold,
            )
            _last_calibration.update({
                "brier_score": cal.get("brier_score", 0.0),
                "status": cal.get("status", "ok"),
                "trades_evaluated": cal.get("trades_evaluated", 0),
            })
            persist_snapshot(cal)

            if cal.get("status") == "halted":
                logger.warning(
                    "calibration_halt_active brier=%.4f threshold=%.4f trades=%d cooldown=%ds",
                    cal["brier_score"], TUNING.calibration_brier_threshold,
                    cal["trades_evaluated"], TUNING.calibration_cooldown_sec,
                )
                await asyncio.sleep(TUNING.calibration_cooldown_sec)
                continue

            result = await engine.run_cycle()
            _last_cycle_result.update(result)
            _last_cycle_result["last_cycle_at"] = datetime.now(timezone.utc).isoformat()
            _last_cycle_result["error"] = None
            _last_cycle_result["settled"] = cal.get("trades_evaluated", 0)

        except Exception as e:
            logger.error("cycle_loop_error", exc_info=True)
            _last_cycle_result["error"] = str(e)

        await asyncio.sleep(TUNING.check_interval_sec)


async def _universe_sync_loop():
    while True:
        try:
            await asyncio.sleep(5)
            await universe.refresh()
            logger.info("universe_refreshed", extra={"count": len(universe._cache)})
        except Exception:
            logger.error("universe_sync_error", exc_info=True)
        await asyncio.sleep(TUNING.market_sync_interval_sec)


async def _calibration_loop():
    while True:
        try:
            await asyncio.sleep(TUNING.calibration_interval_sec)
            cal = compute_brier(
                window_size=TUNING.calibration_window_size,
                threshold=TUNING.calibration_brier_threshold,
            )
            _last_calibration.update({
                "brier_score": cal.get("brier_score", 0.0),
                "status": cal.get("status", "ok"),
                "trades_evaluated": cal.get("trades_evaluated", 0),
            })
            persist_snapshot(cal)
        except Exception:
            logger.error("calibration_loop_error", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("startup", extra={"auto_execute": os.getenv("AUTO_EXECUTE", "false")})
    ok = await api.health_check()
    if not ok:
        logger.error("startup_auth_failed")

    tasks = []
    if ok:
        tasks.append(asyncio.create_task(_universe_sync_loop()))
        tasks.append(asyncio.create_task(_cycle_loop()))
        tasks.append(asyncio.create_task(_calibration_loop()))

    yield

    for t in tasks:
        t.cancel()
    logger.info("shutdown")


app = FastAPI(title=os.getenv("APP_NAME", "Poly Trading MVP"), lifespan=lifespan)

_static_dir = pathlib.Path(__file__).parent.parent / "static"
if _static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


@app.get("/", response_class=HTMLResponse)
async def root():
    index_path = _static_dir / "index.html"
    if index_path.exists():
        return index_path.read_text()
    return {
        "status": "ok",
        "auth_ok": api.auth_ok,
        "auto_execute": os.getenv("AUTO_EXECUTE", "false"),
        "allow_combos": os.getenv("ALLOW_COMBOS", "false"),
        "note": "static/index.html not found; visit /api/dashboard for JSON",
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
    _last_cycle_result.update(result)
    _last_cycle_result["last_cycle_at"] = datetime.now(timezone.utc).isoformat()
    return result


@app.get("/api/dashboard")
async def dashboard():
    cal = _last_calibration
    snap = latest_snapshot()
    positions = await api.get_positions()
    balances = await api.get_balances()

    realized_pnl = sum(p.get("realized_pnl", 0) for p in positions) if positions else 0.0
    wins = sum(1 for p in positions if p.get("realized_pnl", 0) > 0)
    losses = sum(1 for p in positions if p.get("realized_pnl", 0) <= 0)

    return {
        "markets": _last_cycle_result.get("markets", 0),
        "candidates": _last_cycle_result.get("candidates", 0),
        "orders": _last_cycle_result.get("orders", 0),
        "submitted": _last_cycle_result.get("orders", 0),
        "wins": wins,
        "losses": losses,
        "settled": cal.get("trades_evaluated", 0),
        "realized_pnl": round(realized_pnl, 2),
        "calibration": {
            "status": cal.get("status", "ok"),
            "brier": cal.get("brier_score", 0.0),
            "threshold": TUNING.calibration_brier_threshold,
            "trades_in_window": cal.get("trades_evaluated", 0),
            "window_size": TUNING.calibration_window_size,
            "computed_at": snap.computed_at.isoformat() if snap else datetime.now(timezone.utc).isoformat(),
            "buckets": cal.get("bucket_breakdown", {}),
        },
        "system_health": {
            "stage": "running",
            "init_db_ok": True,
            "engine_started": True,
            "uptime": 0,
            "last_sync": datetime.now(timezone.utc).isoformat(),
            "last_cycle": _last_cycle_result.get("last_cycle_at") or datetime.now(timezone.utc).isoformat(),
            "last_reconcile": datetime.now(timezone.utc).isoformat(),
            "last_audit": None,
            "last_error": _last_cycle_result.get("error"),
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
