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
from app.learning import get_learning_engine
from app.polymarket import PolymarketClient
from app.state import EngineState
from app.models import AuditRun, CandidateRun, OrderBookSnapshot, OrderRecord, PositionSnapshot, ResearchNote
from app.risk import category_exposure_ok, duplicate_ticker_ok
from app.selector import best_ask, best_bid, build_candidate, combo_pool, diversified_pool, normalize_markets, rank_candidates, single_pool, validate_market_candidate
from app.services.audit import summarize_settlements
from app.services.execution import execute_candidate
from app.services.universe import persist_markets
from app.services.market_ingestion import AsyncIngestionPipeline, EngineMode, IngestionMetrics, MarketCache, MarketDiscoveryEngine
from app.services.liquidity_engine import LiquidityEngine
from app.liquidity import LiquidityConfig
from app.services.resilience import BreakerRegistry
from app.strategy import TUNING

logger = logging.getLogger(__name__)

RECONCILE_TIMEOUT_S = int(os.getenv("RECONCILE_TIMEOUT_S", "30"))


class TradingEngine:
    def __init__(self) -> None:
        self.poly = PolymarketClient()
        self.state = EngineState()
        self._tasks: list[asyncio.Task] = []
        self._last_audit_date: str | None = None
        self._failure_count = 0
        self._trading_disabled_until = 0.0
        self._market_sync_lock = asyncio.Lock()
        self._engine_cycle_lock = asyncio.Lock()
        self._cycle_lock_owner: str | None = None
        self._cycle_lock_acquired_at: float = 0.0
        self.instance_id = f"{id(self)}"
        self.mode = EngineMode.BOOT
        self.metrics = IngestionMetrics()
        self.market_cache = MarketCache(ttl_seconds=TUNING.market_cache_ttl_sec, max_size=20_000)
        self.discovery = MarketDiscoveryEngine(ttl_seconds=3600, max_tracked=2_500)
        self.pipeline = AsyncIngestionPipeline(self.market_cache, self.discovery, self.metrics)
        self.breakers = BreakerRegistry()
        self.liquidity: LiquidityEngine | None = None
        self._last_discovery_refresh = 0.0
        self._last_liquidity_refresh = 0.0

    async def start(self) -> None:
        logger.info("engine_instance_started pid=%s instance_id=%s", os.getpid(), self.instance_id)
        self.liquidity = LiquidityEngine(LiquidityConfig())
        self.liquidity.load_state()
        try:
            get_learning_engine().load()
        except Exception as exc:
            logger.warning("learning_engine_load_failed err=%s", exc)
        try:
            asyncio.create_task(self._guarded(self._seed_priors_on_boot))
        except Exception as exc:
            logger.warning("seed_priors_schedule_failed err=%s", exc)
        self._tasks = [
            asyncio.create_task(self.market_sync_loop()),
            asyncio.create_task(self.trade_cycle_loop()),
            asyncio.create_task(self.reconcile_loop()),
            asyncio.create_task(self.audit_loop()),
        ]

    async def _seed_priors_on_boot(self) -> None:
        """One-shot rebuild on startup so priors reflect existing settlements
        before the daily audit window comes around."""
        await asyncio.sleep(15)
        try:
            result = get_learning_engine().rebuild_priors(lookback_days=60)
            logger.info("learning_priors_seed_on_boot status=%s", result.get("status"))
        except Exception as exc:
            logger.warning("learning_priors_seed_failed err=%s", exc)

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await self.poly.close()

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
            await self._guarded(self._reconcile_with_timeout)
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
                markets = await self.poly.get_open_markets()
                markets_source = "boot_fetch"
            else:
                cached = list(self.market_cache.snapshot().values())
                if cached:
                    markets = cached
                    markets_source = "cache"
                else:
                    logger.warning("sync_markets_cache_empty falling_back_to_fetch")
                    markets = await self.poly.get_open_markets()
                    markets_source = "fallback_fetch"
            logger.info("sync_markets_source=%s count=%d", markets_source, len(markets))
            api_round_trip_ok = self.mode != EngineMode.BOOT or self.poly.last_paginate_pages > 0
            strategy_tickers: set[str] = set()
            position_tickers = {str(p.get("ticker") or "") for p in await self.poly.get_positions()}
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
            logger.info("sync_markets mode=%s tracked=%d fetched=%d saved=%d auth_ok=%s queue_depth=%d", self.mode.value, len(self.discovery.tracked_markets), len(markets), saved, self.poly.auth_status.ok, self.metrics.queue_depth)

    async def _with_cycle_lock(self, fn_name: str, fn) -> None:
        if self._engine_cycle_lock.locked():
            held_by = self._cycle_lock_owner or "unknown"
            held_for = time.monotonic() - self._cycle_lock_acquired_at if self._cycle_lock_acquired_at else 0.0
            logger.warning(
                "%s_skipped reason=already_running held_by=%s held_for_s=%.1f",
                fn_name, held_by, held_for,
            )
            return
        async with self._engine_cycle_lock:
            self._cycle_lock_owner = fn_name
            self._cycle_lock_acquired_at = time.monotonic()
            try:
                await fn()
            finally:
                self._cycle_lock_owner = None
                self._cycle_lock_acquired_at = 0.0

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
        raw_markets = list(self.market_cache.snapshot().values()) or await self.poly.get_all_open_markets()
        markets = normalize_markets(raw_markets)
        logger.info("run_cycle raw=%d normalized=%d auth_ok=%s", len(raw_markets), len(markets), self.poly.auth_status.ok)
        note_map = await self._manual_notes()
        positions = await self.poly.get_positions()
        self.state.auth_ok = self.poly.auth_status.ok
        position_rows = [
            {"ticker": str(p.get("ticker") or ""), "category": detect_category(p), "status": str(p.get("status") or "open")}
            for p in positions
        ]

        from collections import Counter

        cat_counter = Counter(str(m.get("category") or "missing") for m in markets)
        type_counter = Counter(str(m.get("market_type") or "missing") for m in markets)
        logger.info("market_categories_seen %s", dict(cat_counter.most_common(10)))
        logger.info("market_types_seen %s", dict(type_counter.most_common(5)))

        pool, single_rejects = single_pool(markets)
        logger.info(
            "single_pool_result kept=%d wrong_type=%d wrong_cat=%d no_liq=%d too_close=%d too_far=%d not_same_day=%d missing_close=%d",
            len(pool),
            single_rejects["wrong_market_type"],
            single_rejects["wrong_category"],
            single_rejects["no_liquidity_sign"],
            single_rejects["too_close_to_close"],
            single_rejects["too_far_to_close"],
            single_rejects["not_same_day_settlement"],
            single_rejects["missing_close_time"],
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

        candidate_tickers = [str(m.get("ticker") or "") for m in pool[: max(200, TUNING.max_orderbooks_per_cycle * 10)] if m.get("ticker")]
        try:
            batch_books = await self.poly.get_orderbooks(candidate_tickers, depth=25)
        except Exception:
            logger.exception("orderbook_fetch_unhandled using_partial_data=false")
            batch_books = {}
        liquidity_rank: list[tuple[float, dict]] = []
        markets_with_books = [m for m in pool if batch_books.get(str(m.get("ticker") or ""))]
        markets_without_books = [m for m in pool if not batch_books.get(str(m.get("ticker") or ""))]
        for m in markets_with_books:
            t = str(m.get("ticker") or "")
            if not self.liquidity:
                liquidity_rank.append((0.5, m))
                continue
            snap = self.liquidity.evaluate(t, batch_books.get(t, {}))
            if not snap:
                logger.info("liquidity_skip ticker=%s reason=no_snapshot_or_quotes", t)
                continue
            has_pair = ((snap.yes_bid > 0 and snap.yes_ask > 0) or (snap.no_bid > 0 and snap.no_ask > 0))
            if not has_pair:
                logger.info("liquidity_skip ticker=%s reason=no_usable_bid_ask_pair", t)
                continue
            if snap.liquidity_score > 0:
                liquidity_rank.append((snap.liquidity_score, m))
            else:
                logger.info("liquidity_zero ticker=%s yes_bid=%.4f yes_ask=%.4f no_bid=%.4f no_ask=%.4f spread=%.4f depth=%.2f", t, snap.yes_bid, snap.yes_ask, snap.no_bid, snap.no_ask, snap.spread, snap.effective_depth)
        scored_pool = [m for _, m in sorted(liquidity_rank, key=lambda x: x[0], reverse=True)]
        pool = scored_pool + markets_without_books
        logger.info("liquidity_filter scored=%d unscored=%d total=%d", len(scored_pool), len(markets_without_books), len(pool))
        if self.liquidity:
            logger.info("universe_state total=%d active=%d inactive=%d stale=%d", len(self.liquidity.market_state), len(self.liquidity.active_liquid_markets), len(self.liquidity.inactive_markets), len(self.liquidity.stale_markets))
        pool = diversified_pool(pool, TUNING.max_orderbooks_per_cycle, per_category=8)
        logger.info("pool_after_diversify count=%d", len(pool))

        t_books = time.perf_counter()
        orderbooks = [batch_books.get(str(m.get("ticker") or ""), {}) for m in pool]
        rejected = 0
        candidates = []
        with SessionLocal() as db:
            for market, book in zip(pool, orderbooks):
                if isinstance(book, Exception):
                    logger.info("candidate_rejected ticker=%s reason=%s", market.get("ticker"), "orderbook_fetch_error")
                    rejected += 1
                    continue
                is_valid, validation_reason = validate_market_candidate(market, book)
                yes_bid_px = best_bid(list(book.get("yes_bids") or book.get("yes") or []))
                yes_ask_px = best_ask(list(book.get("yes_asks") or []))
                no_bid_px = best_bid(list(book.get("no_bids") or book.get("no") or []))
                no_ask_px = best_ask(list(book.get("no_asks") or []))
                if yes_ask_px <= 0 and no_bid_px > 0:
                    yes_ask_px = 1 - no_bid_px
                if no_ask_px <= 0 and yes_bid_px > 0:
                    no_ask_px = 1 - yes_bid_px
                spread = (yes_ask_px - yes_bid_px) if yes_ask_px > 0 and yes_bid_px > 0 else 0.0
                logger.info(
                    "candidate_book ticker=%s yes_bid=%.4f yes_ask=%.4f no_bid=%.4f no_ask=%.4f spread=%.4f validation=%s",
                    market.get("ticker"), yes_bid_px, yes_ask_px, no_bid_px, no_ask_px, spread, validation_reason if not is_valid else "precheck_ok"
                )
                if not is_valid:
                    logger.info("candidate_rejected ticker=%s reason=%s", market.get("ticker"), validation_reason)
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
                    yes_bid=yes_bid_px,
                    yes_ask=yes_ask_px,
                    no_bid=no_bid_px,
                    no_ask=no_ask_px,
                    spread_cents=candidate.spread_cents,
                    raw_json=json.dumps(book),
                )
                db.add(ob)
                candidates.append(candidate)

            ranked = rank_candidates(candidates)[: TUNING.max_orders_per_cycle]
            if not self.poly.auth_status.ok:
                ranked = []
            bankroll_usd = float((await self.poly.get_balance()).get("balance") or 1000.0) if self.poly.auth_status.ok else 1000.0
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
                await execute_candidate(self.poly, db, candidate, bankroll_usd=bankroll_usd)
            db.commit()

        if self.liquidity:
            self.liquidity.persist_state()
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

    async def _reconcile_with_timeout(self) -> None:
        try:
            await asyncio.wait_for(self.reconcile(), timeout=RECONCILE_TIMEOUT_S)
        except asyncio.TimeoutError:
            logger.error("reconcile_timeout limit_s=%d", RECONCILE_TIMEOUT_S)

    async def reconcile(self) -> None:
        await self._with_cycle_lock("reconcile", self._reconcile_inner)

    async def _reconcile_inner(self) -> None:
        logger.info("reconcile_start")
        t0 = time.monotonic()
        positions = await self.poly.get_positions()
        self.state.auth_ok = self.poly.auth_status.ok
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

        if self.poly.auth_status.ok:
            try:
                settlements = await self.poly.get_settlements()
            except Exception as exc:
                logger.warning("reconcile_settlements_fetch_failed err=%s", exc)
                settlements = []
            updated = 0
            with SessionLocal() as db:
                for row in settlements:
                    ticker = str(row.get("ticker") or "")
                    if not ticker:
                        continue
                    pnl = float(row.get("pnl") or 0.0)
                    status = "won" if pnl > 0 else "lost" if pnl < 0 else "settled"
                    candidates = db.query(OrderRecord).filter(
                        OrderRecord.ticker == ticker,
                        OrderRecord.status.in_(["submitted", "dry_run", "pending"]),
                    ).all()
                    for order in candidates:
                        order.status = status
                        order.realized_pnl = pnl
                        order.settled_at = datetime.now(timezone.utc)
                        updated += 1
                if updated:
                    db.commit()
            if updated:
                logger.info("reconcile_settled_orders count=%d", updated)

        self.state.last_reconcile_at = datetime.now(timezone.utc).isoformat()
        logger.info("reconcile_ok elapsed_s=%.1f positions=%d", time.monotonic() - t0, len(positions))

    async def run_daily_audit(self) -> None:
        await self._with_cycle_lock("audit", self._run_daily_audit_inner)

    async def _run_daily_audit_inner(self) -> None:
        settlements = await self.poly.get_settlements()

        feature_lookup: dict[str, dict[str, str]] = {}
        win_prob_lookup: dict[str, float] = {}
        try:
            with SessionLocal() as db:
                orders = db.query(OrderRecord).filter(
                    OrderRecord.status.in_(["won", "lost", "settled", "submitted", "dry_run"])
                ).all()
                for order in orders:
                    try:
                        features = json.loads(order.features_json or "{}")
                    except (TypeError, ValueError):
                        features = {}
                    if features:
                        feature_lookup[order.ticker] = features
                    if order.estimated_win_probability:
                        win_prob_lookup[order.ticker] = float(order.estimated_win_probability)
        except Exception as exc:
            logger.warning("audit_feature_lookup_failed err=%s", exc)

        prepared = []
        for row in settlements:
            ticker = row.get("ticker") or ""
            features = feature_lookup.get(ticker) or {}
            prepared.append(
                {
                    "ticker": ticker,
                    "category": detect_category(row),
                    "market_type": row.get("market_type") or "single",
                    "pnl": float(row.get("pnl") or 0.0),
                    "spread_cents": float(row.get("spread_cents") or 0.0),
                    "features": features,
                    "estimated_win_probability": win_prob_lookup.get(ticker, 0.0),
                }
            )
        summary = summarize_settlements(prepared)

        learning_summary: dict = {"status": "skipped"}
        try:
            learning_summary = get_learning_engine().rebuild_priors(lookback_days=30)
        except Exception as exc:
            logger.warning("learning_rebuild_failed err=%s", exc)
            learning_summary = {"status": "failed", "error": str(exc)}

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
                    feature_breakdown_json=json.dumps(summary.get("feature_breakdown") or {}),
                    calibration_json=json.dumps(summary.get("calibration") or {}),
                    learning_summary_json=json.dumps(learning_summary or {}),
                )
            )
            db.commit()
        logger.info(
            "audit_complete trades=%d wins=%d losses=%d win_rate=%.3f pnl=%.2f learning=%s improvements=%d",
            summary["total_trades"], summary["wins"], summary["losses"],
            summary["win_rate"], summary["gross_pnl"],
            learning_summary.get("status"), len(summary.get("improvements") or []),
        )
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
