from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Awaitable, Callable

log = logging.getLogger("app.engine")

RECONCILE_TIMEOUT_S = int(os.getenv("RECONCILE_TIMEOUT_S", "30"))

_reconcile_lock = asyncio.Lock()
_reconcile_start_ts = 0.0


async def reconcile_with_timeout(coro_factory: Callable[[], Awaitable[None]]) -> None:
    global _reconcile_start_ts

    if _reconcile_lock.locked():
        running_s = time.monotonic() - _reconcile_start_ts
        if running_s > RECONCILE_TIMEOUT_S:
            log.warning("reconcile_timeout_exceeded running_s=%.1f limit=%d", running_s, RECONCILE_TIMEOUT_S)
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
            log.error("reconcile_timeout elapsed_s=%.1f limit=%d", elapsed, RECONCILE_TIMEOUT_S)
        except Exception as exc:
            log.error("reconcile_error err=%s", str(exc)[:200])
