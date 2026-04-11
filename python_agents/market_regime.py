"""
Market Regime Detection Module
===============================
ENHANCEMENT #2: Market Regime Detection + Parameter Adaptation

Piyasayı otomatik detect et: BULL / BEAR / SIDEWAYS / HIGH_VOLATILITY
Her regime için optimal parameters'ı öner
"""

import numpy as np
import logging
from typing import Dict, Any, List
from datetime import datetime

logger = logging.getLogger(__name__)


class MarketRegimeDetector:
    """
    Pazar regime'ini detect et ve adaptation parameters'ı suggest et
    """
    
    def __init__(self):
        self.current_regime = "UNKNOWN"
        self.regime_history = []
        self.regime_changes = 0
        
        # Her regime için optimal parameters
        self.optimal_parameters = {
            "BULL": {
                "aggressive_factor": 1.5,
                "take_profit_pct": 5.0,
                "stop_loss_pct": 1.5,
                "position_size_multiplier": 1.2,
                "max_correlation": 0.7,  # Daha aggressive olabilir
                "risk_per_trade_pct": 1.5,
                "description": "🟢 Strong uptrend: agresif al, TP büyük"
            },
            "BEAR": {
                "aggressive_factor": 0.8,
                "take_profit_pct": 2.0,
                "stop_loss_pct": 1.0,
                "position_size_multiplier": 0.7,
                "max_correlation": 0.5,
                "risk_per_trade_pct": 0.8,
                "description": "🔴 Downtrend: dayanıklı stratejiler, kayıpları kontrol"
            },
            "SIDEWAYS": {
                "aggressive_factor": 1.0,
                "take_profit_pct": 1.0,
                "stop_loss_pct": 0.5,
                "position_size_multiplier": 1.0,
                "max_correlation": 0.4,  # Daha selective
                "risk_per_trade_pct": 0.5,
                "description": "➡️ Ranging: range trading, dar TP/SL"
            },
            "HIGH_VOLATILITY": {
                "aggressive_factor": 0.6,
                "take_profit_pct": 3.0,
                "stop_loss_pct": 2.0,
                "position_size_multiplier": 0.6,
                "max_correlation": 0.3,  # Çok selective
                "risk_per_trade_pct": 0.6,
                "description": "⚡ High volatility: dar position, büyük stops"
            }
        }
    
    async def detect_regime(self, 
                           prices: np.ndarray, 
                           volumes: np.ndarray = None,
                           correlation_matrix: np.ndarray = None) -> Dict[str, Any]:
        """
        Market regime'i detect et
        prices: son 100-200 close price
        volumes: trade volumes
        correlation_matrix: coins arası correlation
        
        Returns: regime, metrics, recommended_parameters
        """
        
        if len(prices) < 50:
            return self._unknown_regime()
        
        # 1. Trend Strength Calculation
        recent_closes = prices[-50:]
        past_closes = prices[-100:-50]
        
        mean_recent = np.mean(recent_closes)
        mean_past = np.mean(past_closes)
        trend_strength = abs((mean_recent - mean_past) / mean_past) if mean_past != 0 else 0
        
        direction = "UP" if mean_recent > mean_past else "DOWN" if mean_recent < mean_past else "FLAT"
        
        # 2. Volatility Calculation
        returns = np.diff(np.log(prices[-100:]))
        volatility = np.std(returns)
        volatility_pct = volatility * 100  # Percent
        
        # 3. Correlation avg (if provided)
        correlation_avg = 0.0
        if correlation_matrix is not None and correlation_matrix.size > 0:
            # Get off-diagonal average
            mask = ~np.eye(correlation_matrix.shape[0], dtype=bool)
            correlation_avg = np.mean(np.abs(correlation_matrix[mask]))
        
        # 4. Volume trend (if provided)
        volume_trend = "STABLE"
        if volumes is not None and len(volumes) > 20:
            recent_vol = np.mean(volumes[-20:])
            past_vol = np.mean(volumes[-100:-20])
            if recent_vol > past_vol * 1.3:
                volume_trend = "INCREASING"
            elif recent_vol < past_vol * 0.7:
                volume_trend = "DECREASING"
        
        # 5. Regime Decision Logic
        regime = self._classify_regime(trend_strength, direction, volatility_pct, volume_trend)
        
        # 6. Regime change detection
        if regime != self.current_regime:
            self.regime_changes += 1
            logger.warning(f"🔄 REGIME CHANGE: {self.current_regime} → {regime}")
        
        self.current_regime = regime
        self.regime_history.append({
            "timestamp": datetime.utcnow(),
            "regime": regime,
            "trend_strength": trend_strength,
            "volatility": volatility_pct,
        })
        
        # Keep last 1000 regimes
        if len(self.regime_history) > 1000:
            self.regime_history = self.regime_history[-1000:]
        
        # Return full analysis
        return {
            "regime": regime,
            "confidence": self._calculate_confidence(trend_strength, volatility_pct),
            "metrics": {
                "trend_strength": float(trend_strength),
                "trend_direction": direction,
                "volatility_pct": float(volatility_pct),
                "correlation_avg": float(correlation_avg),
                "volume_trend": volume_trend,
            },
            "recommended_parameters": self.optimal_parameters[regime],
            "regime_changes_total": self.regime_changes,
            "description": self.optimal_parameters[regime]["description"],
        }
    
    def _classify_regime(self, trend_strength: float, direction: str, 
                        volatility_pct: float, volume_trend: str) -> str:
        """Regime'i classify et"""
        
        # HIGH_VOLATILITY check first (priority)
        if volatility_pct > 10.0:  # >10% volatility
            return "HIGH_VOLATILITY"
        
        # TREND check
        if trend_strength > 0.05:  # >5% trend strength
            if direction == "UP":
                return "BULL"
            elif direction == "DOWN":
                return "BEAR"
        
        # Default to SIDEWAYS if low trend
        return "SIDEWAYS"
    
    def _calculate_confidence(self, trend_strength: float, volatility_pct: float) -> float:
        """
        Regime detection confidence 0-1
        Strong signals = high confidence
        Weak signals = low confidence
        """
        # Confidence based on trend clarity
        trend_confidence = min(trend_strength * 10, 1.0)  # 5% trend = 0.5 confidence
        
        # More clarity = better
        volatility_penalty = max(1.0 - (volatility_pct / 15.0), 0.3)
        
        confidence = (trend_confidence * 0.6) + (volatility_penalty * 0.4)
        return float(max(min(confidence, 1.0), 0.1))
    
    def _unknown_regime(self) -> Dict[str, Any]:
        """Return unknown regime response"""
        return {
            "regime": "UNKNOWN",
            "confidence": 0.0,
            "metrics": {},
            "recommended_parameters": self.optimal_parameters["SIDEWAYS"],  # Default conservative
            "regime_changes_total": self.regime_changes,
            "description": "❓ Insufficient data",
        }
    
    def get_adaptation_action(self) -> str:
        """
        Regime çok sık değişirse warning ver
        Eğer >5 change/hour ise sistem unstable
        """
        if len(self.regime_history) >= 60:
            last_60 = self.regime_history[-60:]
            changes = sum(1 for i in range(1, len(last_60)) 
                         if last_60[i]['regime'] != last_60[i-1]['regime'])
            
            if changes > 5:
                return "⚠️ UNSTABLE_MARKET: Regime çok sık değişiyor, dikkat et"
            elif changes > 2:
                return "📊 VOLATILE_MARKET: Regime sık değişiyor, mais normal"
        
        return None
