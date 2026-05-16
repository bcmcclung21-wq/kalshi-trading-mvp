from __future__ import annotations
import asyncio
import logging
import os
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.selector import build_candidate, rank_candidates, single_pool
from app.strategy import TUNER, get_adjusted_thresholds
from app.learning import get_learning_engine

logger = logging.getLogger("app.engine")


@dataclass(slots=True)
class CacheEntry:
    last_price: float
    last_evaluated: float
    edge: float

class MarketDeduplicator:
    __slots__ = ("cache", "price_threshold", "min_recheck_seconds", "max_size", "hits", "misses")

    def __init__(self, price_threshold: float = 0.01, min_recheck_seconds: float = 300, max_size: int = 500):
        self.cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self.price_threshold = price_threshold
        self.min_recheck_seconds = min_recheck_seconds
        self.max_size = max_size
        self.hits = 0
        self.misses = 0

    def should_evaluate(self, ticker: str, current_price: float) -> bool:
        now = time.time()
        entry = self.cache.get(ticker)
        if entry is None:
            self.misses += 1
            return True
        price_moved = abs(current_price - entry.last_price) > self.price_threshold
        time_expired = (now - entry.last_evaluated) > self.min_recheck_seconds
        should = price_moved or time_expired
        if should:
            self.misses += 1
        else:
            self.hits += 1
        return should

    def update(self, ticker: str, current_price: float, edge: float) -> None:
        now = time.time()
        self.cache[ticker] = CacheEntry(current_price, now, edge)
        self.cache.move_to_end(ticker)
        while len(self.cache) > self.max_size:
            self.cache.popitem(last=False)

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0

