import os
import asyncio
import logging
from typing import Optional, Dict, Any, List

from polymarket_us import PolymarketUS

logger = logging.getLogger("app.polymarket")


class PolyMarketAPI:
    def __init__(self):
        key_id = os.getenv("POLYMARKET_KEY_ID")
        secret_key = os.getenv("POLYMARKET_SECRET_KEY")
        if not key_id or not secret_key:
            raise RuntimeError("POLYMARKET_KEY_ID and POLYMARKET_SECRET_KEY required")

        kwargs = {
            "key_id": key_id,
            "secret_key": secret_key,
        }
        gateway_base_url = os.getenv("POLYMARKET_GATEWAY_BASE")
        api_base_url = os.getenv("POLYMARKET_API_BASE")
        if gateway_base_url:
            kwargs["gateway_base_url"] = gateway_base_url
        if api_base_url:
            kwargs["api_base_url"] = api_base_url

        self.client = PolymarketUS(**kwargs)
        self._auth_ok = False

    async def health_check(self) -> bool:
        try:
            bal = await asyncio.to_thread(self.client.account.balances)
            self._auth_ok = bool(bal)
            return self._auth_ok
        except Exception:
            logger.error("auth_health_check_failed", exc_info=True)
            self._auth_ok = False
            return False

    @property
    def auth_ok(self) -> bool:
        return self._auth_ok

    async def get_orderbook(self, ticker: str) -> Optional[Dict[str, Any]]:
        try:
            return await asyncio.to_thread(self.client.markets.book, ticker)
        except Exception as e:
            logger.warning("orderbook_fetch_failed", extra={"ticker": ticker, "error": str(e)})
            return None

    async def place_buy_order(self, ticker: str, price: float, size: float, outcome: str = "YES") -> Optional[Dict]:
        if not self._auth_ok:
            logger.error("place_buy_order_rejected_no_auth", extra={"ticker": ticker})
            return None
        if os.getenv("AUTO_EXECUTE", "false").lower() != "true":
            logger.info("dry_run_buy", extra={"ticker": ticker, "price": price, "size": size, "outcome": outcome})
            return {"dry_run": True}
        try:
            payload = {
                "market": ticker,
                "side": "buy",
                "price": price,
                "size": size,
                "outcome": outcome,
            }
            result = await asyncio.to_thread(self.client.orders.create, payload)
            logger.info("buy_order_submitted", extra={"ticker": ticker, "outcome": outcome, "result": result})
            return result
        except Exception as e:
            logger.error("buy_order_submit_failed", extra={"ticker": ticker, "error": str(e)})
            return None

    async def place_sell_order(self, ticker: str, price: float, size: float, outcome: str = "YES") -> Optional[Dict]:
        if not self._auth_ok:
            logger.error("place_sell_order_rejected_no_auth", extra={"ticker": ticker})
            return None
        if os.getenv("AUTO_EXECUTE", "false").lower() != "true":
            logger.info("dry_run_sell", extra={"ticker": ticker, "price": price, "size": size, "outcome": outcome})
            return {"dry_run": True}
        try:
            payload = {
                "market": ticker,
                "side": "sell",
                "price": price,
                "size": size,
                "outcome": outcome,
            }
            result = await asyncio.to_thread(self.client.orders.create, payload)
            logger.info("sell_order_submitted", extra={"ticker": ticker, "outcome": outcome, "result": result})
            return result
        except Exception as e:
            logger.error("sell_order_submit_failed", extra={"ticker": ticker, "error": str(e)})
            return None

    async def get_markets(self, limit: int = 100, offset: int = 0) -> List[Dict]:
        try:
            return await asyncio.to_thread(
                self.client.markets.list,
                {"limit": limit, "offset": offset, "active": "true", "closed": "false", "archived": "false"}
            )
        except Exception as e:
            logger.error("markets_fetch_failed", extra={"error": str(e)})
            return []

    async def get_positions(self, limit: int = 100) -> list:
        try:
            return await asyncio.to_thread(self.client.portfolio.positions, {"limit": limit})
        except Exception as e:
            logger.error("positions_fetch_failed", extra={"error": str(e)})
            return []

    async def get_activities(self, limit: int = 100) -> list:
        try:
            return await asyncio.to_thread(self.client.portfolio.activities, {"limit": limit})
        except Exception as e:
            logger.error("activities_fetch_failed", extra={"error": str(e)})
            return []

    async def get_balances(self) -> Optional[Dict]:
        try:
            return await asyncio.to_thread(self.client.account.balances)
        except Exception as e:
            logger.error("balances_fetch_failed", extra={"error": str(e)})
            return None
