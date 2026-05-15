from __future__ import annotations

import json
import logging
import time
from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.exc import ProgrammingError, SQLAlchemyError
from sqlalchemy.dialects.postgresql import insert

from app.db import SessionLocal
from app.liquidity import LiquidityConfig, LiquiditySnapshot, RollingMarketState, profile_liquidity
from app.models import MarketMicrostructureState
from app.strategy import TUNING


logger = logging.getLogger(__name__)


class LiquidityEngine:
    def __init__(self, cfg: LiquidityConfig | None = None) -> None:
        self.cfg = cfg or LiquidityConfig(max_slippage=0.06, min_depth_contracts=10.0, max_spread=0.25)
        self.market_state: dict[str, RollingMarketState] = {}
        self.active_liquid_markets: set[str] = set()
        self.inactive_markets: set[str] = set()
        self.stale_markets: set[str] = set()
        self.persistence_enabled = True

    def load_state(self) -> None:
        if not self.persistence_enabled:
            return
        try:
            with SessionLocal() as db:
                inspector = inspect(db.bind)
                if not inspector.has_table(MarketMicrostructureState.__tablename__):
                    self.persistence_enabled = False
                    logger.warning("liquidity_state_table_missing table=%s", MarketMicrostructureState.__tablename__)
                    return
                rows = db.execute(select(MarketMicrostructureState)).scalars().all()
                for row in rows:
                    self.market_state[row.ticker] = RollingMarketState(
                    spread_history=json.loads(row.spread_history_json or "[]"),
                    midpoint_history=json.loads(row.midpoint_history_json or "[]"),
                    liquidity_history=json.loads(row.liquidity_history_json or "[]"),
                    fill_probability=row.fill_probability,
                    replenishment_rate=row.replenishment_rate,
                    last_seen=row.last_seen,
                    stale_cycles=row.stale_cycles,
                    execution_score=row.execution_score,
                    volatility_score=row.volatility_score,
                    )
        except (ProgrammingError, SQLAlchemyError) as exc:
            self.persistence_enabled = False
            logger.warning("liquidity_state_load_failed degraded_mode=true error=%s", exc)

    def evaluate(self, ticker: str, orderbook: dict[str, Any]) -> LiquiditySnapshot | None:
        state = self.market_state.setdefault(ticker, RollingMarketState())
        snap = profile_liquidity(ticker, orderbook, state, self.cfg)
        state.last_seen = time.time()
        if not snap:
            state.stale_cycles += 1
            self.active_liquid_markets.discard(ticker)
            stale_limit_cycles = max(1, int((self.cfg.stale_threshold_minutes * 60) / max(1, TUNING.check_interval_sec)))
            inactive_limit_cycles = max(stale_limit_cycles + 1, int((self.cfg.inactive_threshold_minutes * 60) / max(1, TUNING.check_interval_sec)))
            if state.stale_cycles >= inactive_limit_cycles:
                self.stale_markets.add(ticker)
                self.inactive_markets.discard(ticker)
            elif state.stale_cycles >= stale_limit_cycles:
                self.inactive_markets.add(ticker)
                self.stale_markets.discard(ticker)
            else:
                self.inactive_markets.add(ticker)
            return None
        state.stale_cycles = 0
        if snap.liquidity_score >= 0.2:
            self.active_liquid_markets.add(ticker)
            self.inactive_markets.discard(ticker)
            self.stale_markets.discard(ticker)
        else:
            self.active_liquid_markets.discard(ticker)
            self.inactive_markets.add(ticker)
        return snap

    def persist_state(self) -> None:
        if not self.persistence_enabled:
            return
        try:
            with SessionLocal() as db:
                values = []
                for ticker, state in self.market_state.items():
                    values.append({
                        "ticker": ticker,
                        "spread_history_json": json.dumps(state.spread_history[-50:]),
                        "midpoint_history_json": json.dumps(state.midpoint_history[-50:]),
                        "liquidity_history_json": json.dumps(state.liquidity_history[-50:]),
                        "fill_probability": state.fill_probability,
                        "replenishment_rate": state.replenishment_rate,
                        "last_seen": state.last_seen,
                        "stale_cycles": state.stale_cycles,
                        "execution_score": state.execution_score,
                        "volatility_score": state.volatility_score,
                        "spread": state.spread_history[-1] if state.spread_history else 0.0,
                        "volatility": state.volatility_score,
                        "liquidity_score": state.execution_score,
                        "imbalance": 0.0,
                        "microprice": state.midpoint_history[-1] if state.midpoint_history else 0.0,
                        "status": "active" if ticker in self.active_liquid_markets else ("stale" if ticker in self.stale_markets else "inactive"),
                    })
                if values:
                    chunk_size = 1000
                    for i in range(0, len(values), chunk_size):
                        chunk = values[i:i + chunk_size]
                        stmt = insert(MarketMicrostructureState).values(chunk)
                        stmt = stmt.on_conflict_do_update(
                            index_elements=["ticker"],
                            set_={c: stmt.excluded[c] for c in MarketMicrostructureState.__table__.columns.keys() if c != "ticker"},
                        )
                        db.execute(stmt)
                    db.commit()
        except SQLAlchemyError as exc:
            self.persistence_enabled = False
            logger.warning("liquidity_state_persist_failed degraded_mode=true error=%s", exc)
            return
