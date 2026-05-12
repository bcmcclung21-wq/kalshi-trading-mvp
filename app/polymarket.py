import os
import asyncio
import logging
from typing import Optional, Dict, Any
from polymarket import PolymarketClient  # pip install polymarket-python

logger = logging.getLogger("app.polymarket")

class PolyMarketAPI:
    def __init__(self):
        key_id = os.getenv("POLYMARKET_KEY_ID")
        secret_key = os.getenv("POLYMARKET_SECRET_KEY")
        if not key_id or not secret_key:
            raise RuntimeError("POLYMARKET_KEY_ID and POLYMARKET_SECRET_KEY required")
        self.client = PolymarketClient(key_id=key_id, secret_key=secret_key)
        self._auth_ok = False

    async def health_check(self) -> bool:
        try:
            bal = await asyncio.to_thread(self.client.get_account_balances)
            self._auth_ok = bool(bal)
            return self._auth_ok
        except Exception as e:
            logger.error("auth_health_check_failed", exc_info=True)
            self._auth_ok = False
            return False

    @property
    def auth_ok(self) -> bool:
        return self._auth_ok

    async def get_orderbook(self, ticker: str) -> Optional[Dict[str, Any]]:
        try:
            return await asyncio.to_thread(self.client.get_market_orderbook, ticker)
        except Exception as e:
            logger.warning("orderbook_fetch_failed", extra={"ticker": ticker, "error": str(e)})
            return None

    async def place_buy_order(self, ticker: str, price: float, size: float) -> Optional[Dict]:
        if not self._auth_ok:
            logger.error("place_buy_order_rejected_no_auth", extra={"ticker": ticker})
            return None
        if os.getenv("AUTO_EXECUTE", "false").lower() != "true":
            logger.info("dry_run_buy", extra={"ticker": ticker, "price": price, "size": size})
            return {"dry_run": True}
        try:
            result = await asyncio.to_thread(
                self.client.create_order,
                market=ticker,
                side="buy",
                price=price,
                size=size,
            )
            logger.info("buy_order_submitted", extra={"ticker": ticker, "result": result})
            return result
        except Exception as e:
            logger.error("buy_order_submit_failed", extra={"ticker": ticker, "error": str(e)})
            return None

    async def place_sell_order(self, ticker: str, price: float, size: float) -> Optional[Dict]:
        if not self._auth_ok:
            logger.error("place_sell_order_rejected_no_auth", extra={"ticker": ticker})
            return None
        if os.getenv("AUTO_EXECUTE", "false").lower() != "true":
            logger.info("dry_run_sell", extra={"ticker": ticker, "price": price, "size": size})
            return {"dry_run": True}
        try:
            result = await asyncio.to_thread(
                self.client.create_order,
                market=ticker,
                side="sell",
                price=price,
                size=size,
            )
            logger.info("sell_order_submitted", extra={"ticker": ticker, "result": result})
            return result
        except Exception as e:
            logger.error("sell_order_submit_failed", extra={"ticker": ticker, "error": str(e)})
            return None

    async def get_positions(self, limit: int = 100) -> list:
        try:
            return await asyncio.to_thread(self.client.get_positions, limit=limit)
        except Exception as e:
            logger.error("positions_fetch_failed", extra={"error": str(e)})
            return []

    async def get_activities(self, limit: int = 100) -> list:
        try:
            return await asyncio.to_thread(self.client.get_activities, limit=limit)
        except Exception as e:
            logger.error("activities_fetch_failed", extra={"error": str(e)})
            return []

    async def get_balances(self) -> Optional[Dict]:
        try:
            return await asyncio.to_thread(self.client.get_account_balances)
        except Exception as e:
            logger.error("balances_fetch_failed", extra={"error": str(e)})
            return None
