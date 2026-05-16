"""Strategy configuration with daily learning and auto-tuning."""
from __future__ import annotations
import json, logging, os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

from app.config import settings

logger = logging.getLogger("app.strategy")
SPORTS = "sports"

def bankroll_pct(legs: int) -> float:
    if legs == 1: return 0.02
    if legs == 2: return 0.01
    if legs == 3: return 0.0075
    if legs == 4: return 0.005
    return 0.005

def _env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    return default if v is None else v.lower() in ("1", "true", "yes", "on")

def _env_float(name: str, default: float) -> float:
    try: return float(os.getenv(name, str(default)))
    except ValueError: return default

def _env_int(name: str, default: int) -> int:
    try: return int(os.getenv(name, str(default)))
    except ValueError: return default

@dataclass
class LearningState:
    total_trades: int = 0; winning_trades: int = 0; losing_trades: int = 0
    total_pnl: float = 0.0; avg_pnl_per_trade: float = 0.0
    best_category: str = ""; worst_category: str = ""
    category_performance: Dict[str, Dict] = field(default_factory=dict)
    feature_weights: Dict[str, float] = field(default_factory=dict)
    threshold_history: List[Dict] = field(default_factory=list)
    last_adjustment: Optional[str] = None; days_active: int = 0; brier_score: float = 1.0
    def to_dict(self):
        return {"total_trades": self.total_trades, "winning_trades": self.winning_trades,
                "losing_trades": self.losing_trades, "total_pnl": self.total_pnl,
                "avg_pnl_per_trade": self.avg_pnl_per_trade, "best_category": self.best_category,
                "worst_category": self.worst_category, "category_performance": self.category_performance,
                "feature_weights": self.feature_weights, "threshold_history": self.threshold_history[-30:],
                "last_adjustment": self.last_adjustment, "days_active": self.days_active,
                "brier_score": self.brier_score}

class StrategyTuner:
    def __init__(self): self.learning = LearningState(); self._load_learning_state()
    def _load_learning_state(self):
        try:
            s = os.getenv("STRATEGY_LEARNING_STATE", "")
            if s: self.learning = LearningState(**{k: v for k, v in json.loads(s).items() if k in LearningState.__dataclass_fields__})
        except Exception as e: logger.warning("Could not load learning state: %s", e)
    def save_learning_state(self): logger.info("strategy_learning_state %s", json.dumps(self.learning.to_dict()))
    def record_trade_outcome(self, category: str, predicted_prob: float, actual_outcome: int, pnl: float, confidence: float, edge_bps: float, features: Dict[str, float]):
        self.learning.total_trades += 1; self.learning.total_pnl += pnl
        self.learning.winning_trades += 1 if pnl > 0 else 0
        self.learning.losing_trades += 0 if pnl > 0 else 1
        self.learning.avg_pnl_per_trade = self.learning.total_pnl / self.learning.total_trades
        if category not in self.learning.category_performance:
            self.learning.category_performance[category] = {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0, "avg_confidence": 0.0, "avg_edge": 0.0}
        c = self.learning.category_performance[category]
        c["trades"] += 1; c["wins"] += 1 if pnl > 0 else 0; c["losses"] += 0 if pnl > 0 else 1
        c["pnl"] += pnl; n = c["trades"]
        c["avg_confidence"] = (c["avg_confidence"] * (n - 1) + confidence) / n
        c["avg_edge"] = (c["avg_edge"] * (n - 1) + edge_bps) / n
        for fn, fv in features.items():
            if fn not in self.learning.feature_weights: self.learning.feature_weights[fn] = 1.0
            self.learning.feature_weights[fn] = max(0.1, min(3.0, self.learning.feature_weights[fn] + (0.05 if pnl > 0 else -0.03) * fv))
        self.save_learning_state()
    def update_brier(self, brier: float): self.learning.brier_score = brier
    def get_daily_improvement_plan(self) -> Dict:
        plan = {"date": datetime.now().isoformat(), "current_stats": self.learning.to_dict(), "adjustments": [], "focus_areas": []}
        if self.learning.category_performance:
            sc = sorted(self.learning.category_performance.items(), key=lambda x: x[1]["pnl"] / max(1, x[1]["trades"]), reverse=True)
            self.learning.best_category = sc[0][0] if sc else ""; self.learning.worst_category = sc[-1][0] if sc else ""
            plan["focus_areas"].append(f"Double down on {self.learning.best_category}")
            if len(sc) > 1: plan["focus_areas"].append(f"Avoid or tighten {self.learning.worst_category}")
        wr = self.learning.winning_trades / max(1, self.learning.total_trades)
        if wr < 0.45:
            plan["adjustments"].append({"parameter": "min_total_score_single", "change": "+5.0", "reason": f"Win rate {wr:.1%} too low, raising bar"})
            plan["adjustments"].append({"parameter": "min_edge_bps", "change": "+50", "reason": "Require more edge per trade"})
        elif wr > 0.60 and self.learning.total_trades > 20:
            plan["adjustments"].append({"parameter": "min_total_score_single", "change": "-3.0", "reason": f"Win rate {wr:.1%} strong, lowering bar slightly"})
        if self.learning.brier_score > 0.30:
            plan["focus_areas"].append("Calibration poor - predictions overconfident")
            plan["adjustments"].append({"parameter": "confidence_scaling", "change": "0.8", "reason": "Scale down confidence to match reality"})
        self.learning.threshold_history.append({"date": datetime.now().isoformat(), "plan": plan})
        self.learning.last_adjustment = datetime.now().isoformat(); self.learning.days_active += 1
        return plan
    def get_feature_weight(self, name: str) -> float: return self.learning.feature_weights.get(name, 1.0)

