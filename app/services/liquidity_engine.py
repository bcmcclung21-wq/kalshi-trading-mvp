from __future__ import annotations

import json
import time
from dataclasses import asdict
from typing import Any

from sqlalchemy import select

from app.db import SessionLocal
from app.liquidity import LiquidityConfig, LiquiditySnapshot, RollingMarketState, profile_liquidity
from app.models import MarketMicrostructureState


class LiquidityEngine:
    def __init__(self, cfg: LiquidityConfig | None = None) -> None:
        self.cfg = cfg or LiquidityConfig(max_slippage=0.04, min_depth_contracts=25.0, max_spread=0.16)
        self.market_state: dict[str, RollingMarketState] = {}
        self.active_liquid_markets: set[str] = set()
        self.inactive_markets: set[str] = set()
        self.stale_markets: set[str] = set()
        self.load_state()

    def load_state(self) -> None:
        with SessionLocal() as db:
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
                if row.status == "active":
                    self.active_liquid_markets.add(row.ticker)
                elif row.status == "stale":
                    self.stale_markets.add(row.ticker)
                else:
                    self.inactive_markets.add(row.ticker)

    def evaluate(self, ticker: str, orderbook: dict[str, Any]) -> LiquiditySnapshot | None:
        state = self.market_state.setdefault(ticker, RollingMarketState())
        snap = profile_liquidity(ticker, orderbook, state, self.cfg)
        state.last_seen = time.time()
        if not snap:
            state.stale_cycles += 1
            self.active_liquid_markets.discard(ticker)
            if state.stale_cycles > 5:
                self.stale_markets.add(ticker)
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
        with SessionLocal() as db:
            for ticker, state in self.market_state.items():
                row = db.get(MarketMicrostructureState, ticker) or MarketMicrostructureState(ticker=ticker)
                row.spread_history_json = json.dumps(state.spread_history[-50:])
                row.midpoint_history_json = json.dumps(state.midpoint_history[-50:])
                row.liquidity_history_json = json.dumps(state.liquidity_history[-50:])
                row.fill_probability = state.fill_probability
                row.replenishment_rate = state.replenishment_rate
                row.last_seen = state.last_seen
                row.stale_cycles = state.stale_cycles
                row.execution_score = state.execution_score
                row.volatility_score = state.volatility_score
                row.status = "active" if ticker in self.active_liquid_markets else ("stale" if ticker in self.stale_markets else "inactive")
                db.merge(row)
            db.commit()
