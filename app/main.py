from __future__ import annotations
import logging
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.calibration import CalibrationService
from app.cashout import CashoutManager
from app.config import settings, WALLET_ADDRESS
from app.db import init_db
from app.engine import TradingEngine
from app.polymarket import PolymarketAPI
from app.services.universe import UniverseService
from app.routers import dashboard

logger = logging.getLogger("app.main")
_cycle_lock = asyncio.Lock()

async def _run_cycle_loop(engine: TradingEngine, cashout: CashoutManager, interval_sec: int = 60):
    while True:
        await asyncio.sleep(interval_sec)
        if _cycle_lock.locked():
            logger.warning("cycle_still_running_skipping")
            continue
        try:
            async with _cycle_lock:
                try:
                    cashout_actions = await asyncio.wait_for(cashout.evaluate_all(), timeout=60)
                    if cashout_actions:
                        logger.info("cashout_actions=%d", len(cashout_actions))
                except Exception as e:
                    logger.exception("cashout_eval_failed: %s", e)
                result = await asyncio.wait_for(engine.run_cycle(), timeout=300)
                logger.info("cycle_complete: %s", result)
        except asyncio.TimeoutError:
            logger.error("cycle_timeout_exceeded_300s")
        except Exception as e:
            logger.exception("cycle_failed: %s", e)

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        db_result = init_db()
        logger.info("db_init schema=%s created=%s", db_result.schema_version, db_result.tables_created)
    except Exception as e:
        logger.exception("db_init_failed: %s", e)

    api = PolymarketAPI()
    universe = UniverseService()
    calibration = CalibrationService()
    engine = TradingEngine(api, universe, calibration)
    cashout = CashoutManager(api)

    app.state.api = api
    app.state.universe = universe
    app.state.calibration = calibration
    app.state.engine = engine
    app.state.cashout = cashout
    app.state.settings = settings

    logger.info("startup_wallet_connected wallet=%s", WALLET_ADDRESS or "not_configured")
    try:
        positions = await api.get_positions(limit=1)
        logger.info("startup_positions_fetch_ok items=%d", len(positions))
    except Exception as e:
        logger.warning("startup_positions_fetch_failed: %s", e)

    try:
        await universe.refresh()
        logger.info("initial_universe_refresh markets=%d", len(universe._markets))
    except Exception as e:
        logger.exception("initial_universe_refresh_failed: %s", e)

    app.state._cycle_task = asyncio.create_task(
        _run_cycle_loop(engine, cashout, interval_sec=60)
    )
    yield
    app.state._cycle_task.cancel()
    try:
        await app.state._cycle_task
    except asyncio.CancelledError:
        pass

app = FastAPI(title="Poly Trading MVP", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(dashboard.router, tags=["dashboard"])

@app.get("/")
async def root():
    return FileResponse("static/index.html")



@app.get("/favicon.ico")
@app.get("/apple-touch-icon.png")
@app.get("/apple-touch-icon-precomposed.png")
async def root_favicon():
    favicon_path = "static/favicon.ico"
    if Path(favicon_path).exists():
        return FileResponse(favicon_path)
    raise HTTPException(status_code=404, detail="Not found")

@app.get("/healthz")
async def health(request: Request):
    universe = getattr(request.app.state, "universe", None)
    engine = getattr(request.app.state, "engine", None)
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "markets_cached": len(universe._markets) if universe else 0,
        "last_refresh": universe._last_refresh.isoformat() if universe and universe._last_refresh else None,
        "auto_execute": settings.auto_execute,
        "dry_run": not settings.auto_execute,
        "cycle_running": _cycle_lock.locked(),
        "trades_today": engine.daily_stats["trades_today"] if engine else 0,
    }

@app.post("/cycle")
async def trigger_cycle(request: Request):
    engine = getattr(request.app.state, "engine", None)
    if not engine:
        return {"status": "error", "detail": "engine_not_ready"}
    result = await engine.run_cycle()
    return result

@app.post("/cashout")
async def trigger_cashout(request: Request):
    cashout = getattr(request.app.state, "cashout", None)
    if not cashout:
        return {"status": "error", "detail": "cashout_not_ready"}
    actions = await cashout.evaluate_all()
    return {"status": "ok", "actions": actions}
