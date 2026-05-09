from __future__ import annotations

from typing import Dict, Optional

SPORTS_HINTS = {"game", "match", "innings", "quarter", "points", "touchdown", "goal", "strikeout", "homerun", "rebound", "assist", "runs"}
POLITICS_HINTS = {"election", "president", "senate", "house", "governor", "vote", "primary", "congress", "ballot", "poll"}
CRYPTO_HINTS = {"bitcoin", "btc", "ethereum", "eth", "solana", "sol", "xrp", "crypto", "coin", "token"}
ECONOMICS_HINTS = {"cpi", "ppi", "fed", "fomc", "rate", "gdp", "unemployment", "jobs", "nfp", "inflation", "yield"}
CLIMATE_HINTS = {"temperature", "rain", "snow", "hurricane", "storm", "weather", "wildfire", "drought", "climate"}

# Ticker prefix -> category. Authoritative map. Expand here, never inline.
TICKER_PREFIX_MAP: Dict[str, str] = {
    # sports
    "KXNBA": "sports",
    "KXNFL": "sports",
    "KXMLB": "sports",
    "KXNHL": "sports",
    "KXNCAA": "sports",
    "KXMVE": "sports",
    "KXATP": "sports",
    "KXWTA": "sports",
    "KXUFC": "sports",
    "KXPGA": "sports",
    # politics
    "KXPRES": "politics",
    "KXSEN": "politics",
    "KXHOUSE": "politics",
    "KXGOV": "politics",
    "KXELEC": "politics",
    "PRES": "politics",
    # crypto
    "KXBTC": "crypto",
    "KXETH": "crypto",
    "KXSOL": "crypto",
    "KXXRP": "crypto",
    "KXCOIN": "crypto",
    # economics
    "KXCPI": "economics",
    "KXFED": "economics",
    "KXFOMC": "economics",
    "KXGDP": "economics",
    "KXJOBS": "economics",
    "KXNFP": "economics",
    "KXRATE": "economics",
    # climate / weather
    "KXTEMP": "climate",
    "KXWX": "climate",
    "KXHUR": "climate",
    "KXSTORM": "climate",
}

# Packaged / bundle market prefixes - excluded from singleton trading.
_PACKAGED_PREFIXES = (
    "KXMVE",      # movie / event bundle markets
    "KXBUNDLE",
    "KXPKG",
)


def detect_category(ticker: Optional[str], title: Optional[str] = None) -> str:
    """Return one of: sports, politics, crypto, economics, climate, unknown."""
    t = (ticker or "").strip().upper()
    if t:
        for prefix, cat in TICKER_PREFIX_MAP.items():
            if t.startswith(prefix):
                return cat

    s = (title or "").lower()
    if not s:
        return "unknown"
    if any(h in s for h in SPORTS_HINTS):
        return "sports"
    if any(h in s for h in POLITICS_HINTS):
        return "politics"
    if any(h in s for h in CRYPTO_HINTS):
        return "crypto"
    if any(h in s for h in ECONOMICS_HINTS):
        return "economics"
    if any(h in s for h in CLIMATE_HINTS):
        return "climate"
    return "unknown"


def is_packaged_market(market) -> bool:
    """Return True if the market is a packaged/bundle market that should be
    excluded from singleton-ticker trading. Accepts dict, ticker string, or
    object with a `ticker` attribute."""
    if market is None:
        return False
    if isinstance(market, str):
        ticker = market
    elif isinstance(market, dict):
        ticker = str(market.get("ticker") or market.get("market_ticker") or "")
    else:
        ticker = str(getattr(market, "ticker", "") or "")
    if not ticker:
        return False
    ticker_upper = ticker.upper()
    return any(ticker_upper.startswith(p) for p in _PACKAGED_PREFIXES)


def normalized_market(market: dict) -> dict:
    """Attach category and packaged flag to a market dict without mutating caller's reference."""
    out = dict(market or {})
    out["category"] = detect_category(out.get("ticker"), out.get("title") or out.get("event_title"))
    out["is_packaged"] = is_packaged_market(out)
    return out


# Back-compat aliases used elsewhere in the codebase.
detect_market_category = detect_category
classify_market = normalized_market
