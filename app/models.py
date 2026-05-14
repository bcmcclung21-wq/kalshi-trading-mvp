from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel
from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Category(str, Enum):
    SPORTS = "sports"
    POLITICS = "politics"
    CRYPTO = "crypto"
    CLIMATE = "climate"
    ECONOMY = "economy"
    TECH = "tech"
    OTHER = "other"


class Market(BaseModel):
    id: str
    title: str
    category: Category
    confidence: float = 0.0
    ev: float | None = None
    liquidity: float = 0.0
    spread: float = 1.0
    volume_24h: float = 0.0
    last_price: float = 0.5
    ends_at: datetime
    url: str = ""
    best_bid: float = 0.0
    best_ask: float = 1.0
    market_type: str = "single"
    close_time: str | None = None
    tags: list = []
    question: str = ""


class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    event_ticker: Mapped[str] = mapped_column(String(128), default="", index=True)
    title: Mapped[str] = mapped_column(Text, default="")
    subtitle: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(32), index=True, default="unknown")
    market_type: Mapped[str] = mapped_column(String(32), default="single")
    status: Mapped[str] = mapped_column(String(32), default="open")
    close_time: Mapped[str] = mapped_column(String(64), default="")
    volume: Mapped[float] = mapped_column(Float, default=0.0)
    open_interest: Mapped[float] = mapped_column(Float, default=0.0)
    last_price: Mapped[float] = mapped_column(Float, default=0.0)
    raw_json: Mapped[str] = mapped_column(Text, default="{}")
    spread: Mapped[float] = mapped_column(Float, default=0.0)
    imbalance: Mapped[float] = mapped_column(Float, default=0.0)
    volatility: Mapped[float] = mapped_column(Float, default=0.0)
    microprice: Mapped[float] = mapped_column(Float, default=0.0)
    liquidity_score: Mapped[float] = mapped_column(Float, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class OrderBookSnapshot(Base):
    __tablename__ = "orderbook_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(128), index=True)
    yes_bid: Mapped[float] = mapped_column(Float, default=0.0)
    yes_ask: Mapped[float] = mapped_column(Float, default=0.0)
    no_bid: Mapped[float] = mapped_column(Float, default=0.0)
    no_ask: Mapped[float] = mapped_column(Float, default=0.0)
    spread_cents: Mapped[float] = mapped_column(Float, default=0.0)
    raw_json: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class ResearchNote(Base):
    __tablename__ = "research_notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    category: Mapped[str] = mapped_column(String(32), index=True)
    projection_score: Mapped[float] = mapped_column(Float, default=0.0)
    research_score: Mapped[float] = mapped_column(Float, default=0.0)
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    confirmation_score: Mapped[float] = mapped_column(Float, default=0.0)
    ev_bonus: Mapped[float] = mapped_column(Float, default=0.0)
    rationale: Mapped[str] = mapped_column(Text, default="")
    tags_json: Mapped[str] = mapped_column(Text, default="[]")
    source: Mapped[str] = mapped_column(String(64), default="operator_note")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class CandidateRun(Base):
    __tablename__ = "candidate_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cycle_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    ticker: Mapped[str] = mapped_column(String(128), index=True)
    category: Mapped[str] = mapped_column(String(32), index=True)
    market_type: Mapped[str] = mapped_column(String(32), default="single")
    side: Mapped[str] = mapped_column(String(8), default="YES")
    entry_price: Mapped[float] = mapped_column(Float, default=0.0)
    spread_cents: Mapped[float] = mapped_column(Float, default=0.0)
    projection_score: Mapped[float] = mapped_column(Float, default=0.0)
    research_score: Mapped[float] = mapped_column(Float, default=0.0)
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    confirmation_score: Mapped[float] = mapped_column(Float, default=0.0)
    ev_bonus: Mapped[float] = mapped_column(Float, default=0.0)
    total_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    details_json: Mapped[str] = mapped_column(Text, default="{}")
    rationale: Mapped[str] = mapped_column(Text, default="")


class OrderRecord(Base):
    __tablename__ = "order_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    ticker: Mapped[str] = mapped_column(String(128), index=True)
    category: Mapped[str] = mapped_column(String(32), index=True, default="unknown")
    side: Mapped[str] = mapped_column(String(8))
    market_type: Mapped[str] = mapped_column(String(32), default="single")
    legs: Mapped[int] = mapped_column(Integer, default=1)
    count: Mapped[int] = mapped_column(Integer, default=0)
    price_cents: Mapped[int] = mapped_column(Integer, default=0)
    bankroll_pct: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    kalshi_order_id: Mapped[str] = mapped_column(String(128), default="")
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True)
    rationale: Mapped[str] = mapped_column(Text, default="")
    raw_json: Mapped[str] = mapped_column(Text, default="{}")
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    features_json: Mapped[str] = mapped_column(Text, default="{}")
    estimated_win_probability: Mapped[float] = mapped_column(Float, default=0.0)
    brier_snapshot_json: Mapped[str] = mapped_column(Text, default="{}")
    calibration_status: Mapped[str] = mapped_column(String(16), default="ok")


