from __future__ import annotations
import logging
import os
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
from app.models_router import load_primary_model, model_is_loaded
from app.polymarket import PolymarketAPI
from app.services.universe import UniverseService
from app.routers import dashboard

logger = logging.getLogger("app.main")
_cycle_lock = asyncio.Lock()
MODEL_PATH = Path("/app/models/primary.onnx")

# Worker role detection — CRITICAL: prevents duplicate work across uvicorn workers
ENGINE_WORKER = os.environ.get("ENGINE_WORKER", "false").lower() == "true"

async def _run_cycle_loop(engine: TradingEngine, cashout: CashoutManager, universe: UniverseService, interval_sec: int = 60):
    while True:
        try:
            if _cycle_lock.locked():
                logger.warning("cycle_still_running_skipping")
            else:
                async with _cycle_lock:
                    # ---- CRITICAL: refresh markets & orderbooks before every cycle ----
                    try:
                        await asyncio.wait_for(universe.refresh(), timeout=120)
                    except Exception as e:
                        logger.exception("universe_refresh_failed: %s", e)

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
        await asyncio.sleep(interval_sec)

@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.logging_config import configure_logging
    configure_logging()

    try:
        db_result = init_db()
        logger.info("db_init schema=%s created=%s", db_result.schema_version, db_result.tables_created)
    except Exception as e:
        logger.exception("db_init_failed: %s", e)

    api = PolymarketAPI()
    universe = UniverseService()
    await universe.initialize()

    # ---- CRITICAL: prime the cache before the engine starts ----
    try:
        await universe.refresh()
        logger.info("universe_primed markets=%d", len(universe._markets))
    except Exception as e:
        logger.exception("universe_prime_failed: %s", e)

    calibration = CalibrationService()
    engine = TradingEngine(api, universe, calibration)
    cashout = CashoutManager(api)

    app.state.api = api
    app.state.universe = universe
    app.state.calibration = calibration
    app.state.engine = engine
    app.state.cashout = cashout
    app.state.settings = settings
    app.state.primary_model = load_primary_model()
    app.state.model_loaded = app.state.primary_model is not None
    if app.state.model_loaded:
        logger.info("STARTUP: primary.onnx loaded successfully")
    else:
        logger.warning("STARTUP: primary.onnx missing — falling back to midpoint")

    if ENGINE_WORKER:
        app.state.engine_task = asyncio.create_task(
            _run_cycle_loop(engine, cashout, universe, interval_sec=60)
        )
        logger.info("engine_worker_started worker=true")
    else:
        logger.info("api_worker_started worker=false")

    yield

    if ENGINE_WORKER:
        if hasattr(app.state, 'engine_task'):
            app.state.engine_task.cancel()
            try:
                await app.state.engine_task
            except asyncio.CancelledError:
                pass
    await app.state.universe.aclose()
    from app.http_client import SharedHTTPClient
    await SharedHTTPClient.close()
    await app.state.api.aclose()

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
async def root(request: Request):
    accept = (request.headers.get("accept") or "").lower()
    if "text/html" in accept:
        return FileResponse("static/index.html")
    universe = getattr(request.app.state, "universe", None)
    return {
        "status": "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "active_count": len(universe._markets) if universe else 0,
        "last_refresh_timestamp": universe.last_refresh.isoformat() if universe and universe.last_refresh else None,
    }



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
    model_ok = model_is_loaded()
    mode = "dry" if os.getenv("AUTO_EXECUTE", "").strip().lower() != "true" else "live"
    return {
        "status": "ok" if model_ok else "degraded",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model_loaded": model_ok,
        "mode": mode,
        "markets_cached": len(universe._markets) if universe else 0,
        "active_count": len(universe._markets) if universe else 0,
        "last_refresh": universe.last_refresh.isoformat() if universe and getattr(universe, "last_refresh", None) else None,
        "last_refresh_timestamp": universe.last_refresh.isoformat() if universe and getattr(universe, "last_refresh", None) else None,
        "active_markets": getattr(universe, "active_markets_gauge", 0) if universe else 0,
        "processing_latency_p99": getattr(universe, "processing_latency_p99_ms", 0) if universe else 0,
        "auto_execute": settings.auto_execute,
        "dry_run": settings.dry_run,
        "cycle_running": _cycle_lock.locked(),
        "trades_today": engine.daily_stats["trades_today"] if engine else 0,
    }


@app.get("/healthz/live")
async def health_live(request: Request):
    model_ok = model_is_loaded()
    if not model_ok:
        raise HTTPException(status_code=503, detail="primary_model_unavailable")
    return {"status": "ok", "model_loaded": True, "timestamp": datetime.now(timezone.utc).isoformat()}

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


@app.post("/api/trade/run")
async def trade_run(request: Request):
    """FIX9: Manual on-demand cycle trigger."""
    eng = getattr(request.app.state, "engine", None)
    if not eng: raise HTTPException(503, "engine_not_ready")
    return await eng.run_cycle()


@app.post("/api/test-execution")
async def test_execution(request: Request):
    if os.getenv("AUTO_EXECUTE", "").strip().lower() != "true":
        return {"status": "blocked", "reason": "dry mode"}
    eng = getattr(request.app.state, "engine", None)
    if not eng:
        raise HTTPException(status_code=503, detail="engine_not_ready")
    result = await eng.run_cycle()
    trades = result.get("trades", 0) if isinstance(result, dict) else 0
    if trades <= 0:
        return {"status": "error", "reason": "no_trade_executed", "result": result}
    return {"status": "ok", "result": result, "tx_hash": "simulated_via_engine_cycle"}
