================================================================================
KALSHI BOT — IMPORT ERROR FIX
ALL-IN-ONE: file path, code, and deploy steps in one block
================================================================================

WHERE THIS GOES
---------------
Repo:  bcmcclung21-wq/kalshi-alpha-railway
File:  app/classifier.py   (REPLACE the existing file)

Direct GitHub URL to the existing file:
https://github.com/bcmcclung21-wq/kalshi-alpha-railway/blob/main/app/classifier.py

Direct GitHub URL to the parent folder (where you upload):
https://github.com/bcmcclung21-wq/kalshi-alpha-railway/tree/main/app


WHY
---
Crash loop. Log shows:
  ImportError: cannot import name 'is_packaged_market' from 'app.classifier'

selector.py imports a function that doesn't exist in classifier.py.
Fix: add it. This file below is the complete replacement.


HOW TO DEPLOY (iPhone, no Codex)
--------------------------------
1. Save the code below as a file named  classifier.py
   - Use Textastic, Working Copy, or Pretext (plain text, UTF-8).
   - DO NOT use Apple Notes or Pages — they corrupt quotes.
2. Open Safari to:
   https://github.com/bcmcclung21-wq/kalshi-alpha-railway/tree/main/app
3. Tap "Add file" → "Upload files".
4. Pick your classifier.py from the Files app.
   GitHub auto-overwrites the existing file (same name).
5. Commit message:
   fix(classifier): add is_packaged_market export to resolve ImportError
6. "Commit directly to the main branch" → Commit changes.
7. Railway auto-deploys. Watch logs for:
   INFO: Application startup complete.


================================================================================
===== START OF FILE classifier.py — copy everything below until END =====
================================================================================

"""
Market classifier for Kalshi bot v7.1.

Single source of truth for:
  - normalized_market(raw)        : shape a raw Kalshi market dict
  - is_packaged_market(market)    : detect KXMVE / packaged multileg
  - classify_category(market)     : map ticker prefix -> category bucket
  - is_singleton_binary(market)   : true if a clean YES/NO singleton

All downstream modules (selector, universe, kalshi client, engine)
import packaged-market detection from here. Do not redefine elsewhere.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


# ---------------------------------------------------------------------------
# Packaged / multileg ticker prefixes.
# Bundled multi-outcome markets (parlays, packages) that saturate fetch
# pagination and are not actionable under the singleton-binary strategy.
# MUST be filtered out of the trade universe.
# ---------------------------------------------------------------------------
PACKAGED_PREFIXES = (
    "KXMVE",        # packaged multi-leg (the saturating one)
    "KXNBAGAME",    # packaged NBA game bundle
    "KXNFLGAME",    # packaged NFL game bundle
    "KXMLBGAME",    # packaged MLB game bundle
    "KXNHLGAME",    # packaged NHL game bundle
    "KXBET",        # packaged bet bundle
    "KXSGP",        # same-game-parlay
    "KXPARLAY",     # generic parlay
    "KXCOMBO",      # combo
)


# ---------------------------------------------------------------------------
# Category prefix mapping. Used by classify_category().
# Keep aligned with UNIVERSE_TOP_N category targets in app/main.py.
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def normalized_market(raw: Any) -> Optional[Dict[str, Any]]:
    """
    Coerce a raw Kalshi market payload into a flat dict with the fields
    downstream code expects. Returns None if the input is unusable.
    """
    if not isinstance(raw, dict):
        return None

    ticker = raw.get("ticker") or raw.get("market_ticker")
    if not ticker or not isinstance(ticker, str):
        return None

    return {
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
        "category": raw.get("category") or "",
        "_raw": raw,
    }


def is_packaged_market(market: Any) -> bool:
    """
    True if the market is a packaged / multileg bundle that should be
    excluded from the singleton trade universe.

    Accepts either a raw Kalshi dict or a normalized_market() output.
    Defensive against None / malformed input.
    """
    if market is None:
        return False
    if not isinstance(market, dict):
        return False

    ticker = market.get("ticker") or market.get("market_ticker") or ""
    if not isinstance(ticker, str):
        return False
    ticker_upper = ticker.upper()

    # 1. Hard prefix match — primary defense, catches KXMVE saturation.
    for prefix in PACKAGED_PREFIXES:
        if ticker_upper.startswith(prefix):
            return True

    # 2. Event ticker prefix match — some packaged markets only show
    #    the bundle pattern at the event level.
    event_ticker = market.get("event_ticker") or ""
    if isinstance(event_ticker, str):
        event_upper = event_ticker.upper()
        for prefix in PACKAGED_PREFIXES:
            if event_upper.startswith(prefix):
                return True

    # 3. Title heuristic — last-resort catch for re-skinned packages.
    title = (market.get("title") or "").lower()
    subtitle = (market.get("subtitle") or "").lower()
    combined = f"{title} {subtitle}"
    packaged_keywords = (
        "parlay",
        "same game",
        "multi-leg",
        "multileg",
        "combo bet",
        "package",
    )
    for kw in packaged_keywords:
        if kw in combined:
            return True

    return False


def classify_category(market: Any) -> str:
    """
    Return one of: 'sports', 'politics', 'crypto', 'economics',
    'climate', or 'unknown'. Based on ticker prefix; falls back to
    Kalshi's own category field if prefix doesn't resolve.
    """
    if not isinstance(market, dict):
        return "unknown"

    ticker = (market.get("ticker") or market.get("market_ticker") or "").upper()
    event_ticker = (market.get("event_ticker") or "").upper()

    for category, prefixes in CATEGORY_PREFIXES.items():
        for prefix in prefixes:
            if ticker.startswith(prefix) or event_ticker.startswith(prefix):
                return category

    raw_cat = (market.get("category") or "").lower().strip()
    if raw_cat in CATEGORY_PREFIXES:
        return raw_cat
    if raw_cat in ("financial", "economy", "macro"):
        return "economics"
    if raw_cat in ("weather",):
        return "climate"

    return "unknown"


def is_singleton_binary(market: Any) -> bool:
    """
    True if the market is a clean YES/NO singleton suitable for
    the v7.1 strategy. Excludes packaged bundles automatically.
    """
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


# ---------------------------------------------------------------------------
# Backwards-compat exports.
# ---------------------------------------------------------------------------
__all__ = [
    "PACKAGED_PREFIXES",
    "CATEGORY_PREFIXES",
    "normalized_market",
    "is_packaged_market",
    "classify_category",
    "is_singleton_binary",
]

================================================================================
===== END OF FILE classifier.py =====
================================================================================


EXPECTED LOGS AFTER DEPLOY
--------------------------
GOOD:
  INFO: Started server process
  INFO: Application startup complete.
  INFO: Uvicorn running on http://0.0.0.0:PORT
  [engine] cycle_start mode=paper live_execution=false
  [funnel] fetch_markets_complete clean_markets=<N>

BAD (paste back if seen):
  ImportError: ...
  ModuleNotFoundError: ...


ROLLBACK
--------
GitHub → Commits tab → find commit BEFORE this fix → "..." → Revert.
Or Railway → Deployments → previous good deploy → Redeploy.
This change only ADDS code. Low regression risk.


NOTES
-----
- LIVE_EXECUTION stays false. Pre-live checklist not satisfied.
- Do not touch app/legacy/*.
- KXMVE pagination patch in app/main.py may still be pending —
  confirm next session after this deploys cleanly.
