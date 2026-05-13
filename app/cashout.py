from __future__ import annotations
import logging, os
from dataclasses import dataclass
logger = logging.getLogger('app.cashout')
@dataclass
class CashoutSettings:
    enabled: bool = True; stop_loss_pct: float = -15.0; tp1_pct: float = 25.0; tp1_size_pct: float = 40.0
    @classmethod
    def from_env(cls):
        return cls(enabled=os.getenv('CASHOUT_ENABLED','true').lower() in ('1','true','yes','on'), stop_loss_pct=float(os.getenv('CASHOUT_STOP_LOSS_PCT','-15.0')), tp1_pct=float(os.getenv('CASHOUT_TP1_PCT','25.0')), tp1_size_pct=float(os.getenv('CASHOUT_TP1_SIZE_PCT','40.0')))
class CashoutManager:
    def __init__(self, api): self.api=api; self.settings=CashoutSettings.from_env()
    async def evaluate_all(self): return []