TUNER = StrategyTuner()
BASE_MIN_TOTAL_SCORE_SINGLE = _env_float("MIN_TOTAL_SCORE_SINGLE", 25.0)
BASE_MIN_TOTAL_SCORE_MULTI = _env_float("MIN_TOTAL_SCORE_MULTI", 30.0)
MIN_EDGE_BPS = _env_int("MIN_EDGE_BPS", 15)
MIN_FAIR_PROB_GAP = _env_float("MIN_FAIR_PROB_GAP", 0.01)
MAX_SPREAD_PCT = _env_float("MAX_SPREAD_PCT", 0.08)
MAX_EV_LOSS_PCT = _env_float("MAX_EV_LOSS_PCT", 0.03)
MAX_POSITIONS = _env_int("MAX_POSITIONS", 10)
MAX_DAILY_TRADES = _env_int("MAX_DAILY_TRADES", 5)
MAX_RISK_PER_TRADE_USD = _env_float("MAX_RISK_PER_TRADE_USD", 50.0)
MAX_DAILY_LOSS_USD = _env_float("MAX_DAILY_LOSS_USD", 200.0)
AUTO_EXECUTE = _env_bool("AUTO_EXECUTE", False)
DRY_RUN = _env_bool("DRY_RUN", False)
CASHOUT_ENABLED = _env_bool("CASHOUT_ENABLED", True)
CASHOUT_STOP_LOSS_PCT = _env_float("CASHOUT_STOP_LOSS_PCT", -15.0)
CASHOUT_TP1_PCT = _env_float("CASHOUT_TP1_PCT", 25.0)
CASHOUT_TP1_SIZE_PCT = _env_float("CASHOUT_TP1_SIZE_PCT", 40.0)
BRIER_THRESHOLD = _env_float("BRIER_THRESHOLD", 0.25)
MIN_TRADES_FOR_CALIBRATION = _env_int("MIN_TRADES_FOR_CALIBRATION", 5)

def get_adjusted_thresholds() -> Dict[str, float]:
    wr = TUNER.learning.winning_trades / max(1, TUNER.learning.total_trades)
    sa = 5.0 if wr < 0.45 else (-3.0 if wr > 0.60 and TUNER.learning.total_trades > 20 else 0.0)
    ea = 25 if TUNER.learning.brier_score > 0.30 else 0
    return {"min_total_score_single": BASE_MIN_TOTAL_SCORE_SINGLE + sa, "min_total_score_multi": BASE_MIN_TOTAL_SCORE_MULTI + sa,
            "min_edge_bps": MIN_EDGE_BPS + ea, "min_fair_prob_gap": MIN_FAIR_PROB_GAP, "max_spread_pct": MAX_SPREAD_PCT,
            "max_ev_loss_pct": MAX_EV_LOSS_PCT, "max_positions": MAX_POSITIONS, "max_daily_trades": MAX_DAILY_TRADES,
            "max_risk_per_trade_usd": MAX_RISK_PER_TRADE_USD, "max_daily_loss_usd": MAX_DAILY_LOSS_USD}

class _TuningProxy:
    @property
    def min_total_score_single(self): return get_adjusted_thresholds()["min_total_score_single"]
    @property
    def min_total_score_multi(self): return get_adjusted_thresholds()["min_total_score_multi"]
    @property
    def min_edge_bps(self): return get_adjusted_thresholds()["min_edge_bps"]
    @property
    def min_fair_prob_gap(self): return get_adjusted_thresholds()["min_fair_prob_gap"]
    @property
    def max_spread_pct(self): return get_adjusted_thresholds()["max_spread_pct"]
    @property
    def max_ev_loss_pct(self): return get_adjusted_thresholds()["max_ev_loss_pct"]
    @property
    def max_positions(self): return get_adjusted_thresholds()["max_positions"]
    @property
    def max_daily_trades(self): return get_adjusted_thresholds()["max_daily_trades"]
    @property
    def max_risk_per_trade_usd(self): return get_adjusted_thresholds()["max_risk_per_trade_usd"]
    @property
    def max_daily_loss_usd(self): return get_adjusted_thresholds()["max_daily_loss_usd"]
    @property
    def same_day_only(self): return settings.same_day_only
    @property
    def sports_same_day_only(self): return settings.sports_same_day_only
    @property
    def market_timezone(self): return settings.market_timezone
    @property
    def min_minutes_to_close(self): return settings.min_minutes_to_close
    @property
    def max_settlement_window_hours(self): return settings.max_settlement_window_hours
    @property
    def max_spread_cents(self): return settings.max_spread_cents
    @property
    def min_projection_score(self): return settings.min_projection_score
    @property
    def min_confidence_score(self): return settings.min_confidence_score
    @property
    def extreme_price_min(self): return settings.extreme_price_min
    @property
    def extreme_price_max(self): return settings.extreme_price_max
    @property
    def max_combo_legs(self): return settings.max_combo_legs
    @property
    def category_edge_bps(self): return settings.category_edge_bps
    @property
    def max_category_exposure_pct(self): return 1.0
    auto_execute = AUTO_EXECUTE; dry_run = DRY_RUN; cashout_enabled = CASHOUT_ENABLED
    cashout_stop_loss_pct = CASHOUT_STOP_LOSS_PCT; cashout_tp1_pct = CASHOUT_TP1_PCT
    cashout_tp1_size_pct = CASHOUT_TP1_SIZE_PCT; brier_threshold = BRIER_THRESHOLD
    min_trades_for_calibration = MIN_TRADES_FOR_CALIBRATION

TUNING = _TuningProxy()
