from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.classifier import normalized_market, is_packaged_market
from app.research import build_research_envelope
from app.strategy import SPORTS, TUNING


@dataclass(slots=True)
class Candidate:
    ticker: str
    category: str
    market_type: str
    legs: int
    side: str
    entry_price: float
    spread_cents: float
    projection_score: float
    research_score: float
    confidence_score: float
    confirmation_score: float
    ev_bonus: float
    total_score: float
    rationale: str
    details: dict[str, Any]


def normalize_markets(markets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [normalized_market(m) for m in markets if str(m.get("ticker") or "")]


def single_pool(markets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for market in markets:
        if market.get("market_type") != "single":
            continue
        if market.get("category") not in {"sports", "politics", "crypto", "climate", "economics"}:
            continue
        if float(market.get("volume") or 0.0) < TUNING.min_volume:
            continue
        if float(market.get("open_interest") or 0.0) < TUNING.min_open_interest:
            continue
        minutes = market.get("minutes_to_close")
        if minutes is not None and minutes < TUNING.min_minutes_to_close:
            continue
        out.append(market)
    return out


def combo_pool(markets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not TUNING.allow_combos:
        return []
    out = []
    for market in markets:
        if market.get("market_type") != "combo":
            continue
        if market.get("category") != SPORTS:
            continue
        if int(market.get("legs") or 1) > TUNING.max_combo_legs:
            continue
        out.append(market)
    return out


def _best_quote_side(orderbook: dict[str, Any]) -> tuple[str, float, float] | None:
    yes = list(orderbook.get("yes") or [])
    no = list(orderbook.get("no") or [])
    yes_bid = float((yes[0] or {}).get("price") or 0.0) if yes else 0.0
    yes_ask = float((yes[-1] or {}).get("price") or 0.0) if yes else 0.0
    no_bid = float((no[0] or {}).get("price") or 0.0) if no else 0.0
    no_ask = float((no[-1] or {}).get("price") or 0.0) if no else 0.0
    if yes_ask <= 0 and no_ask <= 0:
        return None
    yes_spread = max(0.0, (yes_ask - yes_bid) * 100) if yes_ask and yes_bid else 999.0
    no_spread = max(0.0, (no_ask - no_bid) * 100) if no_ask and no_bid else 999.0
    yes_quality = abs(yes_ask - 0.5) + (yes_spread / 100.0)
    no_quality = abs(no_ask - 0.5) + (no_spread / 100.0)
    if yes_ask > 0 and yes_quality <= no_quality:
        return ("YES", yes_ask, yes_spread)
    if no_ask > 0:
        return ("NO", no_ask, no_spread)
    return None


def build_candidate(market: dict[str, Any], orderbook: dict[str, Any], manual_note: dict[str, Any] | None = None) -> Candidate | None:
    market = normalized_market(market)
    if market["market_type"] == "combo" and (not TUNING.allow_combos or market["category"] != SPORTS):
        return None
    quote = _best_quote_side(orderbook)
    if quote is None:
        return None
    side, entry_price, spread_cents = quote
    if spread_cents > TUNING.max_spread_cents:
        return None
    if entry_price <= 0 or entry_price >= 0.95:
        return None

    envelope = build_research_envelope(
        market=market,
        entry_price=entry_price,
        spread_cents=spread_cents,
        volume=float(market.get("volume") or 0.0),
        oi=float(market.get("open_interest") or 0.0),
        manual_note=manual_note,
    )
    total_score = (
        (envelope.projection_score * 0.35)
        + (envelope.research_score * 0.25)
        + (envelope.confidence_score * 0.20)
        + (envelope.confirmation_score * 0.15)
        + envelope.ev_bonus
    )
    threshold = TUNING.min_total_score_combo if market["market_type"] == "combo" else TUNING.min_total_score_single
    if envelope.projection_score < TUNING.min_projection_score:
        return None
    if envelope.confidence_score < TUNING.min_confidence_score:
        return None
    if total_score < threshold:
        return None
    return Candidate(
        ticker=market["ticker"],
        category=str(market.get("category") or "unknown"),
        market_type=str(market.get("market_type") or "single"),
        legs=int(market.get("legs") or 1),
        side=side,
        entry_price=round(entry_price, 4),
        spread_cents=round(spread_cents, 2),
        projection_score=envelope.projection_score,
        research_score=envelope.research_score,
        confidence_score=envelope.confidence_score,
        confirmation_score=envelope.confirmation_score,
        ev_bonus=envelope.ev_bonus,
        total_score=round(total_score, 2),
        rationale=envelope.rationale,
        details={
            "tags": envelope.tags,
            "volume": market.get("volume"),
            "open_interest": market.get("open_interest"),
            "minutes_to_close": market.get("minutes_to_close"),
        },
    )


def rank_candidates(candidates: list[Candidate]) -> list[Candidate]:
    singles = [c for c in candidates if c.market_type == "single"]
    combos = [c for c in candidates if c.market_type == "combo"]
    singles.sort(key=lambda c: (c.total_score, c.confidence_score, -c.spread_cents, -c.ev_bonus), reverse=True)
    combos.sort(key=lambda c: (c.total_score, c.confidence_score, -c.spread_cents, -c.ev_bonus), reverse=True)
    return singles + combos
