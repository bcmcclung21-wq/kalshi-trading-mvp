from app.selector import build_candidate, normalize_markets, single_pool


def test_single_pool_keeps_clean_single():
    markets = normalize_markets([
        {"ticker": "ABC", "title": "Will it rain tomorrow?", "volume": 120, "open_interest": 40},
        {"ticker": "KXMVECROSSCATEGORY-123", "title": "Bundled market", "volume": 500, "open_interest": 50},
    ])
    singles = single_pool(markets)
    assert len(singles) == 1
    assert singles[0]["ticker"] == "ABC"


def test_build_candidate_returns_scored_single():
    market = normalize_markets([
        {"ticker": "ABC", "title": "Will Team A win the game?", "volume": 300, "open_interest": 120}
    ])[0]
    orderbook = {"yes": [{"price": 0.47}, {"price": 0.49}], "no": [{"price": 0.46}, {"price": 0.50}]}
    candidate = build_candidate(market, orderbook)
    assert candidate is not None
    assert candidate.market_type == "single"
    assert candidate.total_score >= 74.0
