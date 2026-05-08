from app.classifier import detect_category, infer_market_type, normalized_market


def test_detect_category_crypto():
    assert detect_category({"title": "Will Bitcoin trade above 120k?"}) == "crypto"


def test_infer_combo_market():
    assert infer_market_type({"ticker": "KXMVECROSSCATEGORY-123"}) == "combo"


def test_normalized_market_adds_fields():
    market = normalized_market({"ticker": "ABC", "title": "Will it rain tomorrow?", "volume": 100, "open_interest": 20})
    assert market["ticker"] == "ABC"
    assert market["category"] in {"climate", "unknown"}
