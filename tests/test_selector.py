from app.selector import build_candidate, has_market_liquidity, has_valid_orderbook, normalize_markets, single_pool, validate_market_candidate


def test_single_pool_keeps_clean_single():
    markets = normalize_markets([
        {"ticker": "ABC", "title": "Will it rain tomorrow?", "volume": 120, "open_interest": 40},
        {"ticker": "KXMVECROSSCATEGORY-123", "title": "Bundled market", "volume": 500, "open_interest": 50},
    ])
    singles, rejects = single_pool(markets)
    assert len(singles) == 1
    assert singles[0]["ticker"] == "ABC"
    assert sum(rejects.values()) == 1


def test_build_candidate_returns_scored_single():
    market = normalize_markets([
        {"ticker": "ABC", "title": "Will Team A win the game?", "volume": 300, "open_interest": 120}
    ])[0]
    orderbook = {"yes": [{"price": 0.47}, {"price": 0.49}], "no": [{"price": 0.46}, {"price": 0.50}]}
    candidate, reason = build_candidate(market, orderbook)
    assert reason is None
    assert candidate is not None
    assert candidate.market_type == "single"
    assert candidate.total_score >= 74.0


def test_has_valid_orderbook_accepts_yes_and_no_sides():
    assert has_valid_orderbook({"yes": [{"price": 0.45}], "no": [{"price": 0.55}]}) is True


def test_validate_market_candidate_accepts_orderbook_even_when_metadata_zero():
    market = {"liquidity": 0, "volume_24h": 0, "open_interest": 0}
    valid, reason = validate_market_candidate(market, {"yes": [{"price": 0.4}], "no": [{"price": 0.6}]})
    assert valid is True
    assert reason == "valid"


def test_has_market_liquidity_metadata_is_ignored():
    assert has_market_liquidity({"liquidity": 0, "volume_24h": 0, "open_interest": 0}) is True
