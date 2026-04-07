import asyncio
import logging
from datetime import datetime
from typing import Dict, List, Any

from config import Config
from database import Database

logger = logging.getLogger(__name__)

# Minimum potansiyel getiri filtresi
MIN_POTENTIAL_RETURN = 0.02  # %2


class GhostSimulatorAgent:
    def __init__(self, db: Database, brain=None):
        self.db = db
        self.brain = brain
        self.running = False
        self.last_activity = None
        self.active_simulations: Dict[int, Dict[str, Any]] = {}
        self.total_closed = 0
        self.total_wins = 0
        self.total_pnl = 0.0

    async def initialize(self):
        logger.info("Initializing Ghost Simulator Agent...")
        open_sims = await self.db.get_open_simulations()
        for sim in open_sims:
            self.active_simulations[sim['id']] = sim
        # Geçmiş istatistikleri yükle
        closed = await self.db.get_closed_simulations(limit=1000)
        self.total_closed = len(closed)
        self.total_wins = sum(1 for s in closed if float(s.get('pnl', 0)) > 0)
        self.total_pnl = sum(float(s.get('pnl', 0)) for s in closed)
        logger.info(f"Loaded {len(self.active_simulations)} open simulations, {self.total_closed} historical")

    async def start(self):
        self.running = True
        logger.info("Starting Ghost Simulator Agent...")

        try:
            while self.running:
                await self._process_pending_signals()
                await self._monitor_active_simulations()
                self.last_activity = datetime.utcnow()
                await asyncio.sleep(30)  # 30 saniyede bir kontrol

        except Exception as e:
            logger.error(f"Ghost simulator agent error: {e}")
            raise
        finally:
            await self.stop()

    async def stop(self):
        self.running = False
        logger.info("Stopping Ghost Simulator Agent...")
        for sim_id in list(self.active_simulations.keys()):
            await self._close_simulation(sim_id, "agent_shutdown")
        self.active_simulations.clear()

    async def _process_pending_signals(self):
        try:
            pending_signals = await self.db.get_pending_signals()
            for signal in pending_signals:
                if signal['id'] not in [s.get('signal_id') for s in self.active_simulations.values()]:
                    # Min potansiyel getiri kontrolü
                    metadata = signal.get('metadata', {})
                    if isinstance(metadata, str):
                        import json
                        metadata = json.loads(metadata)

                    # Brain sinyali ise, timeframe tahminlerini kontrol et
                    if 'brain_analysis' in metadata:
                        tf_preds = metadata.get('timeframe_predictions', {})
                        max_change = max(
                            (abs(v.get('avg_change_pct', 0)) for v in tf_preds.values()),
                            default=0
                        )
                        if max_change < MIN_POTENTIAL_RETURN:
                            await self.db.update_signal_status(signal['id'], 'filtered_low_return')
                            logger.debug(f"Filtered signal {signal['id']}: potential return {max_change:.4f} < {MIN_POTENTIAL_RETURN}")
                            continue

                    await self._create_simulation(signal)
        except Exception as e:
            logger.error(f"Error processing pending signals: {e}")

    async def _create_simulation(self, signal: Dict[str, Any]):
        try:
            config = Config.get_agent_config('ghost_simulator')
            position_size = min(config['max_position_size'], 1000)
            entry_price = float(signal['price'])
            metadata = signal.get('metadata', {})
            if isinstance(metadata, str):
                import json
                metadata = json.loads(metadata)

            direction = metadata.get('position_bias') or (
                'long' if signal['signal_type'].endswith('_long') or '_long_' in signal['signal_type']
                else 'short'
            )

            if direction == 'long':
                side = 'long'
                stop_loss = entry_price * (1 - Config.GHOST_STOP_LOSS_PCT)
                take_profit = entry_price * (1 + Config.GHOST_TAKE_PROFIT_PCT)
            else:
                side = 'short'
                stop_loss = entry_price * (1 + Config.GHOST_STOP_LOSS_PCT)
                take_profit = entry_price * (1 - Config.GHOST_TAKE_PROFIT_PCT)

            simulation_data = {
                'signal_id': signal['id'],
                'market_type': signal.get('market_type', 'spot'),
                'symbol': signal['symbol'],
                'entry_price': entry_price,
                'quantity': position_size,
                'side': side,
                'entry_time': datetime.utcnow(),
                'stop_loss': stop_loss,
                'take_profit': take_profit,
                'metadata': {
                    'signal_confidence': float(signal['confidence']),
                    'signal_type': signal['signal_type'],
                    'position_bias': direction,
                    'commission': config['commission_pct'] * position_size * entry_price,
                    'brain_analysis': metadata.get('brain_analysis', False),
                    'timeframe': metadata.get('timeframe', 'unknown'),
                }
            }

            sim_id = await self.db.insert_simulation(simulation_data)
            self.active_simulations[sim_id] = {**simulation_data, 'id': sim_id}
            await self.db.update_signal_status(signal['id'], 'processed')
            logger.info(f"👻 Created simulation {sim_id}: {side} {signal['symbol']} @ ${entry_price:,.2f} "
                         f"(TP: ${take_profit:,.2f} SL: ${stop_loss:,.2f})")

        except Exception as e:
            logger.error(f"Error creating simulation: {e}")

    async def _monitor_active_simulations(self):
        try:
            current_prices = await self._get_current_prices()
            to_close = []

            for sim_id, sim_data in list(self.active_simulations.items()):
                symbol = sim_data['symbol']
                current_price = current_prices.get(symbol)
                if current_price is None or current_price <= 0:
                    continue

                entry = float(sim_data['entry_price'])
                tp = float(sim_data['take_profit'])
                sl = float(sim_data['stop_loss'])

                if sim_data['side'] == 'long':
                    if current_price >= tp:
                        await self._close_simulation(sim_id, 'take_profit', current_price)
                        to_close.append(sim_id)
                    elif current_price <= sl:
                        await self._close_simulation(sim_id, 'stop_loss', current_price)
                        to_close.append(sim_id)
                else:
                    if current_price <= tp:
                        await self._close_simulation(sim_id, 'take_profit', current_price)
                        to_close.append(sim_id)
                    elif current_price >= sl:
                        await self._close_simulation(sim_id, 'stop_loss', current_price)
                        to_close.append(sim_id)

                if sim_id not in to_close and self._check_timeout(sim_data):
                    await self._close_simulation(sim_id, 'timeout', current_price)
                    to_close.append(sim_id)

            for sim_id in to_close:
                self.active_simulations.pop(sim_id, None)

        except Exception as e:
            logger.error(f"Error monitoring simulations: {e}")

    def _check_timeout(self, sim_data: Dict[str, Any]) -> bool:
        entry_time = sim_data.get('entry_time')
        if not entry_time:
            return False
        if isinstance(entry_time, str):
            entry_time = datetime.fromisoformat(entry_time)
        elapsed = datetime.utcnow() - entry_time
        return elapsed.total_seconds() > (Config.SIMULATION_TIMEOUT_HOURS * 3600)

    async def _close_simulation(self, sim_id: int, reason: str, exit_price: float = None):
        try:
            sim_data = self.active_simulations.get(sim_id)
            if not sim_data:
                return

            if exit_price is None:
                current_prices = await self._get_current_prices()
                exit_price = current_prices.get(sim_data['symbol'], float(sim_data['entry_price']))

            entry_price = float(sim_data['entry_price'])
            quantity = float(sim_data['quantity'])
            side = sim_data['side']

            pnl = (exit_price - entry_price) * quantity if side == 'long' else (entry_price - exit_price) * quantity
            commission = sim_data.get('metadata', {}).get('commission', 0)
            pnl -= commission
            pnl_pct = (pnl / (entry_price * quantity)) * 100 if entry_price * quantity else 0.0

            update_data = {
                'exit_price': exit_price,
                'exit_time': datetime.utcnow(),
                'pnl': pnl,
                'pnl_pct': pnl_pct,
                'status': 'closed'
            }

            await self.db.update_simulation(sim_id, update_data)
            self.total_closed += 1
            self.total_pnl += pnl
            was_correct = pnl > 0
            if was_correct:
                self.total_wins += 1

            win_emoji = "✅" if was_correct else "❌"
            logger.info(f"👻 {win_emoji} Closed sim {sim_id}: {reason} | "
                         f"{sim_data['symbol']} {side} | PnL: ${pnl:.2f} ({pnl_pct:.2f}%)")

            # Brain'e feedback gönder (öğrenme döngüsü)
            await self._send_feedback_to_brain(sim_data, was_correct, pnl_pct)

        except Exception as e:
            logger.error(f"Error closing simulation {sim_id}: {e}")

    async def _send_feedback_to_brain(self, sim_data: Dict, was_correct: bool, pnl_pct: float):
        """Sonucu Brain'e ve DB'ye bildir - öğrenme döngüsü"""
        try:
            signal_type = sim_data.get('metadata', {}).get('signal_type', 'unknown')

            # Brain modülüne bildir
            if self.brain:
                self.brain.update_learning(signal_type, was_correct, pnl_pct)

            # DB'ye öğrenme kaydı yaz
            await self.db.insert_learning_log(
                signal_type=signal_type,
                was_correct=was_correct,
                pnl_pct=pnl_pct,
                context={
                    'symbol': sim_data.get('symbol'),
                    'side': sim_data.get('side'),
                    'market_type': sim_data.get('market_type'),
                    'timeframe': sim_data.get('metadata', {}).get('timeframe'),
                    'confidence': sim_data.get('metadata', {}).get('signal_confidence'),
                }
            )
        except Exception as e:
            logger.debug(f"Feedback error: {e}")

    async def _get_current_prices(self) -> Dict[str, float]:
        prices = {}
        try:
            # Açık simülasyonlardaki semboller + watchlist
            symbols = set()
            for sim in self.active_simulations.values():
                symbols.add(sim['symbol'])
            for symbol in symbols:
                recent_trades = await self.db.get_recent_trades(symbol, limit=1)
                if recent_trades:
                    prices[symbol] = float(recent_trades[0]['price'])
        except Exception as e:
            logger.error(f"Error getting current prices: {e}")
        return prices

    async def health_check(self) -> Dict[str, Any]:
        win_rate = (self.total_wins / self.total_closed * 100) if self.total_closed else 0
        return {
            "healthy": True,
            "last_activity": self.last_activity.isoformat() if self.last_activity else None,
            "active_simulations": len(self.active_simulations),
            "total_closed": self.total_closed,
            "total_wins": self.total_wins,
            "win_rate": win_rate,
            "total_pnl": self.total_pnl,
            "brain_connected": self.brain is not None,
        }
