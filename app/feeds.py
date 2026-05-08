from __future__ import annotations

from typing import Any


async def fetch_external_context(category: str) -> dict[str, Any]:
    return {"category": category, "signals": [], "status": "ok"}
