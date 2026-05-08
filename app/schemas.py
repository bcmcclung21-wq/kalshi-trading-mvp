from __future__ import annotations

from pydantic import BaseModel, Field


class ResearchNoteCreate(BaseModel):
    ticker: str | None = None
    category: str
    projection_score: float = Field(ge=0, le=100)
    research_score: float = Field(ge=0, le=100)
    confidence_score: float = Field(ge=0, le=100)
    confirmation_score: float = Field(ge=0, le=100)
    ev_bonus: float = Field(ge=0, le=15, default=0.0)
    rationale: str
    tags: list[str] = Field(default_factory=list)
    source: str = "operator_note"


class SummaryResponse(BaseModel):
    markets: int
    candidates: int
    orders: int
    positions: int
    audits: int
    engine: dict
