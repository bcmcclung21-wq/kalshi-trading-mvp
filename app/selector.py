from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import random
from collections import defaultdict
from typing import Any

from app.classifier import normalized_market, is_packaged_market
from app.research import build_research_envelope
from app.strategy import SPORTS, TUNING


def has_valid_orderbook(orderbook: dict[str, Any]) -> bool:
    if not orderbook:
        return False

    yes_bids = orderbook.get("yes_bids") or orderbook.get("yes") or []
    yes_asks = orderbook.get("yes_asks") or []
    no_bids = orderbook.get("no_bids") or orderbook.get("no") or []
    no_asks = orderbook.get("no_asks") or []

    if not yes_bids and not yes_asks:
        return False
    if not no_bids and not no_asks:
        return False

    return True


def has_market_liquidity(market: dict[str, Any]) -> bool:
    return True


def validate_market_candidate(market: dict[str, Any], orderbook: dict[str, Any]) -> tuple[bool, str]:
    if not has_valid_orderbook(orderbook):
        return False, "invalid_orderbook"

    return True, "valid"


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
    normalized: list[dict[str, Any]] = []
    for market in markets:
        nm = normalized_market(market)
        if nm and str(nm.get("ticker") or ""):
            normalized.append(nm)
    return normalized


def _parse_close_dt(market: dict[str, Any]) -> datetime | None:
    close_value = market.get("close_time") or market.get("expiration_time")
    if not close_value:
        return None
    try:
        text = str(close_value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def single_pool(markets: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    out = []
    rejects = {
        "wrong_market_type": 0,
        "wrong_category": 0,
        "no_liquidity_sign": 0,
        "too_close_to_close": 0,
        "too_far_to_close": 0,
        "not_same_day_settlement": 0,
        "missing_close_time": 0,
        "packaged_market": 0,
    }
    valid_categories = {"sports", "politics", "crypto", "climate", "economics"}
    today_utc = datetime.now(timezone.utc).date()

    for market in markets:
        if market.get("market_type") != "single":
            rejects["wrong_market_type"] += 1
            continue
        if market.get("category") not in valid_categories:
            rejects["wrong_category"] += 1
            continue
        if is_packaged_market(market):
            rejects["packaged_market"] += 1
            continue

        minutes = market.get("minutes_to_close")
        if minutes is None:
            rejects["missing_close_time"] += 1
            continue

        if minutes < TUNING.min_minutes_to_close:
            rejects["too_close_to_close"] += 1
            continue

        if minutes > (TUNING.max_days_to_close * 1440):
            rejects["too_far_to_close"] += 1
            continue

        if TUNING.same_day_only:
            close_dt = _parse_close_dt(market)
            if close_dt is None:
                rejects["missing_close_time"] += 1
                continue
            if close_dt.date() != today_utc:
                rejects["not_same_day_settlement"] += 1
                continue

        out.append(market)
    return out, rejects


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


def diversified_pool(markets: list[dict[str, Any]], max_total: int, per_category: int) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for market in markets:
        grouped[str(market.get("category") or "unknown")].append(market)
    out: list[dict[str, Any]] = []
    categories = list(grouped.keys())
    random.shuffle(categories)
    for category in categories:
        bucket = grouped[category]
        random.shuffle(bucket)
        out.extend(bucket[:per_category])
    random.shuffle(out)
    return out[:max_total]


def _extract_prices(levels: list[Any]) -> list[float]:
    out: list[float] = []
    for level in levels or []:
        if not isinstance(level, dict):
            continue
        try:
            price = float(level.get("price"))
        except (TypeError, ValueError):
            continue
        if 0.0 < price < 1.0:
            out.append(price)
    return out


def best_bid(levels: list[Any]) -> float:
    prices = _extract_prices(levels)
    return max(prices) if prices else 0.0


def best_ask(levels: list[Any]) -> float:
    prices = _extract_prices(levels)
    return min(prices) if prices else 0.0


def _best_quote_side(orderbook: dict[str, Any]) -> tuple[str, float, float] | None:
    yes_bids = list(orderbook.get("yes_bids") or orderbook.get("yes") or [])
    yes_asks = list(orderbook.get("yes_asks") or [])
    no_bids = list(orderbook.get("no_bids") or orderbook.get("no") or [])
    no_asks = list(orderbook.get("no_asks") or [])

    yes_bid = best_bid(yes_bids)
    yes_ask = best_ask(yes_asks)
    no_bid = best_bid(no_bids)
    no_ask = best_ask(no_asks)

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


def build_candidate(market: dict[str, Any], orderbook: dict[str, Any], manual_note: dict[str, Any] | None = None) -> tuple[Candidate | None, str | None]:
    market = normalized_market(market)
    if not market:
        return None, "invalid_market"
    if market["market_type"] == "combo" and (not TUNING.allow_combos or market["category"] != SPORTS):
        return None, "unsupported_combo"
    if market["market_type"] == "single" and is_packaged_market(market):
        return None, "packaged_market"
    quote = _best_quote_side(orderbook)
    if quote is None:
        return None, "invalid_orderbook"
    side, entry_price, spread_cents = quote
    if spread_cents > TUNING.max_spread_cents:
        return None, "bad_spread"
    if entry_price <= 0 or entry_price >= 0.95:
        return None, "bad_price"

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
        return None, "failed_projection"
    if envelope.confidence_score < TUNING.min_confidence_score:
        return None, "low_confidence"
    if total_score < threshold:
        return None, "low_total_score"
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
            "features": getattr(envelope, "features", None) or {},
            "estimated_win_probability": getattr(envelope, "estimated_win_probability", 0.0),
            "expected_value": getattr(envelope, "expected_value", 0.0),
            "learning_multiplier": getattr(envelope, "learning_multiplier", 1.0),
            "learning_components": getattr(envelope, "learning_components", {}) or {},
        },
    ), None


def rank_candidates(candidates: list[Candidate]) -> list[Candidate]:
    singles = [c for c in candidates if c.market_type == "single"]
    combos = [c for c in candidates if c.market_type == "combo"]
    singles.sort(key=lambda c: (c.total_score, c.confidence_score, -c.spread_cents, -c.ev_bonus), reverse=True)
    combos.sort(key=lambda c: (c.total_score, c.confidence_score, -c.spread_cents, -c.ev_bonus), reverse=True)
    return singles + combos
