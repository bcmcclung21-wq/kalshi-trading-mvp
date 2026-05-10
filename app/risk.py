import os
import logging
from typing import Optional

log = logging.getLogger("app.risk")

MIN_TOTAL_SCORE = float(os.getenv("MIN_TOTAL_SCORE", "0.35"))
LOG_SCORE_BREAKDOWN = os.getenv("LOG_SCORE_BREAKDOWN", "true").lower() == "true"

W_EDGE = float(os.getenv("SCORE_W_EDGE", "0.40"))
W_LIQ = float(os.getenv("SCORE_W_LIQ", "0.25"))
W_DEPTH = float(os.getenv("SCORE_W_DEPTH", "0.20"))
W_FRESH = float(os.getenv("SCORE_W_FRESH", "0.15"))

EDGE_NORM = float(os.getenv("EDGE_NORM", "0.10"))
LIQ_NORM = float(os.getenv("LIQ_NORM", "1000.0"))
DEPTH_NORM = float(os.getenv("DEPTH_NORM", "500.0"))
FRESH_HORIZON_S = float(os.getenv("FRESH_HORIZON_S", "600.0"))

R5_MIN_DEPTH_USD = float(os.getenv("R5_MIN_DEPTH_USD", "25.0"))


def _clip01(x: float) -> float:
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


def score_candidate(
    market: dict,
    orderbook: Optional[dict],
    edge: float,
    liquidity: float,
    depth_usd: float,
    freshness_s: float,
) -> dict:
    """
    Score a single candidate market.

    Returns dict with:
      total                : weighted composite [0,1]
      edge,liq,depth,fresh : per-leg normalized scores
      pass                 : bool, True iff total >= MIN_TOTAL_SCORE
      reason               : None if pass else 'low_total_score'

    When LOG_SCORE_BREAKDOWN is true, logs the full breakdown including
    raw inputs and the active threshold for diagnosis.
    """
    edge_score = _clip01(edge / EDGE_NORM) if EDGE_NORM > 0 else 0.0
    liq_score = _clip01(liquidity / LIQ_NORM) if LIQ_NORM > 0 else 0.0
    depth_score = _clip01(depth_usd / DEPTH_NORM) if DEPTH_NORM > 0 else 0.0
    fresh_score = _clip01(1.0 - (freshness_s / FRESH_HORIZON_S)) if FRESH_HORIZON_S > 0 else 0.0

    total = (
        W_EDGE * edge_score
        + W_LIQ * liq_score
        + W_DEPTH * depth_score
        + W_FRESH * fresh_score
    )

    passed = total >= MIN_TOTAL_SCORE
    reason = None if passed else "low_total_score"

    breakdown = {
        "total": round(total, 4),
        "edge": round(edge_score, 4),
        "liq": round(liq_score, 4),
        "depth": round(depth_score, 4),
        "fresh": round(fresh_score, 4),
        "pass": passed,
        "reason": reason,
    }

    if LOG_SCORE_BREAKDOWN:
        log.info(
            "score_breakdown ticker=%s total=%.3f edge=%.3f liq=%.3f depth=%.3f fresh=%.3f "
            "raw_edge=%.4f raw_liq=%.1f raw_depth=%.1f raw_fresh_s=%.0f threshold=%.3f pass=%s",
            market.get("ticker", "?"),
            total, edge_score, liq_score, depth_score, fresh_score,
            edge, liquidity, depth_usd, freshness_s,
            MIN_TOTAL_SCORE, passed,
        )

    return breakdown


def r5_depth_gate(orderbook: Optional[dict], side: str = "yes") -> dict:
    """
    R5 depth gate: require at least R5_MIN_DEPTH_USD of resting liquidity
    on the relevant side.
    Returns {'pass': bool, 'depth_usd': float, 'reason': str|None}.
    """
    if not orderbook:
        return {"pass": False, "depth_usd": 0.0, "reason": "no_orderbook"}

    levels = orderbook.get(side, []) or []
    depth_usd = 0.0
    for lvl in levels:
        try:
            price_cents = float(lvl[0])
            size = float(lvl[1])
            depth_usd += (price_cents / 100.0) * size
        except (IndexError, TypeError, ValueError):
            continue

    if depth_usd < R5_MIN_DEPTH_USD:
        return {"pass": False, "depth_usd": depth_usd, "reason": "insufficient_depth"}
    return {"pass": True, "depth_usd": depth_usd, "reason": None}