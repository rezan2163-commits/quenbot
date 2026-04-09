import asyncio
import json
import logging
from datetime import datetime
from typing import Dict, List, Any

from config import Config
from database import Database

logger = logging.getLogger(__name__)

# Minimum potansiyel getiri filtresi
MIN_POTENTIAL_RETURN = 0.02  # %2


class GhostSimulatorAgent:
    def __init__(self, db: Database, brain=None, state_tracker=None, risk_manager=None):
        self.db = db
        self.brain = brain
        self.state_tracker = state_tracker
        self.risk_manager = risk_manager
        self.running = False
        self.last_activity = None
        self.active_simulations: Dict[int, Dict[str, Any]] = {}
        self.total_closed = 0
        self.total_wins = 0
        self.total_pnl = 0.0

    @staticmethod
    def _parse_metadata(data: dict) -> dict:
        """Ensure metadata field is parsed from JSON string if needed."""
        md = data.get('metadata', {})
        if isinstance(md, str):
            try:
                md = json.loads(md)
            except (json.JSONDecodeError, TypeError):
                md = {}
        data['metadata'] = md if isinstance(md, dict) else {}
        return data

    async def initialize(self):
        logger.info("Initializing Ghost Simulator Agent...")
        open_sims = await self.db.get_open_simulations()

        # Deduplicate: keep only the newest simulation per symbol, close duplicates
        symbol_sims: Dict[str, List[Dict]] = {}
        for sim in open_sims:
            self._parse_metadata(sim)
            sym = sim.get('symbol', '')
            symbol_sims.setdefault(sym, []).append(sim)

        for sym, sims in symbol_sims.items():
            if len(sims) > 1:
                # Sort by id desc (newest first), close older duplicates
                sims.sort(key=lambda s: s.get('id', 0), reverse=True)
                keeper = sims[0]
                self.active_simulations[keeper['id']] = keeper
                for dup in sims[1:]:
                    try:
                        await self._close_simulation_direct(dup, 'duplicate_cleanup')
                        logger.info(f"🧹 Closed duplicate sim #{dup.get('id')} for {sym}")
                    except Exception as e:
                        logger.error(f"Error closing duplicate sim #{dup.get('id')}: {e}")
            else:
                self.active_simulations[sims[0]['id']] = sims[0]

        # Sync state_tracker active_symbols with actual open simulations
        if self.state_tracker:
            actual_symbols = list(set(s.get('symbol', '') for s in self.active_simulations.values()))
            self.state_tracker.state['active_symbols'] = actual_symbols
            await self.state_tracker.save_state()
            logger.info(f"📊 Synced active_symbols: {actual_symbols}")

        # Geçmiş istatistikleri yükle
        closed = await self.db.get_closed_simulations(limit=1000)
        self.total_closed = len(closed)
        self.total_wins = sum(1 for s in closed if float(s.get('pnl', 0)) > 0)
        self.total_pnl = sum(float(s.get('pnl', 0)) for s in closed)
        logger.info(f"Loaded {len(self.active_simulations)} open simulations (deduped), {self.total_closed} historical")

    async def start(self):
        self.running = True
        logger.info("Starting Ghost Simulator Agent...")

        while self.running:
            try:
                await self._process_pending_signals()
                await self._monitor_active_simulations()
                self.last_activity = datetime.utcnow()
            except Exception as e:
                logger.error(f"Ghost simulator cycle error: {e}")
            await asyncio.sleep(30)  # 30 saniyede bir kontrol

    async def stop(self):
        self.running = False
        logger.info("Stopping Ghost Simulator Agent...")
        for sim_id in list(self.active_simulations.keys()):
            await self._close_simulation(sim_id, "agent_shutdown")
        self.active_simulations.clear()

    async def _process_pending_signals(self):
        try:
            pending_signals = await self.db.get_pending_signals()
            # Get currently active symbols from in-memory simulations
            active_syms = set(s.get('symbol', '') for s in self.active_simulations.values())
            for signal in pending_signals:
                if signal['id'] in [s.get('signal_id') for s in self.active_simulations.values()]:
                    continue
                # Block duplicate symbol positions at Ghost level (before risk check)
                if signal['symbol'] in active_syms:
                    await self.db.update_signal_status(signal['id'], 'filtered_duplicate')
                    continue

                # RiskManager gate check
                if self.risk_manager:
                    approved, reason = self.risk_manager.check_signal(signal)
                    if not approved:
                        await self.db.update_signal_status(signal['id'], f'risk_rejected')
                        logger.info(f"🛡 Risk rejected {signal['symbol']}: {reason}")
                        continue

                # Min potansiyel getiri kontrolü
                metadata = signal.get('metadata', {})
                if isinstance(metadata, str):
                    try:
                        metadata = json.loads(metadata)
                    except (json.JSONDecodeError, TypeError):
                        metadata = {}

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
                else:
                    # ALL other signal types: enforce ≥2% via confidence + mean_profit check
                    mean_profit = abs(float(metadata.get('mean_profit', 0)))
                    ref_change = abs(float(metadata.get('reference_change_pct', 0)))
                    price_change = abs(float(metadata.get('price_change_pct', 0)))
                    potential = max(mean_profit, ref_change, price_change)
                    if potential > 0 and potential < MIN_POTENTIAL_RETURN:
                        await self.db.update_signal_status(signal['id'], 'filtered_low_return')
                        logger.debug(f"Filtered signal {signal['id']}: potential {potential:.4f} < {MIN_POTENTIAL_RETURN}")
                        continue

                await self._create_simulation(signal)
        except Exception as e:
            logger.error(f"Error processing pending signals: {e}")

    async def _create_simulation(self, signal: Dict[str, Any]):
        try:
            config = Config.get_agent_config('ghost_simulator')
            entry_price = float(signal['price'])
            metadata = signal.get('metadata', {})
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except (json.JSONDecodeError, TypeError):
                    metadata = {}

            direction = metadata.get('position_bias') or (
                'long' if signal['signal_type'].endswith('_long') or '_long_' in signal['signal_type']
                else 'short'
            )

            # Dynamic TP/SL from RiskManager mode params
            if self.risk_manager:
                mode_params = self.risk_manager.get_mode_params()
                tp_pct = mode_params['take_profit_pct']
                sl_pct = mode_params['stop_loss_pct']
                position_size = self.risk_manager.calculate_position_size(
                    confidence=float(signal.get('confidence', 0.5)),
                    atr_ratio=metadata.get('atr_ratio', 0.02),
                )
            else:
                tp_pct = Config.GHOST_TAKE_PROFIT_PCT
                sl_pct = Config.GHOST_STOP_LOSS_PCT
                position_size = min(config['max_position_size'], 1000)

            if direction == 'long':
                side = 'long'
                stop_loss = entry_price * (1 - sl_pct)
                take_profit = entry_price * (1 + tp_pct)
            else:
                side = 'short'
                stop_loss = entry_price * (1 + sl_pct)
                take_profit = entry_price * (1 - tp_pct)

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

            # StateTracker'a aktif sembol ekle
            if self.state_tracker:
                self.state_tracker.add_active_symbol(signal['symbol'])

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

    async def _close_simulation_direct(self, sim_data: Dict[str, Any], reason: str):
        """Close a simulation directly from its data dict (used during dedup cleanup)."""
        sim_id = sim_data.get('id')
        if not sim_id:
            return
        try:
            entry_price = float(sim_data.get('entry_price', 0))
            current_prices = await self._get_current_prices()
            symbol = sim_data.get('symbol', '')
            exit_price = current_prices.get(symbol, entry_price)
            quantity = float(sim_data.get('quantity', 0))
            side = sim_data.get('side', 'long')

            pnl = (exit_price - entry_price) * quantity if side == 'long' else (entry_price - exit_price) * quantity
            pnl_pct = (pnl / (entry_price * quantity)) * 100 if entry_price * quantity else 0.0

            await self.db.update_simulation(sim_id, {
                'exit_price': exit_price,
                'exit_time': datetime.utcnow(),
                'pnl': pnl,
                'pnl_pct': pnl_pct,
                'status': 'closed'
            })
        except Exception as e:
            logger.error(f"Error in _close_simulation_direct #{sim_id}: {e}")

    async def _close_simulation(self, sim_id: int, reason: str, exit_price: float = None):
        try:
            sim_data = self.active_simulations.get(sim_id)
            if not sim_data:
                return
            self._parse_metadata(sim_data)

            symbol = sim_data['symbol']

            if exit_price is None:
                current_prices = await self._get_current_prices()
                exit_price = current_prices.get(symbol, float(sim_data['entry_price']))

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

            # StateTracker'a trade kaydet
            if self.state_tracker:
                await self.state_tracker.record_trade({
                    'pnl': pnl,
                    'pnl_pct': pnl_pct,
                    'symbol': symbol,
                    'signal_type': sim_data.get('metadata', {}).get('signal_type', 'unknown'),
                    'side': side,
                })
                self.state_tracker.remove_active_symbol(symbol)

            # RiskManager cooldown
            if self.risk_manager and not was_correct:
                self.risk_manager.record_loss()

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
