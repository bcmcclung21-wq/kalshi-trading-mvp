import httpx
import pytest

from app.kalshi import KalshiClient


@pytest.mark.asyncio
async def test_orderbook_400_isolates_invalid_ticker():
    client = KalshiClient()
    calls = []

    async def fake_request(method, path, params=None, json=None, timeout=20.0):
        calls.append(params["tickers"])
        tickers = params["tickers"].split(",")
        if len(tickers) > 1:
            req = httpx.Request("GET", "https://example.com")
            res = httpx.Response(400, request=req)
            raise httpx.HTTPStatusError("bad", request=req, response=res)
        if tickers[0] == "BAD":
            return {"orderbooks": []}
        return {"orderbooks": [{"ticker": tickers[0], "yes": [], "no": []}]}

    client._request = fake_request  # type: ignore[assignment]
    books = await client.get_orderbooks(["A", "BAD", "B"], depth=25)
    assert set(books.keys()) == {"A", "B"}
    assert any(c == "BAD" for c in calls)

