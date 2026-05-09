from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum


class BreakerState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(slots=True)
class CircuitBreaker:
    name: str
    failure_threshold: int = 5
    half_open_success_threshold: int = 3
    open_timeout_sec: float = 10.0
    state: BreakerState = field(default=BreakerState.CLOSED, init=False)
    failures: int = field(default=0, init=False)
    successes: int = field(default=0, init=False)
    open_until: float = field(default=0.0, init=False)
    _lock: asyncio.Lock = field(init=False)

    def __post_init__(self) -> None:
        self.state = BreakerState.CLOSED
        self.failures = 0
        self.successes = 0
        self.open_until = 0.0
        self._lock = asyncio.Lock()

    async def allow(self) -> bool:
        async with self._lock:
            now = time.monotonic()
            if self.state == BreakerState.OPEN and now >= self.open_until:
                self.state = BreakerState.HALF_OPEN
                self.successes = 0
            return self.state != BreakerState.OPEN

    async def record_success(self) -> None:
        async with self._lock:
            if self.state == BreakerState.HALF_OPEN:
                self.successes += 1
                if self.successes >= self.half_open_success_threshold:
                    self.state = BreakerState.CLOSED
                    self.failures = 0
                    self.successes = 0
                return
            self.failures = 0

    async def record_failure(self) -> None:
        async with self._lock:
            self.failures += 1
            if self.failures >= self.failure_threshold:
                self.state = BreakerState.OPEN
                backoff = self.open_timeout_sec * (2 ** max(0, self.failures - self.failure_threshold))
                self.open_until = time.monotonic() + min(backoff, 120)


class BreakerRegistry:
    def __init__(self) -> None:
        self.market_fetch_breaker = CircuitBreaker("market_fetch")
        self.order_submit_breaker = CircuitBreaker("order_submit")
        self.orderbook_breaker = CircuitBreaker("orderbook")
        self.pricing_breaker = CircuitBreaker("pricing")
        self.websocket_breaker = CircuitBreaker("websocket")
