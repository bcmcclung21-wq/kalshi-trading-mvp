from __future__ import annotations

import base64
import time
from dataclasses import dataclass
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from app.cache import TTLCache
from app.config import settings
from app.strategy import TUNING


@dataclass
class AuthStatus:
    ok: bool
    reason: str = ""


class KalshiClient:
    def __init__(self) -> None:
        self.base_url = settings.kalshi_api_base_url
        self.key_id = settings.kalshi_api_key_id
        self.private_key_pem = settings.kalshi_private_key_pem
        self.client = httpx.AsyncClient(timeout=20.0)
        self.cache = TTLCache()
        self.auth_status = AuthStatus(ok=bool(self.key_id and self.private_key_pem), reason="missing credentials")

    def _sign(self, method: str, path: str) -> dict[str, str]:
        if not self.key_id or not self.private_key_pem:
            return {}
        ts = str(int(time.time() * 1000))
        payload = f"{ts}{method.upper()}{path}".encode()
        private_key = serialization.load_pem_private_key(self.private_key_pem.encode(), password=None)
        signature = private_key.sign(payload, padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH), hashes.SHA256())
        return {"KALSHI-ACCESS-KEY": self.key_id, "KALSHI-ACCESS-TIMESTAMP": ts, "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode()}

    async def _request(self, method: str, path: str, *, params: dict[str, Any] | None = None, json: dict[str, Any] | None = None) -> dict[str, Any]:
        response = await self.client.request(method, f"{self.base_url}{path}", params=params, json=json, headers=self._sign(method, path))
        if response.status_code == 401:
            self.auth_status = AuthStatus(ok=False, reason="401 Unauthorized")
            return {}
        response.raise_for_status()
        self.auth_status = AuthStatus(ok=True, reason="")
        return response.json()

    async def get_open_markets(self, limit: int | None = None) -> list[dict[str, Any]]:
        payload = await self._request("GET", "/markets", params={"status": "open", "limit": limit or min(TUNING.max_markets_per_sync, 200)})
        return list(payload.get("markets") or [])

    async def get_orderbook(self, ticker: str) -> dict[str, Any]:
        return await self._request("GET", f"/markets/{ticker}/orderbook", params={"depth": 5})

    async def get_balance(self) -> dict[str, Any]:
        return await self._request("GET", "/portfolio/balance")

    async def get_positions(self) -> list[dict[str, Any]]:
        payload = await self._request("GET", "/portfolio/positions")
        return list(payload.get("positions") or [])

    async def get_settlements(self) -> list[dict[str, Any]]:
        payload = await self._request("GET", "/portfolio/settlements")
        return list(payload.get("settlements") or [])

    async def place_order(self, ticker: str, side: str, count: int, price_cents: int) -> dict[str, Any]:
        return await self._request("POST", "/portfolio/orders", json={"ticker": ticker, "count": count, "side": side.lower(), "action": "buy", "type": "limit", "yes_price": price_cents if side.upper()=="YES" else None, "no_price": price_cents if side.upper()=="NO" else None})

    async def close(self) -> None:
        await self.client.aclose()
