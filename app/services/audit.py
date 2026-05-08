from __future__ import annotations

from collections import Counter


def summarize_settlements(settlements: list[dict]) -> dict:
    wins = 0
    losses = 0
    gross_pnl = 0.0
    by_category = Counter()
    cat_wins = Counter()
    issues = Counter()

    for row in settlements:
        category = str(row.get("category") or "unknown")
        pnl = float(row.get("pnl") or 0.0)
        won = pnl > 0
        by_category[category] += 1
        gross_pnl += pnl
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
    per_category = {
        cat: {
            "trades": cnt,
            "wins": cat_wins[cat],
            "win_rate": round(cat_wins[cat] / cnt, 4) if cnt else 0.0,
        }
        for cat, cnt in by_category.items()
    }
    improvements = []
    if issues["wide_spread"]:
        improvements.append("Tighten spread limits on entries.")
    if issues["combo_usage"] > max(1, total * 0.15):
        improvements.append("Use fewer combos; singles should dominate.")
    for cat, stats in per_category.items():
        if stats["trades"] >= 5 and stats["win_rate"] < 0.5:
            improvements.append(f"Review {cat} research and selection criteria.")
    if not improvements:
        improvements.append("Selection profile is stable; keep auditing category-level drift and entry quality.")

    return {
        "total_trades": total,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / total, 4) if total else 0.0,
        "gross_pnl": round(gross_pnl, 2),
        "by_category": per_category,
        "issues": dict(issues),
        "improvements": improvements,
    }
