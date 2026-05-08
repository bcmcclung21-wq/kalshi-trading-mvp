from __future__ import annotations

from collections import Counter

from app.strategy import bankroll_pct, TUNING


def trade_notional(bankroll_usd: float, legs: int) -> float:
    return round(max(0.0, bankroll_usd) * bankroll_pct(legs), 2)


def contract_count(bankroll_usd: float, legs: int, entry_price: float) -> int:
    if entry_price <= 0:
        return 0
    return max(0, int(trade_notional(bankroll_usd, legs) / entry_price))


def category_exposure_ok(candidate_category: str, open_positions: list[dict]) -> bool:
    counts = Counter(str(p.get("category") or "unknown") for p in open_positions if str(p.get("status") or "open") == "open")
    total = sum(counts.values())
    if total == 0:
        return True
    return (counts[candidate_category] / total) < TUNING.max_category_exposure_pct


def duplicate_ticker_ok(ticker: str, open_positions: list[dict]) -> bool:
    return all(str(p.get("ticker") or "") != ticker for p in open_positions if str(p.get("status") or "open") == "open")
