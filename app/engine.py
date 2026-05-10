"""
Reconcile guard: wraps a reconcile coroutine in a lock + hard timeout.

Why this exists
---------------
Without a guard, a hung reconcile call (network stall, DB lock, etc.) sits
silently while the cycle scheduler keeps firing. Each subsequent tick logs
'reconcile_skipped reason=already_running' forever. This module bounds the
runtime explicitly so a hung reconcile is logged loudly and the next tick
gets a clean attempt.

Usage
-----
    from app.observability.reconcile_guard import reconcile_with_timeout

    asyncio.create_task(reconcile_with_timeout(lambda: reconcile()))

Pass a zero-arg lambda (a coroutine FACTORY), not a coroutine. A coroutine
can only be awaited once; a factory can be re-invoked safely.

Env vars
--------
    RECONCILE_TIMEOUT_S : hard timeout per reconcile attempt (default 30)
"""

import os
import asyncio
import logging
import time
from typing import Awaitable, Callable

log = logging.getLogger("app.engine")

RECONCILE_TIMEOUT_S = int(os.getenv("RECONCILE_TIMEOUT_S", "30"))

_reconcile_lock = asyncio.Lock()
_reconcile_start_ts = 0.0


async def reconcile_with_timeout(
    coro_factory: Callable[[], Awaitable[None]],
) -> None:
    """
    Run a reconcile coroutine under a lock with a hard timeout.

    Parameters
    ----------
    coro_factory : zero-arg callable returning a fresh coroutine.
                   Pass `lambda: reconcile()`, NOT `reconcile()` directly.

    Behavior
    --------
    - Lock free                    -> acquire, run with asyncio.wait_for.
    - Lock held, under timeout     -> log 'reconcile_skipped' and return.
    - Lock held, exceeds timeout   -> log 'reconcile_timeout_exceeded' and
                                      return (the hung task is left to
                                      finish or be GC'd; this tick is
                                      sacrificed but the next will retry).
    - Wrapped coro times out       -> log 'reconcile_timeout', release lock.
    - Wrapped coro raises          -> log 'reconcile_error', release lock.

    Always returns None. Never raises.
    """
    global _reconcile_start_ts

    if _reconcile_lock.locked():
        running_s = time.monotonic() - _reconcile_start_ts
        if running_s > RECONCILE_TIMEOUT_S:
            log.warning(
                "reconcile_timeout_exceeded running_s=%.1f limit=%d",
                running_s, RECONCILE_TIMEOUT_S,
            )
        else:
            log.info(
                "reconcile_skipped reason=already_running running_s=%.1f",
                running_s,
            )
        return

    async with _reconcile_lock:
        _reconcile_start_ts = time.monotonic()
        try:
            await asyncio.wait_for(
                coro_factory(),
                timeout=RECONCILE_TIMEOUT_S,
            )
            elapsed = time.monotonic() - _reconcile_start_ts
            log.info("reconcile_ok elapsed_s=%.1f", elapsed)
        except asyncio.TimeoutError:
            elapsed = time.monotonic() - _reconcile_start_ts
            log.error(
                "reconcile_timeout elapsed_s=%.1f limit=%d",
                elapsed, RECONCILE_TIMEOUT_S,
            )
        except Exception as e:
            log.error("reconcile_error err=%s", str(e)[:200])
