from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Request

router = APIRouter()


@router.get('/dashboard')
async def dashboard(request: Request):
    """Return dashboard data including markets, trades, and system status."""
    from app.main import universe

    markets = []
    if universe and hasattr(universe, '_markets'):
        markets = [
            {
                'id': m.get('id', ''),
                'title': m.get('title', ''),
                'category': m.get('category', ''),
                'slug': m.get('slug', ''),
                'url': m.get('url', ''),
                'active': m.get('active', True),
                'closed': m.get('closed', False),
            }
            for m in universe._markets.values()
            if isinstance(m, dict)
        ][:50]

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
