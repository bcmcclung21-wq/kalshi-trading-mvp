from __future__ import annotations
import asyncio, logging
from contextlib import asynccontextmanager
from datetime import datetime
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from app.calibration import CalibrationService
from app.cashout import CashoutManager
from app.engine import TradingEngine
from app.polymarket import PolymarketAPI
from app.services.universe import UniverseService
from app.routers import dashboard
logger = logging.getLogger('app.main')
@asynccontextmanager
async def lifespan(app: FastAPI):
    global api, universe, calibration, engine, cashout
    api=PolymarketAPI(); universe=UniverseService(); calibration=CalibrationService(); engine=TradingEngine(api,universe,calibration); cashout=CashoutManager(api)
    app.state._cycle_task = asyncio.create_task(asyncio.sleep(999999))
    yield
    app.state._cycle_task.cancel()
app = FastAPI(title='Poly Trading MVP', lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['*'], allow_headers=['*'])
app.mount('/static', StaticFiles(directory='static'), name='static')
app.include_router(dashboard.router, prefix='/api', tags=['dashboard'])
@app.get('/')
async def root(): return FileResponse('static/index.html')
@app.get('/api/health')
async def health(): return {'status':'ok','timestamp':datetime.utcnow().isoformat(),'markets_cached':len(universe._markets),'last_refresh': universe._last_refresh.isoformat() if universe._last_refresh else None}
