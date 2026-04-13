"""
RiskManager - Signal Gate System
=================================
Her signal, RiskManager'dan geçmeden simülasyona dönüşemez.
Drawdown, günlük limit, consecutive loss, cooldown, korelasyon kontrolü.
"""
import logging
import os
from datetime import datetime, timedelta
from typing import Dict, Any, Tuple, Optional

from config import Config

logger = logging.getLogger(__name__)


class RiskManager:
    """Risk kurallarını uygular. Geçemeyen signal iptal edilir."""

    def __init__(self, state_tracker):
        self.state = state_tracker
        self.last_loss_time: Optional[datetime] = None

        # Configurable limits
        self.MAX_DAILY_TRADES = Config.RISK_MAX_DAILY_TRADES
        self.MAX_DAILY_LOSS_PCT = Config.RISK_MAX_DAILY_LOSS_PCT
        self.MAX_CONSECUTIVE_LOSSES = Config.RISK_MAX_CONSECUTIVE_LOSSES
        self.MAX_DRAWDOWN_PCT = Config.RISK_MAX_DRAWDOWN_PCT
        self.MAX_SAME_DIRECTION_POSITIONS = Config.RISK_MAX_SAME_DIRECTION
        self.COOLDOWN_AFTER_LOSS_SEC = Config.RISK_COOLDOWN_AFTER_LOSS_SEC
        self.MAX_OPEN_POSITIONS = Config.RISK_MAX_OPEN_POSITIONS

    def check_signal(self, signal: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Signal'i tüm risk kurallarından geçir.
        Returns: (approved: bool, reason: str)
        """
        state = self.state.state
        mode = self.state.get_mode()

        # Bootstrap modda daha az kısıtlama
        if mode == 'BOOTSTRAP':
            return self._check_bootstrap(signal, state)

        # 1. Günlük işlem limiti
        if state['daily_trade_count'] >= self.MAX_DAILY_TRADES:
            return False, f"Daily trade limit reached ({self.MAX_DAILY_TRADES})"

        # 2. Günlük kayıp limiti
        if state['daily_pnl'] < self.MAX_DAILY_LOSS_PCT:
            return False, f"Daily loss limit reached ({state['daily_pnl']:.2f}%)"

        # 3. Art arda kayıp kontrolü
        if state['consecutive_losses'] >= self.MAX_CONSECUTIVE_LOSSES:
            return False, f"Consecutive losses limit ({state['consecutive_losses']})"

        # 4. Max drawdown kontrolü
        current_drawdown = float(state.get('current_drawdown', 0) or 0)
        if current_drawdown >= abs(self.MAX_DRAWDOWN_PCT):
            if self._is_stale_drawdown_lock(state):
                logger.warning(
                    "Stale drawdown lock bypassed (dd=%.2f, daily_trades=%s, daily_pnl=%.2f)",
                    current_drawdown,
                    state.get('daily_trade_count', 0),
                    float(state.get('daily_pnl', 0) or 0),
                )
            else:
                return False, f"Max drawdown reached ({current_drawdown:.2f}%)"

        # 5. Cooldown kontrolü (son kayıptan sonra bekleme)
        if self.last_loss_time:
            elapsed = (datetime.utcnow() - self.last_loss_time).total_seconds()
            if elapsed < self.COOLDOWN_AFTER_LOSS_SEC:
                remaining = self.COOLDOWN_AFTER_LOSS_SEC - elapsed
                return False, f"Cooldown active ({remaining:.0f}s remaining)"

        # 6. Max açık pozisyon kontrolü
        active_count = len(state.get('active_symbols', []))
        if active_count >= self.MAX_OPEN_POSITIONS:
            return False, f"Max open positions reached ({active_count})"

        # 7. Aynı sembol kontrolü (aynı coin'de çift pozisyon açmayı engelle)
        symbol = signal.get('symbol', '')
        if symbol in state.get('active_symbols', []):
            return False, f"Already have position in {symbol}"

        # 8. Düşük güven filtresi (mode'a göre)
        confidence = signal.get('confidence', 0)
        min_confidence = self._get_min_confidence(mode)
        if confidence < min_confidence:
            return False, f"Confidence too low ({confidence:.2f} < {min_confidence})"

        return True, "approved"

    def _check_bootstrap(self, signal: Dict, state: Dict) -> Tuple[bool, str]:
        """Bootstrap modda basit kontroller"""
        bootstrap_max_open = int(
            os.getenv("QUENBOT_BOOTSTRAP_MAX_OPEN_POSITIONS", str(max(self.MAX_OPEN_POSITIONS, 20)))
        )
        if state['daily_trade_count'] >= self.MAX_DAILY_TRADES * 2:
            return False, "Bootstrap daily limit"
        if len(state.get('active_symbols', [])) >= bootstrap_max_open:
            return False, f"Bootstrap max positions ({bootstrap_max_open})"
        symbol = signal.get('symbol', '')
        if symbol in state.get('active_symbols', []):
            return False, f"Already in {symbol}"
        return True, "bootstrap_approved"

    def _get_min_confidence(self, mode: str) -> float:
        """Mode'a göre minimum güven eşiği"""
        thresholds = {
            'BOOTSTRAP': 0.2,
            'LEARNING': 0.35,
            'WARMUP': 0.45,
            'PRODUCTION': 0.5,
        }
        return thresholds.get(mode, 0.5)

    def _is_stale_drawdown_lock(self, state: Dict[str, Any]) -> bool:
        """
        Detect legacy/corrupted drawdown states that can freeze the system forever.
        """
        active_positions = len(state.get('active_symbols', []))
        total_trades = int(state.get('total_trades', 0) or 0)
        current_drawdown = float(state.get('current_drawdown', 0) or 0)
        cumulative_pnl = float(state.get('cumulative_pnl', 0) or 0)

        # Corrupted: no PnL history but large drawdown
        if cumulative_pnl == 0 and current_drawdown > 5:
            return True

        # No active positions + extreme DD = stale lock
        if (total_trades > 0
                and current_drawdown > abs(self.MAX_DRAWDOWN_PCT) * 3
                and active_positions == 0):
            return True

        return False

    def calculate_position_size(self, confidence: float, atr_ratio: float = 0.02,
                                 balance: float = 10000.0) -> float:
        """
        ATR-adjusted position sizing.
        
        Yüksek volatilite = küçük pozisyon (risk normalize)
        Düşük volatilite = büyük pozisyon
        
        size = base × confidence × (target_risk / atr_ratio)
        """
        mode = self.state.get_mode()
        base_risk_pct = self._get_base_risk(mode)

        # Kelly Criterion simplified: f = win_rate - (1 - win_rate) / payoff_ratio
        win_rate = self.state.get_win_rate()
        if win_rate > 0 and self.state.state['total_trades'] > 20:
            # Simplified Kelly: daha tutucu half-Kelly
            payoff_ratio = 1.5  # avg_win / avg_loss assumption
            kelly = win_rate - (1 - win_rate) / payoff_ratio
            kelly = max(kelly, 0.01)  # Negatif kelly = trade yapma ama biz min tutuyoruz
            kelly = min(kelly, 0.25)  # Max %25 Kelly
            base_risk_pct *= kelly / 0.1  # Normalize

        # ATR adjustment: volatilite yüksekse pozisyonu küçült
        target_atr = 0.02  # Hedef ATR
        atr_multiplier = target_atr / max(atr_ratio, 0.001)
        atr_multiplier = max(0.3, min(atr_multiplier, 3.0))  # Sınırla

        # Final position size
        position_value = balance * base_risk_pct * confidence * atr_multiplier
        max_position = Config.get_agent_config('ghost_simulator')['max_position_size']
        position_size = min(position_value, max_position)
        position_size = max(position_size, 10)  # Minimum $10

        return round(position_size, 2)

    def _get_base_risk(self, mode: str) -> float:
        """Mode'a göre temel risk yüzdesi"""
        risks = {
            'BOOTSTRAP': 0.01,   # %1
            'LEARNING': 0.02,    # %2
            'WARMUP': 0.03,      # %3
            'PRODUCTION': 0.05,  # %5
        }
        return risks.get(mode, 0.02)

    def get_mode_params(self) -> Dict[str, Any]:
        """Mevcut mode'a göre TP/SL parametreleri döndür"""
        mode = self.state.get_mode()
        params = {
            'BOOTSTRAP': {
                'take_profit_pct': 0.01,
                'stop_loss_pct': 0.005,
                'similarity_threshold': 0.0,
                'min_mean_profit': 0.001,
            },
            'LEARNING': {
                'take_profit_pct': 0.03,
                'stop_loss_pct': 0.015,
                'similarity_threshold': 0.2,
                'min_mean_profit': 0.003,
            },
            'WARMUP': {
                'take_profit_pct': 0.04,
                'stop_loss_pct': 0.025,
                'similarity_threshold': 0.35,
                'min_mean_profit': 0.004,
            },
            'PRODUCTION': {
                'take_profit_pct': Config.GHOST_TAKE_PROFIT_PCT,
                'stop_loss_pct': Config.GHOST_STOP_LOSS_PCT,
                'similarity_threshold': Config.SIMILARITY_THRESHOLD,
                'min_mean_profit': Config.STRATEGY_MIN_MEAN_PROFIT,
            },
        }
        return params.get(mode, params['PRODUCTION'])

    def record_loss(self):
        """Son kayıp zamanını kaydet (cooldown için)"""
        self.last_loss_time = datetime.utcnow()

    def get_risk_summary(self) -> Dict[str, Any]:
        """Dashboard/chat için risk özeti"""
        state = self.state.state
        mode = self.state.get_mode()
        return {
            'mode': mode,
            'daily_trades': f"{state['daily_trade_count']}/{self.MAX_DAILY_TRADES}",
            'daily_pnl': state['daily_pnl'],
            'consecutive_losses': f"{state['consecutive_losses']}/{self.MAX_CONSECUTIVE_LOSSES}",
            'drawdown': f"{state['current_drawdown']:.2f}%/{abs(self.MAX_DRAWDOWN_PCT)}%",
            'open_positions': f"{len(state.get('active_symbols', []))}/{self.MAX_OPEN_POSITIONS}",
            'cooldown_active': self.last_loss_time is not None and
                               (datetime.utcnow() - self.last_loss_time).total_seconds() < self.COOLDOWN_AFTER_LOSS_SEC,
            'min_confidence': self._get_min_confidence(mode),
            'mode_params': self.get_mode_params(),
        }