class PositionSnapshot(Base):
    __tablename__ = "position_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    ticker: Mapped[str] = mapped_column(String(128), index=True)
    category: Mapped[str] = mapped_column(String(32), index=True, default="unknown")
    side: Mapped[str] = mapped_column(String(8), default="")
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    avg_price: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(32), default="open")
    raw_json: Mapped[str] = mapped_column(Text, default="{}")


class AuditRun(Base):
    __tablename__ = "audit_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    audit_date: Mapped[str] = mapped_column(String(16), index=True)
    total_trades: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    win_rate: Mapped[float] = mapped_column(Float, default=0.0)
    gross_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    by_category_json: Mapped[str] = mapped_column(Text, default="{}")
    issues_json: Mapped[str] = mapped_column(Text, default="{}")
    improvements_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    feature_breakdown_json: Mapped[str] = mapped_column(Text, default="{}")
    calibration_json: Mapped[str] = mapped_column(Text, default="{}")
    learning_summary_json: Mapped[str] = mapped_column(Text, default="{}")
    rolling_brier: Mapped[float] = mapped_column(Float, default=0.0)
    brier_threshold: Mapped[float] = mapped_column(Float, default=0.25)
    trades_in_window: Mapped[int] = mapped_column(Integer, default=0)


class MarketMicrostructureState(Base):
    __tablename__ = "market_microstructure_state"

    ticker: Mapped[str] = mapped_column(String(128), primary_key=True)
    spread_history_json: Mapped[str] = mapped_column(Text, default="[]")
    midpoint_history_json: Mapped[str] = mapped_column(Text, default="[]")
    liquidity_history_json: Mapped[str] = mapped_column(Text, default="[]")
    fill_probability: Mapped[float] = mapped_column(Float, default=0.0)
    replenishment_rate: Mapped[float] = mapped_column(Float, default=0.0)
    last_seen: Mapped[float] = mapped_column(Float, default=0.0, index=True)
    stale_cycles: Mapped[int] = mapped_column(Integer, default=0)
    execution_score: Mapped[float] = mapped_column(Float, default=0.0)
    volatility_score: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="inactive", index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class CalibrationSnapshot(Base):
    __tablename__ = "calibration_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    window_size: Mapped[int] = mapped_column(Integer, default=50)
    brier_score: Mapped[float] = mapped_column(Float, default=0.0)
    trades_evaluated: Mapped[int] = mapped_column(Integer, default=0)
    threshold: Mapped[float] = mapped_column(Float, default=0.25)
    status: Mapped[str] = mapped_column(String(16), default="ok")
    bucket_breakdown_json: Mapped[str] = mapped_column(Text, default="{}")
    raw_json: Mapped[str] = mapped_column(Text, default="{}")


class LearnedPrior(Base):
    __tablename__ = "learned_priors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    feature_key: Mapped[str] = mapped_column(String(64), index=True)
    bucket: Mapped[str] = mapped_column(String(64), index=True)
    sample_size: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    win_rate: Mapped[float] = mapped_column(Float, default=0.5)
    multiplier: Mapped[float] = mapped_column(Float, default=1.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, index=True)


class CashoutOrder(Base):
    __tablename__ = "cashout_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    original_order_id: Mapped[int] = mapped_column(Integer, index=True)
    ticker: Mapped[str] = mapped_column(String(128), index=True)
    side: Mapped[str] = mapped_column(String(8), default="SELL")
    cashout_type: Mapped[str] = mapped_column(String(32), index=True)
    size: Mapped[float] = mapped_column(Float, default=0.0)
    price: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
