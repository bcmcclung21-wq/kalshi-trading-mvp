import os
import asyncio
import logging
import time
import uuid
from typing import Dict, List, Optional

import httpx

log = logging.getLogger("app.kalshi")

KALSHI_API_BASE = os.getenv("KALSHI_API_BASE", "https://api.elections.kalshi.com")
ORDERBOOK_BATCH_ENABLED = os.getenv("ORDERBOOK_BATCH_ENABLED", "false").lower() == "true"
ORDERBOOK_DEPTH = int(os.getenv("ORDERBOOK_DEPTH", "25"))
ORDERBOOK_CIRCUIT_COOLDOWN_S = int(os.getenv("ORDERBOOK_CIRCUIT_COOLDOWN_S", "30"))
ORDERBOOK_PARALLEL = int(os.getenv("ORDERBOOK_PARALLEL", "6"))
ORDERBOOK_TIMEOUT_S = float(os.getenv("ORDERBOOK_TIMEOUT_S", "8.0"))

KALSHI_PAGINATE_LIMIT = int(os.getenv("KALSHI_PAGINATE_LIMIT", "200"))
KALSHI_PAGINATE_MAX_PAGES = int(os.getenv("KALSHI_PAGINATE_MAX_PAGES", "8"))
KALSHI_PAGINATE_EMPTY_STREAK_LIMIT = int(os.getenv("KALSHI_PAGINATE_EMPTY_STREAK_LIMIT", "3"))
KALSHI_PAGINATE_TARGET = int(os.getenv("KALSHI_PAGINATE_TARGET", "1200"))

_circuit_open_until = 0.0


def _is_circuit_open() -> bool:
    return time.monotonic() < _circuit_open_until


def _open_circuit(reason: str = "") -> None:
    global _circuit_open_until
    _circuit_open_until = time.monotonic() + ORDERBOOK_CIRCUIT_COOLDOWN_S
    log.warning(
        "orderbook_circuit_breaker_open cooldown_sec=%d reason=%s",
        ORDERBOOK_CIRCUIT_COOLDOWN_S, reason,
    )


async def fetch_orderbooks(client: httpx.AsyncClient, tickers: List[str]) -> Dict[str, dict]:
    """
    Fetch orderbooks for a list of tickers.

    The Kalshi /markets/orderbooks endpoint returns HTTP 400 reliably for
    multi-ticker batch calls. We go single-ticker only by default, with
    bounded parallelism via semaphore. Faster than batch+retry because the
    batch path was failing 100% and falling back to single retries anyway.

    Returns: {ticker: orderbook_dict}. Missing tickers omitted on partial fail.
    """
    req_id = uuid.uuid4().hex[:8]

    if _is_circuit_open():
        remaining = _circuit_open_until - time.monotonic()
        log.warning("orderbook_circuit_open_skip req_id=%s remaining_s=%.1f", req_id, remaining)
        return {}

    if not tickers:
        return {}

    results: Dict[str, dict] = {}
    failed: List[str] = []
    sem = asyncio.Semaphore(ORDERBOOK_PARALLEL)

    async def _fetch_one(ticker: str) -> None:
        async with sem:
            try:
                url = f"{KALSHI_API_BASE}/trade-api/v2/markets/orderbooks"
                params = {"tickers": ticker, "depth": ORDERBOOK_DEPTH}
                resp = await client.get(url, params=params, timeout=ORDERBOOK_TIMEOUT_S)
                if resp.status_code == 200:
                    data = resp.json()
                    ob_list = data.get("orderbooks") or []
                    if ob_list:
                        results[ticker] = ob_list[0]
                    else:
                        results[ticker] = {"yes": [], "no": []}
                else:
                    failed.append(ticker)
                    log.warning(
                        "orderbook_single_fail req_id=%s ticker=%s status=%s",
                        req_id, ticker, resp.status_code,
                    )
            except asyncio.TimeoutError:
                failed.append(ticker)
                log.warning("orderbook_single_timeout req_id=%s ticker=%s", req_id, ticker)
            except Exception as e:
                failed.append(ticker)
                log.warning(
                    "orderbook_single_exc req_id=%s ticker=%s err=%s",
                    req_id, ticker, str(e)[:120],
                )

    log.info(
        "orderbook_fetch_start req_id=%s mode=single count=%d parallel=%d",
        req_id, len(tickers), ORDERBOOK_PARALLEL,
    )
    t0 = time.monotonic()

    await asyncio.gather(*[_fetch_one(t) for t in tickers])

    elapsed_ms = int((time.monotonic() - t0) * 1000)
    failure_rate = len(failed) / max(len(tickers), 1)

    if failure_rate >= 0.5 and len(tickers) >= 4:
        _open_circuit(reason=f"failure_rate={failure_rate:.2f}")

    log.info(
        "orderbook_fetch_done req_id=%s requested=%d returned=%d failed=%d elapsed_ms=%d",
        req_id, len(tickers), len(results), len(failed), elapsed_ms,
    )
    return results


async def kalshi_paginate(
    client: httpx.AsyncClient,
    base_url: str,
    params: dict,
    filter_fn=None,
) -> List[dict]:
    """
    Paginate Kalshi markets endpoint with bounded pages and empty-streak cutoff.
    Returns flat list of markets that pass filter_fn (or all if None).
    """
    kept: List[dict] = []
    cursor: Optional[str] = None
    empty_streak = 0
    pages_fetched = 0

    while pages_fetched < KALSHI_PAGINATE_MAX_PAGES:
        pages_fetched += 1
        page_params = dict(params)
        page_params["limit"] = KALSHI_PAGINATE_LIMIT
        if cursor:
            page_params["cursor"] = cursor

        try:
            resp = await client.get(base_url, params=page_params, timeout=10.0)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            log.warning("kalshi_paginate_error page=%d err=%s", pages_fetched, str(e)[:120])
            break

        markets = data.get("markets") or []
        cursor = data.get("cursor")

        if filter_fn:
            kept_this_page = [m for m in markets if filter_fn(m)]
        else:
            kept_this_page = list(markets)

        kept.extend(kept_this_page)

        if not kept_this_page:
            empty_streak += 1
        else:
            empty_streak = 0

        log.info(
            "kalshi_paginate page=%d fetched=%d kept_this_page=%d kept_total=%d empty_streak=%d",
            pages_fetched, len(markets), len(kept_this_page), len(kept), empty_streak,
        )

        if empty_streak >= KALSHI_PAGINATE_EMPTY_STREAK_LIMIT:
            log.warning(
                "kalshi_paginate_giving_up empty_streak=%d pages=%d kept=%d reason=consecutive_zero_kept_pages",
                empty_streak, pages_fetched, len(kept),
            )
            break

        if not cursor:
            break

        if len(kept) >= KALSHI_PAGINATE_TARGET:
            break

    log.info(
        "kalshi_paginate_done pages=%d kept=%d target=%d",
        pages_fetched, len(kept), KALSHI_PAGINATE_TARGET,
    )
    return kept