class TradingEngine:
    def __init__(self, api, universe, calibration):
        self.api = api
        self.universe = universe
        self.calibration = calibration
        self.daily_stats = {
            "trades_today": 0, "daily_pnl": 0.0,
            "last_reset": datetime.now(timezone.utc).date(),
            "last_trades": [], "last_plan": {},
            "brier_score": 1.0, "win_rate": 0.0,
        }
        self._learning_lock = asyncio.Lock()
        self._deduplicator = MarketDeduplicator()

    async def run_cycle(self):
        try:
            today = datetime.now(timezone.utc).date()
            if today != self.daily_stats["last_reset"]:
                await self._run_daily_learning()
                self.daily_stats = {
                    "trades_today": 0, "daily_pnl": 0.0,
                    "last_reset": today, "last_trades": [],
                    "last_plan": {}, "brier_score": 1.0, "win_rate": 0.0,
                }

            markets = await self.universe.get_active_markets()
            if not markets:
                return {"status": "no_markets", "trades": 0}

            brier = self.calibration.brier_score()
            TUNER.update_brier(brier)
            self.daily_stats["brier_score"] = round(brier, 4)

            thresholds = get_adjusted_thresholds()
            if brier > 0.25 and self.calibration.trade_count >= 5:
                thresholds["min_total_score_single"] += 5.0
                thresholds["min_edge_bps"] += 25

            market_dicts = []
            for m in markets:
                d = {
                    "ticker": m.slug or m.id,
                    "market_id": m.id,
                    "title": m.title,
                    "category": str(m.category.value if hasattr(m.category, "value") else m.category),
                    "confidence": m.confidence,
                    "liquidity": m.liquidity,
                    "spread": m.spread,
                    "volume_24h": m.volume_24h,
                    "last_price": m.last_price,
                    "ends_at": m.ends_at,
                    "best_bid": m.best_bid,
                    "best_ask": m.best_ask,
                    "market_type": getattr(m, "market_type", "single"),
                    "legs": 1,
                    "close_time": getattr(m, "close_time", None),
                    "expiration_time": getattr(m, "close_time", None),
                    "endDate": getattr(m, "close_time", None),
                    "tags": getattr(m, "tags", []),
                    "question": getattr(m, "question", m.title),
                    "volume": getattr(m, "volume_24h", 0),
                    "minutes_to_close": getattr(m, "minutes_to_close", None),
                    "slug": getattr(m, "slug", ""),
                    "raw": getattr(m, "raw", {}),
                }
                ob = self.universe.get_orderbook(m.slug) or self.universe.get_orderbook(m.id)
                if ob:
                    d["yes_bid"] = ob.get("yes_bid")
                    d["yes_ask"] = ob.get("yes_ask")
                    d["no_bid"] = ob.get("no_bid")
                    d["no_ask"] = ob.get("no_ask")
                    d["token_id"] = ob.get("token_id")
                market_dicts.append(d)

            pool, rejects = single_pool(market_dicts)
            logger.info("selector_pool size=%d rejects=%s", len(pool), rejects)
            if not pool:
                logger.warning("selector_pool_empty breakdown=%s", rejects)
                return {"status": "no_pool", "trades": 0, "markets_scanned": len(markets), "rejects": rejects}

            candidates = []
            dedup_skipped = 0
            for m in pool:
                ob = self.universe.get_orderbook(m.get("slug", "")) or self.universe.get_orderbook(m.get("ticker", ""))
                if not ob:
                    ob = {
                        "yes_bids": [{"price": m.get("best_bid", 0), "qty": 1}],
                        "yes_asks": [{"price": m.get("best_ask", 1), "qty": 1}],
                        "no_bids": [{"price": 1 - m.get("best_ask", 1), "qty": 1}],
                        "no_asks": [{"price": 1 - m.get("best_bid", 0), "qty": 1}],
                    }
                yes_bid = float(m.get("yes_bid") or m.get("best_bid") or 0.0)
                yes_ask = float(m.get("yes_ask") or m.get("best_ask") or 0.0)
                if yes_bid > 0.0 and yes_ask > 0.0:
                    current_mid = (yes_bid + yes_ask) / 2.0
                else:
                    current_mid = float(m.get("last_price") or 0.0)
                ticker = str(m.get("ticker") or "")
                if not self._deduplicator.should_evaluate(ticker, current_mid):
                    dedup_skipped += 1
                    logger.debug("evaluating_market_skip ticker=%s reason=no_price_change", ticker)
                    continue
                cand, reason = build_candidate(m, ob, all_markets=market_dicts)
                if cand:
                    candidates.append(cand)
                    self._deduplicator.update(ticker, current_mid, float(cand.details.get("edge", 0.0)))
                else:
                    self._deduplicator.update(ticker, current_mid, 0.0)
                    logger.debug("candidate_rejected ticker=%s reason=%s", m.get("ticker"), reason)
                    reject_reasons = []
                    if reason in {"edge_too_low", "low_edge"}:
                        reject_reasons.append("edge_too_low")
                    if reason == "low_total_score":
                        reject_reasons.append("total_score_low")
                    if reason in {"unsupported_projection_model", "failed_projection"}:
                        reject_reasons.append("no_projection")
                    if reject_reasons:
                        logger.info("drop_reasons ticker=%s reasons=%s", m.get("ticker"), reject_reasons)

            if not candidates:
                logger.warning("no_candidates_after_build pool=%d", len(pool))
                return {"status": "no_candidates", "trades": 0, "pool_size": len(pool)}

            ranked = rank_candidates(candidates)
            if not ranked:
                return {"status": "no_candidates", "trades": 0, "markets_scanned": len(markets), "pool_size": len(pool)}

            selected = self._select_trades(ranked, thresholds)
            if not selected:
                return {"status": "no_selected", "trades": 0, "candidates": len(candidates)}

            executed = await self._execute_trades(selected, thresholds)
            for t in executed:
                self.calibration.record_trade(t["market_id"], t["predicted_prob"], t["side"])

            self.daily_stats["last_trades"] = executed[-10:]
            self.daily_stats["win_rate"] = round(TUNER.learning.winning_trades / max(1, TUNER.learning.total_trades), 4)
            model_stats: dict[str, dict[str, float]] = {}
            family_stats: dict[str, int] = {}
            positive_edges = negative_edges = zero_edges = 0
            for c in candidates:
                family_stats[c.category] = family_stats.get(c.category, 0) + 1
                model = str(c.details.get("projection_model", "unknown"))
                edge = float(c.details.get("edge", 0.0))
                ms = model_stats.setdefault(model, {"evaluated": 0, "positive_edges": 0, "edge_sum": 0.0})
                ms["evaluated"] += 1
                ms["edge_sum"] += edge
                if edge > 0:
                    ms["positive_edges"] += 1
                    positive_edges += 1
                elif edge < 0:
                    negative_edges += 1
                else:
                    zero_edges += 1
            for ms in model_stats.values():
                evaluated = max(1, int(ms["evaluated"]))
                ms["avg_edge"] = round(float(ms["edge_sum"]) / evaluated, 4)
                ms.pop("edge_sum", None)

            return {
                "status": "ok",
                "trades": len(executed),
                "candidates": len(candidates),
                "selected": len(selected),
                "models_used": model_stats,
                "dedup_skipped": dedup_skipped,
                "edge_distribution": {"positive": positive_edges, "negative": negative_edges, "zero": zero_edges},
                "families": family_stats,
            }
        except Exception as e:
            logger.exception("run_cycle_fatal: %s", e)
            return {"status": "error", "error": str(e)}

    async def _run_daily_learning(self):
        async with self._learning_lock:
            try:
                trades = await self.api.get_trades(limit=200)
            except Exception:
                trades = []
            yesterday = datetime.now(timezone.utc) - timedelta(days=1)
            day_trades = [t for t in trades if isinstance(t, dict) and self._parse_time(t) >= yesterday]
            post_mortems = []
            for trade in day_trades:
                pnl = trade.get("realized_pnl", 0) or trade.get("pnl", 0) or 0
                mid = trade.get("market_id") or trade.get("id", "")
                cat = "unknown"
                m = None
                try:
                    m = await self.api.get_market(mid)
                    cat = self.universe._infer_category(m.get("tags", []), m.get("question", "")).value
                except Exception:
                    pass
                TUNER.record_trade_outcome(
                    cat, trade.get("price", 0.5), 1 if pnl > 0 else 0, pnl,
                    trade.get("confidence", 0.5), trade.get("edge_bps", 0),
                    {"price": trade.get("price", 0.5), "volume": 0},
                )
                market_title = trade.get("market_title") or trade.get("title") or mid
                edge_bps = trade.get("edge_bps", 0)
                confidence = trade.get("confidence", 0.5)
                predicted_prob = trade.get("predicted_prob", 0.5)
                price = trade.get("price", 0.5)
                if pnl > 0:
                    pm_type, title = "success", f"Win: {market_title[:45]}"
                    body = f"Closed at +${pnl:+.2f}. Entry edge {edge_bps} bps with confidence {confidence:.2f}. Predicted {predicted_prob:.2f} vs market {price:.2f}. Category {cat} performed as expected."
                elif pnl < 0:
                    pm_type, title = "fail", f"Loss: {market_title[:45]}"
                    reasons = []
                    if edge_bps < 50: reasons.append("edge below 50 bps")
                    if confidence < 0.60: reasons.append("confidence under 0.60")
                    if m and m.get("liquidity", 99999) < 2000: reasons.append("liquidity under $2K")
                    if trade.get("spread", 0) > 0.08: reasons.append("spread > 8%")
                    reason_str = "; ".join(reasons) if reasons else "market moved against position"
                    body = f"Closed at ${pnl:+.2f}. Predicted {predicted_prob:.2f} but settled opposite. Likely causes: {reason_str}. Review if {cat} prior needs recalibration."
                else:
                    pm_type, title = "adjust", f"Push: {market_title[:45]}"
                    body = f"Broke even. Edge {edge_bps} bps was likely consumed by spread or slippage. Consider raising min_edge_bps for similar setups."
                post_mortems.append({"type": pm_type, "title": title, "meta": f"PnL ${pnl:+.2f} | {edge_bps} bps | {cat}", "body": body})
            plan = TUNER.get_daily_improvement_plan()
            self.daily_stats["last_plan"] = plan
            self.daily_stats["post_mortems"] = post_mortems[-20:]
            try:
                le = get_learning_engine()
                le.rebuild_priors(lookback_days=30)
            except Exception as e:
                logger.warning("daily_learning_rebuild_failed: %s", e)
            logger.info("daily_learning_complete trades=%d post_mortems=%d plan_adjustments=%d",
                len(day_trades), len(post_mortems), len(plan.get("adjustments", [])))

    @staticmethod
    def _parse_time(trade):
        ts = trade.get("timestamp") or trade.get("created_at") or trade.get("time")
        if not ts:
            return datetime.now(timezone.utc)
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        try:
            return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except Exception:
            return datetime.now(timezone.utc)

    def _select_trades(self, candidates, thresholds):
        max_daily = max(0, thresholds.get("max_daily_trades", 5) - self.daily_stats["trades_today"])
        if max_daily <= 0:
            return []
        max_positions = thresholds.get("max_positions", 10)
        return candidates[:min(max_daily, max_positions)]

    async def _execute_trades(self, selected, thresholds):
        """FIX5: 3-gate safety + token propagation."""
        executed = []
        override_ticker = os.getenv("OVERRIDE_TICKER", "").strip().lower()
        auto = getattr(settings, 'auto_execute', False)
        dry = getattr(settings, 'dry_run', True)
        live = getattr(settings, 'enable_live_trading', False)
        for sel in selected:
            if override_ticker and str(sel.ticker).strip().lower() == override_ticker:
                logger.warning("override_trade_enabled ticker=%s forcing side=YES size=1.0", sel.ticker)
                sel.side = "YES"
            p = sel.entry_price
            rpct = {1: 0.02, 2: 0.01, 3: 0.0075}.get(sel.legs, 0.005)
            risk = settings.bankroll_usd * rpct
            size = 1.0 if override_ticker and str(sel.ticker).strip().lower() == override_ticker else min(risk / max(p, 0.01), 100.0)
            tid = sel.details.get("token_id") if sel.details else None
            sim_tid = f"sim-{uuid.uuid4().hex[:8]}"
            info = {"market_id": sel.ticker, "side": sel.side, "price": round(p, 4), "size": round(size, 4), "total_score": sel.total_score, "edge_bps": int(sel.spread_cents * 100), "predicted_prob": sel.details.get("fair_probability", p) if sel.details else p, "confidence": sel.confidence_score / 100.0, "category": sel.category, "token_id": tid, "risk_usd": round(risk, 2)}
            if dry or not auto or not live:
                info["status"] = "simulated"
                logger.info("SIM %s %s @%.4f sz=%.4f tid=%s", sel.ticker, sel.side, p, size, sim_tid)
            else:
                if not tid:
                    info.update({"status": "failed", "error": "missing_token_id"})
                    logger.error("NO_TID %s", sel.ticker)
                else:
                    logger.info("LIVE %s tid=%s %s sz=%.4f", sel.ticker, tid, sel.side, size)
                    try:
                        r = await self.api.place_order(tid, sel.side, size, p)
                        info.update({"status": "executed", "order_id": r.get("id", "")})
                        self.daily_stats["trades_today"] += 1
                    except Exception as e:
                        info.update({"status": "failed", "error": str(e)})
                        logger.exception("EXEC_FAIL %s", sel.ticker)
            executed.append(info)
        return executed
