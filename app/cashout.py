from __future__ import annotations

import logging
from typing import List

logger = logging.getLogger("app.cashout")


class CashoutManager:
    def __init__(self, api_client, db_session=None):
        self.api = api_client
        self.db = db_session

    async def evaluate_positions(self, positions: List[dict]):
        """Evaluate open positions and optionally execute cashouts."""
        from app.strategy import TUNING as settings

        if not getattr(settings, "cashout_enabled", True):
            return

        stop_loss_pct = getattr(settings, "cashout_stop_loss_pct", -15.0)
        tp1_pct = getattr(settings, "cashout_tp1_pct", 25.0)
        tp1_size = getattr(settings, "cashout_tp1_size_pct", 40.0)

        for pos in positions:
            ticker = pos.get("ticker")
            side = str(pos.get("side") or "YES")
            size = float(pos.get("size", pos.get("quantity", 0)) or 0)
            entry = float(pos.get("entry_price", pos.get("avg_price", 0)) or 0)
            current_bid = float(pos.get("current_bid", pos.get("current_price", 0)) or 0)

            if size <= 0 or entry <= 0:
                continue

            if side.upper() == "YES":
                unrealized_pct = ((current_bid - entry) / entry) * 100
            else:
                unrealized_pct = ((entry - current_bid) / entry) * 100

            action = None
            cashout_size = 0.0
            if unrealized_pct <= stop_loss_pct:
                action = "stop_loss"
                cashout_size = size
            elif unrealized_pct >= tp1_pct:
                action = "take_profit_1"
                cashout_size = size * (tp1_size / 100.0)

            if action and cashout_size > 0:
                logger.info(
                    "cashout_signal ticker=%s action=%s unrealized_pct=%.2f size=%.4f",
                    ticker, action, unrealized_pct, cashout_size
                )
                if getattr(settings, "auto_execute", True):
                    await self._submit_sell(str(ticker), side, cashout_size, current_bid)

    async def _submit_sell(self, ticker: str, side: str, size: float, price: float):
        try:
            result = await self.api.place_sell_order(ticker=ticker, outcome=side, size=int(max(1, round(size))), price=price)
            logger.info("cashout_executed ticker=%s size=%.4f price=%.4f resp=%s", ticker, size, price, result)
        except Exception as exc:
            logger.error("cashout_failed ticker=%s error=%s", ticker, exc)
