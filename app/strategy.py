from __future__ import annotations

from dataclasses import dataclass

CATEGORIES = ["sports", "politics", "crypto", "climate", "economics"]
SPORTS = "sports"

BANKROLL_RULES = {
    1: 0.0200,
    2: 0.0100,
    3: 0.0075,
    4: 0.0050,
}


@dataclass(frozen=True)
class RuntimeTuning:
    auto_execute: bool = False
    allow_combos: bool = False
    max_combo_legs: int = 4
    max_orders_per_cycle: int = 3
    check_interval_sec: int = 20
    market_sync_interval_sec: int = 60
    reconcile_interval_sec: int = 60
    audit_interval_sec: int = 300
    daily_audit_hour_utc: int = 12

    min_volume: float = 5.0
    min_open_interest: float = 2.0
    max_spread_cents: float = 20.0
    min_minutes_to_close: int = 20
    max_days_to_close: int = 14

    min_projection_score: float = 50.0
    min_confidence_score: float = 50.0
    min_total_score_single: float = 58.0
    min_total_score_combo: float = 66.0

    max_markets_per_sync: int = 1200
    max_orderbooks_per_cycle: int = 24
    market_cache_ttl_sec: int = 10
    orderbook_cache_ttl_sec: int = 3
    balance_cache_ttl_sec: int = 10
    positions_cache_ttl_sec: int = 10
    summary_cache_ttl_sec: int = 5

    max_category_exposure_pct: float = 0.30
    max_ticker_reentry_minutes: int = 180


TUNING = RuntimeTuning()


def bankroll_pct(legs: int) -> float:
    legs = max(1, min(4, int(legs)))
    return BANKROLL_RULES[legs]
