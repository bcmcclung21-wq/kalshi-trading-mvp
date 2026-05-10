import os
import time
import logging
from typing import List

log = logging.getLogger("app.services.universe")

MARKET_CACHE_TTL_S = int(os.getenv("MARKET_CACHE_TTL_S", "30"))

UNIVERSE_MAX_DAYS = int(os.getenv("UNIVERSE_MAX_DAYS", "90"))
UNIVERSE_MIN_OI = int(os.getenv("UNIVERSE_MIN_OI", "0"))
UNIVERSE_MIN_SEC = int(os.getenv("UNIVERSE_MIN_SEC", "900"))
UNIVERSE_MIN_VOLUME = int(os.getenv("UNIVERSE_MIN_VOLUME", "5"))
UNIVERSE_TOP_N = int(os.getenv("UNIVERSE_TOP_N", "30"))
UNIVERSE_RECENT_S = int(os.getenv("UNIVERSE_RECENT_S", "240"))

_market_cache = {
    "markets": [],
    "ts": 0.0,
}


def persist_markets(markets: List[dict]) -> int:
    """
    Persist markets to in-process cache with monotonic timestamp.

    Previously the cache write succeeded but the read path was looking at a
    stale dict key, so every cycle fired
    'sync_markets_cache_empty falling_back_to_fetch'. Now read/write share
    the same module-level _market_cache dict and use time.monotonic() so
    we are immune to wall-clock skew.
    """
    global _market_cache
    _market_cache["markets"] = list(markets)
    _market_cache["ts"] = time.monotonic()
    log.info("persist_markets saved=%d cache_ts=%.1f", len(markets), _market_cache["ts"])
    return len(markets)


def get_cached_markets() -> List[dict]:
    """Return cached markets if fresh, empty list otherwise."""
    if not _market_cache["markets"]:
        return []
    age = time.monotonic() - _market_cache["ts"]
    if age <= MARKET_CACHE_TTL_S:
        log.debug("market_cache_hit age_s=%.1f count=%d", age, len(_market_cache["markets"]))
        return list(_market_cache["markets"])
    log.debug("market_cache_stale age_s=%.1f ttl_s=%d", age, MARKET_CACHE_TTL_S)
    return []


def invalidate_cache() -> None:
    """Force the next read to miss. Use after explicit refreshes."""
    global _market_cache
    _market_cache["markets"] = []
    _market_cache["ts"] = 0.0


def universe_filter(markets: List[dict]) -> List[dict]:
    """
    Apply universe gates. Logs total/kept/removed.
    Keeps markets that satisfy all of:
      - close_time within UNIVERSE_MAX_DAYS
      - time to close >= UNIVERSE_MIN_SEC
      - open_interest >= UNIVERSE_MIN_OI
      - volume >= UNIVERSE_MIN_VOLUME
    """
    if not markets:
        log.info("universe_filter total=0 kept=0 removed=0")
        return []

    now_s = time.time()
    max_close_s = now_s + (UNIVERSE_MAX_DAYS * 86400)
    kept: List[dict] = []

    for m in markets:
        close_ts = m.get("close_ts") or m.get("close_time_ts") or 0
        oi = m.get("open_interest", 0) or 0
        vol = m.get("volume", 0) or 0

        if close_ts and close_ts > max_close_s:
            continue
        if close_ts and (close_ts - now_s) < UNIVERSE_MIN_SEC:
            continue
        if oi < UNIVERSE_MIN_OI:
            continue
        if vol < UNIVERSE_MIN_VOLUME:
            continue
        kept.append(m)

    removed = len(markets) - len(kept)
    log.info("universe_filter total=%d kept=%d removed=%d", len(markets), len(kept), removed)
    return kept