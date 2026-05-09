from **future** import annotations

from datetime import datetime, timezone
from typing import Any

from app.strategy import CATEGORIES, SPORTS

SPORTS_HINTS = {“game”, “match”, “innings”, “quarter”, “points”, “touchdown”, “goal”, “strikeout”, “homerun”, “rebound”, “assist”, “runs”}
CRYPTO_HINTS = {“bitcoin”, “btc”, “ethereum”, “eth”, “crypto”, “solana”, “xrp”, “dogecoin”, “altcoin”}
CLIMATE_HINTS = {“temperature”, “rain”, “snow”, “wind”, “hurricane”, “weather”, “precipitation”}
POLITICS_HINTS = {“president”, “senate”, “house”, “election”, “vote”, “approval”, “governor”, “mayor”, “primary”}
ECON_HINTS = {“inflation”, “cpi”, “gdp”, “jobs”, “unemployment”, “fed”, “rate”, “economy”, “recession”, “payrolls”}
PACKAGED_HINTS = {“crosscategory”, “multigame”, “multimarket”, “combo”, “parlay”, “same game parlay”}

TICKER_PREFIX_CATEGORY = {
# sports
“KXNFL”: “sports”, “KXNBA”: “sports”, “KXMLB”: “sports”,
“KXNHL”: “sports”, “KXNCAA”: “sports”, “KXMLS”: “sports”,
“KXEPL”: “sports”, “KXUFC”: “sports”, “KXMMA”: “sports”,
“KXBOX”: “sports”, “KXTENNIS”: “sports”, “KXATP”: “sports”,
“KXWTA”: “sports”, “KXGOLF”: “sports”, “KXPGA”: “sports”,
“KXLPGA”: “sports”, “KXNASCAR”: “sports”, “KXF1”: “sports”,
“KXFORMULA”: “sports”, “KXINDYCAR”: “sports”, “KXOLY”: “sports”,
“KXFIFA”: “sports”, “KXWORLDCUP”: “sports”,
# crypto
“KXBTC”: “crypto”, “KXETH”: “crypto”, “KXSOL”: “crypto”,
“KXXRP”: “crypto”, “KXDOGE”: “crypto”, “KXCRYPTO”: “crypto”,
# economics
“KXCPI”: “economics”, “KXJOBS”: “economics”, “KXGDP”: “economics”,
“KXFED”: “economics”, “KXFOMC”: “economics”, “KXNFP”: “economics”,
“KXPCE”: “economics”, “KXUNEMP”: “economics”,
# politics
“KXPRES”: “politics”, “KXSEN”: “politics”, “KXHOUSE”: “politics”,
“KXGOV”: “politics”, “KXELEC”: “politics”, “KXPOLL”: “politics”,
# climate / weather
“KXTEMP”: “climate”, “KXRAIN”: “climate”, “KXSNOW”: “climate”,
“KXHURR”: “climate”, “KXWEATHER”: “climate”, “KXHIGH”: “climate”,
“KXLOW”: “climate”,
}

def text_blob(market: dict[str, Any]) -> str:
return “ “.join(str(market.get(k) or “”) for k in (“ticker”, “title”, “subtitle”, “event_ticker”, “series_ticker”, “event_title”)).lower()

def _to_float(*candidates: Any) -> float:
“”“Return the first non-None candidate as float. Kalshi returns numerics as strings (e.g. “0.5600”, “10.00”).”””
for c in candidates:
if c is None:
continue
try:
return float(c)
except (TypeError, ValueError):
continue
return 0.0

def detect_category(market: dict[str, Any]) -> str:
explicit = str(market.get(“category”) or “”).lower().strip()
if explicit in CATEGORIES:
return explicit
ticker = str(market.get(“ticker”) or “”).upper()
for prefix, cat in TICKER_PREFIX_CATEGORY.items():
if ticker.startswith(prefix):
return cat
text = text_blob(market)
scores = {
“sports”: sum(1 for w in SPORTS_HINTS if w in text),
“politics”: sum(1 for w in POLITICS_HINTS if w in text),
“crypto”: sum(1 for w in CRYPTO_HINTS if w in text),
“climate”: sum(1 for w in CLIMATE_HINTS if w in text),
“economics”: sum(1 for w in ECON_HINTS if w in text),
}
best = max(scores, key=scores.get)
return best if scores[best] > 0 else “unknown”

def infer_market_type(market: dict[str, Any]) -> str:
explicit = str(market.get(“market_type”) or market.get(“type”) or “”).lower().strip()
if explicit in {“single”, “combo”}:
return explicit
text = text_blob(market)
if any(h in text for h in PACKAGED_HINTS):
return “combo”
return “single”

def combo_legs(market: dict[str, Any]) -> int:
explicit = market.get(“legs”) or market.get(“leg_count”)
try:
if explicit is not None:
return max(1, min(4, int(explicit)))
except Exception:
pass
if infer_market_type(market) == “combo”:
return 2
return 1

def is_packaged_market(market: dict[str, Any]) -> bool:
return infer_market_type(market) == “combo”

def parse_close_time(raw: str | None):
if not raw:
return None
for candidate in [raw, raw.replace(“Z”, “+00:00”)]:
try:
dt = datetime.fromisoformat(candidate)
if dt.tzinfo is None:
dt = dt.replace(tzinfo=timezone.utc)
return dt.astimezone(timezone.utc)
except Exception:
continue
return None

def minutes_to_close(market: dict[str, Any]) -> float | None:
dt = parse_close_time(str(market.get(“close_time”) or market.get(“expiration_time”) or “”))
if dt is None:
return None
delta = dt - datetime.now(timezone.utc)
return delta.total_seconds() / 60.0

def normalized_market(market: dict[str, Any]) -> dict[str, Any]:
out = dict(market)
out[“category”] = detect_category(out)
out[“market_type”] = infer_market_type(out)
out[“legs”] = combo_legs(out)
out[“ticker”] = str(out.get(“ticker”) or “”)
out[“title”] = str(out.get(“title”) or out.get(“subtitle”) or “”)
out[“subtitle”] = str(out.get(“subtitle”) or “”)
out[“event_ticker”] = str(out.get(“event_ticker”) or “”)
out[“status”] = str(out.get(“status”) or “open”)
out[“volume”] = _to_float(out.get(“volume_fp”), out.get(“volume”))
out[“volume_24h”] = _to_float(out.get(“volume_24h_fp”), out.get(“volume_24h”))
out[“liquidity”] = _to_float(out.get(“liquidity_dollars”), out.get(“liquidity”))
out[“open_interest”] = _to_float(out.get(“open_interest_fp”), out.get(“open_interest”), out.get(“openInterest”))
out[“close_time”] = str(out.get(“close_time”) or out.get(“expiration_time”) or “”)
out[“minutes_to_close”] = minutes_to_close(out)
return out