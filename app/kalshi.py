from __future__ import annotations

import base64
import asyncio
import logging
import random
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from app.cache import TTLCache
from app.config import settings
from app.strategy import TUNING

logger = logging.getLogger(__name__)


@dataclass
class AuthStatus:
    ok: bool
    reason: str = ""


class KalshiClient:
    def __init__(self) -> None:
        self.base_url = settings.kalshi_api_base_url
        self._base_path = urlsplit(self.base_url).path.rstrip("/")
        self.key_id = settings.kalshi_api_key_id
        self.private_key_pem = settings.kalshi_private_key_pem
        self.client = httpx.AsyncClient(timeout=20.0)
        self.cache = TTLCache()
        self._inflight_requests: dict[str, asyncio.Task] = {}
        self.auth_status = AuthStatus(ok=bool(self.key_id and self.private_key_pem), reason="missing credentials")

    def _sign(self, method: str, path: str) -> dict[str, str]:
        if not self.key_id or not self.private_key_pem:
            return {}
        ts = str(int(time.time() * 1000))
        full_path = f"{self._base_path}{path}"
        payload = f"{ts}{method.upper()}{full_path}".encode()
        private_key = serialization.load_pem_private_key(self.private_key_pem.encode(), password=None)
        signature = private_key.sign(payload, padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH), hashes.SHA256())
        return {"KALSHI-ACCESS-KEY": self.key_id, "KALSHI-ACCESS-TIMESTAMP": ts, "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode()}

    def _request_key(self, method: str, path: str, params: dict[str, Any] | None, json: dict[str, Any] | None) -> str:
        return f"{method.upper()}|{path}|{tuple(sorted((params or {}).items()))}|{tuple(sorted((json or {}).items()))}"

    async def _request_once(self, method: str, path: str, *, params: dict[str, Any] | None = None, json: dict[str, Any] | None = None, timeout: float = 20.0) -> dict[str, Any]:
        max_attempts = 4
        base_delay = 0.4
        for attempt in range(max_attempts):
            try:
                response = await self.client.request(method, f"{self.base_url}{path}", params=params, json=json, headers=self._sign(method, path), timeout=timeout)
            except httpx.TimeoutException:
                if attempt == max_attempts - 1:
                    raise
                await asyncio.sleep(base_delay * (2**attempt) + random.uniform(0, 0.2))
                continue
            if response.status_code == 401:
                self.auth_status = AuthStatus(ok=False, reason="401 Unauthorized")
                logger.info("kalshi_auth_status ok=%s reason=%s", self.auth_status.ok, self.auth_status.reason)
                return {}
            if response.status_code == 429 or 500 <= response.status_code < 600:
                if attempt == max_attempts - 1:
                    response.raise_for_status()
                await asyncio.sleep(base_delay * (2**attempt) + random.uniform(0, 0.2))
                continue
            response.raise_for_status()
            self.auth_status = AuthStatus(ok=True, reason="ok")
            logger.info("kalshi_auth_status ok=%s reason=%s", self.auth_status.ok, self.auth_status.reason)
            return response.json()
        return {}

    async def _request(self, method: str, path: str, *, params: dict[str, Any] | None = None, json: dict[str, Any] | None = None, timeout: float = 20.0) -> dict[str, Any]:
        key = self._request_key(method, path, params, json)
        existing = self._inflight_requests.get(key)
        if existing:
            return await existing
        task = asyncio.create_task(self._request_once(method, path, params=params, json=json, timeout=timeout))
        self._inflight_requests[key] = task
        try:
            return await task
        finally:
            if self._inflight_requests.get(key) is task:
                self._inflight_requests.pop(key, None)

    async def get_all_open_markets(self) -> list[dict[str, Any]]:
        cached = self.cache.get("markets:open")
        if cached is not None:
            return list(cached)
        out: list[dict[str, Any]] = []
        cursor: str | None = None
        seen_cursors: set[str] = set()
        page = 0
        t0 = time.perf_counter()
        while True:
            if cursor:
                if cursor in seen_cursors:
                    logger.error("duplicate_cursor_detected cursor=%s", cursor)
                    raise RuntimeError("pagination_safety_break_triggered")
                seen_cursors.add(cursor)
            params: dict[str, Any] = {"status": "open", "limit": 1000}
            if cursor:
                params["cursor"] = cursor
            payload = await self._request("GET", "/markets", params=params)
            markets = list(payload.get("markets") or [])
            out.extend(markets)
            page += 1
            logger.info("pagination_page page=%d batch=%d total=%d cursor=%s", page, len(markets), len(out), cursor or "")
            if page > 100:
                raise RuntimeError("pagination_safety_break_triggered")
            next_cursor = payload.get("cursor")
            if not next_cursor:
                break
            cursor = str(next_cursor)
        self.cache.set("markets:open", out, ttl_seconds=TUNING.market_cache_ttl_sec)
        logger.info("pagination_complete total_markets=%d pages=%d duration=%.2f", len(out), page, time.perf_counter() - t0)
        return out

    async def get_open_markets(self, limit: int | None = None) -> list[dict[str, Any]]:
        if limit is None or limit >= 1000:
            return await self.get_all_open_markets()
        payload = await self._request("GET", "/markets", params={"status": "open", "limit": limit})
        return list(payload.get("markets") or [])

    async def get_orderbook(self, ticker: str) -> dict[str, Any]:
        key = f"orderbook:{ticker}"
        cached = self.cache.get(key)
        if cached is not None:
            return dict(cached)
        payload = await self._request("GET", f"/markets/{ticker}/orderbook", params={"depth": 5})
        self.cache.set(key, payload, ttl_seconds=TUNING.orderbook_cache_ttl_sec)
        return payload

    async def get_balance(self) -> dict[str, Any]:
        cached = self.cache.get("portfolio:balance")
        if cached is not None:
            return dict(cached)
        payload = await self._request("GET", "/portfolio/balance")
        self.cache.set("portfolio:balance", payload, ttl_seconds=TUNING.balance_cache_ttl_sec)
        return payload

    async def get_positions(self) -> list[dict[str, Any]]:
        cached = self.cache.get("portfolio:positions")
        if cached is not None:
            return list(cached)
        payload = await self._request("GET", "/portfolio/positions")
        positions = list(payload.get("positions") or [])
        self.cache.set("portfolio:positions", positions, ttl_seconds=TUNING.balance_cache_ttl_sec)
        return positions

    async def get_settlements(self) -> list[dict[str, Any]]:
        payload = await self._request("GET", "/portfolio/settlements")
        return list(payload.get("settlements") or [])

    async def place_order(self, ticker: str, side: str, count: int, price_cents: int) -> dict[str, Any]:
        return await self._request("POST", "/portfolio/orders", json={"ticker": ticker, "count": count, "side": side.lower(), "action": "buy", "type": "limit", "yes_price": price_cents if side.upper()=="YES" else None, "no_price": price_cents if side.upper()=="NO" else None})

    async def close(self) -> None:
        await self.client.aclose()
