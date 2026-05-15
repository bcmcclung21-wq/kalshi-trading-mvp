from __future__ import annotations
import logging
from dataclasses import dataclass
from datetime import datetime
from app.config import settings

logger = logging.getLogger("app.cashout")

@dataclass
class CashoutSettings:
    enabled: bool = True
    stop_loss_pct: float = -15.0
    tp1_pct: float = 25.0
    tp1_size_pct: float = 40.0
    tp2_pct: float = 50.0
    tp2_size_pct: float = 30.0
    tp3_pct: float = 100.0
    tp3_size_pct: float = 30.0

    @classmethod
    def from_env(cls):
        return cls(
            enabled=settings.cashout_enabled,
            stop_loss_pct=settings.cashout_stop_loss_pct,
            tp1_pct=settings.cashout_tp1_pct,
            tp1_size_pct=settings.cashout_tp1_size_pct,
            tp2_pct=settings.cashout_tp2_pct,
            tp2_size_pct=settings.cashout_tp2_size_pct,
            tp3_pct=settings.cashout_tp3_pct,
            tp3_size_pct=settings.cashout_tp3_size_pct,
        )

class CashoutManager:
    def __init__(self, api):
        self.api = api
        self.settings = CashoutSettings.from_env()

    async def evaluate_all(self):
        if not self.settings.enabled:
            return []
        if not settings.auto_execute or settings.dry_run:
            return []
        logger.info("cashout_skipped: positions API dependency removed")
        positions = []

        actions = []
        for pos in positions:
            if not isinstance(pos, dict):
                continue
            ticker = pos.get("ticker") or pos.get("market_id") or "unknown"
            side = pos.get("side", "").upper()
            avg_price = float(pos.get("avg_price", 0) or pos.get("entry_price", 0) or 0)
            current_price = float(pos.get("last_price", 0) or pos.get("price", 0) or 0)
            size = float(pos.get("size", 0) or pos.get("quantity", 0) or 0)
            if avg_price <= 0 or current_price <= 0 or size <= 0:
                continue

            if side in ("YES", "BUY"):
                pnl_pct = ((current_price - avg_price) / avg_price) * 100
            else:
                pnl_pct = ((avg_price - current_price) / avg_price) * 100

            action, cashout_size = None, 0.0
            if pnl_pct <= self.settings.stop_loss_pct:
                action, cashout_size = "stop_loss", size
            elif pnl_pct >= self.settings.tp3_pct:
                action, cashout_size = "take_profit_3", size * (self.settings.tp3_size_pct / 100)
            elif pnl_pct >= self.settings.tp2_pct:
                action, cashout_size = "take_profit_2", size * (self.settings.tp2_size_pct / 100)
            elif pnl_pct >= self.settings.tp1_pct:
                action, cashout_size = "take_profit_1", size * (self.settings.tp1_size_pct / 100)

            if action:
                actions.append({"ticker": ticker, "action": action, "pnl_pct": round(pnl_pct, 2),
                                "cashout_size": round(cashout_size, 4), "current_price": current_price,
                                "avg_price": avg_price, "timestamp": datetime.utcnow().isoformat()})
                try:
                    outcome = "YES" if side in ("YES", "BUY") else "NO"
                    await self.api.sell_position(ticker, outcome, cashout_size)
                    actions[-1]["status"] = "executed"
                except Exception as e:
                    actions[-1]["status"] = f"failed: {e}"

        logger.info("cashout_evaluated: %d actions", len(actions))
        return actions
