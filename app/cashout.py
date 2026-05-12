from __future__ import annotations

import logging
from datetime import datetime, timezone
from sqlalchemy import select

from app.db import SessionLocal
from app.models import CashoutOrder, OrderRecord
from app.strategy import TUNING

logger = logging.getLogger(__name__)


class CashoutManager:
    def __init__(self, poly_client) -> None:
        self.poly = poly_client
        self._last_cashout: dict[str, datetime] = {}

    async def evaluate_positions(self) -> None:
        if not TUNING.cashout_enabled:
            return
        positions = await self.poly.get_positions()
        tickers = [str(p.get("ticker") or "") for p in positions if str(p.get("ticker") or "")]
        books = await self.poly.get_orderbooks(tickers, depth=10) if tickers else {}
        with SessionLocal() as db:
            for p in positions:
                ticker = str(p.get("ticker") or "")
                if not ticker:
                    continue
                now = datetime.now(timezone.utc)
                last_cashout = self._last_cashout.get(ticker)
                if last_cashout and (now - last_cashout).total_seconds() < 60:
                    continue
                pending = db.execute(select(CashoutOrder).where(CashoutOrder.ticker == ticker, CashoutOrder.status == "pending")).scalars().first()
                if pending:
                    continue
                order = db.execute(select(OrderRecord).where(OrderRecord.ticker == ticker).order_by(OrderRecord.created_at.desc())).scalars().first()
                if not order:
                    continue
                side = str(order.side or "YES").upper()
                qty = float(p.get("quantity") or 0)
                if qty <= 0:
                    continue
                entry = float(order.price_cents or 0) / 100.0
                book = books.get(ticker) or {}
                yes_bids = book.get("yes_bids") or book.get("yes") or []
                no_bids = book.get("no_bids") or book.get("no") or []
                yes_bid = max([float(x.get("price", 0)) for x in yes_bids], default=0.0)
                no_bid = max([float(x.get("price", 0)) for x in no_bids], default=0.0)
                cur = yes_bid if side == "YES" else no_bid
                if cur <= 0:
                    continue
                if entry <= 0:
                    continue
                unrealized_pct = ((cur - entry) / entry) * 100.0
                if side == "NO":
                    unrealized_pct = ((no_bid - entry) / entry) * 100.0
                trigger = None
                size = 0.0
                if unrealized_pct <= TUNING.cashout_stop_loss_pct:
                    trigger = "stop_loss"; size = qty
                elif unrealized_pct >= TUNING.cashout_tp3_pct:
                    trigger = "take_profit_3"; size = qty * (TUNING.cashout_tp3_size_pct / 100.0)
                elif unrealized_pct >= TUNING.cashout_tp2_pct:
                    trigger = "take_profit_2"; size = qty * (TUNING.cashout_tp2_size_pct / 100.0)
                elif unrealized_pct >= TUNING.cashout_tp1_pct:
                    trigger = "take_profit_1"; size = qty * (TUNING.cashout_tp1_size_pct / 100.0)
                if not trigger or size <= 0:
                    continue
                self._last_cashout[ticker] = now
                size = min(qty, max(1.0, round(size)))
                logger.info("cashout_triggered ticker=%s type=%s size=%s price=%.4f unrealized_pct=%.2f", ticker, trigger, size, cur, unrealized_pct)
                status = "pending"
                if TUNING.auto_execute and self.poly.auth_status.ok:
                    resp = await self.poly.place_sell_order(ticker=ticker, outcome=side, size=int(size), price=cur)
                    status = str(resp.get("status") or "pending")
                db.add(CashoutOrder(original_order_id=order.id, ticker=ticker, side="SELL", cashout_type=trigger, size=float(size), price=float(cur), status=status))
            db.commit()
