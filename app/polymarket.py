"""Polymarket API wrapper using direct HTTP calls (no deprecated SDK)."""
from __future__ import annotations
import logging, os
import httpx

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

    async def get_positions(self, limit=100):
        async with httpx.AsyncClient(timeout=30, headers=self.headers) as c:
            r = await c.get(f"{self.data_base}/v1/portfolio/positions", params={"limit": limit})
            r.raise_for_status()
            d = r.json()
            return (d.get("positions", []) or d.get("data", []) or []) if isinstance(d, dict) else (d if isinstance(d, list) else [])

    async def get_trades(self, limit=100):
        async with httpx.AsyncClient(timeout=30, headers=self.headers) as c:
            r = await c.get(f"{self.data_base}/v1/portfolio/trades", params={"limit": limit})
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
