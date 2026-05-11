from __future__ import annotations

import asyncio
import random
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Callable


class EngineMode(str, Enum):
    BOOT = "boot"
    LIVE = "live"


@dataclass(slots=True, frozen=True)
class MarketUpdateVersion:
    sequence: int
    source_ts_ms: int

    def __lt__(self, other: "MarketUpdateVersion") -> bool:
        return (self.source_ts_ms, self.sequence) < (other.source_ts_ms, other.sequence)


@dataclass(slots=True)
class MarketState:
    ticker: str
    market: dict[str, Any]
    score: float
    added_at_ms: int
    expires_at_ms: int
    last_seen_ms: int
    version: MarketUpdateVersion


@dataclass(slots=True)
class IngestionMetrics:
    fetch_latency_ms: float = 0
    normalization_latency_ms: float = 0
    queue_depth: int = 0
    duplicate_suppression_rate: float = 0
    cache_hit_rate: float = 0
    api_throughput_rps: float = 0
    ws_lag_ms: float = 0
    ingestion_count: int = 0
    duplicate_count: int = 0

    def to_prometheus(self) -> str:
        return "\n".join(
            [
                f"poly_fetch_latency_ms {self.fetch_latency_ms:.2f}",
                f"poly_normalization_latency_ms {self.normalization_latency_ms:.2f}",
                f"poly_ingestion_queue_depth {self.queue_depth}",
                f"poly_duplicate_suppression_rate {self.duplicate_suppression_rate:.6f}",
                f"poly_cache_hit_rate {self.cache_hit_rate:.6f}",
                f"poly_api_throughput_rps {self.api_throughput_rps:.2f}",
                f"poly_websocket_lag_ms {self.ws_lag_ms:.2f}",
                f"poly_ingested_updates_total {self.ingestion_count}",
                f"poly_duplicate_updates_total {self.duplicate_count}",
            ]
        )


class MarketCache:
    def __init__(self, ttl_seconds: int = 60, max_size: int = 10_000) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_size = max_size
        self._store: OrderedDict[str, tuple[dict[str, Any], float]] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, ticker: str) -> dict[str, Any] | None:
        now = time.monotonic()
        item = self._store.get(ticker)
        if item is None:
            self.misses += 1
            return None
        payload, expires_at = item
        if expires_at < now:
            self._store.pop(ticker, None)
            self.misses += 1
            return None
        self._store.move_to_end(ticker)
        self.hits += 1
        return payload

    def upsert(self, ticker: str, patch: dict[str, Any]) -> None:
        now = time.monotonic()
        base = self._store.get(ticker, ({}, 0))[0]
        merged = {**base, **patch}
        self._store[ticker] = (merged, now + self.ttl_seconds)
        self._store.move_to_end(ticker)
        while len(self._store) > self.max_size:
            self._store.popitem(last=False)

    def snapshot(self) -> MappingProxyType[str, dict[str, Any]]:
        return MappingProxyType({k: v for k, (v, _) in self._store.items()})

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        if total == 0:
            return 0.0
        return self.hits / total


class MarketDiscoveryEngine:
    def __init__(self, ttl_seconds: int = 900, max_tracked: int = 1500) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_tracked = max_tracked
        self.tracked_markets: dict[str, MarketState] = {}

    def _score_market(self, market: dict[str, Any], strategy_tickers: set[str], position_tickers: set[str]) -> float:
        volume = float(market.get("volume") or 0)
        spread = float(market.get("spread") or 100)
        vol = float(market.get("volatility") or 0)
        expiry_s = str(market.get("close_time") or "")
        expiry_boost = 0.0
        if expiry_s:
            try:
                expiry = datetime.fromisoformat(expiry_s.replace("Z", "+00:00"))
                hours = max((expiry - datetime.now(timezone.utc)).total_seconds() / 3600.0, 0)
                expiry_boost = max(0, 72 - hours) / 72
            except ValueError:
                expiry_boost = 0
        strategy_boost = 2.5 if str(market.get("ticker")) in strategy_tickers else 0
        position_boost = 3.5 if str(market.get("ticker")) in position_tickers else 0
        liquidity = min(volume / 50_000, 3)
        spread_penalty = min(spread / 50, 2)
        return liquidity + vol + expiry_boost + strategy_boost + position_boost - spread_penalty

    def reconcile_registry(self, markets: list[dict[str, Any]], strategy_tickers: set[str], position_tickers: set[str]) -> None:
        now_ms = int(time.time() * 1000)
        seen: set[str] = set()
        for market in markets:
            ticker = str(market.get("ticker") or "")
            if not ticker or ticker in seen:
                continue
            seen.add(ticker)
            score = self._score_market(market, strategy_tickers, position_tickers)
            if score < 0.2:
                continue
            expires_at = now_ms + self.ttl_seconds * 1000
            current = self.tracked_markets.get(ticker)
            version = current.version if current else MarketUpdateVersion(0, now_ms)
            self.tracked_markets[ticker] = MarketState(ticker=ticker, market=market, score=score, added_at_ms=current.added_at_ms if current else now_ms, expires_at_ms=expires_at, last_seen_ms=now_ms, version=version)

        stale = [k for k, v in self.tracked_markets.items() if v.expires_at_ms < now_ms]
        for ticker in stale:
            self.tracked_markets.pop(ticker, None)
        if len(self.tracked_markets) > self.max_tracked:
            ranked = sorted(self.tracked_markets.values(), key=lambda m: m.score, reverse=True)[: self.max_tracked]
            self.tracked_markets = {m.ticker: m for m in ranked}


