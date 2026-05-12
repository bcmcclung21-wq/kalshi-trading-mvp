from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import random
from collections import defaultdict
import logging
import os
import re
from zoneinfo import ZoneInfo
from typing import Any

from app.classifier import normalized_market, is_packaged_market
from app.research import build_research_envelope, group_ladder_markets
from app.strategy import SPORTS, TUNING

logger = logging.getLogger(__name__)

MARKET_TZ = ZoneInfo(TUNING.market_timezone)
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_LADDER_ROOT_RE = re.compile(r"^(tc-temp-[a-z0-9-]+high-\d{4}-\d{2}-\d{2})-", re.IGNORECASE)


def _extract_market_date(market: dict[str, Any]) -> date | None:
    for key in ("ticker", "event_ticker", "title", "subtitle"):
        text = str(market.get(key) or "")
        m = _DATE_RE.search(text)
        if not m:
            continue
        try:
            return date.fromisoformat(m.group(1))
        except ValueError:
            continue
    return None


def _best_effort_minutes(market: dict[str, Any], close_dt: datetime | None, now: datetime) -> float | None:
    if close_dt is not None:
        return (close_dt - now).total_seconds() / 60.0
    raw = market.get("minutes_to_close")
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


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


def _settlement_window_ok(close_dt: datetime, now: datetime) -> bool:
    minutes = (close_dt - now).total_seconds() / 60.0
    if minutes < TUNING.min_minutes_to_close:
        return False
    if minutes > TUNING.max_settlement_window_hours * 60:
        return False
    return True


