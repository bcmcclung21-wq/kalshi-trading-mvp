from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

@dataclass
class CalibrationEntry:
    market_id: str
    predicted_prob: float
    side: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    resolved: bool = False
    actual_outcome: Optional[int] = None

class CalibrationService:
    def __init__(self):
        self.entries = []
        self.trade_count = 0

    def record_trade(self, market_id: str, predicted_prob: float, side: str):
        self.entries.append(CalibrationEntry(market_id, predicted_prob, side))
        self.trade_count += 1

    def brier_score(self) -> float:
        """Proper Brier score over resolved trades. 0 = perfect, 1 = worst."""
        resolved = [e for e in self.entries if e.resolved and e.actual_outcome is not None]
        if not resolved:
            return 0.0  # FIX: was hardcoded 1.0 (worst possible)
        total = sum(
            (entry.predicted_prob - entry.actual_outcome) ** 2
            for entry in resolved
        )
        return total / len(resolved)
