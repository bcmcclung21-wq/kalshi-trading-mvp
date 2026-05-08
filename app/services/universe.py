from __future__ import annotations

import json
import logging

from sqlalchemy import select

from app.db import SessionLocal
from app.models import MarketSnapshot
from app.selector import normalize_markets

logger = logging.getLogger(__name__)

SKIP_TICKER_PREFIXES = ("KXMVE",)


def is_skippable_ticker(ticker: str) -> bool:
    if not ticker:
        return True
    return ticker.startswith(SKIP_TICKER_PREFIXES)


def persist_markets(raw_markets: list[dict]) -> int:
    skipped_combo = sum(1 for m in raw_markets if is_skippable_ticker(str(m.get("ticker") or "")))
    markets = [m for m in raw_markets if not is_skippable_ticker(str(m.get("ticker") or ""))]
    logger.info(f"universe_filter skipped_combo={skipped_combo} kept={len(markets)}")
    markets = normalize_markets(markets)

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
            row.volume = float(market.get("volume") or 0.0)
            row.open_interest = float(market.get("open_interest") or 0.0)
            row.last_price = float(market.get("last_price") or 0.0)
            row.raw_json = json.dumps(market)
        db.commit()

    logger.info(f"persist_markets saved={len(markets)}")
    return len(markets)
