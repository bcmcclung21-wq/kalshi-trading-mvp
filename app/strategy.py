from __future__ import annotations

import os
from dataclasses import dataclass, field

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
    if raw in (None, ""):
        return default
    cleaned = str(raw).strip().strip('"').strip("'")
    try:
        return int(cleaned)
    except ValueError:
        import logging
        logging.getLogger("app.strategy").warning(
            f"Invalid int env var {name}={raw!r}, using default {default}"
        )
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    cleaned = str(raw).strip().strip('"').strip("'")
    try:
        return float(cleaned)
    except ValueError:
        import logging
        logging.getLogger("app.strategy").warning(
            f"Invalid float env var {name}={raw!r}, using default {default}"
        )
        return default


@dataclass
class RuntimeTuning:
    auto_execute: bool = True
    allow_combos: bool = False
    max_combo_legs: int = 4
    max_orders_per_cycle: int = 5
    check_interval_sec: int = 20
    market_sync_interval_sec: int = 60
    reconcile_interval_sec: int = 60
    audit_interval_sec: int = 300
    daily_audit_hour_utc: int = 12
    calibration_interval_sec: int = 300
    calibration_window_size: int = 50
    calibration_brier_threshold: float = 0.25
    calibration_cooldown_sec: int = 300

    min_volume: float = 5.0
    min_open_interest: float = 2.0
    max_spread_cents: float = 20.0
    min_minutes_to_close: int = 20
    max_days_to_close: int = 2
    market_timezone: str = "America/New_York"
    max_settlement_window_hours: int = 36
    # SAME_DAY_ONLY now means "enforce near-term settlement window (no futures)"
    # rather than strict UTC calendar-day equality.
    same_day_only: bool = True
    sports_same_day_only: bool = True

    min_projection_score: float = 35.0
    min_confidence_score: float = 45.0
    min_total_score_single: float = 52.0
    min_edge_bps: float = 100.0
    min_fair_prob_gap: float = 0.015
    extreme_price_min: float = 0.02
    extreme_price_max: float = 0.98
    min_total_score_combo: float = 66.0

    cashout_enabled: bool = True
    cashout_stop_loss_pct: float = -15.0
    cashout_tp1_pct: float = 25.0
    cashout_tp1_size_pct: float = 40.0
    cashout_tp2_pct: float = 50.0
    cashout_tp2_size_pct: float = 30.0
    cashout_tp3_pct: float = 100.0
    cashout_tp3_size_pct: float = 30.0

    max_markets_per_sync: int = 1200
    max_orderbooks_per_cycle: int = 24
    market_cache_ttl_sec: int = 10
    orderbook_cache_ttl_sec: int = 3
    balance_cache_ttl_sec: int = 10
    positions_cache_ttl_sec: int = 10
    summary_cache_ttl_sec: int = 5

    max_category_exposure_pct: float = 0.30
    max_ticker_reentry_minutes: int = 180
    max_order_notional_usd: float = 25.0
    category_edge_bps: dict = field(default_factory=lambda: {
        "sports": 75,
        "politics": 100,
        "economics": 100,
        "crypto": 100,
        "climate": 100,
    })


TUNING = RuntimeTuning(
    auto_execute=_env_bool("AUTO_EXECUTE", True),
    allow_combos=_env_bool("ALLOW_COMBOS", False),
    max_combo_legs=_env_int("MAX_COMBO_LEGS", 4),
    max_orders_per_cycle=_env_int("MAX_ORDERS_PER_CYCLE", 5),
    check_interval_sec=_env_int("CHECK_INTERVAL_SEC", 20),
    market_sync_interval_sec=_env_int("MARKET_SYNC_INTERVAL_SEC", 60),
    reconcile_interval_sec=_env_int("RECONCILE_INTERVAL_SEC", 60),
    audit_interval_sec=_env_int("AUDIT_INTERVAL_SEC", 300),
    daily_audit_hour_utc=_env_int("DAILY_AUDIT_HOUR_UTC", 12),
    calibration_interval_sec=_env_int("CALIBRATION_INTERVAL_SEC", 300),
    calibration_window_size=_env_int("CALIBRATION_WINDOW_SIZE", 50),
    calibration_brier_threshold=_env_float("CALIBRATION_BRIER_THRESHOLD", 0.25),
    calibration_cooldown_sec=_env_int("CALIBRATION_COOLDOWN_SEC", 300),
    min_volume=_env_float("MIN_VOLUME", 5.0),
    min_open_interest=_env_float("MIN_OPEN_INTEREST", 2.0),
    max_spread_cents=_env_float("MAX_SPREAD_CENTS", 20.0),
    min_minutes_to_close=_env_int("MIN_MINUTES_TO_CLOSE", 20),
    max_days_to_close=_env_int("MAX_DAYS_TO_CLOSE", 2),
    market_timezone=os.getenv("MARKET_TIMEZONE", "America/New_York"),
    max_settlement_window_hours=_env_int("MAX_SETTLEMENT_WINDOW_HOURS", 36),
    same_day_only=_env_bool("SAME_DAY_ONLY", True),
    sports_same_day_only=_env_bool("SPORTS_SAME_DAY_ONLY", True),
    cashout_enabled=_env_bool("CASHOUT_ENABLED", True),
    cashout_stop_loss_pct=_env_float("CASHOUT_STOP_LOSS_PCT", -15.0),
    cashout_tp1_pct=_env_float("CASHOUT_TP1_PCT", 25.0),
    cashout_tp1_size_pct=_env_float("CASHOUT_TP1_SIZE_PCT", 40.0),
    cashout_tp2_pct=_env_float("CASHOUT_TP2_PCT", 50.0),
    cashout_tp2_size_pct=_env_float("CASHOUT_TP2_SIZE_PCT", 30.0),
    cashout_tp3_pct=_env_float("CASHOUT_TP3_PCT", 100.0),
    cashout_tp3_size_pct=_env_float("CASHOUT_TP3_SIZE_PCT", 30.0),
    min_projection_score=_env_float("MIN_PROJECTION_SCORE", 35.0),
    min_confidence_score=_env_float("MIN_CONFIDENCE_SCORE", 45.0),
    min_total_score_single=_env_float("MIN_TOTAL_SCORE_SINGLE", 52.0),
    min_edge_bps=_env_float("MIN_EDGE_BPS", 100.0),
    min_fair_prob_gap=_env_float("MIN_FAIR_PROB_GAP", 0.015),
    extreme_price_min=_env_float("EXTREME_PRICE_MIN", 0.02),
    extreme_price_max=_env_float("EXTREME_PRICE_MAX", 0.98),
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
    max_order_notional_usd=_env_float("MAX_ORDER_NOTIONAL_USD", 25.0),
)


def bankroll_pct(legs: int) -> float:
    legs = max(1, min(4, int(legs)))
    return BANKROLL_RULES[legs]
