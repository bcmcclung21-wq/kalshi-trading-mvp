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
from sqlalchemy import desc, func, select

from app.config import settings
from app.db import SessionLocal, init_db, verify_required_tables
from app.engine import TradingEngine
from app.models import AuditRun, CandidateRun, MarketSnapshot, OrderRecord, PositionSnapshot, ResearchNote
from app.observability import configure_logging
from app.schemas import ResearchNoteCreate
from app.strategy import BANKROLL_RULES, CATEGORIES, TUNING
from app.calibration import latest_snapshot

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


def _loads(value: str | None, default):
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _dt(value):
    return value.isoformat() if value is not None else None


def _dashboard_payload() -> dict:
    engine_summary = {}
    try:
        engine_summary = app.state.engine.snapshot_summary()
    except Exception as exc:
        engine_summary = {"error": str(exc)}

    with SessionLocal() as db:
        latest_candidates = db.execute(select(CandidateRun).order_by(desc(CandidateRun.id)).limit(25)).scalars().all()
        latest_orders = db.execute(select(OrderRecord).order_by(desc(OrderRecord.id)).limit(25)).scalars().all()
        latest_positions = db.execute(select(PositionSnapshot).order_by(desc(PositionSnapshot.id)).limit(25)).scalars().all()
        latest_audits = db.execute(select(AuditRun).order_by(desc(AuditRun.id)).limit(10)).scalars().all()
        latest_notes = db.execute(select(ResearchNote).order_by(desc(ResearchNote.id)).limit(20)).scalars().all()

        totals = {
            "market_count": db.query(MarketSnapshot).count(),
            "candidate_count": db.query(CandidateRun).count(),
            "order_count": db.query(OrderRecord).count(),
            "position_snapshots": db.query(PositionSnapshot).count(),
            "audit_count": db.query(AuditRun).count(),
            "submitted_count": db.query(OrderRecord).filter(OrderRecord.status == "submitted").count(),
            "dry_run_count": db.query(OrderRecord).filter(OrderRecord.dry_run.is_(True)).count(),
            "won_count": db.query(OrderRecord).filter(OrderRecord.status == "won").count(),
            "lost_count": db.query(OrderRecord).filter(OrderRecord.status == "lost").count(),
            "settled_count": db.query(OrderRecord).filter(OrderRecord.status.in_(["won", "lost", "settled"])).count(),
            "gross_realized_pnl": float(db.query(func.coalesce(func.sum(OrderRecord.realized_pnl), 0.0)).scalar() or 0.0),
        }

        candidates = [
            {
                "cycle_at": _dt(row.cycle_at),
                "ticker": row.ticker,
                "category": row.category,
                "market_type": row.market_type,
                "side": row.side,
                "entry_price": row.entry_price,
                "spread_cents": row.spread_cents,
                "projection_score": row.projection_score,
                "research_score": row.research_score,
                "confidence_score": row.confidence_score,
                "confirmation_score": row.confirmation_score,
                "ev_bonus": row.ev_bonus,
                "total_score": row.total_score,
                "rationale": row.rationale,
                "details": _loads(row.details_json, {}),
            }
            for row in latest_candidates
        ]

        orders = [
            {
                "created_at": _dt(row.created_at),
                "ticker": row.ticker,
                "category": row.category,
                "side": row.side,
                "market_type": row.market_type,
                "legs": row.legs,
                "count": row.count,
                "price_cents": row.price_cents,
                "bankroll_pct": row.bankroll_pct,
                "status": row.status,
                "dry_run": row.dry_run,
                "rationale": row.rationale,
                "realized_pnl": row.realized_pnl,
                "settled_at": _dt(row.settled_at),
                "estimated_win_probability": row.estimated_win_probability,
                "features": _loads(row.features_json, {}),
                "raw": _loads(row.raw_json, {}),
            }
            for row in latest_orders
        ]

        positions = [
            {
                "snapshot_at": _dt(row.snapshot_at),
                "ticker": row.ticker,
                "category": row.category,
                "side": row.side,
                "quantity": row.quantity,
                "avg_price": row.avg_price,
                "status": row.status,
                "raw": _loads(row.raw_json, {}),
            }
            for row in latest_positions
        ]

        audits = [
            {
                "audit_date": row.audit_date,
                "created_at": _dt(row.created_at),
                "total_trades": row.total_trades,
                "wins": row.wins,
                "losses": row.losses,
                "win_rate": row.win_rate,
                "gross_pnl": row.gross_pnl,
                "by_category": _loads(row.by_category_json, {}),
                "issues": _loads(row.issues_json, {}),
                "improvements": _loads(row.improvements_json, []),
                "feature_breakdown": _loads(row.feature_breakdown_json, {}),
                "calibration": _loads(row.calibration_json, {}),
                "learning_summary": _loads(row.learning_summary_json, {}),
            }
            for row in latest_audits
        ]

        notes = [
            {
                "created_at": _dt(row.created_at),
                "ticker": row.ticker,
                "category": row.category,
                "projection_score": row.projection_score,
                "research_score": row.research_score,
                "confidence_score": row.confidence_score,
                "confirmation_score": row.confirmation_score,
                "ev_bonus": row.ev_bonus,
                "rationale": row.rationale,
                "tags": _loads(row.tags_json, []),
                "source": row.source,
            }
            for row in latest_notes
        ]

    latest_audit = audits[0] if audits else None
    latest_calibration = None
    try:
        cal = latest_snapshot()
        if cal:
            latest_calibration = {
                "computed_at": _dt(cal.computed_at),
                "brier_score": cal.brier_score,
                "trades_evaluated": cal.trades_evaluated,
                "threshold": cal.threshold,
                "status": cal.status,
                "buckets": _loads(cal.bucket_breakdown_json, {}),
            }
    except Exception as exc:
        logger.warning("dashboard_calibration_fetch_failed err=%s", exc)
    return {
        "boot_status": {
            **BOOT_STATUS,
            "uptime_sec": round(time.time() - BOOT_STATUS["started_at"], 1),
        },
        "engine_summary": engine_summary,
        "runtime": {
            "auto_execute": TUNING.auto_execute,
            "allow_combos": TUNING.allow_combos,
            "same_day_only": getattr(TUNING, "same_day_only", False),
            "min_minutes_to_close": TUNING.min_minutes_to_close,
            "max_days_to_close": TUNING.max_days_to_close,
            "max_orders_per_cycle": TUNING.max_orders_per_cycle,
            "max_orderbooks_per_cycle": TUNING.max_orderbooks_per_cycle,
            "max_category_exposure_pct": TUNING.max_category_exposure_pct,
            "calibration_brier_threshold": TUNING.calibration_brier_threshold,
            "calibration_window_size": TUNING.calibration_window_size,
            "calibration_cooldown_sec": TUNING.calibration_cooldown_sec,
        },
        "totals": totals,
        "candidates": candidates,
        "orders": orders,
        "positions": positions,
        "audits": audits,
        "latest_audit": latest_audit,
        "latest_calibration": latest_calibration,
        "research_notes": notes,
        "categories": CATEGORIES,
        "bankroll_rules": BANKROLL_RULES,
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
    payload = _dashboard_payload()
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "dashboard": payload,
            "settings": settings,
        },
    )


