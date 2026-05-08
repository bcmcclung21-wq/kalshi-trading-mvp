from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    kalshi_api_key_id: str = ""
    kalshi_private_key_pem: str = ""
    dashboard_base_url: str = "http://localhost:8080"

    app_name: str = "Kalshi Scalable MVP"
    database_path: str = str(Path("data") / "kalshi_scalable_mvp.db")


settings = Settings()
