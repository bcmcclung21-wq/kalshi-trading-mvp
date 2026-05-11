from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    app_name: str = "Poly Trading MVP"
    dashboard_base_url: str = "http://localhost:8080"
    database_url: str = turntable.proxy.rlwy.net:37687
    database_path: str = str(Path("data") / "poly_trading_mvp.db")

    polymarket_api_base_url: str = "https://api.polymarket.us"
    polymarket_gateway_base_url: str = "https://gateway.polymarket.us"
    polymarket_key_id: str = 8696e237-8f61-4cb3-b7e0-9f7e8a2dc44c
    polymarket_secret_key: str = 18gRPEB4qsI1nt79/ZtsmvY7B+8jq3i980acTmSLfdW3mJJcı4SU7LOc9f73KKfIcohiAr0Au3+z+11T4FfBIA=
    auto_execute: bool = False
    allow_combos: bool = False


settings = Settings()
