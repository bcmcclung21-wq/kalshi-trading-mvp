from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import date, datetime, timezone

from sqlalchemy import desc, select

from app.classifier import detect_category, normalized_market
from app.db import SessionLocal
from app.kalshi import KalshiClient
from app.state import EngineState
from app.models import AuditRun, CandidateRun, OrderBookSnapshot, PositionSnapshot, ResearchNote
from app.risk import category_exposure_ok, duplicate_ticker_ok
from app.selector import best_ask, best_bid, build_candidate, combo_pool, diversified_pool, normalize_markets, rank_candidates, single_pool
from app.services.audit import summarize_settlements
from app.services.execution import execute_candidate
from app.services.universe import persist_markets
from app.services.market_ingestion import AsyncIngestionPipeline, EngineMode, IngestionMetrics, MarketCache, MarketDiscoveryEngine
from app.services.resilience import BreakerRegistry
from app.strategy import TUNING

logger = logging.getLogger(__name__)


class TradingEngine:
    def __init__(self) -> None:
        self.kalshi = KalshiClient()
        self.state = EngineState()
        self._tasks: list[asyncio.Task] = []
        self._last_audit_date: str | None = None
        self._failure_count = 0
        self._trading_disabled_until = 0.0
        self._market_sync_lock = asyncio.Lock()
        self._engine_cycle_lock = asyncio.Lock()
        self.instance_id = f"{id(self)}"
        self.mode = EngineMode.BOOT
        self.metrics = IngestionMetrics()
        self.market_cache = MarketCache(ttl_seconds=TUNING.market_cache_ttl_sec, max_size=20_000)
        self.discovery = MarketDiscoveryEngine(ttl_seconds=3600, max_tracked=2_500)
        self.pipeline = AsyncIngestionPipeline(self.market_cache, self.discovery, self.metrics)
        self.breakers = BreakerRegistry()

    async def start(self) -> None:
        logger.info("engine_instance_started pid=%s instance_id=%s", os.getpid(), self.instance_id)
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
            self._failure_count = 0
            self.state.last_error = None
        except Exception as exc:
            self._failure_count += 1
            if self._failure_count >= 5:
                self._trading_disabled_until = time.time() + 120
                logger.warning("circuit_breaker_open failures=%d cooldown_sec=120", self._failure_count)
            self.state.last_error = str(exc)
            logger.exception("engine_loop_error", extra={"fn": getattr(fn, "__name__", "unknown")})

    async def sync_markets(self) -> None:
        if self._market_sync_lock.locked():
            logger.warning("market_sync_skipped reason=lock_active")
            return
        async with self._market_sync_lock:
            if not await self.breakers.market_fetch_breaker.allow():
                logger.warning("market_sync_skipped reason=breaker_open")
                return
            if self.mode == EngineMode.BOOT:
                markets = await self.kalshi.get_open_markets()
                markets_source = "boot_fetch"
            else:
                cached = list(self.market_cache.snapshot().values())
                if cached:
                    markets = cached
                    markets_source = "cache"
                else:
                    logger.warning("sync_markets_cache_empty falling_back_to_fetch")
                    markets = await self.kalshi.get_open_markets()
                    markets_source = "fallback_fetch"
            logger.info("sync_markets_source=%s count=%d", markets_source, len(markets))
            api_round_trip_ok = self.mode != EngineMode.BOOT or self.kalshi.last_paginate_pages > 0
            strategy_tickers: set[str] = set()
            position_tickers = {str(p.get("ticker") or "") for p in await self.kalshi.get_positions()}
            self.discovery.reconcile_registry(markets, strategy_tickers=strategy_tickers, position_tickers=position_tickers)
            for ticker, state in self.discovery.tracked_markets.items():
                await self.pipeline.enqueue(ticker, state.market, self.pipeline.next_version())
            await self.pipeline.run_once()
            saved = persist_markets(markets)
            await self.breakers.market_fetch_breaker.record_success()
            if api_round_trip_ok or len(markets) > 0:
                self._failure_count = 0
                self._trading_disabled_until = 0.0
            if self.mode == EngineMode.BOOT:
                self.mode = EngineMode.LIVE
            self.state.last_sync_at = datetime.now(timezone.utc).isoformat()
            self.state.last_run_metrics["last_sync_saved"] = saved
            logger.info("sync_markets mode=%s tracked=%d fetched=%d saved=%d auth_ok=%s queue_depth=%d", self.mode.value, len(self.discovery.tracked_markets), len(markets), saved, self.kalshi.auth_status.ok, self.metrics.queue_depth)

    async def _with_cycle_lock(self, fn_name: str, fn) -> None:
        if self._engine_cycle_lock.locked():
            logger.warning("%s_skipped reason=already_running", fn_name)
            return
        async with self._engine_cycle_lock:
            await fn()

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
        await self._with_cycle_lock("cycle", self._run_cycle_inner)

    async def _run_cycle_inner(self) -> None:
        if self._trading_disabled_until > time.time():
            logger.warning("circuit_breaker_blocked remaining_sec=%d", int(self._trading_disabled_until - time.time()))
            return
        if self._trading_disabled_until and self._trading_disabled_until <= time.time():
            logger.info("circuit_breaker_closed")
            self._trading_disabled_until = 0.0
            self._failure_count = 0
        t0 = time.perf_counter()
        raw_markets = await self.kalshi.get_all_open_markets()
        markets = normalize_markets(raw_markets)
        logger.info("run_cycle raw=%d normalized=%d auth_ok=%s", len(raw_markets), len(markets), self.kalshi.auth_status.ok)
        note_map = await self._manual_notes()
        positions = await self.kalshi.get_positions()
        self.state.auth_ok = self.kalshi.auth_status.ok
        position_rows = [
            {"ticker": str(p.get("ticker") or ""), "category": detect_category(p), "status": str(p.get("status") or "open")}
            for p in positions
        ]

        from collections import Counter

        cat_counter = Counter(str(m.get("category") or "missing") for m in markets)
        type_counter = Counter(str(m.get("market_type") or "missing") for m in markets)
        logger.info("market_categories_seen %s", dict(cat_counter.most_common(10)))
        logger.info("market_types_seen %s", dict(type_counter.most_common(5)))

        volumes = [float(m.get("volume") or 0.0) for m in markets]
        ois = [float(m.get("open_interest") or 0.0) for m in markets]
        if volumes:
            v_sorted = sorted(volumes)
            o_sorted = sorted(ois)
            n = len(v_sorted)
            p50 = lambda xs: xs[n // 2] if n else 0.0
            p90 = lambda xs: xs[(n * 9) // 10] if n else 0.0
            p99 = lambda xs: xs[(n * 99) // 100] if n else 0.0
            logger.info(
                "market_liquidity_distribution n=%d vol_p50=%.1f vol_p90=%.1f vol_p99=%.1f oi_p50=%.1f oi_p90=%.1f oi_p99=%.1f",
                n, p50(v_sorted), p90(v_sorted), p99(v_sorted),
                p50(o_sorted), p90(o_sorted), p99(o_sorted),
            )

        pool, single_rejects = single_pool(markets)
        logger.info(
            "single_pool_result kept=%d wrong_type=%d wrong_cat=%d low_vol=%d low_oi=%d too_close=%d",
            len(pool),
            single_rejects["wrong_market_type"],
            single_rejects["wrong_category"],
            single_rejects["low_volume"],
            single_rejects["low_open_interest"],
            single_rejects["too_close_to_close"],
        )

        if single_rejects["wrong_category"] > 0:
            unknowns = [m for m in markets if m.get("category") not in {"sports", "politics", "crypto", "climate", "economics"}]
            sample = unknowns[:5]
            for m in sample:
                logger.info(
                    "unknown_category_sample ticker=%s event=%s title=%s subtitle=%s",
                    str(m.get("ticker") or "")[:60],
                    str(m.get("event_ticker") or "")[:40],
                    str(m.get("title") or "")[:80],
                    str(m.get("subtitle") or "")[:80],
                )

        if TUNING.allow_combos:
            pool.extend(combo_pool(markets))
        pool = diversified_pool(pool, TUNING.max_orderbooks_per_cycle, per_category=8)
        logger.info("pool_after_diversify count=%d", len(pool))

        t_books = time.perf_counter()
        orderbooks = await asyncio.gather(*[self.kalshi.get_orderbook(m["ticker"]) for m in pool], return_exceptions=True)
        rejected = 0
        candidates = []
        with SessionLocal() as db:
            for market, book in zip(pool, orderbooks):
                if isinstance(book, Exception):
                    logger.info("candidate_rejected ticker=%s reason=%s", market.get("ticker"), "orderbook_fetch_error")
                    rejected += 1
                    continue
                manual_note = note_map.get(market["ticker"]) or note_map.get(f"category:{market['category']}")
                candidate, reason = build_candidate(market, book, manual_note=manual_note)
                if not candidate:
                    logger.info("candidate_rejected ticker=%s reason=%s", market.get("ticker"), reason or "unknown")
                    rejected += 1
                    continue
                if not duplicate_ticker_ok(candidate.ticker, position_rows):
                    logger.info("candidate_rejected ticker=%s reason=%s", candidate.ticker, "duplicate_market")
                    rejected += 1
                    continue
                if not category_exposure_ok(candidate.category, position_rows):
                    logger.info("candidate_rejected ticker=%s reason=%s", candidate.ticker, "category_exposure")
                    rejected += 1
                    continue
                ob = OrderBookSnapshot(
                    ticker=candidate.ticker,
                    yes_bid=best_bid(list(book.get("yes") or [])),
                    yes_ask=best_ask(list(book.get("yes") or [])),
                    no_bid=best_bid(list(book.get("no") or [])),
                    no_ask=best_ask(list(book.get("no") or [])),
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
        logger.info(
            "cycle_summary markets=%d candidates=%d orders=%d rejected=%d api_ms=%d orderbook_ms=%d total_ms=%d",
            len(markets),
            len(candidates),
            min(len(candidates), TUNING.max_orders_per_cycle),
            rejected,
            int((t_books - t0) * 1000),
            int((time.perf_counter() - t_books) * 1000),
            int((time.perf_counter() - t0) * 1000),
        )

    async def reconcile(self) -> None:
        await self._with_cycle_lock("reconcile", self._reconcile_inner)

    async def _reconcile_inner(self) -> None:
        logger.info("reconcile_start")
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
        await self._with_cycle_lock("audit", self._run_daily_audit_inner)

    async def _run_daily_audit_inner(self) -> None:
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
