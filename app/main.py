from __future__ import annotations
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI
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

# ------------------------------------------------------------------
# Background cycle task — THE ROOT FIX
# ------------------------------------------------------------------
async def _run_cycle_loop(engine: TradingEngine, interval_sec: int = 60):
    """Run trading cycles on a fixed interval with crash recovery."""
    while True:
        try:
            result = await engine.run_cycle()
            logger.info("cycle_complete: %s", result)
        except Exception as e:
            logger.exception("cycle_failed: %s", e)
        await asyncio.sleep(interval_sec)

# ------------------------------------------------------------------
# Lifespan
# ------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    global api, universe, calibration, engine, cashout

    api = PolymarketAPI()
    universe = UniverseService()
    calibration = CalibrationService()
    engine = TradingEngine(api, universe, calibration)
    cashout = CashoutManager(api)

    # START THE REAL BACKGROUND CYCLE (was: asyncio.sleep(999999))
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
app.include_router(dashboard.router, prefix="/api", tags=["dashboard"])

@app.get("/")
async def root():
    return FileResponse("static/index.html")

@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "markets_cached": len(universe._markets) if "universe" in globals() else 0,
        "last_refresh": (
            universe._last_refresh.isoformat()
            if "universe" in globals() and universe._last_refresh
            else None
        ),
        "auto_execute": settings.auto_execute,
        "dry_run": not settings.auto_execute,
    }

@app.post("/api/trigger-cycle")
async def trigger_cycle():
    """Manual trigger for testing."""
    result = await engine.run_cycle()
    return result
