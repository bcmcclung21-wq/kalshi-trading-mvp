from app.risk import contract_count, trade_notional


def test_trade_notional():
    assert trade_notional(1000, 1) == 20.0
    assert trade_notional(1000, 4) == 5.0


def test_contract_count():
    assert contract_count(1000, 1, 0.5) == 40
