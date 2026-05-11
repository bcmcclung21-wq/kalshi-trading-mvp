"""Continuous learning engine.

Reads historical orders + settlements, computes feature -> win-rate priors,
and exposes multipliers used by the scorer. Priors are bucketed by:
- category (sports, politics, crypto, climate, economics)
- entry_price bucket
- spread_cents bucket
- minutes_to_close bucket
- confidence_score bucket

Each prior carries a sample size and is smoothed toward a global baseline
using a Bayesian shrinkage formula so small samples don't dominate scoring.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.db import SessionLocal
from app.models import LearnedPrior, OrderRecord, AuditRun

logger = logging.getLogger(__name__)


PRIOR_BASELINE_WIN_RATE = 0.50
PRIOR_SHRINKAGE_K = 20.0
MIN_SAMPLES_FOR_TRUST = 5


def _bucket_price(price: float) -> str:
    if price < 0.20:
        return "p_lt_20"
    if price < 0.35:
        return "p_20_35"
    if price < 0.50:
        return "p_35_50"
    if price < 0.65:
        return "p_50_65"
    if price < 0.80:
        return "p_65_80"
    return "p_gte_80"


def _bucket_spread(spread_cents: float) -> str:
    if spread_cents <= 3:
        return "s_lte_3"
    if spread_cents <= 6:
        return "s_4_6"
    if spread_cents <= 10:
        return "s_7_10"
    if spread_cents <= 15:
        return "s_11_15"
    return "s_gt_15"


def _bucket_minutes(minutes: float | None) -> str:
    if minutes is None:
        return "t_unknown"
    if minutes < 60:
        return "t_lt_1h"
    if minutes < 60 * 6:
        return "t_1_6h"
    if minutes < 60 * 24:
        return "t_6_24h"
    if minutes < 60 * 24 * 3:
        return "t_1_3d"
    if minutes < 60 * 24 * 7:
        return "t_3_7d"
    return "t_gt_7d"


def _bucket_confidence(confidence: float) -> str:
    if confidence < 50:
        return "c_lt_50"
    if confidence < 60:
        return "c_50_60"
    if confidence < 70:
        return "c_60_70"
    if confidence < 80:
        return "c_70_80"
    return "c_gte_80"


def bucket_features(category: str, entry_price: float, spread_cents: float, minutes_to_close: float | None, confidence: float) -> dict[str, str]:
    return {
        "category": str(category or "unknown").lower(),
        "price_bucket": _bucket_price(float(entry_price or 0.0)),
        "spread_bucket": _bucket_spread(float(spread_cents or 0.0)),
        "time_bucket": _bucket_minutes(minutes_to_close),
        "confidence_bucket": _bucket_confidence(float(confidence or 0.0)),
    }


def shrinkage_win_rate(wins: int, total: int, baseline: float = PRIOR_BASELINE_WIN_RATE, k: float = PRIOR_SHRINKAGE_K) -> float:
    if total <= 0:
        return baseline
    return ((wins + baseline * k) / (total + k))


@dataclass(slots=True)
class FeatureMultiplier:
    feature_key: str
    bucket: str
    win_rate: float
    sample_size: int
    multiplier: float


@dataclass(slots=True)
class LearningSnapshot:
    by_category: dict[str, FeatureMultiplier] = field(default_factory=dict)
    by_price: dict[str, FeatureMultiplier] = field(default_factory=dict)
    by_spread: dict[str, FeatureMultiplier] = field(default_factory=dict)
    by_time: dict[str, FeatureMultiplier] = field(default_factory=dict)
    by_confidence: dict[str, FeatureMultiplier] = field(default_factory=dict)
    global_win_rate: float = PRIOR_BASELINE_WIN_RATE
    global_sample: int = 0


class LearningEngine:
    """Lazy-loaded singleton-style learning store.

    The engine loads learned priors from the DB once and refreshes after
    every audit cycle. Multipliers center around 1.0; >1.0 means the feature
    bucket has historically outperformed the global baseline.
    """

    def __init__(self) -> None:
        self.snapshot = LearningSnapshot()
        self.enabled = True

    def load(self) -> None:
        try:
            with SessionLocal() as db:
                rows = db.execute(select(LearnedPrior)).scalars().all()
        except SQLAlchemyError as exc:
            logger.warning("learning_priors_load_failed err=%s", exc)
            self.enabled = False
            return

        snap = LearningSnapshot()
        for row in rows:
            mult = self._row_to_multiplier(row)
            target = self._target_for_snap(snap, row.feature_key)
            if target is not None:
                target[row.bucket] = mult
            if row.feature_key == "_global":
                snap.global_win_rate = row.win_rate
                snap.global_sample = row.sample_size

        self.snapshot = snap
        logger.info(
            "learning_priors_loaded global_win_rate=%.3f global_n=%d categories=%d price=%d spread=%d time=%d confidence=%d",
            snap.global_win_rate, snap.global_sample,
            len(snap.by_category), len(snap.by_price), len(snap.by_spread),
            len(snap.by_time), len(snap.by_confidence),
        )

    @staticmethod
    def _target_for_snap(snap: "LearningSnapshot", feature_key: str) -> dict[str, FeatureMultiplier] | None:
        if feature_key == "category":
            return snap.by_category
        if feature_key == "price_bucket":
            return snap.by_price
        if feature_key == "spread_bucket":
            return snap.by_spread
        if feature_key == "time_bucket":
            return snap.by_time
        if feature_key == "confidence_bucket":
            return snap.by_confidence
        return None

    def _target_for(self, feature_key: str) -> dict[str, FeatureMultiplier] | None:
        return self._target_for_snap(self.snapshot, feature_key)

    @staticmethod
    def _row_to_multiplier(row: "LearnedPrior") -> FeatureMultiplier:
        mult = row.multiplier if row.multiplier > 0 else 1.0
        return FeatureMultiplier(
            feature_key=row.feature_key,
            bucket=row.bucket,
            win_rate=row.win_rate,
            sample_size=row.sample_size,
            multiplier=mult,
        )

    def adjustment_for(self, category: str, entry_price: float, spread_cents: float, minutes_to_close: float | None, confidence: float) -> dict[str, Any]:
        """Return a composite multiplier (geometric mean of bucket multipliers).

        Geometric mean keeps the effect bounded so a single strongly negative
        bucket cannot collapse the score to zero. Buckets with <MIN_SAMPLES_FOR_TRUST
        samples are skipped to avoid letting noise drive trading.
        """
        if not self.enabled:
            return {"multiplier": 1.0, "components": {}, "trusted": False}

        buckets = bucket_features(category, entry_price, spread_cents, minutes_to_close, confidence)
        components: dict[str, float] = {}
        log_sum = 0.0
        used = 0

        for feature_key, bucket in buckets.items():
            target = self._target_for(feature_key)
            if not target:
                continue
            mult = target.get(bucket)
            if not mult:
                continue
            if mult.sample_size < MIN_SAMPLES_FOR_TRUST:
                continue
            components[f"{feature_key}:{bucket}"] = round(mult.multiplier, 3)
            log_sum += math.log(max(0.25, min(2.5, mult.multiplier)))
            used += 1

        if used == 0:
            return {"multiplier": 1.0, "components": {}, "trusted": False}
        composite = math.exp(log_sum / used)
        return {
            "multiplier": round(max(0.5, min(2.0, composite)), 4),
            "components": components,
            "trusted": True,
        }

    def rebuild_priors(self, lookback_days: int = 30) -> dict[str, Any]:
        """Recompute priors from OrderRecord + AuditRun history.

        For each closed order whose ticker has a matching settlement in
        AuditRun by_category_json or order outcome (status='won'/'lost'),
        increment win/loss buckets. Persist to LearnedPrior table.
        """
        from datetime import datetime, timedelta, timezone

        cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

        try:
            with SessionLocal() as db:
                orders = db.execute(
                    select(OrderRecord).where(OrderRecord.created_at >= cutoff)
                ).scalars().all()
        except SQLAlchemyError as exc:
            logger.warning("learning_priors_rebuild_failed err=%s", exc)
            return {"status": "failed", "error": str(exc)}

        if not orders:
            logger.info("learning_priors_rebuild_skipped reason=no_orders lookback_days=%d", lookback_days)
            return {"status": "no_data", "orders_seen": 0}

        feature_counts: dict[str, dict[str, dict[str, int]]] = {
            "category": {},
            "price_bucket": {},
            "spread_bucket": {},
            "time_bucket": {},
            "confidence_bucket": {},
        }
        global_wins = 0
        global_losses = 0

        for order in orders:
            outcome = self._infer_outcome(order)
            if outcome is None:
                continue
            won = outcome == "won"
            global_wins += int(won)
            global_losses += int(not won)

            features = self._safe_json(order.features_json) or {}
            if not features:
                features = self._derive_features_from_order(order)
            for feature_key, bucket in features.items():
                bucket_map = feature_counts.setdefault(feature_key, {}).setdefault(bucket, {"wins": 0, "total": 0})
                bucket_map["wins"] += int(won)
                bucket_map["total"] += 1

        total = global_wins + global_losses
        if total == 0:
            logger.info("learning_priors_rebuild_skipped reason=no_outcomes orders_seen=%d", len(orders))
            return {"status": "no_outcomes", "orders_seen": len(orders)}

        global_win_rate = global_wins / total
        rows_written = 0
        try:
            with SessionLocal() as db:
                db.query(LearnedPrior).delete()
                db.add(LearnedPrior(
                    feature_key="_global",
                    bucket="_all",
                    sample_size=total,
                    wins=global_wins,
                    win_rate=round(global_win_rate, 4),
                    multiplier=1.0,
                ))
                rows_written += 1
                for feature_key, buckets in feature_counts.items():
                    for bucket, counts in buckets.items():
                        wins = counts["wins"]
                        n = counts["total"]
                        if n == 0:
                            continue
                        shrunk = shrinkage_win_rate(wins, n, baseline=global_win_rate)
                        multiplier = 1.0 + ((shrunk - global_win_rate) * 2.0)
                        multiplier = max(0.5, min(2.0, multiplier))
                        db.add(LearnedPrior(
                            feature_key=feature_key,
                            bucket=bucket,
                            sample_size=n,
                            wins=wins,
                            win_rate=round(shrunk, 4),
                            multiplier=round(multiplier, 4),
                        ))
                        rows_written += 1
                db.commit()
        except SQLAlchemyError as exc:
            logger.warning("learning_priors_persist_failed err=%s", exc)
            return {"status": "persist_failed", "error": str(exc)}

        self.load()
        logger.info(
            "learning_priors_rebuilt orders=%d outcomes=%d global_win_rate=%.3f rows=%d lookback_days=%d",
            len(orders), total, global_win_rate, rows_written, lookback_days,
        )
        return {
            "status": "ok",
            "orders": len(orders),
            "outcomes": total,
            "global_win_rate": round(global_win_rate, 4),
            "rows": rows_written,
        }

    @staticmethod
    def _safe_json(text: str | None) -> dict[str, Any] | None:
        if not text:
            return None
        try:
            value = json.loads(text)
            return value if isinstance(value, dict) else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _infer_outcome(order: "OrderRecord") -> str | None:
        status = str(order.status or "").lower()
        if status in {"won", "win", "settled_win"}:
            return "won"
        if status in {"lost", "loss", "settled_loss"}:
            return "lost"
        return None

    @staticmethod
    def _derive_features_from_order(order: "OrderRecord") -> dict[str, str]:
        price = float(order.price_cents or 0) / 100.0
        return bucket_features(
            category=order.category or "unknown",
            entry_price=price,
            spread_cents=0.0,
            minutes_to_close=None,
            confidence=0.0,
        )


_GLOBAL_ENGINE: LearningEngine | None = None


def get_learning_engine() -> LearningEngine:
    global _GLOBAL_ENGINE
    if _GLOBAL_ENGINE is None:
        _GLOBAL_ENGINE = LearningEngine()
        _GLOBAL_ENGINE.load()
    return _GLOBAL_ENGINE
