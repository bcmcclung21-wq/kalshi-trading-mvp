"""Polymarket API wrapper using direct HTTP calls (no deprecated SDK)."""
from __future__ import annotations
import asyncio
import logging, os
import httpx

from app.config import WALLET_ADDRESS

logger = logging.getLogger("app.polymarket")

class PolymarketAPI:
    def __init__(self):
        # FIX: env var names now match README and Railway config
        self.api_key = os.getenv("POLYMARKET_KEY_ID", "")
        self.api_secret = os.getenv("POLYMARKET_SECRET_KEY", "")
        self.passphrase = os.getenv("POLYMARKET_PASSPHRASE", "")
        self.gamma_base = os.getenv("POLYMARKET_GAMMA_BASE", "https://gamma-api.polymarket.com")
        self.data_base = os.getenv("POLYMARKET_DATA_BASE", "https://data-api.polymarket.com")
        self.api_base = os.getenv("POLYMARKET_API_BASE", "https://api.polymarket.us")
        self.wallet_address = WALLET_ADDRESS
        self.headers = {}
        if self.api_key:
            self.headers["POLYMARKET_API_KEY"] = self.api_key
        if self.api_secret:
            self.headers["POLYMARKET_API_SECRET"] = self.api_secret
        if self.passphrase:
            self.headers["POLYMARKET_PASSPHRASE"] = self.passphrase

    async def get_markets(self, limit=100, offset=0, closed=False, tag=None):
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(
                f"{self.gamma_base}/markets",
                params={"limit": limit, "offset": offset, "closed": str(closed).lower(), **({"tag": tag} if tag else {})}
            )
            r.raise_for_status()
            return r.json()

    async def get_market(self, market_id):
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{self.gamma_base}/markets/{market_id}")
            r.raise_for_status()
            return r.json()

    async def get_orderbook(self, ticker):
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{self.gamma_base}/orderbook/{ticker}")
            r.raise_for_status()
            return r.json()

    async def get_balances(self):
        async with httpx.AsyncClient(timeout=30, headers=self.headers) as c:
            r = await c.get(f"{self.api_base}/v1/account/balances")
            r.raise_for_status()
            return r.json()

    async def fetch_with_retry(self, url: str, params: dict | None = None, retries: int = 3):
        last_error = None
        for i in range(retries):
            try:
                async with httpx.AsyncClient(timeout=30, headers=self.headers) as c:
                    r = await c.get(url, params=params)
                    r.raise_for_status()
                    return r.json()
            except httpx.HTTPStatusError as e:
                request_url = str(e.request.url) if e.request else url
                logger.warning(
                    "polymarket_request_failed status=%s url=%s response=%s",
                    e.response.status_code if e.response else "unknown",
                    request_url,
                    (e.response.text[:500] if e.response else ""),
                )
                if e.response.status_code == 404:
                    raise
                last_error = e
            except httpx.HTTPError as e:
                last_error = e
            await asyncio.sleep(2 ** i)
        if last_error:
            raise last_error
        return None

    async def get_positions(self, limit=100):
        data_url = f"{self.data_base}/positions"

        if not self.wallet_address:
            logger.warning(
                "positions_fetch_skipped reason=missing_wallet data_api_requires_user_param limit=%s",
                limit,
            )
            return []

        data_params = {"user": self.wallet_address, "limit": limit}
        logger.info("fetching_positions source=data-api wallet=%s limit=%s", self.wallet_address, limit)
        d = await self.fetch_with_retry(data_url, params=data_params)
        return (d.get("positions", []) or d.get("data", []) or []) if isinstance(d, dict) else (d if isinstance(d, list) else [])

    async def get_trades(self, limit=100):
        async with httpx.AsyncClient(timeout=30, headers=self.headers) as c:
            r = await c.get(f"{self.data_base}/trades", params={"limit": limit})
            r.raise_for_status()
            d = r.json()
            return (d.get("trades", []) or d.get("data", []) or []) if isinstance(d, dict) else (d if isinstance(d, list) else [])

    async def place_order(self, market_id, side, size, price):
        # FIX: send numbers, not strings
        payload = {
            "marketId": market_id,
            "side": side.upper(),
            "size": float(size),
            "price": float(price),
            "type": "limit",
        }
        async with httpx.AsyncClient(timeout=30, headers=self.headers) as c:
            r = await c.post(f"{self.api_base}/v1/orders", json=payload)
            r.raise_for_status()
            return r.json()

    async def cancel_order(self, order_id):
        async with httpx.AsyncClient(timeout=30, headers=self.headers) as c:
            r = await c.delete(f"{self.api_base}/v1/orders/{order_id}")
            r.raise_for_status()
            return r.json()

    async def sell_position(self, market_id, outcome, size):
        payload = {
            "marketId": market_id,
            "side": "SELL",
            "size": float(size),
            "price": 0.01,
            "type": "limit",
            "outcome": outcome,
        }
        async with httpx.AsyncClient(timeout=30, headers=self.headers) as c:
            r = await c.post(f"{self.api_base}/v1/orders", json=payload)
            r.raise_for_status()
            return r.json()
