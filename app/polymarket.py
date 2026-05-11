from __future__ import annotations

import asyncio
import logging
import math
import os
from dataclasses import dataclass
from typing import Any, Optional

from app.config import settings

try:
    from polymarket_us import PolymarketUS
except Exception:  # pragma: no cover
    PolymarketUS = None

log = logging.getLogger("app.polymarket")

POLYMARKET_API_BASE = os.getenv("POLYMARKET_API_BASE", settings.polymarket_api_base_url)
POLYMARKET_GATEWAY_BASE = os.getenv("POLYMARKET_GATEWAY_BASE", settings.polymarket_gateway_base_url)
ORDERBOOK_DEPTH = int(os.getenv("ORDERBOOK_DEPTH", "25"))
POLYMARKET_MARKET_PAGE_SIZE = int(os.getenv("POLYMARKET_MARKET_PAGE_SIZE", "100"))
POLYMARKET_MAX_PAGES = int(os.getenv("POLYMARKET_MAX_PAGES", "10"))
POLYMARKET_ORDER_TIF = os.getenv("POLYMARKET_ORDER_TIF", "TIME_IN_FORCE_IMMEDIATE_OR_CANCEL")


@dataclass(slots=True)
class AuthStatus:
    ok: bool
    reason: str = ""


def _safe_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    if isinstance(value, dict) and "value" in value:
        value = value.get("value")
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class PolymarketClient:
    def __init__(self) -> None:
        self.key_id = os.getenv("POLYMARKET_KEY_ID", settings.polymarket_key_id).strip()
        self.secret_key = os.getenv("POLYMARKET_SECRET_KEY", settings.polymarket_secret_key).strip()
        self.api_base_url = POLYMARKET_API_BASE
        self.gateway_base_url = POLYMARKET_GATEWAY_BASE
        self.client = None
        self.last_paginate_pages = 0

        if PolymarketUS is not None:
            kwargs = {
                "gateway_base_url": self.gateway_base_url,
                "api_base_url": self.api_base_url,
                "timeout": 30.0,
            }
            if self.key_id and self.secret_key:
                kwargs.update({"key_id": self.key_id, "secret_key": self.secret_key})
            self.client = PolymarketUS(**kwargs)

        self.auth_status = AuthStatus(ok=bool(self.client and self.key_id and self.secret_key), reason="" if (self.key_id and self.secret_key) else "missing_polymarket_credentials")

    async def close(self) -> None:
        if self.client and hasattr(self.client, "close"):
            await asyncio.to_thread(self.client.close)

    async def _request(self, method: str, path: str, params: dict | None = None, json: dict | None = None, timeout: float = 20.0) -> dict:
        if not self.client:
            raise RuntimeError("polymarket_sdk_unavailable")
        return await asyncio.to_thread(self._sync_request, method, path, params or {}, json or {})

    def _sync_request(self, method: str, path: str, params: dict, json_payload: dict) -> dict:
        if path == "/markets/list":
            return self.client.markets.list(params)
        if path == "/markets/book":
            slug = params.get("marketSlug") or params.get("slug") or json_payload.get("marketSlug")
            return self.client.markets.book(slug)
        if path == "/markets/orderbooks":
            slugs = str(params.get("tickers") or "").split(",")
            out = []
            for slug in [s.strip() for s in slugs if s.strip()]:
                book = self.client.markets.book(slug)
                out.append({"ticker": slug, **self._normalize_orderbook(slug, book)})
            return {"orderbooks": out}
        if path == "/orders/create":
            return self.client.orders.create(json_payload)
        if path == "/orders/list":
            return self.client.orders.list(params)
        if path == "/portfolio/positions":
            return self.client.portfolio.positions(params)
        if path == "/portfolio/activities":
            return self.client.portfolio.activities(params)
        if path == "/account/balances":
            return self.client.account.balances()
        if path == "/markets/settlement":
            slug = params.get("marketSlug") or params.get("slug")
            return self.client.markets.settlement(slug)
        raise ValueError(f"unsupported_path={path}")

    def _normalize_market(self, raw: dict[str, Any]) -> dict[str, Any]:
        slug = str(raw.get("slug") or raw.get("marketSlug") or raw.get("market_slug") or "").strip()
        event_slug = str(raw.get("eventSlug") or raw.get("event_slug") or "").strip()
        last_trade = _safe_float((raw.get("stats") or {}).get("lastTradePx"), _safe_float(raw.get("lastTradePx")))
        open_interest = _safe_float((raw.get("stats") or {}).get("openInterest"), _safe_float(raw.get("openInterest")))
        return {
            "ticker": slug,
            "market_ticker": slug,
            "event_ticker": event_slug,
            "title": str(raw.get("title") or raw.get("question") or slug),
            "subtitle": str(raw.get("description") or raw.get("outcome") or ""),
            "status": "open" if bool(raw.get("active", True)) and not bool(raw.get("closed", False)) else "closed",
            "volume": _safe_float(raw.get("volume")),
            "volume_24h": _safe_float(raw.get("volume")),
            "open_interest": open_interest,
            "liquidity": _safe_float(raw.get("liquidity")),
            "last_price": last_trade,
            "close_time": str(raw.get("closeTime") or raw.get("close_time") or ""),
            "expiration_time": str(raw.get("settledAt") or raw.get("settled_at") or ""),
            "category": str(raw.get("category") or "").lower(),
            "market_type": "single",
            "legs": 1,
            "_raw": raw,
        }

    def _normalize_orderbook(self, slug: str, book: dict[str, Any]) -> dict[str, Any]:
        yes_bids: list[dict[str, float]] = []
        yes_asks: list[dict[str, float]] = []
        no_bids: list[dict[str, float]] = []
        no_asks: list[dict[str, float]] = []

        bids = list(book.get("bids") or [])[:ORDERBOOK_DEPTH]
        offers = list(book.get("offers") or [])[:ORDERBOOK_DEPTH]
        for level in bids:
            px = _safe_float(level.get("px") if isinstance(level, dict) else None)
            qty = _safe_float(level.get("qty") if isinstance(level, dict) else None, 1.0)
            if px > 0:
                yes_bids.append({"price": round(px, 6), "qty": qty})
                comp = max(0.01, min(0.99, 1.0 - px))
                no_asks.append({"price": round(comp, 6), "qty": qty})
        for level in offers:
            px = _safe_float(level.get("px") if isinstance(level, dict) else None)
            qty = _safe_float(level.get("qty") if isinstance(level, dict) else None, 1.0)
            if px > 0:
                yes_asks.append({"price": round(px, 6), "qty": qty})
                comp = max(0.01, min(0.99, 1.0 - px))
                no_bids.append({"price": round(comp, 6), "qty": qty})

        return {
            "ticker": slug,
            "yes_bids": yes_bids,
            "yes_asks": yes_asks,
            "no_bids": no_bids,
            "no_asks": no_asks,
            "bids": bids,
            "offers": offers,
            "raw": book,
        }

    async def get_open_markets(self) -> list[dict[str, Any]]:
        return await self.get_all_open_markets()

    async def get_all_open_markets(self) -> list[dict[str, Any]]:
        if not self.client:
            return []
        markets: list[dict[str, Any]] = []
        offset = 0
        pages = 0
        while pages < POLYMARKET_MAX_PAGES:
            pages += 1
            data = await self._request("GET", "/markets/list", params={
                "limit": POLYMARKET_MARKET_PAGE_SIZE,
                "offset": offset,
                "active": True,
                "closed": False,
                "archived": False,
            })
            rows = list((data or {}).get("markets") or [])
            self.last_paginate_pages = pages
            if not rows:
                break
            markets.extend(self._normalize_market(row) for row in rows)
            if len(rows) < POLYMARKET_MARKET_PAGE_SIZE:
                break
            offset += POLYMARKET_MARKET_PAGE_SIZE
        return markets

    async def get_orderbooks(self, tickers: list[str], depth: int = 25) -> dict[str, dict]:
        if not tickers:
            return {}
        try:
            response = await self._request("GET", "/markets/orderbooks", params={"tickers": ",".join(tickers), "depth": depth})
            books = response.get("orderbooks") or []
            out = {}
            for book in books:
                slug = str(book.get("ticker") or book.get("marketSlug") or "").strip()
                if not slug:
                    continue
                if all(side in book for side in ("yes_bids", "yes_asks", "no_bids", "no_asks")):
                    out[slug] = book
                else:
                    out[slug] = self._normalize_orderbook(slug, book)
            return out
        except Exception:
            out: dict[str, dict] = {}
            for slug in tickers:
                try:
                    data = await self._request("GET", "/markets/orderbooks", params={"tickers": slug, "depth": depth})
                    books = list((data or {}).get("orderbooks") or [])
                    if books:
                        book = books[0]
                        out[slug] = book if all(side in book for side in ("yes_bids", "yes_asks", "no_bids", "no_asks")) else self._normalize_orderbook(slug, book)
                except Exception as exc:
                    log.warning("orderbook_fetch_failed slug=%s err=%s", slug, str(exc)[:160])
            return out

    async def get_positions(self) -> list[dict[str, Any]]:
        if not self.auth_status.ok:
            return []
        out: list[dict[str, Any]] = []
        cursor: Optional[str] = None
        while True:
            params: dict[str, Any] = {"limit": 100}
            if cursor:
                params["cursor"] = cursor
            data = await self._request("GET", "/portfolio/positions", params=params)
            positions = (data or {}).get("positions") or {}
            for _, row in positions.items():
                meta = row.get("marketMetadata") or {}
                net = _safe_float(row.get("netPosition"))
                qty = abs(int(round(net)))
                cost = _safe_float(row.get("cost"))
                avg_price = (cost / qty) if qty > 0 else 0.0
                out.append({
                    "ticker": str(meta.get("slug") or ""),
                    "event_ticker": str(meta.get("eventSlug") or ""),
                    "title": str(meta.get("title") or meta.get("slug") or ""),
                    "subtitle": str(meta.get("outcome") or ""),
                    "side": "LONG" if net >= 0 else "SHORT",
                    "quantity": qty,
                    "average_price": round(avg_price, 4),
                    "status": "open" if qty > 0 else "flat",
                    "raw": row,
                })
            cursor = (data or {}).get("nextCursor") or ""
            if not cursor or bool((data or {}).get("eof", True)):
                break
        return out

    async def get_balance(self) -> dict[str, Any]:
        if not self.auth_status.ok:
            return {"balance": 0.0, "buying_power": 0.0}
        data = await self._request("GET", "/account/balances")
        balances = list((data or {}).get("balances") or [])
        if not balances:
            return {"balance": 0.0, "buying_power": 0.0}
        selected = next((row for row in balances if str(row.get("currency") or "").upper() == "USD"), balances[0])
        return {
            "balance": _safe_float(selected.get("buyingPower"), _safe_float(selected.get("currentBalance"))),
            "buying_power": _safe_float(selected.get("buyingPower")),
            "current_balance": _safe_float(selected.get("currentBalance")),
            "currency": str(selected.get("currency") or "USD"),
        }

    async def place_order(self, ticker: str, side: str, count: int, price_cents: int) -> dict[str, Any]:
        intent = "ORDER_INTENT_BUY_LONG" if str(side).upper() == "YES" else "ORDER_INTENT_BUY_SHORT"
        price_value = max(0.01, min(0.99, price_cents / 100.0))
        payload = {
            "marketSlug": ticker,
            "intent": intent,
            "type": "ORDER_TYPE_LIMIT",
            "price": {"value": f"{price_value:.2f}", "currency": "USD"},
            "quantity": int(count),
            "tif": POLYMARKET_ORDER_TIF,
            "manualOrderIndicator": "MANUAL_ORDER_INDICATOR_AUTOMATIC",
            "synchronousExecution": True,
        }
        if not self.auth_status.ok:
            return {"status": "dry_run", "request": payload}
        try:
            data = await self._request("POST", "/orders/create", json=payload)
            return {"status": "submitted", "order_id": str(data.get("id") or ""), "raw": data}
        except Exception as exc:
            log.exception("order_submit_failed ticker=%s", ticker)
            return {"status": "rejected", "error": str(exc), "request": payload}

    async def get_settlements(self) -> list[dict[str, Any]]:
        if not self.auth_status.ok:
            return []
        try:
            data = await self._request("GET", "/portfolio/activities", params={
                "limit": 100,
                "types": ["ACTIVITY_TYPE_TRADE", "ACTIVITY_TYPE_POSITION_RESOLUTION"],
                "sortOrder": "SORT_ORDER_DESCENDING",
            })
        except Exception:
            return []
        out: list[dict[str, Any]] = []
        for activity in list((data or {}).get("activities") or []):
            if activity.get("type") == "ACTIVITY_TYPE_TRADE":
                trade = activity.get("trade") or {}
                out.append({
                    "ticker": trade.get("marketSlug"),
                    "pnl": _safe_float(trade.get("realizedPnl")),
                    "market_type": "single",
                })
            elif activity.get("type") == "ACTIVITY_TYPE_POSITION_RESOLUTION":
                resolution = activity.get("positionResolution") or {}
                after_pos = resolution.get("afterPosition") or {}
                out.append({
                    "ticker": resolution.get("marketSlug"),
                    "pnl": _safe_float(after_pos.get("realized")),
                    "market_type": "single",
                })
        return out
