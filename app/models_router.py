from __future__ import annotations

import logging
import os
from threading import Lock
from typing import Any

from app.fallback_midpoint import compute

logger = logging.getLogger("app.models_router")

MODEL_PATH = os.environ.get("PRIMARY_MODEL_PATH", "/app/models/primary.onnx")
_MODEL_SINGLETON: Any = None
_MODEL_LOCK = Lock()


def _load_onnx_model(path: str) -> Any:
    try:
        import onnxruntime as ort

        return ort.InferenceSession(path)
    except Exception as exc:
        logger.error("PRIMARY_MODEL_LOAD_FAILED path=%s err=%s", path, exc)
        return None


def load_primary_model() -> Any:
    global _MODEL_SINGLETON
    if _MODEL_SINGLETON is not None:
        return _MODEL_SINGLETON
    with _MODEL_LOCK:
        if _MODEL_SINGLETON is not None:
            return _MODEL_SINGLETON
        if (not os.path.exists(MODEL_PATH)) or os.path.getsize(MODEL_PATH) < 1024:
            logger.warning("PRIMARY_MODEL_MISSING_OR_EMPTY path=%s", MODEL_PATH)
            return None
        _MODEL_SINGLETON = _load_onnx_model(MODEL_PATH)
        return _MODEL_SINGLETON


def model_is_loaded() -> bool:
    return load_primary_model() is not None


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
