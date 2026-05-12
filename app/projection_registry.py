from typing import Dict, Optional, Tuple
from dataclasses import dataclass
from abc import ABC, abstractmethod
import re
import math
from datetime import datetime

import requests


@dataclass
class ProjectionResult:
    fair_value: float
    confidence: float
    projection: float
    metadata: dict


class BaseProjectionModel(ABC):
    @abstractmethod
    def project(self, ticker: str, market_data: dict, orderbook: dict) -> ProjectionResult:
        raise NotImplementedError

    @property
    @abstractmethod
    def supported_families(self) -> list[str]:
        raise NotImplementedError


class TemperatureProjectionModel(BaseProjectionModel):
    API = "https://api.weather.gov"
    STATIONS = {"lax": "KLAX", "sfo": "KSFO", "mia": "KMIA", "nyc": "KNYC", "mdw": "KMDW"}
    COORDS = {
        "lax": "33.9425,-118.4081",
        "sfo": "37.6213,-122.3790",
        "mia": "25.7959,-80.2870",
        "nyc": "40.7831,-73.9712",
        "mdw": "41.7868,-87.7522",
    }
    CLIM = {
        "lax": [68, 68, 69, 70, 72, 74, 76, 77, 76, 74, 70, 68],
        "sfo": [58, 60, 62, 63, 65, 67, 67, 68, 70, 68, 63, 58],
        "mia": [76, 77, 79, 81, 84, 87, 89, 89, 88, 85, 81, 77],
        "nyc": [39, 42, 50, 61, 71, 79, 84, 83, 75, 64, 54, 43],
        "mdw": [32, 36, 47, 59, 70, 80, 84, 82, 75, 62, 49, 37],
    }

    def __init__(self, cache_ttl: int = 300):
        self._cache: Dict[str, tuple] = {}
        self._cache_ttl = cache_ttl

    @property
    def supported_families(self) -> list[str]:
        return [f"tc-temp-{c}high" for c in self.STATIONS]

    def project(self, ticker, market_data, orderbook) -> ProjectionResult:
        parsed = self._parse_ticker(ticker)
        if not parsed:
            return self._error("parse_failed", ticker)
        city, target_date, temp_range = parsed
        forecast = self._get_forecast(city, target_date)
        if not forecast:
            return self._error("forecast_unavailable", ticker)
        prob = self._range_prob(forecast, temp_range)
        conf = self._confidence(forecast, target_date)
        return ProjectionResult(
            fair_value=prob,
            confidence=conf,
            projection=forecast.get("expected_high", 0),
            metadata={
                "city": city,
                "station": self.STATIONS.get(city),
                "source": forecast.get("source", "NWS"),
                "range": temp_range,
                "forecast_high": forecast.get("expected_high"),
                "forecast_std": forecast.get("std_dev", 5.0),
            },
        )

    def _parse_ticker(self, t: str) -> Optional[Tuple[str, datetime, dict]]:
        m = re.match(r"^tc-temp-([a-z]+)high-(\d{4}-\d{2}-\d{2})-(.+?)f$", t)
        if not m:
            return None
        city, dstr, rstr = m.group(1), m.group(2), m.group(3)
        if city not in self.STATIONS:
            return None
        try:
            td = datetime.strptime(dstr, "%Y-%m-%d")
        except ValueError:
            return None
        rng = self._parse_range(rstr)
        return (city, td, rng) if rng else None

    def _parse_range(self, s: str) -> Optional[dict]:
        r = {"gte": None, "lt": None}
        if m := re.match(r"^gte(\d+)lt(\d+)$", s):
            r["gte"], r["lt"] = int(m.group(1)), int(m.group(2))
        elif m := re.match(r"^lt(\d+)$", s):
            r["lt"] = int(m.group(1))
        elif m := re.match(r"^gte(\d+)$", s):
            r["gte"] = int(m.group(1))
        elif m := re.match(r"^eq(\d+)$", s):
            r["gte"], r["lt"] = int(m.group(1)), int(m.group(1)) + 1
        else:
            return None
        return r

    def _get_forecast(self, city: str, target: datetime) -> Optional[dict]:
        key = f"{city}:{target.strftime('%Y-%m-%d')}"
        if key in self._cache:
            ts, res = self._cache[key]
            if (datetime.now() - ts).seconds < self._cache_ttl:
                return res
        try:
            pts = requests.get(
                f"{self.API}/points/{self.COORDS[city]}", timeout=10, headers={"User-Agent": "TempBot/1.0"}
            ).json()
            fc = requests.get(pts["properties"]["forecast"], timeout=10, headers={"User-Agent": "TempBot/1.0"}).json()
            res = self._extract_period(fc, target)
            if res:
                self._cache[key] = (datetime.now(), res)
                return res
        except Exception:
            pass
        return self._fallback(city, target)

    def _extract_period(self, data: dict, target: datetime) -> Optional[dict]:
        tstr = target.strftime("%Y-%m-%d")
        for p in data.get("properties", {}).get("periods", []):
            if tstr in p.get("startTime", ""):
                t = p.get("temperature", 0)
                txt = p.get("detailedForecast", "").lower()
                return {
                    "expected_high": float(t),
                    "std_dev": 2.5 + 2.0 * ("chance" in txt or "possible" in txt),
                    "source": "NWS",
                }
        return None

    def _fallback(self, city: str, target: datetime) -> dict:
        avg = self.CLIM.get(city, [65] * 12)[target.month - 1]
        return {"expected_high": avg, "std_dev": 6.0, "source": "climatology"}

    def _range_prob(self, fc: dict, rng: dict) -> float:
        mu, sigma = fc.get("expected_high", 65), fc.get("std_dev", 3.0)

        def phi(x):
            return 0.5 * (1 + math.erf((x - mu) / (sigma * 2**0.5)))

        gte, lt = rng.get("gte"), rng.get("lt")
        if gte is not None and lt is not None:
            p = phi(lt) - phi(gte)
        elif lt is not None:
            p = phi(lt)
        elif gte is not None:
            p = 1 - phi(gte)
        else:
            p = 0.5
        return max(0.0, min(1.0, p))

    def _confidence(self, fc: dict, target: datetime) -> float:
        days = (target.date() - datetime.now().date()).days
        base = 0.90 if days <= 1 else 0.75 if days <= 3 else 0.60 if days <= 7 else 0.45
        if fc.get("source") == "climatology":
            base *= 0.6
        if fc.get("std_dev", 3.0) > 5.0:
            base *= 0.8
        return max(0.1, min(1.0, base))

    def _error(self, code: str, ticker: str) -> ProjectionResult:
        return ProjectionResult(0.5, 0.0, 0.0, {"error": code, "ticker": ticker})


