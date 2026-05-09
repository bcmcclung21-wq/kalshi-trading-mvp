from __future__ import annotations

import time
from dataclasses import dataclass, field

from app.liquidity import LiquidityConfig, RollingMarketState, profile_liquidity


@dataclass(slots=True)
class ActiveUniverse:
    active_liquid_markets: set[str] = field(default_factory=set)
    inactive_markets: set[str] = field(default_factory=set)
    stale_markets: set[str] = field(default_factory=set)
    market_state: dict[str, RollingMarketState] = field(default_factory=dict)

    def evaluate(self, ticker: str, orderbook: dict, cfg: LiquidityConfig) -> float:
        state = self.market_state.setdefault(ticker, RollingMarketState())
        snap = profile_liquidity(ticker, orderbook, state, cfg)
        state.last_seen = time.time()
        if not snap:
            state.stale_cycles += 1
            self.active_liquid_markets.discard(ticker)
            self.inactive_markets.add(ticker)
            if state.stale_cycles > 5:
                self.stale_markets.add(ticker)
            return 0.0
        state.stale_cycles = 0
        if snap.liquidity_score >= 0.2:
            self.active_liquid_markets.add(ticker)
            self.inactive_markets.discard(ticker)
            self.stale_markets.discard(ticker)
        else:
            self.active_liquid_markets.discard(ticker)
            self.inactive_markets.add(ticker)
        return snap.liquidity_score
