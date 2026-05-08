from app.strategy import BANKROLL_RULES, CATEGORIES


def test_categories_and_sizing_contract():
    assert CATEGORIES == ["sports", "politics", "crypto", "climate", "economics"]
    assert BANKROLL_RULES[1] == 0.02
    assert BANKROLL_RULES[2] == 0.01
    assert BANKROLL_RULES[3] == 0.0075
    assert BANKROLL_RULES[4] == 0.005
