```python
from __future__ import annotations

import os
from dataclasses import dataclass

CATEGORIES = ["sports", "politics", "crypto", "climate", "economics"]
SPORTS = "sports"

BANKROLL_RULES = {
    1: 0.0200,
    2: 0.0100,
    3: 0.0075,
    4: 0.0050,
}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(str(raw).strip()) if raw not in (None, "") else default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(str(raw).strip()) if raw not in (None, "") else default


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


TUNING = RuntimeTuning(
    auto_execute=_env_bool("AUTO_EXECUTE", False),
    allow_combos=_env_bool("ALLOW_COMBOS", False),
    max_combo_legs=_env_int("MAX_COMBO_LEGS", 4),
    max_orders_per_cycle=_env_int("MAX_ORDERS_PER_CYCLE", 3),
    check_interval_sec=_env_int("CHECK_INTERVAL_SEC", 20),
    market_sync_interval_sec=_env_int("MARKET_SYNC_INTERVAL_SEC", 60),
    reconcile_interval_sec=_env_int("RECONCILE_INTERVAL_SEC", 60),
    audit_interval_sec=_env_int("AUDIT_INTERVAL_SEC", 300),
    daily_audit_hour_utc=_env_int("DAILY_AUDIT_HOUR_UTC", 12),
    min_volume=_env_float("MIN_VOLUME", 5.0),
    min_open_interest=_env_float("MIN_OPEN_INTEREST", 2.0),
    max_spread_cents=_env_float("MAX_SPREAD_CENTS", 20.0),
    min_minutes_to_close=_env_int("MIN_MINUTES_TO_CLOSE", 20),
    max_days_to_close=_env_int("MAX_DAYS_TO_CLOSE", 14),
    min_projection_score=_env_float("MIN_PROJECTION_SCORE", 50.0),
    min_confidence_score=_env_float("MIN_CONFIDENCE_SCORE", 50.0),
    min_total_score_single=_env_float("MIN_TOTAL_SCORE_SINGLE", 58.0),
    min_total_score_combo=_env_float("MIN_TOTAL_SCORE_COMBO", 66.0),
    max_markets_per_sync=_env_int("MAX_MARKETS_PER_SYNC", 1200),
    max_orderbooks_per_cycle=_env_int("MAX_ORDERBOOKS_PER_CYCLE", 24),
    market_cache_ttl_sec=_env_int("MARKET_CACHE_TTL_SEC", 10),
    orderbook_cache_ttl_sec=_env_int("ORDERBOOK_CACHE_TTL_SEC", 3),
    balance_cache_ttl_sec=_env_int("BALANCE_CACHE_TTL_SEC", 10),
    positions_cache_ttl_sec=_env_int("POSITIONS_CACHE_TTL_SEC", 10),
    summary_cache_ttl_sec=_env_int("SUMMARY_CACHE_TTL_SEC", 5),
    max_category_exposure_pct=_env_float("MAX_CATEGORY_EXPOSURE_PCT", 0.30),
    max_ticker_reentry_minutes=_env_int("MAX_TICKER_REENTRY_MINUTES", 180),
)


def bankroll_pct(legs: int) -> float:
    legs = max(1, min(4, int(legs)))
    return BANKROLL_RULES[legs]
