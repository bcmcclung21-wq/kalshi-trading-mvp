from __future__ import annotations

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    auto_execute: bool = False
    allow_combos: bool = False


settings = Settings()
