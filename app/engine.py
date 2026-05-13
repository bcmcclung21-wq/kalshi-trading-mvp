from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.services.universe import UniverseService
from app.strategy import TUNER, get_adjusted_thresholds
from app.learning import get_learning_engine

logger = logging.getLogger("app.engine")

class TradingEngine:
    def __init__(self, api, universe, calibration):
        self.api = api
        self.universe = universe
        self.calibration = calibration
        self.daily_stats = {
            "trades_today": 0,
            "daily_pnl": 0.0,
            "last_reset": datetime.now(timezone.utc).date(),
            "last_trades": [],
            "last_plan": {},
            "brier_score": 0.0,
            "win_rate": 0.0,
        }
        self._learning_lock = asyncio.Lock()

    async def run_cycle(self):
        try:
            today = datetime.now(timezone.utc).date()
            if today != self.daily_stats["last_reset"]:
                await self._run_daily_learning()
                self.daily_stats = {
                    "trades_today": 0, "daily_pnl": 0.0,
                    "last_reset": today, "last_trades": [],
                    "last_plan": {}, "brier_score": 0.0, "win_rate": 0.0,
                }

            markets = await self.universe.get_active_markets()
            if not markets:
                return {"status": "no_markets", "trades": 0}

            # UPDATE BRIER — FIX #1
            brier = self.calibration.brier_score()
            TUNER.update_brier(brier)
            self.daily_stats["brier_score"] = round(brier, 4)

            thresholds = get_adjusted_thresholds()
            if brier > 0.25 and self.calibration.trade_count >= 5:
                thresholds["min_total_score_single"] += 5.0
                thresholds["min_edge_bps"] += 25

            candidates = self._score_candidates(markets, thresholds)
            if not candidates:
                return {"status": "no_candidates", "trades": 0, "markets_scanned": len(markets)}

            selected = self._select_trades(candidates, thresholds)
            if not selected:
                return {"status": "no_selected", "trades": 0, "candidates": len(candidates)}

            executed = await self._execute_trades(selected, thresholds)
            for t in executed:
                self.calibration.record_trade(t["market_id"], t["predicted_prob"], t["side"])

            # Update daily stats with trades
            self.daily_stats["last_trades"] = executed[-10:]
            self.daily_stats["win_rate"] = round(
                TUNER.learning.winning_trades / max(1, TUNER.learning.total_trades), 4
            )

            return {
                "status": "ok",
                "trades": len(executed),
                "candidates": len(candidates),
                "selected": len(selected),
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

            for trade in day_trades:
                pnl = trade.get("realized_pnl", 0) or trade.get("pnl", 0) or 0
                mid = trade.get("market_id") or trade.get("id", "")
                cat = "unknown"
                try:
                    m = await self.api.get_market(mid)
                    cat = UniverseService._infer_category(
                        m.get("tags", []), m.get("question", "")
                    ).value
                except Exception:
                    pass

                TUNER.record_trade_outcome(
                    cat,
                    trade.get("price", 0.5),
                    1 if pnl > 0 else 0,
                    pnl,
                    trade.get("confidence", 0.5),
                    trade.get("edge_bps", 0),
                    {"price": trade.get("price", 0.5), "volume": 0},
                )

            # FIX #2 & #3: Generate plan AND apply it
            plan = TUNER.get_daily_improvement_plan()
            self.daily_stats["last_plan"] = plan

            # Rebuild learning priors from DB
            try:
                le = get_learning_engine()
                le.rebuild_priors(lookback_days=30)
            except Exception as e:
                logger.warning("daily_learning_rebuild_failed: %s", e)

            logger.info("daily_learning_complete plan_adjustments=%d", len(plan.get("adjustments", [])))

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

    def _score_candidates(self, markets, thresholds):
        candidates = []
        max_positions = thresholds.get("max_positions", 10)
        min_score = thresholds.get("min_total_score_single", 50.0)
        min_edge = thresholds.get("min_edge_bps", 50)
        max_spread = thresholds.get("max_spread_pct", 0.08)

        for m in markets[:max_positions * 3]:
            if hasattr(m, "spread"):
                spread, liquidity, confidence = m.spread, m.liquidity, m.confidence
                mid, title = m.id, m.title
                category = m.category.value if hasattr(m.category, "value") else str(m.category)
            else:
                spread = m.get("spread", 1.0)
                liquidity = m.get("liquidity", 0.0)
                confidence = m.get("confidence", 0.0)
                mid = m.get("id", "")
                title = m.get("title", "Untitled")
                category = str(m.get("category", "other"))

            if spread > max_spread or liquidity < 500:
                continue

            if hasattr(m, "spread"):
                market_price = getattr(m, "last_price", 0.5) or 0.5
            else:
                market_price = m.get("last_price", 0.5) or 0.5
            try:
                fair_prob = confidence
                edge_bps = int(abs(fair_prob - market_price) * 10000)
            except Exception:
                edge_bps = 0
                fair_prob = 0.5

            total_score = (
                (confidence * 40) +
                (min(liquidity / 10000, 1.0) * 20) +
                (max(0, 1 - spread / max_spread) * 20) +
                (min(edge_bps / 200, 1.0) * 20)
            )

            if total_score >= min_score and edge_bps >= min_edge:
                # Near resolution bonus
                try:
                    ends = getattr(m, "ends_at", None) or m.get("ends_at")
                    if ends:
                        mins = (ends - datetime.now(timezone.utc)).total_seconds() / 60
                        if 5 < mins < 60 and confidence > 0.85:
                            total_score += 15
                            edge_bps += 25
                except Exception:
                    pass

                candidates.append({
                    "market": m,
                    "total_score": round(total_score, 2),
                    "edge_bps": edge_bps,
                    "fair_prob": fair_prob,
                })

        candidates.sort(key=lambda x: x["total_score"], reverse=True)
        return candidates

    def _select_trades(self, candidates, thresholds):
        max_daily = max(0, thresholds.get("max_daily_trades", 5) - self.daily_stats["trades_today"])
        if max_daily <= 0:
            return []
        return candidates[:max_daily]

    async def _execute_trades(self, selected, thresholds):
        executed = []
        auto_execute = settings.auto_execute
        dry_run = not auto_execute

        for sel in selected:
            m = sel["market"]
            price = sel.get("fair_prob", 0.5)
            size = min(thresholds.get("max_risk_per_trade_usd", 50.0) / max(price, 0.01), 100.0)

            if hasattr(m, "id"):
                market_id, market_title = m.id, m.title
                m_confidence = m.confidence
                m_category = m.category.value if hasattr(m.category, "value") else str(m.category)
            else:
                market_id = m.get("id", "")
                market_title = m.get("title", "Untitled")
                m_confidence = m.get("confidence", 0.0)
                m_category = str(m.get("category", "other"))

            info = {
                "market_id": market_id, "market_title": market_title, "side": "BUY",
                "price": round(price, 4), "size": round(size, 4),
                "total_score": sel["total_score"], "edge_bps": sel["edge_bps"],
                "predicted_prob": price, "confidence": m_confidence, "category": m_category,
            }

            if not dry_run and auto_execute:
                try:
                    result = await self.api.place_order(market_id, "BUY", size, price)
                    info.update({"status": "executed", "order_id": result.get("id", "")})
                    self.daily_stats["trades_today"] += 1
                except Exception as e:
                    info.update({"status": "failed", "error": str(e)})
            else:
                info["status"] = "dry_run"

            executed.append(info)

        return executed
