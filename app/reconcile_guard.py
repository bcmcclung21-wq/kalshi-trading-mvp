NOTE: app/engine.py is ~2000+ lines and you should NOT replace it wholesale.
Instead, we add a new helper module and you update ONE call site in engine.py
(shown after this file).

─────────────────── START OF app/observability/reconcile_guard.py ─────────────
import os
import asyncio
import logging
import time
from typing import Callable, Awaitable

log = logging.getLogger("app.engine")

RECONCILE_TIMEOUT_S = int(os.getenv("RECONCILE_TIMEOUT_S", "30"))

_reconcile_lock = asyncio.Lock()
_reconcile_start_ts = 0.0


async def reconcile_with_timeout(coro_factory: Callable[[], Awaitable[None]]) -> None:
    """
    Run a reconcile coroutine with a lock and a hard timeout.

    coro_factory : zero-arg callable returning a fresh coroutine. Pass a
                   lambda so we never await a coroutine that was already
                   consumed by a prior call.

    Behavior:
      - If lock is held and previous run is under timeout: skip cleanly.
      - If lock is held and previous run exceeds timeout: log loudly and
        still skip this tick (prior task will be cancelled by GC; if you
        need hard kill, track the Task object externally).
      - If lock is free: acquire, run with asyncio.wait_for, log outcome.
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
            log.info("reconcile_skipped reason=already_running running_s=%.1f", running_s)
        return

    async with _reconcile_lock:
        _reconcile_start_ts = time.monotonic()
        try:
            await asyncio.wait_for(coro_factory(), timeout=RECONCILE_TIMEOUT_S)
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