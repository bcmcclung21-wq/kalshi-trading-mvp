from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean
from typing import Any


@dataclass(slots=True)
class LiquidityConfig:
    max_slippage: float = 0.04
    min_depth_contracts: float = 20.0
    max_spread: float = 0.20


@dataclass(slots=True)
class LiquiditySnapshot:
    ticker: str
    yes_bid: float
    no_bid: float
    yes_ask: float
    no_ask: float
    midpoint: float
    spread: float
    effective_depth: float
    ladder_density: float
    stability: float
    replenishment: float
    liquidity_score: float
    executable_size: float


@dataclass(slots=True)
class RollingMarketState:
    spread_history: list[float] = field(default_factory=list)
    midpoint_history: list[float] = field(default_factory=list)
    liquidity_history: list[float] = field(default_factory=list)
    fill_probability: float = 0.0
    replenishment_rate: float = 0.0
    last_seen: float = 0.0
    stale_cycles: int = 0
    execution_score: float = 0.0
    volatility_score: float = 0.0


def _levels(side: list[Any]) -> list[tuple[float, float]]:
    def _normalize_price(price: float) -> float:
        if price <= 0:
            return 0.0
        if 0 < price < 1:
            return price
        if 1 <= price <= 100:
            return price / 100.0
        if 100 < price <= 1000:
            return price / 1000.0
        if 1000 < price <= 10000:
            return price / 10000.0
        return 0.0

    out: list[tuple[float, float]] = []
    for lvl in side or []:
        if isinstance(lvl, dict):
            try:
                p = float((lvl.get("price") or lvl.get("px") or lvl.get("value") or 0.0).get("value") if isinstance((lvl.get("price") or lvl.get("px") or lvl.get("value") or 0.0), dict) else (lvl.get("price") or lvl.get("px") or lvl.get("value") or 0.0))
                q = float(lvl.get("qty") or lvl.get("quantity") or lvl.get("size") or lvl.get("count") or 0.0)
            except (TypeError, ValueError):
                continue
        elif isinstance(lvl, (list, tuple)) and len(lvl) >= 2:
            try:
                p, q = float(lvl[0]), float(lvl[1])
            except (TypeError, ValueError):
                continue
        else:
            continue
        p = _normalize_price(p)
        if 0.0 < p < 1.0 and q > 0:
            out.append((p, q))
    return out


def profile_liquidity(ticker: str, orderbook: dict[str, Any], state: RollingMarketState, cfg: LiquidityConfig) -> LiquiditySnapshot | None:
    yes_bids = _levels(orderbook.get("yes_bids") or orderbook.get("yes") or [])
    yes_asks = _levels(orderbook.get("yes_asks") or [])
    no_bids = _levels(orderbook.get("no_bids") or orderbook.get("no") or [])
    no_asks = _levels(orderbook.get("no_asks") or [])
    if not yes_bids and not yes_asks and not no_bids and not no_asks:
        return None
    yes_bid = max([p for p, _ in yes_bids], default=0.0)
    no_bid = max([p for p, _ in no_bids], default=0.0)
    yes_ask = min([p for p, _ in yes_asks], default=0.0)
    no_ask = min([p for p, _ in no_asks], default=0.0)
    if yes_ask <= 0 and no_bid > 0:
        yes_ask = 1 - no_bid
    if yes_bid <= 0 and no_ask > 0:
        yes_bid = 1 - no_ask
    if no_ask <= 0 and yes_bid > 0:
        no_ask = 1 - yes_bid
    if no_bid <= 0 and yes_ask > 0:
        no_bid = 1 - yes_ask
    midpoint = mean([x for x in [yes_bid, yes_ask] if x > 0]) if (yes_bid > 0 or yes_ask > 0) else 0.5
    spread = max(0.0, yes_ask - yes_bid) if yes_ask and yes_bid else 1.0
    all_levels = yes_bids + yes_asks + [(1 - p, q) for p, q in no_bids if 0 < p < 1] + [(1 - p, q) for p, q in no_asks if 0 < p < 1]
    effective_depth = sum(q for p, q in all_levels if abs(p - midpoint) <= cfg.max_slippage)
    executable_size = sum(q for _, q in all_levels)
    ladder_density = len(all_levels) / max(1.0, spread * 100)

    state.spread_history = (state.spread_history + [spread])[-20:]
    state.midpoint_history = (state.midpoint_history + [midpoint])[-20:]
    state.liquidity_history = (state.liquidity_history + [effective_depth])[-20:]
    stability = 1.0 / (1.0 + (max(state.spread_history) - min(state.spread_history) if len(state.spread_history) > 1 else spread))
    replenishment = max(0.0, state.liquidity_history[-1] - state.liquidity_history[-2]) if len(state.liquidity_history) > 1 else 0.0

    spread_score = max(0.0, 1.0 - (spread / cfg.max_spread))
    depth_score = min(1.0, effective_depth / max(1.0, cfg.min_depth_contracts))
    replenishment_score = min(1.0, replenishment / max(1.0, cfg.min_depth_contracts * 0.1))
    stability_score = min(1.0, stability)
    liquidity_score = spread_score * depth_score * max(0.1, replenishment_score) * max(0.1, stability_score)

    state.replenishment_rate = replenishment
    state.fill_probability = min(1.0, depth_score * stability_score)
    state.execution_score = liquidity_score
    state.volatility_score = 1.0 - stability_score

    return LiquiditySnapshot(
        ticker=ticker,
        yes_bid=yes_bid,
        no_bid=no_bid,
        yes_ask=yes_ask,
        no_ask=no_ask,
        midpoint=midpoint,
        spread=spread,
        effective_depth=effective_depth,
        ladder_density=ladder_density,
        stability=stability_score,
        replenishment=replenishment_score,
        liquidity_score=liquidity_score,
        executable_size=executable_size,
    )
