from __future__ import annotations

import logging
from typing import Any

from app.fallback_midpoint import compute

logger = logging.getLogger("app.models_router")


def load_primary_model() -> Any:
    """Placeholder primary model loader; returns None until integrated."""
    return None


def model_route(ticker: str, market_data: dict[str, Any], orderbook: dict[str, Any]) -> dict[str, Any]:
    primary = load_primary_model()
    if primary is None:
        logger.error("PRIMARY_MODEL_NOT_LOADED ticker=%s", ticker)
        fb = compute(ticker=ticker, orderbook=orderbook)
        return {"primary_ok": False, "primary_edge": fb.get("edge"), "fallback": True}
    try:
        edge = primary.predict(market_data, orderbook)
        if edge is None or abs(edge) < 1e-6:
            fb = compute(ticker=ticker, orderbook=orderbook)
            return {"primary_ok": False, "primary_edge": fb.get("edge", 0.0), "fallback": True}
        return {"primary_ok": True, "primary_edge": edge, "fallback": False}
    except Exception as e:
        logger.error("PRIMARY_MODEL_CRASH ticker=%s err=%s", ticker, e)
        fb = compute(ticker=ticker, orderbook=orderbook)
        return {"primary_ok": False, "primary_edge": fb.get("edge"), "fallback": True}
