"""Market classifier shared across the trading stack.

Polymarket US market rows are slug-based rather than ticker-based.
This module normalizes either legacy ticker payloads or Polymarket US
slug payloads into a common internal contract.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

PACKAGED_PREFIXES: tuple[str, ...] = (
    "KXMVE",
    "KXBET",
    "KXSGP",
    "KXPARLAY",
    "KXCOMBO",
)

CATEGORY_PREFIXES: Dict[str, tuple] = {
    "sports": ("nba", "nfl", "mlb", "nhl", "ncaa", "epl", "ufc", "soccer", "wnba", "mls", "nwsl", "win the game", "winner", "vs", "match", "cover", "over", "under", "goal", "score"),
    "politics": ("election", "president", "senate", "house", "governor", "congress", "scotus"),
    "crypto": ("bitcoin", "btc", "ethereum", "eth", "solana", "sol", "xrp", "crypto", "altcoin", "link"),
    "economics": ("cpi", "ppi", "gdp", "jobs", "payroll", "inflation", "fed", "rate", "unemployment"),
    "climate": ("temperature", "rain", "snow", "wind", "weather", "climate", "hurricane"),
}


def _as_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, dict) and "value" in value:
        value = value.get("value")
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def normalized_market(raw: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(raw, dict):
        return None

    ticker = raw.get("ticker") or raw.get("market_ticker") or raw.get("marketSlug") or raw.get("market_slug") or raw.get("slug")
    if not ticker or not isinstance(ticker, str):
        return None

    base: Dict[str, Any] = {
        "ticker": ticker,
        "event_ticker": raw.get("event_ticker") or raw.get("eventSlug") or raw.get("event_slug") or "",
        "title": raw.get("title") or raw.get("question") or raw.get("marketTitle") or ticker,
        "subtitle": raw.get("subtitle") or raw.get("description") or raw.get("outcome") or "",
        "status": raw.get("status") or ("open" if raw.get("active", True) and not raw.get("closed", False) else "closed"),
        "yes_bid": raw.get("yes_bid"),
        "yes_ask": raw.get("yes_ask"),
        "no_bid": raw.get("no_bid"),
        "no_ask": raw.get("no_ask"),
        "last_price": raw.get("last_price") or _as_float(raw.get("lastTradePx")) or _as_float((raw.get("stats") or {}).get("lastTradePx")),
        "volume": raw.get("volume") or 0,
        "volume_24h": raw.get("volume_24h") or raw.get("volume") or 0,
        "open_interest": raw.get("open_interest") or raw.get("openInterest") or _as_float((raw.get("stats") or {}).get("openInterest")),
        "close_time": raw.get("close_time") or raw.get("closeTime") or raw.get("settledAt"),
        "expiration_time": raw.get("expiration_time") or raw.get("closeTime") or raw.get("settledAt"),
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
    ticker = str(market.get("ticker") or market.get("market_ticker") or market.get("marketSlug") or "")
    ticker_upper = ticker.upper()
    if any(ticker_upper.startswith(prefix) for prefix in PACKAGED_PREFIXES):
        return True
    combined = f"{market.get('title') or ''} {market.get('subtitle') or ''}".lower()
    return any(kw in combined for kw in ("parlay", "same game", "multi-leg", "multileg", "combo bet", "package bet"))


def _detect_category_for_payload(raw: Dict[str, Any], normalized: Dict[str, Any]) -> str:
    raw_cat = str(raw.get("category") or "").lower().strip()
    if raw_cat in CATEGORY_PREFIXES:
        return raw_cat
    title_combined = f"{normalized.get('title') or ''} {normalized.get('subtitle') or ''} {normalized.get('event_ticker') or ''}".lower()
    for category, keywords in CATEGORY_PREFIXES.items():
        if any(kw in title_combined for kw in keywords):
            return category
    return "unknown"


def detect_category(market: Any) -> str:
    if not isinstance(market, dict):
        return "unknown"
    if isinstance(market.get("category"), str) and market.get("category"):
        return str(market["category"])
    return _detect_category_for_payload(market, market)


def classify_category(market: Any) -> str:
    return detect_category(market)


def _infer_market_type_for_payload(raw: Dict[str, Any], normalized: Dict[str, Any]) -> str:
    if is_packaged_market(normalized):
        return "combo"
    explicit = str(raw.get("market_type") or "").strip().lower()
    if explicit in ("combo", "multileg", "multi-leg", "packaged"):
        return "combo"
    return "single"


def infer_market_type(market: Any) -> str:
    if not isinstance(market, dict):
        return "single"
    return _infer_market_type_for_payload(market, market)


def _infer_legs(raw: Dict[str, Any], market_type: str) -> int:
    if market_type == "single":
        return 1
    for key in ("legs", "leg_count"):
        try:
            value = int(raw.get(key) or 0)
            if value > 0:
                return value
        except (TypeError, ValueError):
            continue
    return 2


def _minutes_to_close(raw: Dict[str, Any]) -> float | None:
    close_value = raw.get("close_time") or raw.get("closeTime") or raw.get("expiration_time") or raw.get("settledAt")
    if not close_value:
        return None
    try:
        text = str(close_value).replace("Z", "+00:00")
        close_dt = datetime.fromisoformat(text)
        now = datetime.now(timezone.utc)
        if close_dt.tzinfo is None:
            close_dt = close_dt.replace(tzinfo=timezone.utc)
        return max(0.0, (close_dt - now).total_seconds() / 60.0)
    except Exception:
        return None


def is_singleton_binary(market: Any) -> bool:
    return infer_market_type(market) == "single" and not is_packaged_market(market)

