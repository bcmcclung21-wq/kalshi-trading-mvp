from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc, select

from app.db import SessionLocal
from app.models import CalibrationSnapshot, OrderRecord

logger = logging.getLogger(__name__)

MIN_VALID_TRADES_FOR_HALT = 10


def compute_brier(window_size: int = 50, threshold: float = 0.25) -> dict[str, Any]:
    with SessionLocal() as db:
        orders = db.execute(
            select(OrderRecord)
            .where(OrderRecord.status.in_(["won", "lost", "settled"]))
            .where(OrderRecord.estimated_win_probability > 0.0)
            .where(OrderRecord.estimated_win_probability.isnot(None))
            .order_by(desc(OrderRecord.settled_at))
            .limit(window_size)
        ).scalars().all()

    if not orders:
        return {
            "brier_score": 0.0,
            "trades_evaluated": 0,
            "valid_trades": 0,
            "status": "ok",
            "bucket_breakdown": {},
            "raw": {
                "note": "no settled trades with valid probabilities",
                "threshold": threshold,
                "window_size": window_size,
                "computed_at": datetime.now(timezone.utc).isoformat(),
            },
        }

    brier_sum = 0.0
    buckets = {
        "0.0-0.2": {"n": 0, "wins": 0, "brier": 0.0},
        "0.2-0.4": {"n": 0, "wins": 0, "brier": 0.0},
        "0.4-0.6": {"n": 0, "wins": 0, "brier": 0.0},
        "0.6-0.8": {"n": 0, "wins": 0, "brier": 0.0},
        "0.8-1.0": {"n": 0, "wins": 0, "brier": 0.0},
    }

    for order in orders:
        p = float(order.estimated_win_probability)
        p = max(0.0, min(1.0, p))
        won = order.status == "won"
        o = 1.0 if won else 0.0
        sq_error = (p - o) ** 2
        brier_sum += sq_error

        if p < 0.2:
            key = "0.0-0.2"
        elif p < 0.4:
            key = "0.2-0.4"
        elif p < 0.6:
            key = "0.4-0.6"
        elif p < 0.8:
            key = "0.6-0.8"
        else:
            key = "0.8-1.0"

        buckets[key]["n"] += 1
        buckets[key]["wins"] += int(won)
        buckets[key]["brier"] += sq_error

    n = len(orders)
    brier = brier_sum / n

    for key in buckets:
        bn = buckets[key]["n"]
        if bn:
            buckets[key]["avg_brier"] = round(buckets[key]["brier"] / bn, 4)
            buckets[key]["actual_rate"] = round(buckets[key]["wins"] / bn, 3)
        else:
            buckets[key]["avg_brier"] = None
            buckets[key]["actual_rate"] = None

    if n < MIN_VALID_TRADES_FOR_HALT:
        status = "ok"
        logger.info(
            "calibration_insufficient_data valid_trades=%d min_required=%d brier=%.4f - not halting",
            n, MIN_VALID_TRADES_FOR_HALT, brier,
        )
    else:
        status = "ok" if brier <= threshold else "halted"

    return {
        "brier_score": round(brier, 4),
        "trades_evaluated": n,
        "valid_trades": n,
        "status": status,
        "bucket_breakdown": buckets,
        "raw": {
            "threshold": threshold,
            "window_size": window_size,
            "min_valid_trades": MIN_VALID_TRADES_FOR_HALT,
            "computed_at": datetime.now(timezone.utc).isoformat(),
        },
    }


def persist_snapshot(result: dict[str, Any]) -> CalibrationSnapshot:
    raw = result.get("raw") or {}
    snap = CalibrationSnapshot(
        window_size=raw.get("window_size", 50),
        brier_score=result["brier_score"],
        trades_evaluated=result["trades_evaluated"],
        threshold=raw.get("threshold", 0.25),
        status=result["status"],
        bucket_breakdown_json=json.dumps(result["bucket_breakdown"]),
        raw_json=json.dumps(raw),
    )
    with SessionLocal() as db:
        db.add(snap)
        db.commit()
        db.refresh(snap)
    logger.info(
        "calibration_snapshot brier=%.4f status=%s trades=%d",
        snap.brier_score, snap.status, snap.trades_evaluated,
    )
    return snap


def latest_snapshot() -> CalibrationSnapshot | None:
    with SessionLocal() as db:
        return db.execute(
            select(CalibrationSnapshot).order_by(desc(CalibrationSnapshot.computed_at)).limit(1)
        ).scalar_one_or_none()


def is_trading_halted(threshold: float = 0.25, window_size: int = 50) -> bool:
    result = compute_brier(window_size=window_size, threshold=threshold)
    return result["status"] == "halted"
