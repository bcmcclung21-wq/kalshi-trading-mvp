from __future__ import annotations

import base64
import asyncio
import logging
import random
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from app.cache import TTLCache
from app.config import settings
from app.services.universe import is_skippable_ticker
from app.strategy import TUNING
from app.services.market_ingestion import AdaptiveRateLimiter

logger = logging.getLogger(__name__)


ORDERBOOK_BATCH_SIZE = 20
ORDERBOOK_URL_MAX_LEN = 6000

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
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(connect=5.0, read=20.0, write=20.0, pool=5.0))
        self.cache = TTLCache()
        self._inflight_requests: dict[str, asyncio.Task] = {}
        self.auth_status = AuthStatus(ok=bool(self.key_id and self.private_key_pem), reason="missing credentials")
        self.rate_limiter = AdaptiveRateLimiter()
        self.rate_limiter.configure_endpoint("GET:/markets", rate=6, capacity=12, concurrency=3)
        self.rate_limiter.configure_endpoint("GET:/markets/orderbooks", rate=4, capacity=8, concurrency=2)
        self.request_semaphore = asyncio.Semaphore(3)
        self.last_paginate_pages = 0
        self.last_paginate_kept = 0

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
        endpoint = f"{method.upper()}:{path}" if path != "/markets" else "GET:/markets"
        for attempt in range(max_attempts):
            release = await self.rate_limiter.acquire(endpoint) if endpoint in self.rate_limiter._buckets else None
            try:
                async with self.request_semaphore:
                    await asyncio.sleep(random.uniform(0.02, 0.15))
                    response = await self.client.request(method, f"{self.base_url}{path}", params=params, json=json, headers=self._sign(method, path), timeout=timeout)
            except httpx.TimeoutException:
                if attempt == max_attempts - 1:
                    raise
                await self.rate_limiter.backoff(attempt)
                continue
            finally:
                if release:
                    release()
            if response.status_code == 401:
                self.auth_status = AuthStatus(ok=False, reason="401 Unauthorized")
                logger.info("kalshi_auth_status ok=%s reason=%s", self.auth_status.ok, self.auth_status.reason)
                return {}
            if response.status_code == 429 or 500 <= response.status_code < 600:
                if attempt == max_attempts - 1:
                    response.raise_for_status()
                await self.rate_limiter.backoff(attempt)
                continue
            response.raise_for_status()
            self.auth_status = AuthStatus(ok=True, reason="ok")
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
            self.last_paginate_pages = 0
            self.last_paginate_kept = len(cached)
            return list(cached)
        target_kept = TUNING.max_markets_per_sync
        page_limit = 200
        max_pages = 80
        max_consecutive_zero_kept_pages = 2

        now_ts = int(time.time())
        min_close_ts = now_ts + (TUNING.min_minutes_to_close * 60)
        max_close_ts = now_ts + (TUNING.max_days_to_close * 86400)

        kept: list[dict[str, Any]] = []
        cursor: str | None = None
        pages = 0
        empty_streak = 0

        while pages < max_pages and len(kept) < target_kept:
            params: dict[str, Any] = {
                "status": "open",
                "limit": page_limit,
                "min_close_ts": min_close_ts,
                "max_close_ts": max_close_ts,
            }
            if cursor:
                params["cursor"] = cursor

            payload = await self._request("GET", "/markets", params=params)
            page_markets = list(payload.get("markets") or [])
            pages += 1

            page_kept = [m for m in page_markets if not is_skippable_ticker(str(m.get("ticker") or ""))]
            kept.extend(page_kept)

            if not page_kept:
                empty_streak += 1
            else:
                empty_streak = 0

            logger.info(
                "kalshi_paginate page=%d fetched=%d kept_this_page=%d kept_total=%d empty_streak=%d",
                pages, len(page_markets), len(page_kept), len(kept), empty_streak,
            )

            cursor = payload.get("cursor") or None
            if not cursor:
                break
            if empty_streak >= max_consecutive_zero_kept_pages and len(kept) < int(target_kept * 0.1):
                logger.warning(
                    "kalshi_paginate_giving_up empty_streak=%d pages=%d kept=%d reason=consecutive_zero_kept_pages",
                    empty_streak, pages, len(kept),
                )
                break

        self.last_paginate_pages = pages
        self.last_paginate_kept = len(kept)
        self.cache.set("markets:open", kept, ttl_seconds=TUNING.market_cache_ttl_sec)
        logger.info(
            "kalshi_paginate_done pages=%d kept=%d target=%d",
            pages, len(kept), target_kept,
        )
        return kept

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


    async def get_orderbooks(self, tickers: list[str], depth: int = 25) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        clean = [t for t in dict.fromkeys(tickers) if t]
        if not clean:
            return out

        failure_streak = 0
        recovery_count = 0
        failed_tickers = 0
        batch_sizes: list[int] = []
        url_lengths: list[int] = []

        i = 0
        while i < len(clean):
            chunk_size = min(ORDERBOOK_BATCH_SIZE, len(clean) - i)
            chunk = clean[i:i + chunk_size]
            while chunk_size > 1:
                est_url_len = len(f"{self.base_url}/markets/orderbooks?tickers={','.join(chunk)}&depth={depth}")
                if est_url_len <= ORDERBOOK_URL_MAX_LEN:
                    break
                chunk_size = max(1, chunk_size // 2)
                chunk = clean[i:i + chunk_size]
            params = {"tickers": ",".join(chunk), "depth": depth}
            req_id = str(uuid.uuid4())[:8]
            est_url_len = len(f"{self.base_url}/markets/orderbooks?tickers={params['tickers']}&depth={depth}")
            logger.info("orderbook_batch_start req_id=%s chunk_size=%d est_url_len=%d", req_id, len(chunk), est_url_len)
            batch_sizes.append(len(chunk))
            url_lengths.append(est_url_len)
            try:
                payload = await self._request("GET", "/markets/orderbooks", params=params, timeout=20.0)
                books = payload.get("orderbooks") or payload.get("markets") or []
                for row in books:
                    ticker = str(row.get("ticker") or "")
                    if ticker:
                        out[ticker] = row
                logger.info("orderbook_batch_success req_id=%s returned=%d", req_id, len(books))
                failure_streak = 0
            except httpx.HTTPStatusError as exc:
                failure_streak += 1
                logger.warning("orderbook_batch_failed req_id=%s status=%s chunk_size=%d", req_id, exc.response.status_code if exc.response else None, len(chunk))
                if exc.response is not None and exc.response.status_code == 400:
                    for ticker in chunk:
                        logger.info("orderbook_single_retry ticker=%s", ticker)
                        try:
                            payload = await self._request("GET", "/markets/orderbooks", params={"tickers": ticker, "depth": depth}, timeout=10.0)
                            books = payload.get("orderbooks") or payload.get("markets") or []
                            if books:
                                out[ticker] = books[0]
                                recovery_count += 1
                            else:
                                failed_tickers += 1
                                logger.warning("orderbook_invalid_ticker ticker=%s reason=empty_response", ticker)
                        except Exception:
                            failed_tickers += 1
                            logger.warning("orderbook_invalid_ticker ticker=%s reason=request_failed", ticker)
                    logger.info("orderbook_partial_recovery req_id=%s recovered=%d failed=%d", req_id, recovery_count, failed_tickers)
                if failure_streak >= 3:
                    logger.warning("orderbook_circuit_breaker_open cooldown_sec=30")
                    await asyncio.sleep(30)
                    failure_streak = 0
            except Exception:
                failure_streak += 1
                logger.exception("orderbook_batch_failed req_id=%s status=exception", req_id)
            i += chunk_size

        avg_batch = (sum(batch_sizes) / len(batch_sizes)) if batch_sizes else 0
        avg_url = (sum(url_lengths) / len(url_lengths)) if url_lengths else 0
        logger.info(
            "orderbook_final_summary requested=%d returned=%d failed_tickers=%d recovered=%d avg_batch_size=%.2f avg_url_len=%.2f",
            len(clean), len(out), failed_tickers, recovery_count, avg_batch, avg_url
        )
        return out
