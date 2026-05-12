import os
import logging
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta

from app.strategy import TUNING
from app.db import SessionLocal
from app.models import OrderRecord

logger = logging.getLogger("app.engine")

BANKROLL_PCT = {
    1: 0.02,
    2: 0.01,
    3: 0.0075,
    4: 0.0050,
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
        self.max_category_exposure = TUNING.max_category_exposure_pct
        self.max_orders_per_cycle = TUNING.max_orders_per_cycle
        from app.cashout import CashoutManager
        self.cashout_manager = CashoutManager(api_client=self.api, db_session=None)

    def _calculate_fair(self, book: CandidateBook) -> float:
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
        if edge <= 0:
            return 0.0
        raw = edge * confidence * 100
        return round(min(raw, 100.0), 2)

    def _calculate_total(self, projection: float, confidence: float) -> float:
        return round((projection * 0.6) + (confidence * 0.4), 2)

    def _get_position_size(self, legs: int, bankroll: float) -> float:
        pct = BANKROLL_PCT.get(legs, 0.0050)
        return round(bankroll * pct, 2)

    def _category_confidence(self, category: str) -> float:
        """Category-specific confidence baseline."""
        defaults = {
            "sports": 70.0,
            "politics": 65.0,
            "climate": 66.0,
            "economics": 60.0,
            "crypto": 60.0,
        }
        return defaults.get(category, 66.0)

    def score_candidate(self, book: CandidateBook) -> Optional[CandidateModel]:
        fair = self._calculate_fair(book)
        confidence = self._category_confidence(book.category)

        edge_yes = self._calculate_edge(book, fair, "yes")
        proj_yes = self._calculate_projection(edge_yes, confidence)
        total_yes = self._calculate_total(proj_yes, confidence)

        edge_no = self._calculate_edge(book, fair, "no")
        proj_no = self._calculate_projection(edge_no, confidence)
        total_no = self._calculate_total(proj_no, confidence)

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
            return None

        return CandidateModel(
            ticker=book.ticker,
            family=book.ticker.rsplit("-", 2)[0] if "-" in book.ticker else book.ticker,
            fair=fair,
            edge=edge,
            projection=projection,
            confidence=confidence,
            total=total,
            side=side,
        )

    def validate_candidate(self, model: CandidateModel, book: CandidateBook,
                          current_exposure: Dict[str, float], bankroll: float,
                          family_tickers: set) -> tuple[bool, str]:
        min_edge = TUNING.min_edge_bps / 10000.0
        min_proj = TUNING.min_projection_score
        min_conf = TUNING.min_confidence_score
        max_spread = TUNING.max_spread_cents / 100.0

        if book.spread > max_spread:
            return False, f"spread_too_wide|{book.spread}"

        if model.edge < min_edge:
            return False, f"low_edge|{model.edge}"

        if model.projection < min_proj:
            return False, f"failed_projection|{model.projection}"

        if model.confidence < min_conf:
            return False, f"low_confidence|{model.confidence}"

        cat_exposure = current_exposure.get(book.category, 0.0)
        position_size = self._get_position_size(book.legs, bankroll)
        new_exposure = cat_exposure + position_size
        if new_exposure > bankroll * self.max_category_exposure:
            return False, f"category_exposure|{book.category}|{new_exposure/bankroll:.2%}"

        if model.family in family_tickers:
            return False, f"duplicate_market|{model.family}"

        return True, "precheck_ok"

    def _is_same_day_sports(self, market: Dict[str, Any]) -> bool:
        """Sports markets must close today (UTC)."""
        if market.get("category") != "sports":
            return True
        close_str = market.get("close_time") or market.get("endDate") or ""
        if not close_str:
            return False
        try:
            close_str = close_str.replace("Z", "+00:00")
            close_dt = datetime.fromisoformat(close_str)
            today = datetime.now(timezone.utc).date()
            return close_dt.date() == today
        except Exception:
            return False

    async def run_cycle(self) -> Dict[str, Any]:
        logger.info("run_cycle_start")

        if not await self.api.health_check():
            logger.error("cycle_aborted_auth_failed")
            return {"markets": 0, "candidates": 0, "orders": 0, "rejected": 0, "error": "auth_failed"}

        markets = await self.universe.get_active_markets()
        logger.info("universe_loaded", extra={"count": len(markets)})

        # Filter to singles in allowed categories
        allowed = {"sports", "politics", "crypto", "climate", "economics"}
        singles = [
            m for m in markets
            if m.get("type") == "single" and m.get("category") in allowed
            and self._is_same_day_sports(m)
        ]
        logger.info("singles_filtered", extra={"count": len(singles)})

        # Respect max orderbooks per cycle
        max_books = TUNING.max_orderbooks_per_cycle
        singles = singles[:max_books]

        books = []
        for m in singles:
            ob = await self.api.get_orderbook(m["ticker"])
            if ob:
                books.append((m, ob))

        current_exposure = await self._get_current_exposure()
        balances = await self.api.get_balances()
        bankroll = balances.get("available", 1000.0) if balances else 1000.0

        # Build set of existing family tickers for duplicate check
        positions = await self.api.get_positions()
        family_tickers = set()
        for p in positions:
            t = p.get("ticker", "")
            family = t.rsplit("-", 2)[0] if "-" in t else t
            family_tickers.add(family)

        candidates = []
        rejected = []

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

            if book.yes_bid == 0 and book.yes_ask == 0:
                rejected.append((book.ticker, "no_snapshot_or_quotes"))
                continue

            model = self.score_candidate(book)
            if not model:
                rejected.append((book.ticker, "no_positive_edge"))
                continue

            ok, reason = self.validate_candidate(model, book, current_exposure, bankroll, family_tickers)
            if not ok:
                rejected.append((book.ticker, reason))
                continue

            candidates.append((model, book))

        candidates.sort(key=lambda x: x[0].total, reverse=True)

        # Cashout evaluation
        if hasattr(self, "cashout_manager") and self.cashout_manager:
            try:
                await self.cashout_manager.evaluate_positions(positions)
            except Exception as e:
                logger.warning(f"cashout_evaluate_error error={e}")

        orders_placed = []
        for model, book in candidates[:self.max_orders_per_cycle]:
            size = self._get_position_size(book.legs, bankroll)
            if model.side == "yes":
                price = book.yes_ask
                result = await self.api.place_buy_order(model.ticker, price, size, outcome="YES")
            else:
                price = book.no_ask
                result = await self.api.place_buy_order(model.ticker, price, size, outcome="NO")

            if result:
                orders_placed.append({
                    "ticker": model.ticker,
                    "side": model.side,
                    "price": price,
                    "size": size,
                    "dry_run": result.get("dry_run", False),
                })
                # Persist to DB for Brier tracking
                if not result.get("dry_run"):
                    try:
                        with SessionLocal() as db:
                            rec = OrderRecord(
                                ticker=model.ticker,
                                category=book.category,
                                side=model.side.upper(),
                                market_type="single",
                                legs=book.legs,
                                count=int(size),
                                price_cents=int(price * 100),
                                bankroll_pct=BANKROLL_PCT.get(book.legs, 0.005),
                                status="pending",
                                dry_run=False,
                                estimated_win_probability=model.fair,
                                calibration_status="ok",
                            )
                            db.add(rec)
                            db.commit()
                            logger.info("order_persisted", extra={"ticker": model.ticker, "id": rec.id})
                    except Exception as e:
                        logger.error("order_persist_failed", extra={"ticker": model.ticker, "error": str(e)})

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
