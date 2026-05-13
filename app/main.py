from __future__ import annotations
import logging
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.calibration import CalibrationService
from app.cashout import CashoutManager
from app.config import settings
from app.engine import TradingEngine
from app.polymarket import PolymarketAPI
from app.services.universe import UniverseService
from app.routers import dashboard

logger = logging.getLogger("app.main")
_cycle_lock = asyncio.Lock()

# ------------------------------------------------------------------
# Background cycle task
# ------------------------------------------------------------------
async def _run_cycle_loop(engine: TradingEngine, interval_sec: int = 60):
    while True:
        await asyncio.sleep(interval_sec)
        if _cycle_lock.locked():
            logger.warning("cycle_still_running_skipping")
            continue
        try:
            async with _cycle_lock:
                result = await asyncio.wait_for(engine.run_cycle(), timeout=300)
                logger.info("cycle_complete: %s", result)
        except asyncio.TimeoutError:
            logger.error("cycle_timeout_exceeded_300s")
        except Exception as e:
            logger.exception("cycle_failed: %s", e)

# ------------------------------------------------------------------
# Lifespan
# ------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    api = PolymarketAPI()
    universe = UniverseService()
    calibration = CalibrationService()
    engine = TradingEngine(api, universe, calibration)
    cashout = CashoutManager(api)

    # CRITICAL: expose to routers via request.app.state
    app.state.api = api
    app.state.universe = universe
    app.state.calibration = calibration
    app.state.engine = engine
    app.state.cashout = cashout
    app.state.settings = settings

    app.state._cycle_task = asyncio.create_task(
        _run_cycle_loop(engine, interval_sec=60)
    )

    yield

    app.state._cycle_task.cancel()
    try:
        await app.state._cycle_task
    except asyncio.CancelledError:
        pass

# ------------------------------------------------------------------
# App
# ------------------------------------------------------------------
app = FastAPI(title="Poly Trading MVP", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(dashboard.router, prefix="/v1", tags=["dashboard"])

@app.get("/")
async def root():
    return FileResponse("static/index.html")

@app.get("/v1/health")
async def health(request: Request):
    universe = getattr(request.app.state, "universe", None)
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "markets_cached": len(universe._markets) if universe else 0,
        "last_refresh": (
            universe._last_refresh.isoformat()
            if universe and universe._last_refresh
            else None
        ),
        "auto_execute": settings.auto_execute,
        "dry_run": not settings.auto_execute,
    }

@app.post("/v1/trigger-cycle")
async def trigger_cycle(request: Request):
    engine = getattr(request.app.state, "engine", None)
    if not engine:
        return {"status": "error", "detail": "engine_not_ready"}
    result = await engine.run_cycle()
    return result
