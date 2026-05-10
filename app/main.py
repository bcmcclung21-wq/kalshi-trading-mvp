from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, select

from app.config import settings
from app.db import SessionLocal, init_db, verify_required_tables
from app.engine import TradingEngine
from app.models import AuditRun, CandidateRun, MarketSnapshot, OrderRecord, PositionSnapshot, ResearchNote
from app.observability import configure_logging
from app.schemas import ResearchNoteCreate
from app.strategy import BANKROLL_RULES, CATEGORIES, TUNING

logger = logging.getLogger(__name__)
templates = Jinja2Templates(directory="app/templates")


BOOT_STATUS = {
    "stage": "pre_lifespan",
    "started_at": time.time(),
    "init_db_ok": False,
    "engine_started": False,
    "last_error": None,
    "skip_db_bootstrap": os.getenv("SKIP_DB_BOOTSTRAP", "").strip() in ("1", "true", "True", "yes"),
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    BOOT_STATUS["stage"] = "configure_logging_done"
    logger.info("startup_stage=init_db_begin skip_bootstrap=%s", BOOT_STATUS["skip_db_bootstrap"])

    bootstrap = None
    try:
        bootstrap = await asyncio.wait_for(asyncio.to_thread(init_db), timeout=45.0)
        BOOT_STATUS["init_db_ok"] = True
        BOOT_STATUS["stage"] = "init_db_done"
        logger.info("startup_stage=init_db_done")
    except asyncio.TimeoutError:
        BOOT_STATUS["stage"] = "init_db_timeout"
        BOOT_STATUS["last_error"] = "init_db_timeout_45s"
        logger.warning("startup_stage=init_db_timeout proceeding_in_degraded_mode")
    except Exception as exc:
        BOOT_STATUS["stage"] = "init_db_error"
        BOOT_STATUS["last_error"] = f"init_db: {exc}"
        logger.exception("startup_stage=init_db_error proceeding_in_degraded_mode")

    required = ["market_microstructure_state"]
    try:
        missing = verify_required_tables(required)
    except Exception as exc:
        missing = required
        BOOT_STATUS["last_error"] = f"verify_tables: {exc}"
        logger.exception("startup_stage=verify_tables_error")
    if missing:
        logger.warning("startup_degraded missing_tables=%s", missing)

    BOOT_STATUS["stage"] = "engine_init"
    logger.info("startup_stage=engine_init")
    app.state.engine = TradingEngine()
    app.state.db_bootstrap = bootstrap
    app.state.degraded_mode = bool(missing)

    BOOT_STATUS["stage"] = "engine_start"
    logger.info("startup_stage=engine_start")
    try:
        await app.state.engine.start()
        BOOT_STATUS["engine_started"] = True
        BOOT_STATUS["stage"] = "running"
        logger.info("startup_stage=running")
    except Exception as exc:
        BOOT_STATUS["stage"] = "engine_start_error"
        BOOT_STATUS["last_error"] = f"engine_start: {exc}"
        logger.exception("startup_stage=engine_start_error")

    yield

    try:
        await app.state.engine.stop()
    except Exception:
        logger.exception("shutdown_engine_stop_error")


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.get("/health")
async def health():
    return {"ok": True, "service": settings.app_name}


@app.get("/debug/status")
async def debug_status():
    payload = dict(BOOT_STATUS)
    payload["uptime_sec"] = round(time.time() - BOOT_STATUS["started_at"], 1)
    try:
        payload["engine_summary"] = app.state.engine.snapshot_summary()
    except Exception as exc:
        payload["engine_summary_error"] = str(exc)
    return JSONResponse(payload)


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    try:
        summary = request.app.state.engine.snapshot_summary()
    except Exception as exc:
        return HTMLResponse(f"<pre>boot_status={BOOT_STATUS}\nerror={exc}</pre>", status_code=503)
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "summary": summary,
            "settings": settings,
            "categories": CATEGORIES,
            "bankroll_rules": BANKROLL_RULES,
        },
    )


@app.get("/api/summary")
async def api_summary():
    return app.state.engine.snapshot_summary()


@app.get("/api/settings")
async def api_settings():
    return {
        "dashboard_base_url": settings.dashboard_base_url,
        "categories": CATEGORIES,
        "bankroll_rules": BANKROLL_RULES,
        "runtime": TUNING.__dict__,
    }


