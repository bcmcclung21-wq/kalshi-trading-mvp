from __future__ import annotations
from pathlib import Path
import os
from eth_account import Account
from pydantic_settings import BaseSettings, SettingsConfigDict



def get_wallet_address() -> str:
    """Derive wallet address from PRIVATE_KEY env var."""
    pk = os.getenv("PRIVATE_KEY", "").strip()
    if not pk:
        raise ValueError("PRIVATE_KEY env var not set")
    pk = pk.removeprefix("0x") if pk.startswith("0x") else pk
    return Account.from_key(pk).address


def resolve_wallet_address() -> str:
    """Resolve wallet via explicit env vars, else derive from PRIVATE_KEY."""
    override = os.getenv("POLYMARKET_WALLET_ADDRESS", "").strip() or os.getenv("WALLET_ADDRESS", "").strip()
    if override:
        return override
    try:
        return get_wallet_address()
    except Exception:
        return ""

class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    app_name: str = "Poly Trading MVP"
    dashboard_base_url: str = "http://localhost:8080"

    database_url: str = ""
    database_path: str = str(Path("data") / "poly_trading_mvp.db")

    polymarket_api_base_url: str = "https://api.polymarket.us"
    polymarket_gateway_base_url: str = "https://gateway.polymarket.us"
    polymarket_key_id: str = ""
    polymarket_secret_key: str = ""

    auto_execute: bool = False       # SAFETY: default False
    dry_run: bool = True  # SAFETY: default True until explicitly disabled
    allow_combos: bool = False
    max_orders_per_cycle: int = 5
    same_day_only: bool = True
    sports_same_day_only: bool = True
    market_timezone: str = "America/New_York"
    min_minutes_to_close: float = 20.0
    max_settlement_window_hours: float = 168.0
    max_spread_cents: float = 10.0
    min_projection_score: float = 35.0
    min_confidence_score: float = 50.0
    extreme_price_min: float = 0.05
    extreme_price_max: float = 0.95
    max_combo_legs: int = 4
    category_edge_bps: dict = {}

    cashout_enabled: bool = True
    cashout_stop_loss_pct: float = -15.0
    cashout_tp1_pct: float = 25.0
    cashout_tp1_size_pct: float = 40.0
    cashout_tp2_pct: float = 50.0
    cashout_tp2_size_pct: float = 30.0
    cashout_tp3_pct: float = 100.0
    cashout_tp3_size_pct: float = 30.0

    # Strategy thresholds (also defined in strategy.py — keep in sync)
    min_total_score_single: float = 50.0
    min_total_score_multi: float = 45.0
    min_edge_bps: int = 50
    max_spread_pct: float = 0.08
    max_daily_trades: int = 5
    max_risk_per_trade_usd: float = 50.0
    bankroll_usd: float = 2500.0

settings = Settings()


WALLET_ADDRESS = resolve_wallet_address()
