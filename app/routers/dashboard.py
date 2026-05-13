from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Request

router = APIRouter()


@router.get('/dashboard')
async def dashboard(request: Request):
    """Return dashboard data including markets, trades, and system status."""
    try:
        from app.main import universe
    except Exception:
        universe = None

    markets: list[dict] = []
    if universe is not None and hasattr(universe, '_markets'):
        raw = universe._markets
        if isinstance(raw, dict):
            items = raw.values()
        elif isinstance(raw, list):
            items = raw
        else:
            items = []

        for market in items:
            if not isinstance(market, dict):
                continue
            markets.append(
                {
                    'id': market.get('id', ''),
                    'title': market.get('title', 'Untitled'),
                    'category': market.get('category', 'unknown'),
                    'slug': market.get('slug', ''),
                    'url': market.get('url', ''),
                    'active': market.get('active', True),
                    'closed': market.get('closed', False),
                }
            )
            if len(markets) >= 50:
                break

    return {
        'status': 'ok',
        'timestamp': datetime.utcnow().isoformat(),
        'markets': markets,
        'markets_count': len(markets),
        'trades': [],
        'balance': None,
        'auto_execute': False,
        'allow_combos': False,
    }
