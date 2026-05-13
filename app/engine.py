from __future__ import annotations
import asyncio
import logging
import os
from datetime import datetime, timedelta

from app.config import settings
from app.services.universe import UniverseService
from app.strategy import TUNER, get_adjusted_thresholds

logger = logging.getLogger("app.engine")

class TradingEngine:
    def __init__(self, api, universe, calibration):
        self.api = api
        self.universe = universe
        self.calibration = calibration
        self.daily_stats = {"trades_today": 0, "daily_pnl": 0.0, "last_reset": datetime.utcnow().date()}
        self._learning_lock = asyncio.Lock()

    async def run_cycle(self):
        """Full trading cycle with exception safety at top level."""
        try:
            today = datetime.utcnow().date()
            if today != self.daily_stats["last_reset"]:
                await self._run_daily_learning()
                self.daily_stats = {"trades_today": 0, "daily_pnl": 0.0, "last_reset": today}

            markets = await self.universe.get_active_markets()
            if not markets:
                return {"status": "no_markets", "trades": 0}

            brier = self.calibration.brier_score()
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
            yesterday = datetime.utcnow() - timedelta(days=1)
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

    @staticmethod
    def _parse_time(trade):
        ts = trade.get("timestamp") or trade.get("created_at") or trade.get("time")
        if not ts:
            return datetime.utcnow()
        if isinstance(ts, (int, float)):
            return datetime.utcfromtimestamp(ts)
        try:
            return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except Exception:
            return datetime.utcnow()

    def _score_candidates(self, markets, thresholds):
        """Real scoring: filter by spread, liquidity, compute edge & confidence."""
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

            # Compute edge from confidence vs market price
            market_price = 0.5  # default; ideally fetch from orderbook
            try:
                # Use confidence as proxy for "fair probability"
                fair_prob = confidence
                edge_bps = int(abs(fair_prob - market_price) * 10000)
            except Exception:
                edge_bps = 0
                fair_prob = 0.5

            # Total score = weighted composite
            total_score = (
                (confidence * 40) +
                (min(liquidity / 10000, 1.0) * 20) +
                (max(0, 1 - spread / max_spread) * 20) +
                (min(edge_bps / 200, 1.0) * 20)
            )

            if total_score >= min_score and edge_bps >= min_edge:
                candidates.append({
                    "market": m,
                    "total_score": round(total_score, 2),
                    "edge_bps": edge_bps,
                    "fair_prob": fair_prob,
                })

        # Sort by total score descending
        candidates.sort(key=lambda x: x["total_score"], reverse=True)
        return candidates

    def _select_trades(self, candidates, thresholds):
        """Select top N candidates respecting daily trade limit."""
        max_daily = max(0, thresholds.get("max_daily_trades", 5) - self.daily_stats["trades_today"])
        if max_daily <= 0:
            return []
        return candidates[:max_daily]

    async def _execute_trades(self, selected, thresholds):
        """Execute or dry-run selected trades."""
        executed = []
        # FIX: read from settings, not os.getenv
        auto_execute = settings.auto_execute
        dry_run = not auto_execute  # safety: if auto_execute is False, always dry-run

        for sel in selected:
            m = sel["market"]
            price = sel.get("fair_prob", 0.5)
            size = min(
                thresholds.get("max_risk_per_trade_usd", 50.0) / max(price, 0.01),
                100.0,
            )

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
