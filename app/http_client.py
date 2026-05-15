"""Shared persistent HTTP client with HTTP/2 multiplexing."""
import httpx
import asyncio
from typing import Optional


class SharedHTTPClient:
    _instance: Optional[httpx.AsyncClient] = None
    _lock = asyncio.Lock()

    @classmethod
    async def get_client(cls) -> httpx.AsyncClient:
        if cls._instance is None:
            async with cls._lock:
                if cls._instance is None:
                    cls._instance = httpx.AsyncClient(
                        timeout=30,
                        headers={"User-Agent": "PolyTradingMVP/1.4"},
                        limits=httpx.Limits(
                            max_connections=100,
                            max_keepalive_connections=50,
                        ),
                        http2=True,
                    )
        return cls._instance

    @classmethod
    async def close(cls):
        if cls._instance:
            await cls._instance.aclose()
            cls._instance = None


async def get_client() -> httpx.AsyncClient:
    return await SharedHTTPClient.get_client()
