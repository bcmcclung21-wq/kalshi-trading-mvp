"""Daily settlement audit with continuous-improvement output.

Replaces the old category-only summary with:
  - feature-bucket win/loss breakdown
  - calibration check (did predicted win-probability match reality?)
  - actionable improvements derived from where we underperformed
  - trigger for LearningEngine.rebuild_priors so the next day's scoring
    leans on the most recent evidence
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from typing import Any

logger = logging.getLogger(__name__)


def _bucket_outcomes(settlements: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, int]]]:
    breakdown: dict[str, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(lambda: {"wins": 0, "total": 0, "pnl": 0.0}))
    for row in settlements:
        features = row.get("features") or {}
        won = float(row.get("pnl") or 0.0) > 0
        pnl = float(row.get("pnl") or 0.0)
        for feature_key, bucket in features.items():
            entry = breakdown[feature_key][bucket]
            entry["wins"] += int(won)
            entry["total"] += 1
            entry["pnl"] = round(entry["pnl"] + pnl, 4)
    return {k: dict(v) for k, v in breakdown.items()}


def _calibration_check(settlements: list[dict[str, Any]]) -> dict[str, Any]:
    buckets = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.0)]
    per_bucket: list[dict[str, Any]] = []
    grand_n = 0
    grand_brier = 0.0
    for low, high in buckets:
        n = 0
        wins = 0
        prob_sum = 0.0
        brier = 0.0
        for row in settlements:
            p = float(row.get("estimated_win_probability") or 0.0)
            if not (low <= p < high) and not (high == 1.0 and p == 1.0):
                continue
            n += 1
            won = float(row.get("pnl") or 0.0) > 0
            wins += int(won)
            prob_sum += p
            brier += (p - (1.0 if won else 0.0)) ** 2
        if n == 0:
            per_bucket.append({"range": f"{low:.1f}-{high:.1f}", "n": 0})
            continue
        avg_predicted = prob_sum / n
        actual = wins / n
        per_bucket.append({
            "range": f"{low:.1f}-{high:.1f}",
            "n": n,
            "predicted": round(avg_predicted, 3),
            "actual": round(actual, 3),
            "gap": round(actual - avg_predicted, 3),
        })
        grand_n += n
        grand_brier += brier
    return {
        "buckets": per_bucket,
        "total_n": grand_n,
        "brier_score": round(grand_brier / grand_n, 4) if grand_n else None,
    }


def _improvements_from_breakdown(
    breakdown: dict[str, dict[str, dict[str, int]]],
    calibration: dict[str, Any],
    global_win_rate: float,
) -> list[str]:
    out: list[str] = []
    weak_threshold = max(0.40, global_win_rate - 0.10)

    for feature_key, buckets in breakdown.items():
        weakest_bucket = None
        weakest_rate = 1.0
        for bucket, stats in buckets.items():
            n = stats["total"]
            if n < 8:
                continue
            rate = stats["wins"] / n
            if rate < weakest_rate:
                weakest_rate = rate
                weakest_bucket = bucket
        if weakest_bucket and weakest_rate < weak_threshold:
            out.append(
                f"Reduce exposure to {feature_key}={weakest_bucket} "
                f"(win_rate={weakest_rate:.2f} vs baseline {global_win_rate:.2f})"
            )

    for feature_key, buckets in breakdown.items():
        for bucket, stats in buckets.items():
            n = stats["total"]
            if n < 8:
                continue
            rate = stats["wins"] / n
            if rate > global_win_rate + 0.10:
                out.append(
                    f"Increase exposure to {feature_key}={bucket} "
                    f"(win_rate={rate:.2f} vs baseline {global_win_rate:.2f}, n={n})"
                )

    brier = calibration.get("brier_score")
    if brier is not None:
        if brier > 0.28:
            out.append(
                f"Confidence model is poorly calibrated (Brier={brier:.3f}). "
                "Consider tightening MIN_CONFIDENCE_SCORE."
            )
        elif brier < 0.18:
            out.append(
                f"Confidence model well-calibrated (Brier={brier:.3f}). "
                "Could safely lower MIN_CONFIDENCE_SCORE to increase volume."
            )

    for bucket in calibration.get("buckets") or []:
        if bucket.get("n", 0) < 5:
            continue
        gap = bucket.get("gap")
        if gap is not None and abs(gap) > 0.15:
            direction = "over" if gap < 0 else "under"
            out.append(
                f"Model {direction}-confident in {bucket['range']} band "
                f"(predicted {bucket['predicted']}, actual {bucket['actual']}, n={bucket['n']})"
            )

    if not out:
        out.append("Selection profile is stable; continue monitoring drift.")
    return out


def summarize_settlements(settlements: list[dict[str, Any]]) -> dict[str, Any]:
    wins = 0
    losses = 0
    gross_pnl = 0.0
    by_category: Counter[str] = Counter()
    cat_wins: Counter[str] = Counter()
    cat_pnl: dict[str, float] = defaultdict(float)
    issues: Counter[str] = Counter()

    for row in settlements:
        category = str(row.get("category") or "unknown")
        pnl = float(row.get("pnl") or 0.0)
        won = pnl > 0
        by_category[category] += 1
        gross_pnl += pnl
        cat_pnl[category] += pnl
        if won:
            wins += 1
            cat_wins[category] += 1
        else:
            losses += 1
        if float(row.get("spread_cents") or 0.0) > 8:
            issues["wide_spread"] += 1
        if str(row.get("market_type") or "single") == "combo":
            issues["combo_usage"] += 1

    total = wins + losses
    global_win_rate = wins / total if total else 0.5

    per_category = {
        cat: {
            "trades": cnt,
            "wins": cat_wins[cat],
            "win_rate": round(cat_wins[cat] / cnt, 4) if cnt else 0.0,
            "pnl": round(cat_pnl[cat], 2),
        }
        for cat, cnt in by_category.items()
    }

    feature_breakdown = _bucket_outcomes(settlements)
    calibration = _calibration_check(settlements)
    improvements = _improvements_from_breakdown(feature_breakdown, calibration, global_win_rate)

    if issues.get("wide_spread") and issues["wide_spread"] >= max(2, total * 0.20):
        improvements.append("Tighten MAX_SPREAD_CENTS; wide-spread trades are over-represented.")
    if issues.get("combo_usage", 0) > max(1, total * 0.15):
        improvements.append("Use fewer combos; singles should dominate.")

    return {
        "total_trades": total,
        "wins": wins,
        "losses": losses,
        "win_rate": round(global_win_rate, 4),
        "gross_pnl": round(gross_pnl, 2),
        "by_category": per_category,
        "issues": dict(issues),
        "improvements": improvements,
        "feature_breakdown": feature_breakdown,
        "calibration": calibration,
    }
