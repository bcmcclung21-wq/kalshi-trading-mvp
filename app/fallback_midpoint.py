from __future__ import annotations


def compute(ticker: str, orderbook: dict, confidence: float = 72.0) -> dict:
    best_bid = float(orderbook.get("bids", [[0, 0]])[0][0])
    best_ask = float(orderbook.get("asks", [[1, 0]])[0][0])
    midpoint = (best_bid + best_ask) / 2
    spread = best_ask - best_bid
    fair = midpoint
    edge = abs(fair - 0.50) - (spread * 0.3)
    edge = max(edge, 0.001)
    projection = edge * confidence * 0.10
    total = projection + confidence * 0.45
    return {
        "fair": round(fair, 4),
        "edge": round(edge, 4),
        "projection": round(projection, 2),
        "confidence": confidence,
        "total": round(total, 2),
    }
