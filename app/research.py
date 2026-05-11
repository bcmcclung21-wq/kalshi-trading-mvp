"""Confidence-led, cross-category research scoring.

Replaces the old per-category hardcoded confirmation logic with a single
unified model that works the same way across sports, politics, crypto,
climate, and economics. The category becomes a feature bucket the learning
engine uses to adjust scores based on actual settled-bet performance, NOT
a hardcoded multiplier that assumes one category is inherently better.

    final_confidence = base_confidence * learned_multiplier
    final_score      = final_confidence + ev_bonus

base_confidence is built from:
  - price_quality        (markets near 50/50 are more informative)
  - liquidity_quality    (volume, open interest, spread)
  - time_quality         (markets resolving in a useful window)
  - clarity_quality      (clearly defined resolution criterion)

learned_multiplier starts at 1.0 and adjusts up/down per bucket once
enough trades have settled.

ev_bonus uses Kelly EV adjusted for spread cost.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.classifier import detect_category
from app.learning import bucket_features, get_learning_engine


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


CLARITY_KEYWORDS: tuple[str, ...] = (
    "win", "winner", "settle", "above", "below", "over", "under",
    "cover", "score", "goal", "yes", "no", "exceed", "reach", "hit",
    "elected", "approved", "passed", "exact", "between", "range",
)


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


def implied_edge(entry_price: float, win_probability: float) -> float:
    return float(win_probability) - max(0.01, min(0.99, float(entry_price or 0.0)))


def kelly_expected_value(entry_price: float, win_probability: float, spread_cents: float) -> float:
    p = max(0.01, min(0.99, float(entry_price or 0.0)))
    w = max(0.01, min(0.99, float(win_probability or 0.0)))
    payoff_if_win = (1.0 / p) - 1.0
    raw_ev = w * payoff_if_win - (1.0 - w)
    adjusted_ev = raw_ev - (float(spread_cents or 0.0) / 200.0)
    if adjusted_ev <= 0:
        return 0.0
    return round(min(15.0, adjusted_ev * 30.0), 2)


def estimate_win_probability(
    entry_price: float,
    base_confidence: float,
    learning_multiplier: float,
) -> float:
    p_market = max(0.01, min(0.99, float(entry_price or 0.0)))
    confidence_normalized = max(0.0, min(1.0, float(base_confidence or 0.0) / 100.0))
    centered = (confidence_normalized - 0.5)
    learned_signal = (learning_multiplier - 1.0)
    deviation = (centered * 0.20) + (learned_signal * 0.15)
    estimate = p_market + deviation
    return max(0.02, min(0.98, estimate))


def build_research_envelope(
    market: dict[str, Any],
    entry_price: float,
    spread_cents: float,
    volume: float,
    oi: float,
    manual_note: dict[str, Any] | None = None,
) -> ResearchEnvelope:
    category = detect_category(market)
    minutes_to_close = market.get("minutes_to_close")

    price_q = price_quality(entry_price)
    liq_q = liquidity_quality(volume, oi, spread_cents)
    time_q = time_quality(minutes_to_close)
    clarity_q = clarity_score(market)

    base_confidence = (
        price_q * 0.25
        + liq_q * 0.35
        + time_q * 0.20
        + clarity_q * 0.20
    )
    base_confidence = round(min(100.0, base_confidence), 2)

    learning = get_learning_engine().adjustment_for(
        category=category,
        entry_price=entry_price,
        spread_cents=spread_cents,
        minutes_to_close=minutes_to_close,
        confidence=base_confidence,
    )
    learning_multiplier = float(learning.get("multiplier") or 1.0)

    win_prob = estimate_win_probability(entry_price, base_confidence, learning_multiplier)
    edge = implied_edge(entry_price, win_prob)
    ev = kelly_expected_value(entry_price, win_prob, spread_cents)

    projection_score = round(min(100.0, base_confidence * learning_multiplier), 2)
    research_score = liq_q
    confirmation_score = round(min(100.0, (time_q * 0.5) + (clarity_q * 0.5)), 2)
    confidence_score = round(min(100.0, base_confidence * learning_multiplier), 2)

    features = bucket_features(category, entry_price, spread_cents, minutes_to_close, base_confidence)

    rationale_parts = [
        f"price_q={price_q}",
        f"liq_q={liq_q}",
        f"time_q={time_q}",
        f"clarity_q={clarity_q}",
        f"mult={learning_multiplier:.2f}",
        f"win_p={win_prob:.3f}",
        f"edge={edge:.3f}",
    ]
    if learning.get("trusted"):
        rationale_parts.append("learned")
    rationale = " ".join(rationale_parts)

    tags: list[str] = []
    if spread_cents > 8:
        tags.append("wider_spread")
    if volume < 75:
        tags.append("lighter_volume")
    if learning_multiplier >= 1.10:
        tags.append("favorable_priors")
    elif learning_multiplier <= 0.90:
        tags.append("unfavorable_priors")
    if ev >= 5.0:
        tags.append("positive_ev")

    if manual_note:
        projection_score = max(projection_score, float(manual_note.get("projection_score") or 0.0))
        research_score = max(research_score, float(manual_note.get("research_score") or 0.0))
        confirmation_score = max(confirmation_score, float(manual_note.get("confirmation_score") or 0.0))
        confidence_score = max(confidence_score, float(manual_note.get("confidence_score") or 0.0))
        ev = max(ev, float(manual_note.get("ev_bonus") or 0.0))
        manual_rationale = str(manual_note.get("rationale") or "").strip()
        if manual_rationale:
            rationale = f"{rationale} | manual:{manual_rationale}"
        tags.extend(list(manual_note.get("tags") or []))

    return ResearchEnvelope(
        projection_score=projection_score,
        research_score=research_score,
        confidence_score=confidence_score,
        confirmation_score=confirmation_score,
        ev_bonus=round(min(15.0, ev), 2),
        rationale=rationale,
        tags=sorted(set(tags)),
        learning_multiplier=learning_multiplier,
        learning_components=learning.get("components") or {},
        estimated_win_probability=round(win_prob, 4),
        expected_value=round(edge, 4),
        features=features,
    )
