from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.classifier import detect_category


@dataclass(slots=True)
class ResearchEnvelope:
    projection_score: float
    research_score: float
    confidence_score: float
    confirmation_score: float
    ev_bonus: float
    rationale: str
    tags: list[str]


CATEGORY_CONFIRMATION_BASE = {
    "sports": 70.0,
    "politics": 62.0,
    "crypto": 60.0,
    "climate": 63.0,
    "economics": 62.0,
}


def market_quality_score(volume: float, oi: float, spread_cents: float, minutes_to_close: float | None) -> float:
    score = min(volume / 8.0, 35.0) + min(oi / 8.0, 25.0)
    if spread_cents <= 4:
        score += 20.0
    elif spread_cents <= 8:
        score += 14.0
    elif spread_cents <= 12:
        score += 8.0
    if minutes_to_close is not None:
        if 60 <= minutes_to_close <= 60 * 24 * 4:
            score += 15.0
        elif 20 <= minutes_to_close <= 60 * 24 * 14:
            score += 10.0
    return min(score, 100.0)


def price_quality_bonus(entry_price: float) -> float:
    if 0.38 <= entry_price <= 0.62:
        return 8.0
    if 0.30 <= entry_price <= 0.70:
        return 5.0
    return 1.0


def event_projection_proxy(category: str, market: dict[str, Any]) -> float:
    title = str(market.get("title") or "").lower()
    base = 58.0
    if category == "sports":
        if any(word in title for word in ["win", "cover", "over", "under", "score", "goal"]):
            base += 10.0
    elif category == "politics":
        if any(word in title for word in ["election", "vote", "approval", "win"]):
            base += 6.0
    elif category == "crypto":
        if any(word in title for word in ["above", "below", "settle", "range"]):
            base += 6.0
    elif category == "climate":
        if any(word in title for word in ["temperature", "rain", "snow", "wind"]):
            base += 6.0
    elif category == "economics":
        if any(word in title for word in ["cpi", "jobs", "rate", "gdp"]):
            base += 6.0
    return min(base, 100.0)


def ev_bonus(entry_price: float, spread_cents: float) -> float:
    bonus = 0.0
    if 0.35 <= entry_price <= 0.60:
        bonus += 4.0
    if spread_cents <= 6:
        bonus += 3.0
    return min(bonus, 7.0)


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

    base_projection = event_projection_proxy(category, market)
    base_research = market_quality_score(volume, oi, spread_cents, minutes_to_close)
    base_confirmation = CATEGORY_CONFIRMATION_BASE.get(category, 58.0)
    base_confidence = min((base_projection * 0.45) + (base_research * 0.35) + (base_confirmation * 0.20) + price_quality_bonus(entry_price), 100.0)
    base_ev = ev_bonus(entry_price, spread_cents)
    rationale = "market-quality and category-aware projection scoring"
    tags: list[str] = []

    if manual_note:
        base_projection = max(base_projection, float(manual_note.get("projection_score") or 0.0))
        base_research = max(base_research, float(manual_note.get("research_score") or 0.0))
        base_confirmation = max(base_confirmation, float(manual_note.get("confirmation_score") or 0.0))
        base_confidence = max(base_confidence, float(manual_note.get("confidence_score") or 0.0))
        base_ev = max(base_ev, float(manual_note.get("ev_bonus") or 0.0))
        rationale = str(manual_note.get("rationale") or rationale)
        tags = list(manual_note.get("tags") or [])

    if spread_cents > 8:
        tags.append("wider_spread")
    if volume < 75:
        tags.append("lighter_volume")

    return ResearchEnvelope(
        projection_score=round(min(base_projection, 100.0), 2),
        research_score=round(min(base_research, 100.0), 2),
        confidence_score=round(min(base_confidence, 100.0), 2),
        confirmation_score=round(min(base_confirmation, 100.0), 2),
        ev_bonus=round(min(base_ev, 10.0), 2),
        rationale=rationale,
        tags=sorted(set(tags)),
    )