class FallbackMidpointModel(BaseProjectionModel):
    @property
    def supported_families(self) -> list[str]:
        return ["*"]

    def project(self, ticker, market_data, orderbook) -> ProjectionResult:
        yes_bid = float((market_data or {}).get("yes_bid") or 0.0)
        yes_ask = float((market_data or {}).get("yes_ask") or 0.0)
        no_bid = float((market_data or {}).get("no_bid") or 0.0)
        no_ask = float((market_data or {}).get("no_ask") or 0.0)
        if yes_ask <= 0 and no_bid > 0:
            yes_ask = 1.0 - no_bid
        if no_ask <= 0 and yes_bid > 0:
            no_ask = 1.0 - yes_bid
        has_yes = yes_bid > 0 and yes_ask > 0 and yes_ask >= yes_bid
        has_no = no_bid > 0 and no_ask > 0 and no_ask >= no_bid
        if has_yes:
            mid = (yes_bid + yes_ask) / 2.0
            conf = 0.55
        elif has_no:
            mid = 1.0 - ((no_bid + no_ask) / 2.0)
            conf = 0.55
        else:
            mid = 0.5
            conf = 0.3
        return ProjectionResult(
            fair_value=max(0.01, min(0.99, mid)),
            confidence=conf,
            projection=mid,
            metadata={"source": "fallback_midpoint", "ticker": ticker},
        )


class ProjectionModelRegistry:
    def __init__(self):
        self._models: Dict[str, BaseProjectionModel] = {}
        self._register_defaults()

    def _register_defaults(self):
        m = TemperatureProjectionModel()
        for f in m.supported_families:
            self._models[f] = m
        self._models["*"] = FallbackMidpointModel()

    def get_model(self, ticker: str) -> Optional[BaseProjectionModel]:
        for prefix, model in self._models.items():
            if prefix != "*" and ticker.startswith(prefix):
                return model
        return self._models.get("*")

    def project(self, ticker: str, market_data: dict = None, orderbook: dict = None) -> ProjectionResult:
        model = self.get_model(ticker)
        if not model:
            return ProjectionResult(0.5, 0.0, 0.0, {"error": "model=unsupported", "ticker": ticker})
        return model.project(ticker, market_data or {}, orderbook or {})


_registry = None


def get_registry() -> ProjectionModelRegistry:
    global _registry
    if _registry is None:
        _registry = ProjectionModelRegistry()
    return _registry


def project(ticker: str, market_data: dict = None, orderbook: dict = None) -> ProjectionResult:
    return get_registry().project(ticker, market_data, orderbook)
