import os
import logging
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

from app.strategy import TUNING

logger = logging.getLogger("app.engine")

BANKROLL_PCT = {
    1: 0.02,   # 1 leg = 2.00%
    2: 0.01,   # 2 legs = 1.00%
    3: 0.0075, # 3 legs = 0.75%
    4: 0.0050, # 4 legs = 0.50%
}

@dataclass
class CandidateBook:
    ticker: str
    yes_bid: float
    yes_ask: float
    no_bid: float
    no_ask: float
    spread: float
    category: str
    legs: int = 1

@dataclass
class CandidateModel:
    ticker: str
    family: str
    fair: float
    edge: float
    projection: float
    confidence: float
    total: float
    side: str = "NA"

class TradingEngine:
    def __init__(self, api, universe_service):
        self.api = api
        self.universe = universe_service
        self.min_edge = 0.02          # 2% minimum edge
        self.min_confidence = 60.0
        self.min_projection = 50.0
        self.max_spread = 0.05        # 5 cents max spread
        self.max_category_exposure = TUNING.max_category_exposure_pct
        self.max_orders_per_cycle = TUNING.max_orders_per_cycle
        from app.cashout import CashoutManager
        self.cashout_manager = CashoutManager(api_client=self.api, db_session=None)

    def _calculate_fair(self, book: CandidateBook) -> float:
        """Midpoint fair value with spread penalty."""
        mid = (book.yes_bid + book.yes_ask) / 2
        spread_penalty = book.spread * 0.1
        return round(mid - spread_penalty, 4)

    def _calculate_edge(self, book: CandidateBook, fair: float, side: str) -> float:
        if side == "yes":
            return round(fair - book.yes_ask, 4)
        elif side == "no":
            return round(fair - book.no_ask, 4)
        return 0.0

    def _calculate_projection(self, edge: float, confidence: float) -> float:
        """Weighted score: edge * confidence, scaled to 0-100."""
        if edge <= 0:
            return 0.0
        raw = edge * confidence * 100
        return round(min(raw, 100.0), 2)

    def _calculate_total(self, projection: float, confidence: float) -> float:
        return round((projection * 0.6) + (confidence * 0.4), 2)

    def _get_position_size(self, legs: int, bankroll: float) -> float:
        pct = BANKROLL_PCT.get(legs, 0.0050)
        return round(bankroll * pct, 2)

    def score_candidate(self, book: CandidateBook) -> Optional[CandidateModel]:
        fair = self._calculate_fair(book)

        # Evaluate YES side
        edge_yes = self._calculate_edge(book, fair, "yes")
        proj_yes = self._calculate_projection(edge_yes, 66.0)
        total_yes = self._calculate_total(proj_yes, 66.0)

        # Evaluate NO side
        edge_no = self._calculate_edge(book, fair, "no")
        proj_no = self._calculate_projection(edge_no, 66.0)
        total_no = self._calculate_total(proj_no, 66.0)

        # Pick better side
        if total_yes >= total_no and edge_yes > 0:
            side = "yes"
            edge = edge_yes
            projection = proj_yes
            total = total_yes
        elif edge_no > 0:
            side = "no"
            edge = edge_no
            projection = proj_no
            total = total_no
        else:
            return None  # No positive edge

        return CandidateModel(
            ticker=book.ticker,
            family=book.ticker.rsplit("-", 2)[0] if "-" in book.ticker else book.ticker,
            fair=fair,
            edge=edge,
            projection=projection,
            confidence=66.0,
            total=total,
            side=side,
        )

    def validate_candidate(self, model: CandidateModel, book: CandidateBook,
                          current_exposure: Dict[str, float], bankroll: float) -> tuple[bool, str]:
        # Spread check
        if book.spread > self.max_spread:
            return False, f"spread_too_wide|{book.spread}"

        # Edge check
        if model.edge < self.min_edge:
            return False, f"low_edge|{model.edge}"

        # Projection check
        if model.projection < self.min_projection:
            return False, f"failed_projection|{model.projection}"

        # Confidence check
        if model.confidence < self.min_confidence:
            return False, f"low_confidence|{model.confidence}"

        # Category exposure check
        cat_exposure = current_exposure.get(book.category, 0.0)
        position_size = self._get_position_size(book.legs, bankroll)
        new_exposure = cat_exposure + position_size
        if new_exposure > bankroll * self.max_category_exposure:
            return False, f"category_exposure|{book.category}|{new_exposure/bankroll:.2%}"

        # Duplicate / family check
        family_positions = [p for p in current_exposure if p.startswith(model.family)]
        if len(family_positions) > 0:
            return False, f"duplicate_market|{model.family}"

        return True, "precheck_ok"

    async def run_cycle(self) -> Dict[str, Any]:
        logger.info("run_cycle_start")

        # Auth check
        if not await self.api.health_check():
            logger.error("cycle_aborted_auth_failed")
            return {"markets": 0, "candidates": 0, "orders": 0, "rejected": 0, "error": "auth_failed"}

        # Fetch universe
        markets = await self.universe.get_active_markets()
        logger.info("universe_loaded", extra={"count": len(markets)})

        # Filter to singles only
        singles = [m for m in markets if m.get("type") == "single" and m.get("category") in
                   ["sports", "politics", "crypto", "climate", "economics"]]
        logger.info("singles_filtered", extra={"count": len(singles)})

        # Fetch orderbooks
        books = []
        for m in singles:
            ob = await self.api.get_orderbook(m["ticker"])
            if ob:
                books.append((m, ob))

        # Normalize and score
        candidates = []
        rejected = []
        current_exposure = await self._get_current_exposure()
        balances = await self.api.get_balances()
        bankroll = balances.get("available", 1000.0) if balances else 1000.0

        for market, ob in books:
            book = CandidateBook(
                ticker=market["ticker"],
                yes_bid=ob.get("yes_bid", 0),
                yes_ask=ob.get("yes_ask", 0),
                no_bid=ob.get("no_bid", 0),
                no_ask=ob.get("no_ask", 0),
                spread=ob.get("spread", 1.0),
                category=market.get("category", "unknown"),
                legs=market.get("legs", 1),
            )

            # Skip empty / zero liquidity
            if book.yes_bid == 0 and book.yes_ask == 0:
                rejected.append((book.ticker, "no_snapshot_or_quotes"))
                continue

            model = self.score_candidate(book)
            if not model:
                rejected.append((book.ticker, "no_positive_edge"))
                continue

            ok, reason = self.validate_candidate(model, book, current_exposure, bankroll)
            if not ok:
                rejected.append((book.ticker, reason))
                continue

            candidates.append((model, book))

        # Sort by total score
        candidates.sort(key=lambda x: x[0].total, reverse=True)

        open_positions = await self.api.get_positions()
        if hasattr(self, "cashout_manager") and self.cashout_manager:
            try:
                await self.cashout_manager.evaluate_positions(open_positions)
            except Exception as e:
                logger.warning(f"cashout_evaluate_error error={e}")

        # Execute top N (respecting AUTO_EXECUTE)
        orders_placed = []
        for model, book in candidates[:self.max_orders_per_cycle]:
            size = self._get_position_size(book.legs, bankroll)
            price = book.yes_ask if model.side == "yes" else book.no_ask

            if model.side == "yes":
                result = await self.api.place_buy_order(model.ticker, price, size)
            else:
                result = await self.api.place_sell_order(model.ticker, price, size)

            if result:
                orders_placed.append({
                    "ticker": model.ticker,
                    "side": model.side,
                    "price": price,
                    "size": size,
                    "dry_run": result.get("dry_run", False),
                })

        # Cashout / stop-loss check (only if AUTO_EXECUTE=true)
        if os.getenv("AUTO_EXECUTE", "false").lower() == "true":
            await self._check_stop_losses(current_exposure)

        summary = {
            "markets": len(singles),
            "candidates": len(candidates),
            "orders": len([o for o in orders_placed if not o.get("dry_run")]),
            "dry_runs": len([o for o in orders_placed if o.get("dry_run")]),
            "rejected": len(rejected),
        }
        logger.info("cycle_summary", extra=summary)
        return summary

    async def _get_current_exposure(self) -> Dict[str, float]:
        positions = await self.api.get_positions()
        exposure = {}
        for p in positions:
            cat = p.get("category", "unknown")
            exposure[cat] = exposure.get(cat, 0.0) + p.get("size", 0.0) * p.get("avg_price", 0.0)
        return exposure

    async def _check_stop_losses(self, exposure: Dict[str, float]):
        positions = await self.api.get_positions()
        for p in positions:
            unrealized = p.get("unrealized_pnl_pct", 0.0)
            if unrealized < -0.20:  # 20% stop loss
                ticker = p.get("ticker")
                logger.info("stop_loss_triggered", extra={
                    "ticker": ticker,
                    "unrealized_pct": unrealized,
                })
                # Place closing order
                await self.api.place_sell_order(ticker, price=p.get("current_price", 0.5), size=p.get("size", 0))
