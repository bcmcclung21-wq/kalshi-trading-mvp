from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select

from app.db import SessionLocal
from app.models import MarketSnapshot
from app.selector import normalize_markets

logger = logging.getLogger(__name__)

SUPPORTED_MARKET_TYPES = {"single", "combo"}
SKIP_TICKER_PREFIXES = ("KXMVE",)


def is_supported_market(market: dict[str, Any]) -> bool:
    if not isinstance(market, dict):
        return False
    ticker = str(market.get("ticker") or "").strip()
    if not ticker:
        return False
    market_type = str(market.get("market_type") or "single").strip().lower()
    if market_type not in SUPPORTED_MARKET_TYPES:
        return False
    return True


def is_skippable_ticker(ticker: str) -> bool:
    ticker_clean = str(ticker or "").strip().upper()
    return not ticker_clean or ticker_clean.startswith(SKIP_TICKER_PREFIXES)


def persist_markets(raw_markets: list[dict]) -> int:
    normalized = normalize_markets(raw_markets)
    markets = [m for m in normalized if is_supported_market(m)]
    removed = len(raw_markets) - len(markets)
    logger.info("universe_filter total=%d kept=%d removed=%d", len(raw_markets), len(markets), removed)

    with SessionLocal() as db:
        for market in markets:
            row = db.execute(select(MarketSnapshot).where(MarketSnapshot.ticker == market["ticker"])).scalar_one_or_none()
            if row is None:
                row = MarketSnapshot(ticker=market["ticker"])
                db.add(row)
            row.event_ticker = str(market.get("event_ticker") or "")
            row.title = str(market.get("title") or "")
            row.subtitle = str(market.get("subtitle") or "")
            row.category = str(market.get("category") or "unknown")
            row.market_type = str(market.get("market_type") or "single")
            row.status = str(market.get("status") or "open")
            row.close_time = str(market.get("close_time") or "")
            row.volume = 0.0
            row.open_interest = 0.0
            row.last_price = float(market.get("last_price") or 0.0)
            row.raw_json = json.dumps(market)
        db.commit()

    logger.info("persist_markets saved=%d", len(markets))
    return len(markets)