class AdaptiveRateLimiter:
    def __init__(self) -> None:
        self._buckets: dict[str, dict[str, float]] = {}
        self._limits: dict[str, asyncio.Semaphore] = {}

    def configure_endpoint(self, endpoint: str, rate: float, capacity: float, concurrency: int) -> None:
        self._buckets[endpoint] = {"tokens": capacity, "last": time.monotonic(), "rate": rate, "capacity": capacity}
        self._limits[endpoint] = asyncio.Semaphore(concurrency)

    async def acquire(self, endpoint: str) -> Callable[[], None]:
        bucket = self._buckets[endpoint]
        limiter = self._limits[endpoint]
        await limiter.acquire()
        while True:
            now = time.monotonic()
            elapsed = now - bucket["last"]
            bucket["tokens"] = min(bucket["capacity"], bucket["tokens"] + elapsed * bucket["rate"])
            bucket["last"] = now
            if bucket["tokens"] >= 1:
                bucket["tokens"] -= 1
                break
            await asyncio.sleep(0.02)

        def _release() -> None:
            limiter.release()

        return _release

    async def backoff(self, attempt: int, retry_after: float | None = None) -> None:
        if retry_after:
            await asyncio.sleep(retry_after)
            return
        base = min(0.2 * (2**attempt), 5)
        await asyncio.sleep(base + random.uniform(0, 0.3))


class AsyncIngestionPipeline:
    def __init__(self, cache: MarketCache, discovery: MarketDiscoveryEngine, metrics: IngestionMetrics, queue_size: int = 20_000) -> None:
        self.cache = cache
        self.discovery = discovery
        self.metrics = metrics
        self.queue: asyncio.Queue[tuple[str, dict[str, Any], MarketUpdateVersion]] = asyncio.Queue(maxsize=queue_size)
        self.seen_market_updates: set[str] = set()
        self.last_versions: dict[str, MarketUpdateVersion] = {}
        self._seq = 0

    def next_version(self, source_ts_ms: int | None = None) -> MarketUpdateVersion:
        self._seq += 1
        return MarketUpdateVersion(sequence=self._seq, source_ts_ms=source_ts_ms or int(time.time() * 1000))

    async def enqueue(self, ticker: str, payload: dict[str, Any], version: MarketUpdateVersion) -> None:
        digest = f"{ticker}:{version.source_ts_ms}:{version.sequence}"
        if digest in self.seen_market_updates:
            self.metrics.duplicate_count += 1
            return
        self.seen_market_updates.add(digest)
        await self.queue.put((ticker, payload, version))
        self.metrics.queue_depth = self.queue.qsize()

    async def run_once(self) -> int:
        processed = 0
        while not self.queue.empty():
            ticker, payload, version = await self.queue.get()
            latest = self.last_versions.get(ticker)
            if latest and version < latest:
                self.metrics.duplicate_count += 1
                self.queue.task_done()
                continue
            self.last_versions[ticker] = version
            self.cache.upsert(ticker, payload)
            tracked = self.discovery.tracked_markets.get(ticker)
            if tracked:
                tracked.market = {**tracked.market, **payload}
                tracked.version = version
                tracked.last_seen_ms = int(time.time() * 1000)
            processed += 1
            self.metrics.ingestion_count += 1
            self.queue.task_done()
        self.metrics.queue_depth = self.queue.qsize()
        total = self.metrics.ingestion_count + self.metrics.duplicate_count
        self.metrics.duplicate_suppression_rate = (self.metrics.duplicate_count / total) if total else 0.0
        self.metrics.cache_hit_rate = self.cache.hit_rate
        return processed
