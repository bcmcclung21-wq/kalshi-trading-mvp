from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Any, Callable


logger = logging.getLogger(__name__)

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
class ReconcileStats:
    fetched_total: int = 0
    seen_unique: int = 0
    tracked_after_reconcile: int = 0
    retained_positions: int = 0
    retained_strategy: int = 0
    retained_same_day: int = 0
    retained_score: int = 0
    dropped_score: int = 0


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
    dropped_count: int = 0

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
                f"poly_dropped_updates_total {self.dropped_count}",
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

    @staticmethod
    def _same_day_eligible(market: dict[str, Any], now_utc: datetime) -> bool:
        if str(market.get("market_type") or "") != "single":
            return False
        close_value = market.get("close_time") or market.get("expiration_time")
        if not close_value:
            return False
        try:
            close_dt = datetime.fromisoformat(str(close_value).replace("Z", "+00:00"))
            if close_dt.tzinfo is None:
                close_dt = close_dt.replace(tzinfo=timezone.utc)
            close_dt = close_dt.astimezone(timezone.utc)
        except ValueError:
            return False
        return close_dt.date() == now_utc.date()

    def reconcile_registry(self, markets: list[dict[str, Any]], strategy_tickers: set[str], position_tickers: set[str]) -> ReconcileStats:
        stats = ReconcileStats(fetched_total=len(markets))
        now_ms = int(time.time() * 1000)
        now_utc = datetime.now(timezone.utc)
        seen: set[str] = set()
        for market in markets:
            ticker = str(market.get("ticker") or "")
            if not ticker or ticker in seen:
                continue
            seen.add(ticker)
            stats.seen_unique += 1
            score = self._score_market(market, strategy_tickers, position_tickers)
            keep_for_position = ticker in position_tickers
            keep_for_strategy = ticker in strategy_tickers
            keep_for_same_day = self._same_day_eligible(market, now_utc)
            keep_for_score = score >= 0.2
            if not (keep_for_position or keep_for_strategy or keep_for_same_day or keep_for_score):
                stats.dropped_score += 1
                continue
            expires_at = now_ms + self.ttl_seconds * 1000
            current = self.tracked_markets.get(ticker)
            version = current.version if current else MarketUpdateVersion(0, now_ms)
            self.tracked_markets[ticker] = MarketState(ticker=ticker, market=market, score=score, added_at_ms=current.added_at_ms if current else now_ms, expires_at_ms=expires_at, last_seen_ms=now_ms, version=version)
            if keep_for_position:
                stats.retained_positions += 1
            if keep_for_strategy:
                stats.retained_strategy += 1
            if keep_for_same_day:
                stats.retained_same_day += 1
            if keep_for_score:
                stats.retained_score += 1

        stale = [k for k, v in self.tracked_markets.items() if v.expires_at_ms < now_ms]
        for ticker in stale:
            self.tracked_markets.pop(ticker, None)
        if len(self.tracked_markets) > self.max_tracked:
            ranked = sorted(self.tracked_markets.values(), key=lambda m: m.score, reverse=True)[: self.max_tracked]
            self.tracked_markets = {m.ticker: m for m in ranked}
        stats.tracked_after_reconcile = len(self.tracked_markets)
        return stats


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
    def __init__(self, dedup_ttl_seconds: int = 300, queue_maxsize: int = 10000):
        self._seen_digests: OrderedDict[str, float] = OrderedDict()
        self._dedup_ttl = dedup_ttl_seconds
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=queue_maxsize)
        self.metrics = type("Metrics", (), {
            "duplicate_count": 0,
            "dropped_count": 0,
            "enqueued_count": 0,
        })()

    async def enqueue(self, ticker: str, payload: dict, version) -> None:
        digest = f"{ticker}:{version.source_ts_ms}:{version.sequence}"
        now = time.time()

        while self._seen_digests:
            oldest_digest, oldest_ts = next(iter(self._seen_digests.items()))
            if now - oldest_ts > self._dedup_ttl:
                del self._seen_digests[oldest_digest]
            else:
                break

        if digest in self._seen_digests:
            self.metrics.duplicate_count += 1
            return

        self._seen_digests[digest] = now
        self._seen_digests.move_to_end(digest)
        self.metrics.enqueued_count += 1

        try:
            self.queue.put_nowait((ticker, payload, version))
        except asyncio.QueueFull:
            self.metrics.dropped_count += 1
            logger.warning("queue_full dropped=%s", ticker)
