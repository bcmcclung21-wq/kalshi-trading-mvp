"""UniverseService with concurrent fetching, thread pool scoring, shared HTTP client."""
import asyncio
import heapq
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, List, Dict, Any
import logging

from app.http_client import get_client

logger = logging.getLogger(__name__)

class UniverseService:
    def __init__(self):
        self._markets: List[Any] = []
        self._orderbooks: Dict[str, Any] = {}
        self._score_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="scorer")
        self._refresh_lock = asyncio.Lock()
        self._latency_samples_ms: List[int] = []
        self._max_latency_samples = 5000
        self._client = None
        
    async def initialize(self):
        self._client = await get_client()
        
    async def refresh(self) -> None:
        async with self._refresh_lock:
            t0 = time.monotonic()
            
            raw = await self._fetch_all_markets()
            
            loop = asyncio.get_event_loop()
            scored = await loop.run_in_executor(
                self._score_executor,
                lambda: [self._score(r) for r in raw]
            )
            
            active = [s for s in scored if self._is_active(s)]
            self._markets = active[:150]
            
            await self._fetch_orderbooks_concurrent()
            
            latency_ms = int((time.monotonic() - t0) * 1000)
            self._latency_samples_ms.append(latency_ms)
            if len(self._latency_samples_ms) > self._max_latency_samples:
                self._latency_samples_ms = self._latency_samples_ms[-self._max_latency_samples:]
            
            logger.info("refresh_complete markets=%d orderbooks=%d latency_ms=%d",
                       len(raw), len(self._orderbooks), latency_ms)
    
    async def _fetch_all_markets(self) -> List[dict]:
        resp = await self._client.get(
            "https://gamma-api.polymarket.com/markets",
            params={"limit": 100, "offset": 0, "closed": "false", "active": "true"}
        )
        resp.raise_for_status()
        first_batch = resp.json()
        
        if len(first_batch) < 100:
            return first_batch
            
        pages = [first_batch]
        offsets = range(100, 1000, 100)
        
        async def _fetch_page(offset):
            r = await self._client.get(
                "https://gamma-api.polymarket.com/markets",
                params={"limit": 100, "offset": offset, "closed": "false", "active": "true"}
            )
            r.raise_for_status()
            return r.json()
        
        remaining = await asyncio.gather(*[_fetch_page(o) for o in offsets])
        for batch in remaining:
            pages.extend(batch)
            if len(batch) < 100:
                break
                
        return [m for page in pages for m in page]
    
    async def _fetch_orderbooks_concurrent(self, max_concurrency: int = 20) -> None:
        self._orderbooks.clear()
        if not self._markets:
            return
            
        semaphore = asyncio.Semaphore(max_concurrency)
        
        async def _fetch_one(market) -> Optional[tuple]:
            async with semaphore:
                token_id = self._get_yes_token_id(market)
                if not token_id:
                    return None
                try:
                    resp = await self._client.get(
                        "https://clob.polymarket.com/book",
                        params={"token_id": token_id},
                        timeout=10
                    )
                    if resp.status_code == 200:
                        data = resp.json()
                        ob = self._convert_clob_orderbook(data, token_id, market)
                        return (market.id, getattr(market, "slug", None), ob)
                except Exception as e:
                    logger.debug("ob_fetch_fail token=%s... error=%s", str(token_id)[:16], e)
                return None
        
        results = await asyncio.gather(*[_fetch_one(m) for m in self._markets])
        for res in results:
            if res:
                mid, slug, ob = res
                self._orderbooks[mid] = ob
                if slug:
                    self._orderbooks[slug] = ob
    
    @property
    def processing_latency_p99_ms(self) -> int:
        if not self._latency_samples_ms:
            return 0
        n = len(self._latency_samples_ms)
        if n <= 100:
            return max(self._latency_samples_ms)
        return heapq.nlargest(max(1, n // 100), self._latency_samples_ms)[-1]
    
    async def aclose(self):
        self._score_executor.shutdown(wait=True)
    
    # ========== PORTED FROM ORIGINAL CODE — DO NOT MODIFY SIGNATURES ==========
    
    def _score(self, raw: dict) -> Any:
        """Parse raw Gamma API market dict into Market model.
        
        FIX: Replaced broken Market.from_gamma(raw) with Pydantic v2/v1 
        compatible constructor. If your Market model has a custom from_gamma
        factory method, replace the body below with that call.
        """
        from app.models import Market  # Adjust import path to your actual Market model
        
        # Pydantic v2 (default for pydantic>=2.0)
        if hasattr(Market, 'model_validate'):
            return Market.model_validate(raw)
        # Pydantic v1 fallback (pydantic<<2.0)
        elif hasattr(Market, 'parse_obj'):
            return Market.parse_obj(raw)
        # Direct instantiation fallback
        else:
            return Market(**raw)
    
    def _is_active(self, scored: Any) -> bool:
        """Return True if market should be included in active set.
        Port this from your original universe.py _is_active or filter logic.
        """
        # Default: include all scored markets. Replace with your actual filter.
        return getattr(scored, 'active', True) if hasattr(scored, 'active') else True
    
    def _get_yes_token_id(self, market: Any) -> Optional[str]:
        """Extract YES token ID from market for CLOB orderbook fetch.
        Port this from your original universe.py token extraction logic.
        """
        # Common Polymarket patterns — adjust to match your Market model
        if hasattr(market, 'tokens') and market.tokens:
            for token in market.tokens:
                if getattr(token, 'outcome', '').lower() in ('yes', 'yes token'):
                    return getattr(token, 'token_id', None) or getattr(token, 'id', None)
        if hasattr(market, 'yes_token_id'):
            return market.yes_token_id
        if hasattr(market, 'token_id'):
            return market.token_id
        return None
    
    def _convert_clob_orderbook(self, data: dict, token_id: str, market: Any) -> Any:
        """Convert CLOB API response to internal orderbook format.
        Port this from your original universe.py orderbook parsing.
        """
        # Minimal pass-through. Replace with your actual OrderBook model constructor.
        from app.models import OrderBook  # Adjust import path
        if hasattr(OrderBook, 'model_validate'):
            return OrderBook.model_validate(data)
        elif hasattr(OrderBook, 'parse_obj'):
            return OrderBook.parse_obj(data)
        else:
            return OrderBook(**data)
