from __future__ import annotations

import logging
import os
from typing import Any

from app.fallback_midpoint import compute

logger = logging.getLogger("app.models_router")

MODEL_PATH = os.environ.get("PRIMARY_MODEL_PATH", "models/primary.onnx")


def _load_onnx_model(path: str) -> Any:
    try:
        import onnxruntime as ort

        return ort.InferenceSession(path)
    except Exception as exc:
        logger.error("PRIMARY_MODEL_LOAD_FAILED path=%s err=%s", path, exc)
        return None


def load_primary_model() -> Any:
    if not os.path.exists(MODEL_PATH):
        logger.error("MODEL_FILE_MISSING path=%s cwd=%s", MODEL_PATH, os.getcwd())
        return None
    return _load_onnx_model(MODEL_PATH)


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
