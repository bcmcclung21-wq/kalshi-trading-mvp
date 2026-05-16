from __future__ import annotations

import asyncio
import logging
import os

import httpx

from app.config import WALLET_ADDRESS

logger = logging.getLogger("app.polymarket")


class PolymarketAPI:
    def __init__(self):
        self.api_key = os.getenv("POLYMARKET_KEY_ID", "")
        self.api_secret = os.getenv("POLYMARKET_SECRET_KEY", "")
        self.passphrase = os.getenv("POLYMARKET_PASSPHRASE", "")
        self.gamma_base = os.getenv("POLYMARKET_GAMMA_BASE", "https://gamma-api.polymarket.com")
        self.data_base = os.getenv("POLYMARKET_DATA_BASE", "https://data-api.polymarket.com")
        self.api_base = os.getenv("POLYMARKET_API_BASE", "https://api.polymarket.us")
        self.clob_base = os.getenv("POLYMARKET_CLOB_BASE", "https://clob.polymarket.com")
        self.wallet_address = WALLET_ADDRESS

        self.headers = {}
        if self.api_key:
            self.headers["POLYMARKET-API-KEY"] = self.api_key
        if self.api_secret:
            self.headers["POLYMARKET-API-SECRET"] = self.api_secret
        if self.passphrase:
            self.headers["POLYMARKET-PASSPHRASE"] = self.passphrase
        self._public_client = httpx.AsyncClient(
            timeout=30,
            headers={"User-Agent": "PolyTradingMVP/1.3"},
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        )
        self._auth_client = httpx.AsyncClient(
            timeout=30,
            headers={**self.headers, "User-Agent": "PolyTradingMVP/1.3"},
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        )

    async def get_markets(self, limit=100, offset=0, closed=False, tag=None):
        r = await self._public_client.get(
            f"{self.gamma_base}/markets",
            params={"limit": limit, "offset": offset, "closed": str(closed).lower(), **({"tag": tag} if tag else {})},
        )
        r.raise_for_status()
        return r.json()

    async def get_market(self, market_id):
        r = await self._public_client.get(f"{self.gamma_base}/markets/{market_id}")
        r.raise_for_status()
        return r.json()

    async def get_orderbook(self, token_id: str):
        r = await self._public_client.get(f"{self.clob_base}/book", params={"token_id": token_id}, timeout=10)
        r.raise_for_status()
        return r.json()

    async def get_balances(self):
        r = await self._auth_client.get(f"{self.api_base}/v1/account/balances")
        r.raise_for_status()
        return r.json()

    async def fetch_with_retry(self, url: str, params: dict | None = None, retries: int = 3):
        last_error = None
        for i in range(retries):
            try:
                r = await self._auth_client.get(url, params=params)
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
            await asyncio.sleep(2**i)
        if last_error:
            raise last_error
        return None

    async def get_positions(self, limit=100):
        data_url = f"{self.data_base}/positions"
        wallet_address = self.wallet_address or os.getenv("WALLET_ADDRESS", "")
        logger.info("fetching_positions source=data-api limit=%s wallet_present=%s", limit, bool(wallet_address))

        last_error: Exception | None = None
        param_attempts: list[dict[str, object]] = []
        if wallet_address:
            param_attempts.extend([{"user": wallet_address, "limit": limit}, {"wallet": wallet_address, "limit": limit}])
        param_attempts.append({"limit": limit})

        for params in param_attempts:
            try:
                d = await self.fetch_with_retry(data_url, params=params)
                logger.info("positions_fetch_ok params=%s", ",".join(sorted(params.keys())))
                return (d.get("positions", []) or d.get("data", []) or []) if isinstance(d, dict) else (d if isinstance(d, list) else [])
            except httpx.HTTPStatusError as exc:
                last_error = exc
                logger.warning("positions_fetch_param_failed params=%s status=%s", ",".join(sorted(params.keys())), exc.response.status_code if exc.response else "unknown")
                if exc.response is None or exc.response.status_code not in (400, 404):
                    break
            except Exception as exc:
                last_error = exc
                logger.warning("positions_fetch_param_failed params=%s error=%s", ",".join(sorted(params.keys())), exc)
                break

        if last_error:
            raise last_error
        return []

    async def get_trades(self, limit=100):
        r = await self._auth_client.get(f"{self.data_base}/trades", params={"limit": limit})
        r.raise_for_status()
        d = r.json()
        return (d.get("trades", []) or d.get("data", []) or []) if isinstance(d, dict) else (d if isinstance(d, list) else [])

    async def place_order(self, token_id: str, side: str, size: float, price: float):
        trade_side = side.upper()
        if trade_side == "YES":
            trade_side = "BUY"
        elif trade_side == "NO":
            trade_side = "SELL"

        payload = {
            "token_id": token_id,
            "side": trade_side,
            "size": float(size),
            "price": float(price),
            "type": "limit",
        }
        r = await self._auth_client.post(f"{self.clob_base}/order", json=payload)
        r.raise_for_status()
        return r.json()

    async def cancel_order(self, order_id):
        r = await self._auth_client.delete(f"{self.clob_base}/order/{order_id}")
        r.raise_for_status()
        return r.json()

    async def sell_position(self, token_id, outcome, size):
        """FIX6: cashout passes token_id first — was ticker causing crash."""
        r = await self._auth_client.post(f"{self.clob_base}/order", json={"token_id": token_id, "side": "SELL", "size": float(size), "price": 0.01, "type": "limit"})
        r.raise_for_status()
        return r.json()

    async def aclose(self) -> None:
        await self._public_client.aclose()
        await self._auth_client.aclose()