def single_pool(markets: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    out = []
    rejects = {
        "wrong_market_type": 0,
        "wrong_category": 0,
        "no_liquidity_sign": 0,
        "too_close_to_close": 0,
        "too_far_to_close": 0,
        "outside_settlement_window": 0,
        "not_same_day_settlement": 0,
        "sports_not_today": 0,
        "missing_close_time": 0,
        "packaged_market": 0,
    }
    valid_categories = {"sports", "politics", "crypto", "climate", "economics"}
    now = datetime.now(timezone.utc)
    today_market_tz = datetime.now(MARKET_TZ).date()
    today_utc = datetime.now(timezone.utc).date()

    for market in markets:
        cat = str(market.get("category") or "unknown").lower()
        if cat == "sports":
            max_hours = 24
        elif cat == "politics":
            max_hours = 168
        elif cat == "economics":
            max_hours = 48
        elif cat == "crypto":
            max_hours = 72
        elif cat == "climate":
            max_hours = 48
        else:
            max_hours = TUNING.max_settlement_window_hours
        max_minutes = max_hours * 60

        if market.get("market_type") != "single":
            rejects["wrong_market_type"] += 1
            continue
        if market.get("category") not in valid_categories:
            rejects["wrong_category"] += 1
            continue
        if is_packaged_market(market):
            rejects["packaged_market"] += 1
            continue

        close_dt = _parse_close_dt(market)
        minutes = _best_effort_minutes(market, close_dt, now)
        if minutes is None:
            rejects["missing_close_time"] += 1
            continue
        if minutes < TUNING.min_minutes_to_close:
            rejects["too_close_to_close"] += 1
            continue
        if cat == "sports":
            market_dt = market.get("close_date") or market.get("end_date")
            market_date = None
            if hasattr(market_dt, "date"):
                market_date = market_dt.date()
            elif market_dt:
                parsed = _parse_iso(market_dt)
                if parsed is not None:
                    market_date = parsed.astimezone(timezone.utc).date()
            if market_date is None and close_dt is not None:
                market_date = close_dt.astimezone(timezone.utc).date()
            if market_date != today_utc:
                rejects["sports_not_today"] += 1
                continue
        elif minutes > max_minutes:
            rejects["too_far_to_close"] += 1
            continue

        enforce_same_day = TUNING.same_day_only if cat != "sports" else TUNING.sports_same_day_only
        if enforce_same_day and cat != "sports":
            market_date = _extract_market_date(market)
            if market_date is not None:
                compare_date = market_date
            elif close_dt is not None:
                compare_date = close_dt.astimezone(MARKET_TZ).date()
            else:
                rejects["missing_close_time"] += 1
                continue
            if compare_date != today_market_tz:
                rejects["not_same_day_settlement"] += 1
                continue

        out.append(market)

    sports_kept = sum(1 for m in out if str(m.get("category") or "").lower() == "sports")
    climate_kept = sum(1 for m in out if str(m.get("category") or "").lower() == "climate")
    politics_kept = sum(1 for m in out if str(m.get("category") or "").lower() == "politics")
    logger.info("pool_category_breakdown sports_kept=%d climate_kept=%d politics_kept=%d", sports_kept, climate_kept, politics_kept)
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


def diversified_pool(markets, max_total, per_category):
    from collections import defaultdict
    grouped = defaultdict(list)
    for market in markets:
        grouped[str(market.get("category") or "unknown")].append(market)
    out = []
    for category in list(grouped.keys()):
        bucket = grouped[category]
        out.extend(bucket[:per_category])
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
    """
    Select the best executable side (YES or NO) from an orderbook.

    Uses full binary complement reconstruction to derive effective prices,
    then computes spread from those effective prices. Handles edge cases:
    - Missing bids/asks on one side (reconstructed from complement)
    - Zero prices (treated as valid, not falsy)
    - Inverted markets (ask < bid) detected and rejected

    Returns: (side, entry_price, spread_cents) or None if untradeable
    """
    yes_bids = list(orderbook.get("yes_bids") or orderbook.get("yes") or [])
    yes_asks = list(orderbook.get("yes_asks") or [])
    no_bids = list(orderbook.get("no_bids") or orderbook.get("no") or [])
    no_asks = list(orderbook.get("no_asks") or [])

    # Native prices from orderbook
    yes_bid_native = best_bid(yes_bids)
    yes_ask_native = best_ask(yes_asks)
    no_bid_native = best_bid(no_bids)
    no_ask_native = best_ask(no_asks)

    # Derive missing prices from the other side's complement BEFORE computing spreads.
    yes_bid = yes_bid_native
    yes_ask = yes_ask_native
    no_bid = no_bid_native
    no_ask = no_ask_native

    if yes_ask <= 0.0 and no_bid > 0.0:
        yes_ask = 1.0 - no_bid
    if yes_bid <= 0.0 and no_ask > 0.0:
        yes_bid = 1.0 - no_ask
    if no_ask <= 0.0 and yes_bid > 0.0:
        no_ask = 1.0 - yes_bid
    if no_bid <= 0.0 and yes_ask > 0.0:
        no_bid = 1.0 - yes_ask

    yes_bid = max(0.0, min(0.99, yes_bid))
    yes_ask = max(0.0, min(0.99, yes_ask))
    no_bid = max(0.0, min(0.99, no_bid))
    no_ask = max(0.0, min(0.99, no_ask))

    yes_executable = yes_ask > 0.0
    no_executable = no_ask > 0.0

    if not yes_executable and not no_executable:
        return None

    def _compute_spread(ask: float, bid: float, executable: bool) -> float:
        """Compute spread in cents. Returns 999.0 for non-executable or inverted."""
        if not executable or ask <= 0.0:
            return 999.0
        if bid < 0.0:
            return 999.0
        if ask < bid:
            return 999.0
        if bid == 0.0:
            return ask * 100.0
        return (ask - bid) * 100.0

    yes_spread = _compute_spread(yes_ask, yes_bid, yes_executable)
    no_spread = _compute_spread(no_ask, no_bid, no_executable)

    yes_quality = abs(yes_ask - 0.5) + (yes_spread / 100.0)
    no_quality = abs(no_ask - 0.5) + (no_spread / 100.0)

    if yes_executable and yes_quality <= no_quality:
        return ("YES", yes_ask, yes_spread)
    if no_executable:
        return ("NO", no_ask, no_spread)
    return None


def _family_key(market: dict[str, Any]) -> str:
    ticker = str(market.get("ticker") or "").lower()
    event = str(market.get("event_ticker") or "").lower()
    mt = _LADDER_ROOT_RE.match(ticker)
    return mt.group(1) if mt else event


def build_candidate(
    market: dict[str, Any],
    orderbook: dict[str, Any],
    all_markets: list[dict[str, Any]] | None = None,
    sibling_markets: list[dict[str, Any]] | None = None,
    manual_note: dict[str, Any] | None = None,
) -> tuple[Candidate | None, str | None]:
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
    # Extract native prices from orderbook for logging/context
    yes_bid_native = best_bid(list(orderbook.get("yes_bids") or orderbook.get("yes") or []))
    yes_ask_native = best_ask(list(orderbook.get("yes_asks") or []))
    no_bid_native = best_bid(list(orderbook.get("no_bids") or orderbook.get("no") or []))
    no_ask_native = best_ask(list(orderbook.get("no_asks") or []))

    # Compute effective prices using IDENTICAL logic to _best_quote_side().
    yes_bid_eff = yes_bid_native
    yes_ask_eff = yes_ask_native
    no_bid_eff = no_bid_native
    no_ask_eff = no_ask_native

    if yes_ask_eff <= 0.0 and no_bid_eff > 0.0:
        yes_ask_eff = 1.0 - no_bid_eff
    if yes_bid_eff <= 0.0 and no_ask_eff > 0.0:
        yes_bid_eff = 1.0 - no_ask_eff
    if no_ask_eff <= 0.0 and yes_bid_eff > 0.0:
        no_ask_eff = 1.0 - yes_bid_eff
    if no_bid_eff <= 0.0 and yes_ask_eff > 0.0:
        no_bid_eff = 1.0 - yes_ask_eff

    yes_bid_eff = max(0.0, min(0.99, yes_bid_eff))
    yes_ask_eff = max(0.0, min(0.99, yes_ask_eff))
    no_bid_eff = max(0.0, min(0.99, no_bid_eff))
    no_ask_eff = max(0.0, min(0.99, no_ask_eff))

    market["yes_bid"] = yes_bid_eff
    market["yes_ask"] = yes_ask_eff
    market["no_bid"] = no_bid_eff
    market["no_ask"] = no_ask_eff
    market["yes_bid_native"] = yes_bid_native
    market["yes_ask_native"] = yes_ask_native
    market["no_bid_native"] = no_bid_native
    market["no_ask_native"] = no_ask_native

    if spread_cents > TUNING.max_spread_cents:
        return None, "bad_spread"
    if entry_price <= 0.0 or entry_price >= 0.95:
        return None, "bad_price"

    candidate_siblings = sibling_markets or []
    if not candidate_siblings and all_markets:
        ladders = group_ladder_markets(all_markets)
        candidate_siblings = ladders.get(_family_key(market), [])
    envelope = build_research_envelope(
        market=market,
        entry_price=entry_price,
        spread_cents=spread_cents,
        volume=float(market.get("volume") or 0.0),
        oi=float(market.get("open_interest") or 0.0),
        side=side,
        sibling_markets=candidate_siblings,
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
    logger.info(
        "candidate_model ticker=%s family=%s siblings=%d model=%s fair=%.4f edge=%.4f projection=%.2f confidence=%.2f total=%.2f",
        market.get("ticker"),
        _family_key(market),
        len(candidate_siblings),
        envelope.projection_model,
        float(envelope.fair_probability),
        float(envelope.edge),
        envelope.projection_score,
        envelope.confidence_score,
        round(total_score, 2),
    )
    if not envelope.projection_supported:
        return None, "unsupported_projection_model"
    category = str(market.get("category") or "unknown").lower()
    if category == "sports":
        mid = (yes_ask_eff + yes_bid_eff) / 2.0 if yes_ask_eff > 0 and yes_bid_eff >= 0 else entry_price
        depth_levels = len(orderbook.get("yes_bids") or []) + len(orderbook.get("yes_asks") or [])
        mins = float(market.get("minutes_to_close") or 120.0)
        time_boost = max(0.0, min(25.0, (240.0 - mins) / 12.0))
        depth_boost = max(0.0, min(25.0, depth_levels * 2.5))
        envelope.confidence_score = max(envelope.confidence_score, 50.0 + time_boost + depth_boost)
        if spread_cents > 5.0:
            return None, "sports_spread_too_wide"
        envelope.fair_probability = mid
        envelope.edge = max(0.0, abs(float(envelope.fair_probability) - float(entry_price)))
        envelope.projection_model = "SportsFairValue"
    elif category == "politics":
        implied = (yes_ask_eff + yes_bid_eff) / 2.0 if yes_ask_eff > 0 and yes_bid_eff >= 0 else entry_price
        envelope.fair_probability = implied
        envelope.edge = max(0.0, abs(float(envelope.fair_probability) - float(entry_price)))
        envelope.projection_model = "MarketImplied"
        if envelope.confidence_score < 60:
            return None, "low_confidence"
        if envelope.edge < 0.03:
            return None, "low_edge"

    cat_edge = (getattr(TUNING, 'category_edge_bps', None) or {}).get(category, -1)
    min_edge_bps = cat_edge if cat_edge > 0 else TUNING.min_edge_bps
    fair_gap = abs(float(envelope.fair_probability) - float(entry_price))
    if (entry_price <= TUNING.extreme_price_min or entry_price >= TUNING.extreme_price_max) and (float(envelope.edge) * 10000.0 < min_edge_bps):
        return None, "extreme_price_without_edge"
    if float(envelope.edge) * 10000.0 < min_edge_bps:
        return None, "low_edge"
    sports_min_gap = 0.02 if category == "sports" and spread_cents <= 5.0 and envelope.confidence_score >= 60 else TUNING.min_fair_prob_gap
    if fair_gap < sports_min_gap:
        return None, "low_fair_prob_gap"
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
            "fair_probability": getattr(envelope, "fair_probability", 0.0),
            "edge": getattr(envelope, "edge", 0.0),
            "projection_model": getattr(envelope, "projection_model", "unknown"),
        },
    ), None


def rank_candidates(candidates: list[Candidate]) -> list[Candidate]:
    singles = [c for c in candidates if c.market_type == "single"]
    combos = [c for c in candidates if c.market_type == "combo"]
    singles.sort(key=lambda c: (c.total_score, c.confidence_score, -c.spread_cents, -c.ev_bonus), reverse=True)
    combos.sort(key=lambda c: (c.total_score, c.confidence_score, -c.spread_cents, -c.ev_bonus), reverse=True)
    return singles + combos
