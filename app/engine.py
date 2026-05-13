from __future__ import annotations
import asyncio, logging, os
from datetime import datetime, timedelta
from app.services.universe import UniverseService
from app.strategy import TUNER, get_adjusted_thresholds
logger = logging.getLogger('app.engine')

class TradingEngine:
    def __init__(self, api, universe, calibration):
        self.api = api; self.universe = universe; self.calibration = calibration
        self.daily_stats = {'trades_today': 0, 'daily_pnl': 0.0, 'last_reset': datetime.utcnow().date()}
        self._learning_lock = asyncio.Lock()
    async def run_cycle(self):
        today = datetime.utcnow().date()
        if today != self.daily_stats['last_reset']:
            await self._run_daily_learning(); self.daily_stats = {'trades_today': 0, 'daily_pnl': 0.0, 'last_reset': today}
        markets = await self.universe.get_active_markets()
        if not markets: return {'status':'no_markets','trades':0}
        brier = self.calibration.brier_score(); thresholds = get_adjusted_thresholds()
        if brier > 0.25 and self.calibration.trade_count >= 5: thresholds['min_total_score_single'] += 5.0; thresholds['min_edge_bps'] += 25
        candidates = self._score_candidates(markets, thresholds)
        if not candidates: return {'status':'no_candidates','trades':0}
        selected = self._select_trades(candidates, thresholds)
        executed = await self._execute_trades(selected, thresholds)
        for t in executed: self.calibration.record_trade(t['market_id'], t['predicted_prob'], t['side'])
        return {'status':'ok','trades':len(executed),'candidates':len(candidates),'selected':len(selected)}
    async def _run_daily_learning(self):
        async with self._learning_lock:
            try: trades = await self.api.get_trades(limit=200)
            except Exception: trades = []
            yesterday = datetime.utcnow() - timedelta(days=1)
            day_trades = [t for t in trades if isinstance(t, dict) and self._parse_time(t) >= yesterday]
            for trade in day_trades:
                pnl = trade.get('realized_pnl', 0) or trade.get('pnl', 0) or 0
                mid = trade.get('market_id') or trade.get('id', '')
                cat = 'unknown'
                try:
                    m = await self.api.get_market(mid)
                    cat = UniverseService._infer_category(m.get('tags', []), m.get('question', '')).value
                except: pass
                TUNER.record_trade_outcome(cat, trade.get('price',0.5), 1 if pnl>0 else 0, pnl, trade.get('confidence',0.5), trade.get('edge_bps',0), {'price': trade.get('price',0.5), 'volume':0})
    @staticmethod
    def _parse_time(trade):
        ts = trade.get('timestamp') or trade.get('created_at') or trade.get('time')
        if not ts: return datetime.utcnow()
        if isinstance(ts,(int,float)): return datetime.utcfromtimestamp(ts)
        try: return datetime.fromisoformat(str(ts).replace('Z','+00:00'))
        except: return datetime.utcnow()
    def _score_candidates(self, markets, thresholds): return [] if not markets else [{'market':m,'total_score':99,'edge_bps':100} for m in markets[:thresholds.get('max_positions',10)]]
    def _select_trades(self, candidates, thresholds): return candidates[:max(0, thresholds.get('max_daily_trades',5)-self.daily_stats['trades_today'])]
    async def _execute_trades(self, selected, thresholds):
        executed=[]; auto_execute=os.getenv('AUTO_EXECUTE','true').lower() in ('1','true','yes','on'); dry_run=os.getenv('DRY_RUN','false').lower() in ('1','true','yes','on')
        for sel in selected:
            m=sel['market']; price=0.5; size=min(thresholds.get('max_risk_per_trade_usd',50.0)/max(price,0.01),100.0)
            info={'market_id':m.id,'market_title':m.title,'side':'BUY','price':price,'size':size,'total_score':sel['total_score'],'edge_bps':sel['edge_bps'],'predicted_prob':price,'confidence':m.confidence,'category':m.category.value}
            if not dry_run and auto_execute:
                try: result=await self.api.place_order(m.id,'BUY',size,price); info.update({'status':'executed','order_id':result.get('id','')}); self.daily_stats['trades_today']+=1
                except Exception as e: info.update({'status':'failed','error':str(e)})
            else: info['status']='dry_run'
            executed.append(info)
        return executed
