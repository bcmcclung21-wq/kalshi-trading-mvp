from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import re

from app.classifier import detect_category
from app.learning import bucket_features, get_learning_engine
from app.projection_registry import project as project_market


@dataclass(slots=True)
class ResearchEnvelope:
    projection_score: float
    research_score: float
    confidence_score: float
    confirmation_score: float
    ev_bonus: float
    rationale: str
    tags: list[str]
    learning_multiplier: float = 1.0
    learning_components: dict[str, float] | None = None
    estimated_win_probability: float = 0.0
    expected_value: float = 0.0
    features: dict[str, str] | None = None
    fair_probability: float = 0.0
    edge: float = 0.0
    projection_supported: bool = False
    projection_model: str = "generic"
    ladder_consistency: float = 0.0


CLARITY_KEYWORDS: tuple[str, ...] = (
    "win", "winner", "settle", "above", "below", "over", "under",
    "cover", "score", "goal", "yes", "no", "exceed", "reach", "hit",
    "elected", "approved", "passed", "exact", "between", "range",
)
LADDER_TICKER_RE = re.compile(r"^(tc-temp-[a-z0-9-]+high-\d{4}-\d{2}-\d{2})-", re.IGNORECASE)


def group_ladder_markets(markets: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for m in markets:
        ticker = str(m.get("ticker") or "").lower()
        event = str(m.get("event_ticker") or "").lower()
        key = event
        mt = LADDER_TICKER_RE.match(ticker)
        if mt:
            key = mt.group(1)
        if key and ("temp" in ticker or "range" in ticker or "ladder" in ticker):
            grouped.setdefault(key, []).append(m)
    return grouped


def infer_band_probability(price_points: list[float]) -> float:
    vals = [max(0.01, min(0.99, float(v))) for v in price_points if v is not None]
    if not vals:
        return 0.5
    return sum(vals) / len(vals)


def compute_side_edge(side: str, entry_price: float, bin_probability: float) -> tuple[float, float]:
    fair = bin_probability if side == "YES" else (1.0 - bin_probability)
    return fair, fair - entry_price


def price_quality(entry_price: float) -> float:
    p = max(0.01, min(0.99, float(entry_price or 0.0)))
    distance_from_extreme = min(p, 1 - p)
    return round(100.0 * (distance_from_extreme / 0.50) ** 0.65, 2)


def liquidity_quality(volume: float, oi: float, spread_cents: float) -> float:
    vol_component = min(40.0, (float(volume or 0.0) / 8.0))
    oi_component = min(25.0, (float(oi or 0.0) / 8.0))
    if spread_cents <= 3:
        spread_component = 35.0
    elif spread_cents <= 6:
        spread_component = 28.0
    elif spread_cents <= 10:
        spread_component = 18.0
    elif spread_cents <= 15:
        spread_component = 10.0
    else:
        spread_component = 3.0
    return round(min(100.0, vol_component + oi_component + spread_component), 2)


def time_quality(minutes_to_close: float | None) -> float:
    if minutes_to_close is None:
        return 50.0
    m = float(minutes_to_close)
    if m < 20:
        return 25.0
    if m <= 60 * 6:
        return 95.0
    if m <= 60 * 24:
        return 90.0
    if m <= 60 * 24 * 3:
        return 80.0
    if m <= 60 * 24 * 7:
        return 65.0
    if m <= 60 * 24 * 30:
        return 45.0
    return 25.0


def clarity_score(market: dict[str, Any]) -> float:
    text = f"{market.get('title') or ''} {market.get('subtitle') or ''}".lower()
    hits = sum(1 for kw in CLARITY_KEYWORDS if kw in text)
    return min(100.0, 55.0 + hits * 8.0)


def build_research_envelope(
    market: dict[str, Any],
    entry_price: float,
    spread_cents: float,
    volume: float,
    oi: float,
    side: str = "YES",
    sibling_markets: list[dict[str, Any]] | None = None,
    manual_note: dict[str, Any] | None = None,
) -> ResearchEnvelope:
    category = detect_category(market)
    minutes_to_close = market.get("minutes_to_close")
    liq_q = liquidity_quality(volume, oi, spread_cents)
    time_q = time_quality(minutes_to_close)
    clarity_q = clarity_score(market)

    learning = get_learning_engine().adjustment_for(
        category=category,
        entry_price=entry_price,
        spread_cents=spread_cents,
        minutes_to_close=minutes_to_close,
        confidence=50.0,
    )
    learning_multiplier = float(learning.get("multiplier") or 1.0)

    projection_supported = False
    projection_model = "unsupported"
    fair_probability = entry_price
    edge = 0.0
    ladder_consistency = 0.0
    tags: list[str] = []

    if sibling_markets:
        implied = [float(m.get("entry_price") or m.get("midpoint") or m.get("last_price") or 0.0) for m in sibling_markets]
        base_probs = [max(0.01, min(0.99, p)) for p in implied if p > 0]
        if len(base_probs) >= 2:
            s = sum(base_probs)
            norm = [p / s for p in base_probs] if s > 0 else []
            smoothed = []
            for i, p in enumerate(norm):
                prev_p = norm[i - 1] if i > 0 else p
                next_p = norm[i + 1] if i < len(norm) - 1 else p
                smoothed.append((prev_p + (2 * p) + next_p) / 4.0)
            target = infer_band_probability(smoothed)
            fair_probability, edge = compute_side_edge(side, entry_price, target)
            ladder_consistency = max(0.0, 1.0 - abs(sum(smoothed) - 1.0))
            projection_supported = True
            projection_model = "ladder_range"

    if not projection_supported:
        yes_bid = float(market.get("yes_bid") or 0.0)
        yes_ask = float(market.get("yes_ask") or 0.0)
        no_bid = float(market.get("no_bid") or 0.0)
        no_ask = float(market.get("no_ask") or 0.0)
        if yes_ask <= 0 and no_bid > 0:
            yes_ask = max(0.01, min(0.99, 1.0 - no_bid))
        if no_ask <= 0 and yes_bid > 0:
            no_ask = max(0.01, min(0.99, 1.0 - yes_bid))

        has_yes_pair = yes_bid > 0 and yes_ask > 0 and yes_ask >= yes_bid
        spread_ok = spread_cents <= 6.0
        near_term_ok = (minutes_to_close is not None) and (20.0 <= float(minutes_to_close) <= (36.0 * 60.0))
        if has_yes_pair and spread_ok and near_term_ok and liq_q >= 45.0 and clarity_q >= 60.0:
            midpoint = max(0.01, min(0.99, (yes_bid + yes_ask) / 2.0))
            fair_probability, edge = compute_side_edge(side, entry_price, midpoint)
            ladder_consistency = 0.5
            projection_supported = True
            projection_model = "binary_quote_fallback"
            tags.append("fallback_binary_quote")

    if not projection_supported:
        ticker = str(market.get("ticker") or "")
        if ticker.startswith("tc-temp-"):
            pr = project_market(ticker, market, {})
            fair_probability, edge = compute_side_edge(side, entry_price, pr.fair_value)
            projection_supported = True
            projection_model = pr.metadata.get("source", "temperature_registry")
            ladder_consistency = max(0.0, min(1.0, pr.confidence))
            tags.append("temperature_registry")

    if not projection_supported:
        return ResearchEnvelope(
            projection_score=38.0,
            research_score=liq_q,
            confidence_score=round(min(100.0, (time_q * 0.5) + (clarity_q * 0.5)), 2),
            confirmation_score=round(min(100.0, (time_q * 0.5) + (clarity_q * 0.5)), 2),
            ev_bonus=0.0,
            rationale="fallback_projection_neutral",
            tags=["fallback_projection_neutral"],
            learning_multiplier=learning_multiplier,
            learning_components=learning.get("components") or {},
            estimated_win_probability=round(entry_price, 4),
            expected_value=0.0,
            features=bucket_features(category, entry_price, spread_cents, minutes_to_close, 50.0),
            fair_probability=round(entry_price, 4),
            edge=0.0,
            projection_supported=True,
            projection_model="fallback_midpoint",
            ladder_consistency=0.0,
        )

    edge_bps = edge * 10000.0
    projection_score = max(0.0, min(100.0, 50.0 + (edge_bps / 60.0) + (ladder_consistency * 20.0)))
    confidence_score = max(0.0, min(100.0, (time_q * 0.25) + (clarity_q * 0.25) + (liq_q * 0.30) + (ladder_consistency * 20.0)))
    ev_bonus = max(0.0, min(15.0, edge_bps / 100.0))

    return ResearchEnvelope(
        projection_score=round(projection_score, 2),
        research_score=liq_q,
        confidence_score=round(confidence_score, 2),
        confirmation_score=round(min(100.0, (time_q * 0.5) + (clarity_q * 0.5)), 2),
        ev_bonus=round(ev_bonus, 2),
        rationale=f"model={projection_model} fair={fair_probability:.4f} edge={edge:.4f} consistency={ladder_consistency:.3f}",
        tags=tags,
        learning_multiplier=learning_multiplier,
        learning_components=learning.get("components") or {},
        estimated_win_probability=round(fair_probability, 4),
        expected_value=round(edge, 4),
        features=bucket_features(category, entry_price, spread_cents, minutes_to_close, confidence_score),
        fair_probability=round(fair_probability, 4),
        edge=round(edge, 4),
        projection_supported=True,
        projection_model=projection_model,
        ladder_consistency=round(ladder_consistency, 4),
    )
