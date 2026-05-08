from __future__ import annotations

import json

from app.models import OrderRecord
from app.risk import contract_count
from app.strategy import TUNING, bankroll_pct


async def execute_candidate(kalshi, db, candidate, bankroll_usd: float) -> OrderRecord | None:
    count = contract_count(bankroll_usd=bankroll_usd, legs=candidate.legs, entry_price=candidate.entry_price)
    if count <= 0:
        return None
    payload = {"status": "dry_run"}
    if TUNING.auto_execute:
        payload = await kalshi.place_order(
            ticker=candidate.ticker,
            side=candidate.side,
            count=count,
            price_cents=int(round(candidate.entry_price * 100)),
        )
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
        kalshi_order_id=str(payload.get("order_id") or ""),
        dry_run=not TUNING.auto_execute,
        rationale=candidate.rationale,
        raw_json=json.dumps(payload),
    )
    db.add(order)
    return order
