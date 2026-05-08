from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    app_name: str = "Kalshi Autonomous MVP"
    dashboard_base_url: str = "http://localhost:8080"
    database_url: str = ""
    database_path: str = str(Path("data") / "kalshi_autonomous_mvp.db")

    kalshi_api_base_url: str = "https://api.elections.kalshi.com/trade-api/v2"
    kalshi_api_key_id: str = ""
    kalshi_private_key_pem: str = ""

    auto_execute: bool = False
    allow_combos: bool = False


settings = Settings()
