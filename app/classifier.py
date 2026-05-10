"""Kalshi market classifier.

Pure-ASCII module. All downstream code (engine, selector, research,
universe service, kalshi client, tests) imports from here. Do NOT
redefine packaged-market detection or category mapping anywhere else.

Public API:
  - PACKAGED_PREFIXES         : tuple of ticker prefixes for packaged bundles
  - CATEGORY_PREFIXES         : dict[category -> tuple of ticker prefixes]
  - normalized_market(raw)    : coerce raw Kalshi payload to flat dict
                                with market_type, category, legs,
                                minutes_to_close already populated
  - is_packaged_market(m)     : True for KXMVE / parlay / SGP bundles
  - detect_category(m)        : map market -> sports/politics/crypto/
                                economics/climate/unknown
  - classify_category(m)      : alias of detect_category (back-compat)
  - infer_market_type(m)      : 'combo' for packaged bundles, else 'single'
  - is_singleton_binary(m)    : True for clean YES/NO singletons
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional


PACKAGED_PREFIXES: tuple[str, ...] = (
    "KXMVE",
    "KXNBAGAME",
    "KXNFLGAME",
    "KXMLBGAME",
    "KXNHLGAME",
    "KXBET",
    "KXSGP",
    "KXPARLAY",
    "KXCOMBO",
)


CATEGORY_PREFIXES: Dict[str, tuple] = {
    "sports": (
        "KXNBA", "KXNFL", "KXMLB", "KXNHL", "KXNCAAF", "KXNCAAB",
        "KXEPL", "KXUCL", "KXMMA", "KXUFC", "KXBOX", "KXTEN",
        "KXGOLF", "KXPGA", "KXF1", "KXNASCAR", "KXWNBA", "KXMLS",
    ),
    "politics": (
        "KXPRES", "KXSEN", "KXHOUSE", "KXGOV", "KXELECT", "KXPOL",
        "KXCONG", "KXSCOTUS", "KXFED",
    ),
    "crypto": (
        "KXBTC", "KXETH", "KXSOL", "KXXRP", "KXCRYPTO", "KXCOIN",
        "KXATOM", "KXONDO", "KXLINK",
    ),
    "economics": (
        "KXCPI", "KXJOBS", "KXGDP", "KXFOMC", "KXRATE", "KXUNEMP",
        "KXPPI", "KXPCE", "KXPAYROLL", "KXNFP", "KXECON",
    ),
    "climate": (
        "KXTEMP", "KXHIGH", "KXLOW", "KXRAIN", "KXSNOW", "KXHURR",
        "KXWX", "KXCLIM", "KXCO2",
    ),
}


_CATEGORY_TITLE_KEYWORDS: Dict[str, tuple[str, ...]] = {
    "crypto": (
        "bitcoin", "btc", "ethereum", "eth", "solana", "sol", "xrp",
        "ripple", "crypto", "altcoin", "stablecoin", "ondo", "atom",
        "chainlink", "link",
    ),
    "sports": (
        "nba", "nfl", "mlb", "nhl", "ncaa", "epl", "premier league",
        "champions league", "mma", "ufc", "boxing", "tennis", "golf",
        "pga", "f1", "formula 1", "nascar", "wnba", "mls", "score",
        "win the game", "cover", "spread",
    ),
    "politics": (
        "election", "president", "senate", "house of representatives",
        "governor", "vote", "congress", "scotus", "supreme court",
        "approval rating", "primary", "midterm",
    ),
    "economics": (
        "cpi", "ppi", "pce", "gdp", "unemployment", "jobs report",
        "nonfarm payroll", "fomc", "fed", "interest rate", "rate cut",
        "rate hike", "inflation",
    ),
    "climate": (
        "temperature", "high temp", "low temp", "rainfall", "snowfall",
        "hurricane", "tropical storm", "weather", "climate", "co2",
        "rain", "snow", "wind",
    ),
}


def normalized_market(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None

    ticker = raw.get("ticker") or raw.get("market_ticker")
    if not ticker or not isinstance(ticker, str):
        return None

    base: Dict[str, Any] = {
        "ticker": ticker,
        "event_ticker": raw.get("event_ticker") or "",
        "title": raw.get("title") or raw.get("yes_sub_title") or "",
        "subtitle": raw.get("subtitle") or raw.get("yes_sub_title") or "",
        "status": raw.get("status") or "",
        "yes_bid": raw.get("yes_bid"),
        "yes_ask": raw.get("yes_ask"),
        "no_bid": raw.get("no_bid"),
        "no_ask": raw.get("no_ask"),
        "last_price": raw.get("last_price"),
        "volume": raw.get("volume") or 0,
        "volume_24h": raw.get("volume_24h") or 0,
        "open_interest": raw.get("open_interest") or 0,
        "close_time": raw.get("close_time"),
        "expiration_time": raw.get("expiration_time"),
        "_raw": raw,
    }

    base["category"] = _detect_category_for_payload(raw, base)
    base["market_type"] = _infer_market_type_for_payload(raw, base)
    base["legs"] = _infer_legs(raw, base["market_type"])
    base["minutes_to_close"] = _minutes_to_close(raw)

    return base


def is_packaged_market(market: Any) -> bool:
    if not isinstance(market, dict):
        return False

    ticker = market.get("ticker") or market.get("market_ticker") or ""
    if not isinstance(ticker, str):
        return False
    ticker_upper = ticker.upper()

    for prefix in PACKAGED_PREFIXES:
        if ticker_upper.startswith(prefix):
            return True

    event_ticker = market.get("event_ticker") or ""
    if isinstance(event_ticker, str):
        event_upper = event_ticker.upper()
        for prefix in PACKAGED_PREFIXES:
            if event_upper.startswith(prefix):
                return True

    title = (market.get("title") or "").lower()
    subtitle = (market.get("subtitle") or "").lower()
    combined = title + " " + subtitle
    packaged_keywords = (
        "parlay",
        "same game",
        "multi-leg",
        "multileg",
        "combo bet",
        "package bet",
    )
    for kw in packaged_keywords:
        if kw in combined:
            return True

    mtype = str(market.get("market_type") or "").lower()
    if mtype in ("combo", "multileg", "multi-leg", "packaged"):
        return True

    return False


def _detect_category_for_payload(raw: Dict[str, Any], normalized: Dict[str, Any]) -> str:
    ticker = (raw.get("ticker") or raw.get("market_ticker") or "").upper()
    event_ticker = (raw.get("event_ticker") or "").upper()

    for category, prefixes in CATEGORY_PREFIXES.items():
        for prefix in prefixes:
            if ticker.startswith(prefix) or event_ticker.startswith(prefix):
                return category

    raw_cat = (raw.get("category") or "").lower().strip()
    if raw_cat in CATEGORY_PREFIXES:
        return raw_cat
    if raw_cat in ("financial", "economy", "macro", "macroeconomics"):
        return "economics"
    if raw_cat in ("weather",):
        return "climate"

    title_combined = (
        (normalized.get("title") or "").lower()
        + " "
        + (normalized.get("subtitle") or "").lower()
    )
    if title_combined.strip():
        for category, keywords in _CATEGORY_TITLE_KEYWORDS.items():
            for kw in keywords:
                if kw in title_combined:
                    return category

    return "unknown"


def detect_category(market: Any) -> str:
    if not isinstance(market, dict):
        return "unknown"
    if "_raw" in market and isinstance(market.get("category"), str) and market["category"]:
        return market["category"]
    return _detect_category_for_payload(market, market)


def classify_category(market: Any) -> str:
    return detect_category(market)


def _infer_market_type_for_payload(raw: Dict[str, Any], normalized: Dict[str, Any]) -> str:
    if is_packaged_market(normalized):
        return "combo"
    explicit = str(raw.get("market_type") or "").strip().lower()
    if explicit in ("combo", "multileg", "multi-leg", "packaged"):
        return "combo"
    if explicit in ("single", "binary", ""):
        return "single"
    return "single"


def infer_market_type(market: Any) -> str:
    if not isinstance(market, dict):
        return "single"
    if isinstance(market.get("market_type"), str) and "_raw" in market:
        return market["market_type"]
    return _infer_market_type_for_payload(market, market)


def _infer_legs(raw: Dict[str, Any], market_type: str) -> int:
    explicit = raw.get("legs") or raw.get("leg_count")
    if isinstance(explicit, int) and explicit >= 1:
        return explicit
    if isinstance(explicit, float) and explicit >= 1:
        return int(explicit)
    if isinstance(explicit, str) and explicit.isdigit():
        n = int(explicit)
        if n >= 1:
            return n
    if market_type == "combo":
        return 2
    return 1


def _minutes_to_close(raw: Dict[str, Any]) -> Optional[float]:
    close_iso = raw.get("close_time") or raw.get("expiration_time")
    if not close_iso or not isinstance(close_iso, str):
        return None
    try:
        if close_iso.endswith("Z"):
            close_iso = close_iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(close_iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = dt - datetime.now(timezone.utc)
        return max(delta.total_seconds() / 60.0, 0.0)
    except (ValueError, TypeError):
        return None


def is_singleton_binary(market: Any) -> bool:
    if not isinstance(market, dict):
        return False
    if is_packaged_market(market):
        return False

    status = (market.get("status") or "").lower()
    if status not in ("active", "open", ""):
        return False

    yes_bid = market.get("yes_bid")
    yes_ask = market.get("yes_ask")
    if yes_bid is None or yes_ask is None:
        return False

    return True


__all__ = [
    "PACKAGED_PREFIXES",
    "CATEGORY_PREFIXES",
    "normalized_market",
    "is_packaged_market",
    "detect_category",
    "classify_category",
    "infer_market_type",
    "is_singleton_binary",
]
