"""
StateTracker - Persistent Memory System
========================================
Her restart sonrası tam kaldığı yerden devam eder.
Tüm state DB'de tutulur, RAM'de cache'lenir.
"""
import json
import logging
from datetime import datetime, timedelta, date
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)


class StateTracker:
    """Bot state'ini DB'de persistent tutar. Restart = kaldığın yerden devam."""

    DEFAULT_STATE = {
        'cumulative_pnl': 0.0,
        'peak_pnl': 0.0,
        'current_drawdown': 0.0,
        'consecutive_losses': 0,
        'consecutive_wins': 0,
        'daily_pnl': 0.0,
        'daily_trade_count': 0,
        'daily_reset_date': None,
        'total_trades': 0,
        'total_wins': 0,
        'last_trade_time': None,
        'signal_type_stats': {},
        'active_symbols': [],
        'bootstrap_mode': True,
        'system_start_time': None,
        'forced_mode': None,
        'mode': 'BOOTSTRAP',  # BOOTSTRAP → LEARNING → WARMUP → PRODUCTION
    }

    def __init__(self, db):
        self.db = db
        self.state: Dict[str, Any] = dict(self.DEFAULT_STATE)
        self._dirty = False

    async def load_state(self):
        """DB'den tam state yükle (restart recovery)"""
        try:
            async with self.db.pool.acquire() as conn:
                row = await conn.fetchrow(
                    "SELECT state_value FROM bot_state WHERE state_key = 'main'")
                if row:
                    saved = json.loads(row['state_value'])
                    self.state.update(saved)
                    self._normalize_corrupted_state()
                    logger.info(f"📦 State loaded: PnL={self.state['cumulative_pnl']:.4f} "
                                f"Trades={self.state['total_trades']} "
                                f"Mode={self.state['mode']}")
                else:
                    self.state['system_start_time'] = datetime.utcnow().isoformat()
                    self.state['daily_reset_date'] = str(date.today())
                    await self.save_state()
                    logger.info("📦 State initialized (fresh start)")
        except Exception as e:
            logger.error(f"State load error: {e}")

    async def save_state(self):
        """Atomik state kaydet"""
        try:
            serializable = self._make_serializable(self.state)
            async with self.db.pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO bot_state (state_key, state_value, updated_at)
                    VALUES ('main', $1, CURRENT_TIMESTAMP)
                    ON CONFLICT (state_key)
                    DO UPDATE SET state_value = $1, updated_at = CURRENT_TIMESTAMP
                """, json.dumps(serializable))
            self._dirty = False
        except Exception as e:
            logger.error(f"State save error: {e}")

    async def record_trade(self, sim_result: Dict[str, Any]):
        """Yeni işlem sonucunu kaydet + tüm state'i güncelle"""
        pnl = float(sim_result.get('pnl', 0))
        pnl_pct = float(sim_result.get('pnl_pct', 0))
        signal_type = sim_result.get('signal_type', 'unknown')
        symbol = sim_result.get('symbol', '')
        was_win = pnl > 0

        # Günlük reset kontrolü
        self._check_daily_reset()

        # Temel metrikler
        self.state['cumulative_pnl'] += pnl
        self.state['daily_pnl'] += pnl
        self.state['daily_trade_count'] += 1
        self.state['total_trades'] += 1
        self.state['last_trade_time'] = datetime.utcnow().isoformat()

        if was_win:
            self.state['total_wins'] += 1
            self.state['consecutive_wins'] += 1
            self.state['consecutive_losses'] = 0
        else:
            self.state['consecutive_losses'] += 1
            self.state['consecutive_wins'] = 0

        # Peak / Drawdown
        if self.state['cumulative_pnl'] > self.state['peak_pnl']:
            self.state['peak_pnl'] = self.state['cumulative_pnl']
        if self.state['peak_pnl'] > 0:
            self.state['current_drawdown'] = (
                (self.state['peak_pnl'] - self.state['cumulative_pnl'])
                / self.state['peak_pnl'] * 100
            )
        else:
            self.state['current_drawdown'] = 0.0
        self._normalize_corrupted_state()

        # Signal type stats
        if signal_type not in self.state['signal_type_stats']:
            self.state['signal_type_stats'][signal_type] = {
                'wins': 0, 'total': 0, 'total_pnl': 0.0
            }
        stats = self.state['signal_type_stats'][signal_type]
        stats['total'] += 1
        stats['total_pnl'] += pnl_pct
        if was_win:
            stats['wins'] += 1

        # Active symbols güncelle
        if symbol in self.state['active_symbols']:
            self.state['active_symbols'].remove(symbol)

        # Mode geçişi kontrolü
        self._check_mode_transition()

        # Kaydet
        await self.save_state()
        await self._save_history_snapshot()

        logger.info(f"📊 Trade recorded: {symbol} {'WIN' if was_win else 'LOSS'} "
                    f"PnL={pnl:.4f} Cumulative={self.state['cumulative_pnl']:.4f} "
                    f"DD={self.state['current_drawdown']:.2f}%")

    def add_active_symbol(self, symbol: str):
        """Yeni pozisyon açıldığında"""
        if symbol not in self.state['active_symbols']:
            self.state['active_symbols'].append(symbol)
            self._dirty = True

    def remove_active_symbol(self, symbol: str):
        """Pozisyon kapandığında"""
        if symbol in self.state['active_symbols']:
            self.state['active_symbols'].remove(symbol)
            self._dirty = True

    def get_win_rate(self) -> float:
        if self.state['total_trades'] == 0:
            return 0.0
        return self.state['total_wins'] / self.state['total_trades']

    def get_signal_type_win_rate(self, signal_type: str) -> float:
        stats = self.state['signal_type_stats'].get(signal_type, {})
        total = stats.get('total', 0)
        if total == 0:
            return 0.0
        return stats.get('wins', 0) / total

    def get_last_n_results(self, n: int = 5) -> List[bool]:
        """Son N işlemin sonucu (True=win, False=loss)"""
        # Consecutive tracking'den çıkar
        wins = self.state['consecutive_wins']
        losses = self.state['consecutive_losses']
        if wins > 0:
            return [True] * min(wins, n)
        elif losses > 0:
            return [False] * min(losses, n)
        return []

    def is_bootstrap_mode(self) -> bool:
        return self.state['mode'] == 'BOOTSTRAP'

    def is_production_mode(self) -> bool:
        return self.state['mode'] == 'PRODUCTION'

    def get_mode(self) -> str:
        return self.state['mode']

    async def set_mode(self, mode: Optional[str]):
        """Force bot mode or clear override with AUTO."""
        normalized = (mode or "").strip().upper()
        valid_modes = {'BOOTSTRAP', 'LEARNING', 'WARMUP', 'PRODUCTION'}

        if not normalized or normalized == 'AUTO':
            self.state['forced_mode'] = None
            self._check_mode_transition()
        elif normalized in valid_modes:
            self.state['forced_mode'] = normalized
            self.state['mode'] = normalized
        else:
            raise ValueError(f"Unsupported mode: {mode}")

        await self.save_state()

    def _check_daily_reset(self):
        """Gün değişiminde günlük metrikleri sıfırla"""
        today = str(date.today())
        if self.state['daily_reset_date'] != today:
            logger.info(f"📅 Daily reset: PnL {self.state['daily_pnl']:.4f} → 0 "
                        f"Trades {self.state['daily_trade_count']} → 0")
            self.state['daily_pnl'] = 0.0
            self.state['daily_trade_count'] = 0
            self.state['daily_reset_date'] = today

    def _check_mode_transition(self):
        """Sistem modunu kontrol et ve geçiş yap"""
        forced_mode = str(self.state.get('forced_mode') or '').upper()
        if forced_mode in {'BOOTSTRAP', 'LEARNING', 'WARMUP', 'PRODUCTION'}:
            self.state['mode'] = forced_mode
            return

        start_str = self.state.get('system_start_time')
        if not start_str:
            return

        try:
            start = datetime.fromisoformat(start_str)
        except (ValueError, TypeError):
            return

        hours_running = (datetime.utcnow() - start).total_seconds() / 3600
        old_mode = self.state['mode']

        if hours_running < 2:
            self.state['mode'] = 'BOOTSTRAP'
        elif hours_running < 12:
            self.state['mode'] = 'LEARNING'
        elif hours_running < 24:
            self.state['mode'] = 'WARMUP'
        else:
            self.state['mode'] = 'PRODUCTION'

        if old_mode != self.state['mode']:
            logger.info(f"🔄 Mode transition: {old_mode} → {self.state['mode']} "
                        f"(running {hours_running:.1f}h)")

    def _normalize_corrupted_state(self):
        current_drawdown = float(self.state.get('current_drawdown', 0) or 0)
        if current_drawdown > 100:
            logger.warning("StateTracker drawdown normalized: %.2f -> 100.00", current_drawdown)
            self.state['current_drawdown'] = 100.0
            self._dirty = True

    async def _save_history_snapshot(self):
        """Her trade sonrası state_history tablosuna snapshot yaz"""
        try:
            async with self.db.pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO state_history
                    (timestamp, mode, cumulative_pnl, daily_pnl,
                     daily_trade_count, current_drawdown, win_rate,
                     active_positions, total_trades, metadata)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """, datetime.utcnow(),
                    self.state['mode'],
                    self.state['cumulative_pnl'],
                    self.state['daily_pnl'],
                    self.state['daily_trade_count'],
                    self.state['current_drawdown'],
                    self.get_win_rate(),
                    len(self.state['active_symbols']),
                    self.state['total_trades'],
                    json.dumps({
                        'consecutive_losses': self.state['consecutive_losses'],
                        'consecutive_wins': self.state['consecutive_wins'],
                    }))
        except Exception as e:
            logger.debug(f"History snapshot error: {e}")

    def _make_serializable(self, obj):
        """JSON serializable hale getir"""
        if isinstance(obj, dict):
            return {k: self._make_serializable(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [self._make_serializable(i) for i in obj]
        elif isinstance(obj, datetime):
            return obj.isoformat()
        elif isinstance(obj, date):
            return str(obj)
        return obj

    def get_state_summary(self) -> Dict[str, Any]:
        """Dashboard/chat için özet döndür"""
        return {
            'mode': self.state['mode'],
            'cumulative_pnl': self.state['cumulative_pnl'],
            'daily_pnl': self.state['daily_pnl'],
            'daily_trades': self.state['daily_trade_count'],
            'total_trades': self.state['total_trades'],
            'win_rate': self.get_win_rate(),
            'consecutive_losses': self.state['consecutive_losses'],
            'consecutive_wins': self.state['consecutive_wins'],
            'drawdown_pct': self.state['current_drawdown'],
            'peak_pnl': self.state['peak_pnl'],
            'active_positions': len(self.state['active_symbols']),
            'active_symbols': self.state['active_symbols'],
            'best_streak': self.state['consecutive_wins'],
            'worst_streak': self.state['consecutive_losses'],
            'current_drawdown': self.state['current_drawdown'],
        }

    def update_mode(self):
        """Mode geçişini kontrol et (health monitor'dan çağrılır)"""
        self._check_daily_reset()
        self._check_mode_transition()

    async def snapshot_history(self):
        """State history snapshot'ı kaydet (health monitor'dan çağrılır)"""
        await self._save_history_snapshot()
