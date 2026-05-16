from __future__ import annotations

import json
import math

from app.models import OrderRecord
from app.risk import contract_count, trade_notional
from app.strategy import TUNING, bankroll_pct


async def execute_candidate(exchange, db, candidate, bankroll_usd: float) -> OrderRecord | None:
    edge = float((candidate.details or {}).get("edge") or 0.0)
    fair = float((candidate.details or {}).get("fair_probability") or candidate.entry_price or 0.0)
    fees = 0.02 * fair
    net_edge = edge - fees
    if net_edge <= 0:
        return None
    notional = trade_notional(bankroll_usd=bankroll_usd, legs=candidate.legs)
    notional = min(notional, getattr(TUNING, 'max_order_notional_usd', 999999.0))
    count = max(0, math.floor(notional / candidate.entry_price)) if candidate.entry_price > 0 else 0
    if count <= 0:
        return None
    payload = {"status": "dry_run"}
    if TUNING.auto_execute:
        payload = await exchange.place_order(
            ticker=candidate.ticker,
            side=candidate.side,
            count=count,
            price_cents=int(round(candidate.entry_price * 100)),
        )
    details = candidate.details or {}
    features = details.get("features") or {}
    win_prob = float(details.get("estimated_win_probability") or 0.0)
    order = OrderRecord(
        ticker=candidate.ticker,
        category=candidate.category,
        side=candidate.side,
        market_type=candidate.market_type,
        legs=candidate.legs,
        count=count,
        price_cents=int(round(candidate.entry_price * 100)),
        bankroll_pct=bankroll_pct(candidate.legs),
        status=str(payload.get("status") or ("submitted" if TUNING.auto_execute else "dry_run")),
        kalshi_order_id=str(payload.get("order_id") or payload.get("id") or ""),
        dry_run=not TUNING.auto_execute,
        rationale=candidate.rationale,
        raw_json=json.dumps(payload),
        features_json=json.dumps(features),
        estimated_win_probability=win_prob,
    )
    db.add(order)
    return order
