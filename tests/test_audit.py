from app.services.audit import summarize_settlements


def test_audit_summary():
    summary = summarize_settlements([
        {"category": "sports", "pnl": 5.0, "market_type": "single", "spread_cents": 4.0},
        {"category": "sports", "pnl": -4.0, "market_type": "combo", "spread_cents": 9.0},
    ])
    assert summary["total_trades"] == 2
    assert summary["wins"] == 1
    assert summary["losses"] == 1
