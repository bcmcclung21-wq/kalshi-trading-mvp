from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timezone

from sqlalchemy import desc, select

from app.classifier import detect_category, normalized_market
from app.db import SessionLocal
from app.kalshi import KalshiClient
from app.state import EngineState
from app.models import AuditRun, CandidateRun, OrderBookSnapshot, PositionSnapshot, ResearchNote
from app.risk import category_exposure_ok, duplicate_ticker_ok
from app.selector import build_candidate, combo_pool, normalize_markets, rank_candidates, single_pool
from app.services.audit import summarize_settlements
from app.services.execution import execute_candidate
from app.services.universe import persist_markets
from app.strategy import TUNING

logger = logging.getLogger(__name__)


class TradingEngine:
    def __init__(self) -> None:
        self.kalshi = KalshiClient()
        self.state = EngineState()
        self._tasks: list[asyncio.Task] = []
        self._last_audit_date: str | None = None

    async def start(self) -> None:
        self._tasks = [
            asyncio.create_task(self.market_sync_loop()),
            asyncio.create_task(self.trade_cycle_loop()),
            asyncio.create_task(self.reconcile_loop()),
            asyncio.create_task(self.audit_loop()),
        ]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await self.kalshi.close()

    async def market_sync_loop(self) -> None:
        while True:
            await self._guarded(self.sync_markets)
            await asyncio.sleep(TUNING.market_sync_interval_sec)

    async def trade_cycle_loop(self) -> None:
        while True:
            await self._guarded(self.run_cycle)
            await asyncio.sleep(TUNING.check_interval_sec)

    async def reconcile_loop(self) -> None:
        while True:
            await self._guarded(self.reconcile)
            await asyncio.sleep(TUNING.reconcile_interval_sec)

    async def audit_loop(self) -> None:
        while True:
            now = datetime.now(timezone.utc)
            if now.hour == TUNING.daily_audit_hour_utc and self._last_audit_date != str(date.today()):
                await self._guarded(self.run_daily_audit)
                self._last_audit_date = str(date.today())
            await asyncio.sleep(TUNING.audit_interval_sec)

    async def _guarded(self, fn) -> None:
        try:
            await fn()
            self.state.last_error = None
        except Exception as exc:
            self.state.last_error = str(exc)
            logger.exception("engine_loop_error", extra={"fn": getattr(fn, "__name__", "unknown")})

    async def sync_markets(self) -> None:
        markets = await self.kalshi.get_open_markets()
        saved = persist_markets(markets)
        self.state.last_sync_at = datetime.now(timezone.utc).isoformat()
        self.state.last_run_metrics["last_sync_saved"] = saved

    async def _manual_notes(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        with SessionLocal() as db:
            rows = db.execute(select(ResearchNote).order_by(desc(ResearchNote.id)).limit(200)).scalars().all()
            for note in rows:
                payload = {
                    "projection_score": note.projection_score,
                    "research_score": note.research_score,
                    "confidence_score": note.confidence_score,
                    "confirmation_score": note.confirmation_score,
                    "ev_bonus": note.ev_bonus,
                    "rationale": note.rationale,
                    "tags": json.loads(note.tags_json or "[]"),
                }
                if note.ticker:
                    out[note.ticker] = payload
                out.setdefault(f"category:{note.category}", payload)
        return out

    async def run_cycle(self) -> None:
        raw_markets = await self.kalshi.get_open_markets()
        markets = normalize_markets(raw_markets)
        note_map = await self._manual_notes()
        positions = await self.kalshi.get_positions()
        self.state.auth_ok = self.kalshi.auth_status.ok
        position_rows = [
            {"ticker": str(p.get("ticker") or ""), "category": detect_category(p), "status": str(p.get("status") or "open")}
            for p in positions
        ]

        pool = single_pool(markets)
        if TUNING.allow_combos:
            pool.extend(combo_pool(markets))
        pool = pool[: TUNING.max_orderbooks_per_cycle]

        orderbooks = await asyncio.gather(*[self.kalshi.get_orderbook(m["ticker"]) for m in pool], return_exceptions=True)
        candidates = []
        with SessionLocal() as db:
            for market, book in zip(pool, orderbooks):
                if isinstance(book, Exception):
                    continue
                manual_note = note_map.get(market["ticker"]) or note_map.get(f"category:{market['category']}")
                candidate = build_candidate(market, book, manual_note=manual_note)
                if not candidate:
                    continue
                if not duplicate_ticker_ok(candidate.ticker, position_rows):
                    continue
                if not category_exposure_ok(candidate.category, position_rows):
                    continue
                ob = OrderBookSnapshot(
                    ticker=candidate.ticker,
                    yes_bid=float((book.get("yes") or [{}])[0].get("price") or 0.0) if book.get("yes") else 0.0,
                    yes_ask=float((book.get("yes") or [{}])[-1].get("price") or 0.0) if book.get("yes") else 0.0,
                    no_bid=float((book.get("no") or [{}])[0].get("price") or 0.0) if book.get("no") else 0.0,
                    no_ask=float((book.get("no") or [{}])[-1].get("price") or 0.0) if book.get("no") else 0.0,
                    spread_cents=candidate.spread_cents,
                    raw_json=json.dumps(book),
                )
                db.add(ob)
                candidates.append(candidate)

            ranked = rank_candidates(candidates)[: TUNING.max_orders_per_cycle]
            if not self.kalshi.auth_status.ok:
                ranked = []
            bankroll_usd = float((await self.kalshi.get_balance()).get("balance") or 1000.0) if self.kalshi.auth_status.ok else 1000.0
            for candidate in ranked:
                db.add(
                    CandidateRun(
                        ticker=candidate.ticker,
                        category=candidate.category,
                        market_type=candidate.market_type,
                        side=candidate.side,
                        entry_price=candidate.entry_price,
                        spread_cents=candidate.spread_cents,
                        projection_score=candidate.projection_score,
                        research_score=candidate.research_score,
                        confidence_score=candidate.confidence_score,
                        confirmation_score=candidate.confirmation_score,
                        ev_bonus=candidate.ev_bonus,
                        total_score=candidate.total_score,
                        details_json=json.dumps(candidate.details),
                        rationale=candidate.rationale,
                    )
                )
                await execute_candidate(self.kalshi, db, candidate, bankroll_usd=bankroll_usd)
            db.commit()

        self.state.last_cycle_at = datetime.now(timezone.utc).isoformat()
        self.state.last_run_metrics["candidate_count"] = len(candidates)

    async def reconcile(self) -> None:
        positions = await self.kalshi.get_positions()
        self.state.auth_ok = self.kalshi.auth_status.ok
        with SessionLocal() as db:
            for row in positions:
                db.add(
                    PositionSnapshot(
                        ticker=str(row.get("ticker") or ""),
                        category=detect_category(row),
                        side=str(row.get("side") or ""),
                        quantity=int(row.get("quantity") or 0),
                        avg_price=float(row.get("average_price") or 0.0),
                        status=str(row.get("status") or "open"),
                        raw_json=json.dumps(row),
                    )
                )
            db.commit()
        self.state.last_reconcile_at = datetime.now(timezone.utc).isoformat()

    async def run_daily_audit(self) -> None:
        settlements = await self.kalshi.get_settlements()
        prepared = []
        for row in settlements:
            prepared.append(
                {
                    "ticker": row.get("ticker"),
                    "category": detect_category(row),
                    "market_type": row.get("market_type") or "single",
                    "pnl": float(row.get("pnl") or 0.0),
                    "spread_cents": float(row.get("spread_cents") or 0.0),
                }
            )
        summary = summarize_settlements(prepared)
        with SessionLocal() as db:
            db.add(
                AuditRun(
                    audit_date=str(date.today()),
                    total_trades=summary["total_trades"],
                    wins=summary["wins"],
                    losses=summary["losses"],
                    win_rate=summary["win_rate"],
                    gross_pnl=summary["gross_pnl"],
                    by_category_json=json.dumps(summary["by_category"]),
                    issues_json=json.dumps(summary["issues"]),
                    improvements_json=json.dumps(summary["improvements"]),
                )
            )
            db.commit()
        self.state.last_audit_at = datetime.now(timezone.utc).isoformat()

    def snapshot_summary(self) -> dict:
        with SessionLocal() as db:
            return {
                "markets": db.query(__import__("app.models", fromlist=["MarketSnapshot"]).MarketSnapshot).count(),
                "candidates": db.query(__import__("app.models", fromlist=["CandidateRun"]).CandidateRun).count(),
                "orders": db.query(__import__("app.models", fromlist=["OrderRecord"]).OrderRecord).count(),
                "positions": db.query(__import__("app.models", fromlist=["PositionSnapshot"]).PositionSnapshot).count(),
                "audits": db.query(__import__("app.models", fromlist=["AuditRun"]).AuditRun).count(),
                "engine": {
                    "last_sync_at": self.state.last_sync_at,
                    "last_cycle_at": self.state.last_cycle_at,
                    "last_reconcile_at": self.state.last_reconcile_at,
                    "last_audit_at": self.state.last_audit_at,
                    "last_error": self.state.last_error,
                    "metrics": self.state.last_run_metrics,
                    "auth_ok": self.state.auth_ok,
                    "auto_execute": TUNING.auto_execute,
                    "allow_combos": TUNING.allow_combos,
                },
            }
