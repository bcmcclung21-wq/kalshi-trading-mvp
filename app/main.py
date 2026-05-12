import os
import logging
from fastapi import FastAPI, HTTPException
from contextlib import asynccontextmanager

from app.polymarket import PolyMarketAPI
from app.engine import TradingEngine
from app.services.universe import UniverseService

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

@app.get("/positions")
async def positions():
    return await api.get_positions()

@app.get("/balances")
async def balances():
    return await api.get_balances()
