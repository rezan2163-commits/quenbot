"""
Adaptive Strategy Evolution
=============================
ENHANCEMENT #4: Auto-tune parameters based on performance

Performance'a göre strategy parameters'ı otomatik değiştir:
- Win rate düşse → conservative yap
- Win rate artsa → aggressive yap
- Regime değişse → regime-optimal params'ı uygula
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
import asyncio

logger = logging.getLogger(__name__)


class AdaptiveStrategyEvolver:
    """
    System performance'ı track et ve strategy'yi auto-evolve et
    """
    
    def __init__(self, db_connection):
        self.db = db_connection
        self.evolution_history = []  # Track all changes
        self.last_evaluation = None
        self.evolution_config = {
            "evaluation_interval_hours": 2,  # Her 2 saatte eval
            "min_trades_for_eval": 5,        # En az 5 trade
            "win_rate_threshold_down": 0.9,  # 90% eski win rate'in altına düşerse
            "win_rate_threshold_up": 1.15,   # 115% eski win rate'den yüksekse
            "max_parameter_change_pct": 15,  # Max 15% bir seferde değişir
        }
    
    async def evaluate_and_evolve(self, market_regime: str) -> Optional[Dict[str, Any]]:
        """
        Performance'ı eval et, parameters'ı adjust et
        Returns: adaptation action (ya da None if no change needed)
        """
        
        # Check if time for evaluation
        now = datetime.utcnow()
        if self.last_evaluation:
            time_since = (now - self.last_evaluation).total_seconds() / 3600  # hours
            if time_since < self.evolution_config["evaluation_interval_hours"]:
                return None  # Too soon
        
        # Get last 50 trades for this regime
        try:
            trades = await self._get_recent_trades(market_regime, limit=50)
        except Exception as e:
            logger.error(f"❌ Error fetching trades: {e}")
            return None
        
        if len(trades) < self.evolution_config["min_trades_for_eval"]:
            logger.debug(f"⏳ Not enough trades ({len(trades)}) for evolution yet")
            return None
        
        # Calculate metrics
        metrics = self._calculate_trade_metrics(trades)
        
        # Get current parameters
        current_params = await self._get_current_parameters(market_regime)
        
        # Get historical performance baseline
        historical_wr = await self._get_historical_win_rate(market_regime)
        
        logger.info(f"📊 Evolution eval ({market_regime}): "
                   f"trades={metrics['count']}, wr={metrics['win_rate']:.1%}, "
                   f"baseline={historical_wr:.1%}")
        
        # Decision: Conservative, Keep, or Aggressive
        decision = self._make_adaptation_decision(
            metrics['win_rate'],
            historical_wr,
            metrics['avg_pnl'],
            metrics['sharpe_ratio']
        )
        
        if decision == "KEEP":
            logger.debug("✅ Performance stable, no changes")
            return None
        
        # Calculate new parameters
        new_params = self._calculate_new_parameters(
            current_params,
            decision,
            metrics
        )
        
        # Apply new parameters
        await self._apply_parameters(market_regime, new_params)
        
        # Record evolution
        evolution_record = {
            "timestamp": now,
            "regime": market_regime,
            "decision": decision,
            "old_params": current_params,
            "new_params": new_params,
            "metrics": metrics,
        }
        self.evolution_history.append(evolution_record)
        
        # Keep last 1000 evolutions
        if len(self.evolution_history) > 1000:
            self.evolution_history = self.evolution_history[-1000:]
        
        self.last_evaluation = now
        
        # Return summary for logging/alerting
        return {
            "action": decision,
            "regime": market_regime,
            "old_params": current_params,
            "new_params": new_params,
            "reason": self._format_reason(decision, metrics, historical_wr),
        }
    
    async def _get_recent_trades(self, regime: str, limit: int = 100) -> List[Dict]:
        """Get recent trades for a regime"""
        try:
            result = await self.db.db_query("""
                SELECT pnl_pct, success, duration_sec
                FROM position_attribution
                WHERE market_regime = %s
                ORDER BY timestamp DESC
                LIMIT %s
            """, (regime, limit))
            
            return [
                {
                    "pnl_pct": float(row[0]),
                    "success": row[1],
                    "duration_sec": row[2],
                }
                for row in (result or [])
            ]
        except:
            return []
    
    def _calculate_trade_metrics(self, trades: List[Dict]) -> Dict[str, float]:
        """Calculate performance metrics from trades"""
        
        if not trades:
            return {
                "count": 0,
                "win_rate": 0.0,
                "avg_pnl": 0.0,
                "sharpe_ratio": 0.0,
                "max_win": 0.0,
                "max_loss": 0.0,
            }
        
        pnls = [t["pnl_pct"] for t in trades]
        wins = [p for p in pnls if p > 0]
        
        avg_pnl = sum(pnls) / len(pnls)
        std_pnl = (sum((p - avg_pnl) ** 2 for p in pnls) / len(pnls)) ** 0.5
        sharpe = avg_pnl / std_pnl if std_pnl > 0 else 0
        
        return {
            "count": len(trades),
            "win_rate": len(wins) / len(pnls),
            "avg_pnl": avg_pnl,
            "sharpe_ratio": sharpe,
            "max_win": max(pnls),
            "max_loss": min(pnls),
        }
    
    async def _get_current_parameters(self, regime: str) -> Dict[str, float]:
        """Get current strategy parameters for a regime"""
        try:
            result = await self.db.db_query("""
                SELECT aggressive_factor, take_profit_pct, stop_loss_pct, position_size_mult
                FROM strategy_parameters
                WHERE market_regime = %s
                ORDER BY updated_at DESC
                LIMIT 1
            """, (regime,))
            
            if result:
                return {
                    "aggressive": float(result[0][0]),
                    "take_profit": float(result[0][1]),
                    "stop_loss": float(result[0][2]),
                    "position_size": float(result[0][3]),
                }
        except:
            pass
        
        # Fallback to defaults
        defaults = {
            "BULL": {"aggressive": 1.5, "take_profit": 5.0, "stop_loss": 1.5, "position_size": 1.2},
            "BEAR": {"aggressive": 0.8, "take_profit": 2.0, "stop_loss": 1.0, "position_size": 0.7},
            "SIDEWAYS": {"aggressive": 1.0, "take_profit": 1.0, "stop_loss": 0.5, "position_size": 1.0},
            "HIGH_VOLATILITY": {"aggressive": 0.6, "take_profit": 3.0, "stop_loss": 2.0, "position_size": 0.6},
        }
        return defaults.get(regime, defaults["SIDEWAYS"])
    
    async def _get_historical_win_rate(self, regime: str) -> float:
        """Get baseline win rate for this regime (last 200 trades)"""
        try:
            result = await self.db.db_query("""
                SELECT AVG(success) FROM (
                    SELECT success FROM position_attribution
                    WHERE market_regime = %s
                    ORDER BY timestamp DESC
                    LIMIT 200
                ) t
            """, (regime,))
            
            if result and result[0][0]:
                return float(result[0][0])
        except:
            pass
        
        return 0.5  # Default assumption
    
    def _make_adaptation_decision(self, 
                                  current_wr: float, 
                                  historical_wr: float,
                                  avg_pnl: float,
                                  sharpe: float) -> str:
        """
        Decide: CONSERVATIVE (down-tune), KEEP (no change), AGGRESSIVE (up-tune)
        """
        
        threshold_down = self.evolution_config["win_rate_threshold_down"]
        threshold_up = self.evolution_config["win_rate_threshold_up"]
        
        # Check if win rate significantly dropped
        if current_wr < historical_wr * threshold_down and historical_wr > 0:
            logger.warning(f"⚠️ Win rate dropped: {current_wr:.1%} < {historical_wr * threshold_down:.1%}")
            return "CONSERVATIVE"
        
        # Check if win rate significantly improved
        if current_wr > historical_wr * threshold_up:
            logger.info(f"✅ Win rate improved: {current_wr:.1%} > {historical_wr * threshold_up:.1%}")
            return "AGGRESSIVE"
        
        # Sharpe ratio check (riskadjusted return)
        if sharpe < -0.5:  # Very negative Sharpe
            return "CONSERVATIVE"
        elif sharpe > 1.5:  # Very good Sharpe
            return "AGGRESSIVE"
        
        return "KEEP"
    
    def _calculate_new_parameters(self, 
                                  current: Dict[str, float],
                                  decision: str,
                                  metrics: Dict[str, float]) -> Dict[str, float]:
        """Calculate new parameters based on decision"""
        
        max_change = self.evolution_config["max_parameter_change_pct"] / 100.0
        
        new_params = current.copy()
        
        if decision == "CONSERVATIVE":
            # Reduce aggression, tighten stops, reduce size
            adjustment = (1.0 - max_change)
            new_params["aggressive"] *= adjustment
            new_params["take_profit"] *= adjustment
            new_params["stop_loss"] *= adjustment * 0.9  # Stop tighter
            new_params["position_size"] *= adjustment
            
            logger.info(f"📉 DOWN-TUNED: {current} → {new_params}")
        
        elif decision == "AGGRESSIVE":
            # Increase aggression, wider targets, bigger size
            adjustment = (1.0 + max_change)
            new_params["aggressive"] *= adjustment
            new_params["take_profit"] *= adjustment
            new_params["stop_loss"] *= (1.0 + max_change * 0.5)  # Stop not as much
            new_params["position_size"] *= adjustment * 0.95  # Slightly less aggressive than others
            
            logger.info(f"📈 UP-TUNED: {current} → {new_params}")
        
        return new_params
    
    async def _apply_parameters(self, regime: str, params: Dict[str, float]):
        """Save new parameters to DB"""
        try:
            await self.db.db_execute("""
                INSERT INTO strategy_parameters 
                (market_regime, aggressive_factor, take_profit_pct, stop_loss_pct, 
                 position_size_mult, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    aggressive_factor = %s,
                    take_profit_pct = %s,
                    stop_loss_pct = %s,
                    position_size_mult = %s,
                    updated_at = %s
            """, (
                regime,
                params["aggressive"],
                params["take_profit"],
                params["stop_loss"],
                params["position_size"],
                datetime.utcnow(),
                params["aggressive"],
                params["take_profit"],
                params["stop_loss"],
                params["position_size"],
                datetime.utcnow(),
            ))
        except Exception as e:
            logger.error(f"❌ Error applying parameters: {e}")
    
    def _format_reason(self, decision: str, metrics: Dict, historical_wr: float) -> str:
        """Format readable reason for change"""
        wr = metrics.get("win_rate", 0)
        pnl = metrics.get("avg_pnl", 0)
        
        if decision == "CONSERVATIVE":
            return f"WR dropped to {wr:.1%} (was {historical_wr:.1%}), avg PnL {pnl:.3f}%"
        elif decision == "AGGRESSIVE":
            return f"WR improved to {wr:.1%} (was {historical_wr:.1%}), avg PnL {pnl:.3f}%"
        else:
            return "Performance stable"
