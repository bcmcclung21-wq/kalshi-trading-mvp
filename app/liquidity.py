from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class LiquidityConfig:
    max_slippage: float = 0.05
    min_depth_contracts: float = 10.0
    max_spread: float = 0.20


@dataclass(slots=True)
class LiquiditySnapshot:
    ticker: str
    yes_bid: float
    yes_ask: float
    no_bid: float
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
    replenishment_rate: float = 0.0
    fill_probability: float = 0.0
    execution_score: float = 0.0
    volatility_score: float = 0.0
    last_seen: float = 0.0
    stale_cycles: int = 0


# Backward/forward compatibility alias.
MarketState = RollingMarketState


def _flatten(levels: Any) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    if not levels:
        return out

    for lvl in levels:
        if isinstance(lvl, dict):
            p = lvl.get("price")
            q = lvl.get("size") or lvl.get("quantity") or lvl.get("qty")
        elif isinstance(lvl, (list, tuple)) and len(lvl) >= 2:
            p, q = lvl[0], lvl[1]
        else:
            continue

        try:
            pf = float(p)
            qf = float(q)
        except (TypeError, ValueError):
            continue

        if pf > 0 and qf > 0:
            out.append((pf, qf))

    return out


def compute_snapshot(
    ticker: str,
    orderbook: dict[str, Any],
    cfg: LiquidityConfig,
    state: RollingMarketState,
) -> LiquiditySnapshot | None:
    yes_bids = _flatten(orderbook.get("yes_bids") or orderbook.get("yes") or [])
    yes_asks = _flatten(orderbook.get("yes_asks") or [])
    no_bids = _flatten(orderbook.get("no_bids") or orderbook.get("no") or [])
    no_asks = _flatten(orderbook.get("no_asks") or [])

    if not yes_bids and not yes_asks and not no_bids and not no_asks:
        return None

    yes_bid = max([p for p, _ in yes_bids], default=0.0)
    no_bid = max([p for p, _ in no_bids], default=0.0)
    yes_ask = min([p for p, _ in yes_asks], default=0.0)
    no_ask = min([p for p, _ in no_asks], default=0.0)

    # Full binary complement reconstruction.
    if yes_ask <= 0 and no_bid > 0:
        yes_ask = 1.0 - no_bid
    if yes_bid <= 0 and no_ask > 0:
        yes_bid = 1.0 - no_ask
    if no_ask <= 0 and yes_bid > 0:
        no_ask = 1.0 - yes_bid
    if no_bid <= 0 and yes_ask > 0:
        no_bid = 1.0 - yes_ask

    yes_bid = max(0.0, min(0.99, yes_bid))
    yes_ask = max(0.0, min(0.99, yes_ask))
    no_bid = max(0.0, min(0.99, no_bid))
    no_ask = max(0.0, min(0.99, no_ask))

    if yes_bid > 0 and yes_ask > 0:
        midpoint = (yes_bid + yes_ask) / 2.0
    elif yes_bid > 0:
        midpoint = yes_bid
    elif yes_ask > 0:
        midpoint = yes_ask
    elif no_bid > 0 and no_ask > 0:
        midpoint = 1.0 - ((no_bid + no_ask) / 2.0)
    else:
        midpoint = 0.5

    if yes_ask > 0 and yes_bid > 0 and yes_ask >= yes_bid:
        spread = yes_ask - yes_bid
    elif no_ask > 0 and no_bid > 0 and no_ask >= no_bid:
        spread = no_ask - no_bid
    else:
        spread = 1.0
    spread = min(1.0, max(0.0, spread))

    all_levels = (
        yes_bids
        + yes_asks
        + [(1.0 - p, q) for p, q in no_bids if 0.0 < p < 1.0]
        + [(1.0 - p, q) for p, q in no_asks if 0.0 < p < 1.0]
    )

    effective_depth = sum(q for p, q in all_levels if abs(p - midpoint) <= cfg.max_slippage)
    executable_size = sum(q for _, q in all_levels)
    ladder_density = len(all_levels) / max(1.0, spread * 100.0)

    state.spread_history = (state.spread_history + [spread])[-20:]
    state.midpoint_history = (state.midpoint_history + [midpoint])[-20:]
    state.liquidity_history = (state.liquidity_history + [effective_depth])[-20:]

    if len(state.spread_history) > 1:
        spread_range = max(state.spread_history) - min(state.spread_history)
    else:
        spread_range = spread

    stability_raw = 1.0 / (1.0 + spread_range)
    replenishment_raw = (
        max(0.0, state.liquidity_history[-1] - state.liquidity_history[-2])
        if len(state.liquidity_history) > 1
        else 0.0
    )

    spread_score = max(0.0, 1.0 - (spread / max(0.01, cfg.max_spread)))
    depth_score = min(1.0, effective_depth / max(1.0, cfg.min_depth_contracts))
    replenishment_score = min(1.0, replenishment_raw / max(1.0, cfg.min_depth_contracts * 0.1))
    stability_score = min(1.0, stability_raw)

    raw_score = spread_score * depth_score * max(0.1, replenishment_score) * max(0.1, stability_score)
    floor_score = 0.01 * depth_score
    liquidity_score = max(raw_score, floor_score)

    state.replenishment_rate = replenishment_raw
    state.fill_probability = min(1.0, depth_score * stability_score)
    state.execution_score = liquidity_score
    state.volatility_score = 1.0 - stability_score

    return LiquiditySnapshot(
        ticker=ticker,
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        no_bid=no_bid,
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


def profile_liquidity(
    ticker: str,
    orderbook: dict[str, Any],
    state: RollingMarketState,
    cfg: LiquidityConfig,
) -> LiquiditySnapshot | None:
    # Compatibility wrapper for the existing service layer import/signature.
    return compute_snapshot(
        ticker=ticker,
        orderbook=orderbook,
        cfg=cfg,
        state=state,
    )


__all__ = [
    "LiquidityConfig",
    "LiquiditySnapshot",
    "RollingMarketState",
    "MarketState",
    "compute_snapshot",
    "profile_liquidity",
]