@app.get("/api/markets")
async def api_markets(limit: int = 50):
    with SessionLocal() as db:
        rows = db.execute(select(MarketSnapshot).order_by(desc(MarketSnapshot.updated_at)).limit(limit)).scalars().all()
        return [
            {
                "ticker": row.ticker,
                "event_ticker": row.event_ticker,
                "title": row.title,
                "category": row.category,
                "market_type": row.market_type,
                "volume": row.volume,
                "open_interest": row.open_interest,
                "updated_at": row.updated_at.isoformat(),
            }
            for row in rows
        ]


@app.get("/api/candidates")
async def api_candidates(limit: int = 50):
    with SessionLocal() as db:
        rows = db.execute(select(CandidateRun).order_by(desc(CandidateRun.id)).limit(limit)).scalars().all()
        return [
            {
                "ticker": row.ticker,
                "category": row.category,
                "market_type": row.market_type,
                "side": row.side,
                "entry_price": row.entry_price,
                "spread_cents": row.spread_cents,
                "total_score": row.total_score,
                "rationale": row.rationale,
            }
            for row in rows
        ]


@app.get("/api/orders")
async def api_orders(limit: int = 50):
    with SessionLocal() as db:
        rows = db.execute(select(OrderRecord).order_by(desc(OrderRecord.id)).limit(limit)).scalars().all()
        return [
            {
                "ticker": row.ticker,
                "category": row.category,
                "side": row.side,
                "market_type": row.market_type,
                "legs": row.legs,
                "count": row.count,
                "status": row.status,
                "dry_run": row.dry_run,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]


@app.get("/api/positions")
async def api_positions(limit: int = 50):
    with SessionLocal() as db:
        rows = db.execute(select(PositionSnapshot).order_by(desc(PositionSnapshot.id)).limit(limit)).scalars().all()
        return [
            {
                "ticker": row.ticker,
                "category": row.category,
                "side": row.side,
                "quantity": row.quantity,
                "avg_price": row.avg_price,
                "status": row.status,
                "snapshot_at": row.snapshot_at.isoformat(),
            }
            for row in rows
        ]


@app.get("/api/audits")
async def api_audits(limit: int = 10):
    with SessionLocal() as db:
        rows = db.execute(select(AuditRun).order_by(desc(AuditRun.id)).limit(limit)).scalars().all()
        return [
            {
                "audit_date": row.audit_date,
                "total_trades": row.total_trades,
                "wins": row.wins,
                "losses": row.losses,
                "win_rate": row.win_rate,
                "gross_pnl": row.gross_pnl,
                "by_category": json.loads(row.by_category_json or "{}"),
                "issues": json.loads(row.issues_json or "{}"),
                "improvements": json.loads(row.improvements_json or "[]"),
            }
            for row in rows
        ]


@app.post("/api/research-notes")
async def create_research_note(note: ResearchNoteCreate):
    with SessionLocal() as db:
        rec = ResearchNote(
            ticker=note.ticker,
            category=note.category,
            projection_score=note.projection_score,
            research_score=note.research_score,
            confidence_score=note.confidence_score,
            confirmation_score=note.confirmation_score,
            ev_bonus=note.ev_bonus,
            rationale=note.rationale,
            tags_json=json.dumps(note.tags),
            source=note.source,
        )
        db.add(rec)
        db.commit()
        db.refresh(rec)
        return {"ok": True, "id": rec.id}


@app.post("/api/engine/run-once")
async def run_once():
    await app.state.engine.sync_markets()
    await app.state.engine.run_cycle()
    await app.state.engine.reconcile()
    return {"ok": True}


@app.post("/research-notes", response_class=HTMLResponse)
async def create_research_note_form(
    request: Request,
    category: str = Form(...),
    ticker: str = Form(""),
    projection_score: float = Form(...),
    research_score: float = Form(...),
    confidence_score: float = Form(...),
    confirmation_score: float = Form(...),
    ev_bonus: float = Form(0.0),
    rationale: str = Form(...),
):
    await create_research_note(
        ResearchNoteCreate(
            ticker=ticker or None,
            category=category,
            projection_score=projection_score,
            research_score=research_score,
            confidence_score=confidence_score,
            confirmation_score=confirmation_score,
            ev_bonus=ev_bonus,
            rationale=rationale,
        )
    )
    return await dashboard(request)