@app.get("/api/dashboard")
async def api_dashboard():
    return _dashboard_payload()


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
                "close_time": row.close_time,
                "volume": row.volume,
                "open_interest": row.open_interest,
                "updated_at": row.updated_at.isoformat(),
            }
            for row in rows
        ]


@app.get("/api/candidates")
async def api_candidates(limit: int = 50):
    return _dashboard_payload()["candidates"][:limit]


@app.get("/api/orders")
async def api_orders(limit: int = 50):
    return _dashboard_payload()["orders"][:limit]


@app.get("/api/positions")
async def api_positions(limit: int = 50):
    return _dashboard_payload()["positions"][:limit]


@app.get("/api/audits")
async def api_audits(limit: int = 10):
    return _dashboard_payload()["audits"][:limit]


@app.get("/api/calibration")
async def api_calibration():
    cal = latest_snapshot()
    if not cal:
        return {"ok": False, "detail": "no snapshots"}
    return {
        "ok": True,
        "computed_at": cal.computed_at.isoformat() if cal.computed_at else None,
        "brier_score": cal.brier_score,
        "trades_evaluated": cal.trades_evaluated,
        "threshold": cal.threshold,
        "status": cal.status,
        "buckets": _loads(cal.bucket_breakdown_json, {}),
    }


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
