from __future__ import annotations

import base64
import time
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from app.cache import TTLCache
from app.config import settings
from app.strategy import TUNING


class KalshiClient:
    def __init__(self) -> None:
        self.base_url = "https://api.elections.kalshi.com/trade-api/v2"
        self.key_id = settings.kalshi_api_key_id
        self.private_key_pem = settings.kalshi_private_key_pem
        self.client = httpx.AsyncClient(timeout=20.0)
        self.cache = TTLCache()

    def _sign(self, method: str, path: str) -> dict[str, str]:
        if not self.key_id or not self.private_key_pem:
            return {}
        ts = str(int(time.time() * 1000))
        payload = f"{ts}{method.upper()}{path}".encode()
        private_key = serialization.load_pem_private_key(self.private_key_pem.encode(), password=None)
        signature = private_key.sign(
            payload,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
        }

    async def _request(self, method: str, path: str, *, params: dict[str, Any] | None = None, json: dict[str, Any] | None = None) -> dict[str, Any]:
        response = await self.client.request(method, f"{self.base_url}{path}", params=params, json=json, headers=self._sign(method, path))
        response.raise_for_status()
        return response.json()

    async def get_open_markets(self, limit: int | None = None) -> list[dict[str, Any]]:
        cached = self.cache.get("open_markets")
        if cached is not None:
            return cached
        page_limit = 200
        markets: list[dict[str, Any]] = []
        cursor = None
        while True:
            params = {"status": "open", "limit": page_limit}
            if cursor:
                params["cursor"] = cursor
            payload = await self._request("GET", "/markets", params=params)
            page = list(payload.get("markets") or [])
            markets.extend(page)
            cursor = payload.get("cursor") or payload.get("next_cursor")
            if not cursor:
                break
            if len(markets) >= TUNING.max_markets_per_sync:
                break
        if limit is not None:
            markets = markets[:limit]
        self.cache.set("open_markets", markets, TUNING.market_cache_ttl_sec)
        return markets

    async def get_orderbook(self, ticker: str) -> dict[str, Any]:
        key = f"orderbook:{ticker}"
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        payload = await self._request("GET", f"/markets/{ticker}/orderbook", params={"depth": 5})
        self.cache.set(key, payload, TUNING.orderbook_cache_ttl_sec)
        return payload

    async def get_balance(self) -> dict[str, Any]:
        cached = self.cache.get("balance")
        if cached is not None:
            return cached
        payload = await self._request("GET", "/portfolio/balance")
        self.cache.set("balance", payload, TUNING.balance_cache_ttl_sec)
        return payload

    async def get_positions(self) -> list[dict[str, Any]]:
        payload = await self._request("GET", "/portfolio/positions")
        return list(payload.get("positions") or [])

    async def get_orders(self) -> list[dict[str, Any]]:
        payload = await self._request("GET", "/portfolio/orders")
        return list(payload.get("orders") or [])

    async def get_settlements(self) -> list[dict[str, Any]]:
        payload = await self._request("GET", "/portfolio/settlements")
        return list(payload.get("settlements") or [])

    async def place_order(self, ticker: str, side: str, count: int, price_cents: int) -> dict[str, Any]:
        payload = {
            "ticker": ticker,
            "client_order_id": f"{ticker}-{side}-{int(time.time())}",
            "type": "limit",
            "action": "buy",
            "count": count,
            "side": side.lower(),
            "yes_price": price_cents if side.upper() == "YES" else None,
            "no_price": price_cents if side.upper() == "NO" else None,
        }
        return await self._request("POST", "/portfolio/orders", json=payload)

    async def close(self) -> None:
        await self.client.aclose()
