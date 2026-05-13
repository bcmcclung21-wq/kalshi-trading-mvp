"""Arbitrage scanner: finds YES+NO combinations where total < $1."""
from __future__ import annotations
import logging
from typing import Any

logger = logging.getLogger("app.arbitrage")

def scan_arbitrage(market: dict[str, Any], orderbook: dict[str, Any]) -> dict[str, Any] | None:
    """Return arbitrage opportunity if YES_ask + NO_ask < 1.0."""
    yes_ask = float(orderbook.get("yes_ask") or orderbook.get("bestAskYes") or 0)
    no_ask = float(orderbook.get("no_ask") or orderbook.get("bestAskNo") or 0)
    
    if yes_ask <= 0 or no_ask <= 0:
        return None
    
    total = yes_ask + no_ask
    if total >= 1.0:
        return None
    
    edge = (1.0 - total) / total if total > 0 else 0
    return {
        "type": "arbitrage",
        "yes_ask": yes_ask,
        "no_ask": no_ask,
        "total": total,
        "edge": edge,
        "edge_bps": int(edge * 10000),
        "profit_per_dollar": 1.0 - total,
    }
