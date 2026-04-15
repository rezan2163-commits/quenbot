import asyncio
import json
import logging
from datetime import datetime
from typing import Dict, List, Any

from config import Config
from database import Database
from event_bus import Event, EventType, get_event_bus
from qwen_models import CommandAction, ExecutionFeedback
from market_activity_tracker import get_market_tracker

logger = logging.getLogger(__name__)

# Lazy LLM bridge import
_llm_bridge = None
def _get_llm_bridge():
    global _llm_bridge
    if _llm_bridge is None:
        try:
            from llm_bridge import get_llm_bridge
            _llm_bridge = get_llm_bridge()
        except Exception:
            _llm_bridge = None
    return _llm_bridge

# Minimum potansiyel getiri filtresi
MIN_POTENTIAL_RETURN = 0.005  # %0.5

# ─── Spread & Latency Modelling ───
# Gerçekçi paper trade simülasyonu için spread ve gecikme parametreleri
SPREAD_MODEL = {
    # symbol_prefix → (spread_bps, latency_ms)  
    # basis points spread + simulated execution latency
    'BTC':  (1.5, 50),    # Liquid pair: tight spread, fast fill
    'ETH':  (2.0, 50),    
    'SOL':  (3.0, 80),    
    'BNB':  (2.5, 60),    
    'XRP':  (3.5, 80),    
    '_DEFAULT': (5.0, 100),  # Less liquid alts: wider spread, slower fill
}

def _get_spread_params(symbol: str) -> tuple:
    """Sembol bazlı spread ve latency parametreleri döndür"""
    symbol_upper = symbol.upper().replace('USDT', '')
    for prefix, params in SPREAD_MODEL.items():
        if prefix != '_DEFAULT' and symbol_upper.startswith(prefix):
            return params
    return SPREAD_MODEL['_DEFAULT']

def _apply_slippage(price: float, side: str, spread_bps: float) -> float:
    """
    Gerçekçi slippage modeli.
    Long giriş = fiyat yukarı kayar (ask'e yakın)
    Short giriş = fiyat aşağı kayar (bid'e yakın)
    Çıkışta tersi.
    """
    spread_pct = spread_bps / 10000.0
    half_spread = spread_pct / 2.0
    if side == 'long':
        return price * (1 + half_spread)  # Ask side
    else:
        return price * (1 - half_spread)  # Bid side


class GhostSimulatorAgent:
    def __init__(self, db: Database, brain=None, state_tracker=None, risk_manager=None,
                 rca_engine=None, decision_core=None):
        self.db = db
        self.brain = brain
        self.state_tracker = state_tracker
        self.risk_manager = risk_manager
        self.rca_engine = rca_engine
        self.decision_core = decision_core
        self.event_bus = get_event_bus()
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
                if self._is_stale_open(keeper):
                    await self._close_simulation_direct(keeper, 'startup_stale_cleanup')
                    logger.info(f"🧹 Closed stale keeper sim #{keeper.get('id')} for {sym}")
                else:
                    self.active_simulations[keeper['id']] = keeper
                for dup in sims[1:]:
                    try:
                        await self._close_simulation_direct(dup, 'duplicate_cleanup')
                        logger.info(f"🧹 Closed duplicate sim #{dup.get('id')} for {sym}")
                    except Exception as e:
                        logger.error(f"Error closing duplicate sim #{dup.get('id')}: {e}")
            else:
                only = sims[0]
                if self._is_stale_open(only):
                    await self._close_simulation_direct(only, 'startup_stale_cleanup')
                    logger.info(f"🧹 Closed stale sim #{only.get('id')} for {sym}")
                else:
                    self.active_simulations[only['id']] = only

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
        tracker = get_market_tracker()

        while self.running:
            try:
                await self._reconcile_active_simulations()
                await self._process_pending_signals()
                await self._monitor_active_simulations()
                self.last_activity = datetime.utcnow()
            except Exception as e:
                logger.error(f"Ghost simulator cycle error: {e}")

            # Aktif simülasyon varsa daha sık kontrol, yoksa tracker'a bağlı bekle
            if self.active_simulations:
                await asyncio.sleep(10)  # Aktif pozisyon izleme: 10s
            else:
                await tracker.wait_for_activity(timeout=30)  # Pozisyon yoksa event bekle

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
                        if self.risk_manager.should_log_rejection(signal['symbol'], reason):
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
            raw_entry_price = float(signal['price'])
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

            # ─── Spread & Latency Modelling ───
            spread_bps, latency_ms = _get_spread_params(signal['symbol'])
            entry_price = _apply_slippage(raw_entry_price, direction, spread_bps)

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
                    'spread_bps': spread_bps,
                    'latency_ms': latency_ms,
                    'raw_entry_price': raw_entry_price,
                    'slippage_pct': abs(entry_price - raw_entry_price) / max(raw_entry_price, 1e-8) * 100,
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
                    # No fresh trade tick: still allow timeout based closure.
                    if self._check_timeout(sim_data):
                        await self._close_simulation(sim_id, 'timeout', float(sim_data['entry_price']))
                        to_close.append(sim_id)
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
        try:
            if isinstance(entry_time, str):
                entry_time = datetime.fromisoformat(entry_time)

            # Handle both naive and timezone-aware datetimes safely.
            if getattr(entry_time, 'tzinfo', None) is not None:
                now = datetime.now(entry_time.tzinfo)
            else:
                now = datetime.utcnow()

            elapsed = now - entry_time
            return elapsed.total_seconds() > (Config.SIMULATION_TIMEOUT_HOURS * 3600)
        except Exception as e:
            logger.debug(f"Simulation timeout parse error: {e}")
            return False

    def _is_stale_open(self, sim_data: Dict[str, Any]) -> bool:
        """Startup/cycle cleanup guard for very old open simulations."""
        entry_time = sim_data.get('entry_time')
        if not entry_time:
            return False
        try:
            if isinstance(entry_time, str):
                entry_time = datetime.fromisoformat(entry_time)
            if getattr(entry_time, 'tzinfo', None) is not None:
                now = datetime.now(entry_time.tzinfo)
            else:
                now = datetime.utcnow()
            return (now - entry_time).total_seconds() > (Config.SIMULATION_TIMEOUT_HOURS * 3600)
        except Exception:
            return False

    async def _reconcile_active_simulations(self):
        """Keep in-memory active map and state tracker aligned with DB reality."""
        try:
            db_open = await self.db.get_open_simulations()
            open_map = {}
            for sim in db_open:
                self._parse_metadata(sim)
                if self._is_stale_open(sim):
                    await self._close_simulation_direct(sim, 'stale_recovery')
                    continue
                open_map[int(sim['id'])] = sim

            self.active_simulations = open_map

            if self.state_tracker:
                actual_symbols = sorted({s.get('symbol', '') for s in self.active_simulations.values() if s.get('symbol')})
                if actual_symbols != sorted(self.state_tracker.state.get('active_symbols', [])):
                    self.state_tracker.state['active_symbols'] = actual_symbols
                    await self.state_tracker.save_state()
                    logger.info(f"📊 Reconciled active_symbols: {actual_symbols}")
        except Exception as e:
            logger.debug(f"Active simulation reconcile skipped: {e}")

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

            updated = await self.db.update_simulation(sim_id, {
                'exit_price': exit_price,
                'exit_time': datetime.utcnow(),
                'pnl': pnl,
                'pnl_pct': pnl_pct,
                'status': 'closed'
            })
            if not updated:
                logger.debug(f"Skip duplicate close for sim #{sim_id} ({reason})")
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

            # ─── Exit slippage modelling ───
            # Çıkışta ters yönde spread uygulanır (long çıkış = bid, short çıkış = ask)
            spread_bps = float(sim_data.get('metadata', {}).get('spread_bps', 0) or 0)
            if spread_bps > 0:
                exit_side = 'short' if side == 'long' else 'long'  # Çıkış = ters taraf
                exit_price = _apply_slippage(exit_price, exit_side, spread_bps)

            pnl = (exit_price - entry_price) * quantity if side == 'long' else (entry_price - exit_price) * quantity
            commission = sim_data.get('metadata', {}).get('commission', 0)
            pnl -= commission
            pnl_pct = (pnl / (entry_price * quantity)) * 100 if entry_price * quantity else 0.0
            exit_time = datetime.utcnow()

            update_data = {
                'exit_price': exit_price,
                'exit_time': exit_time,
                'pnl': pnl,
                'pnl_pct': pnl_pct,
                'status': 'closed'
            }

            updated = await self.db.update_simulation(sim_id, update_data)
            if not updated:
                # Already closed by another cycle/process; avoid duplicate accounting/logging.
                self.active_simulations.pop(sim_id, None)
                logger.debug(f"Skip duplicate close for sim #{sim_id} ({reason})")
                return
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

            loss_analysis = None
            if not was_correct:
                loss_analysis = await self._analyze_loss(sim_id, sim_data, reason, exit_price, pnl_pct)

            # Brain'e feedback gönder (öğrenme döngüsü)
            await self._send_feedback_to_brain(
                sim_data,
                was_correct,
                pnl_pct,
                reason=reason,
                exit_price=exit_price,
                loss_analysis=loss_analysis,
                simulation_id=sim_id,
            )

            signal_id = sim_data.get('signal_id')
            outcome_details = {
                'outcome_recorded_at': exit_time.isoformat() + 'Z',
                'close_reason': reason,
                'exit_price': float(exit_price),
                'signal_id': signal_id,
                'simulation_id': sim_id,
                'signal_type': sim_data.get('metadata', {}).get('signal_type', 'unknown'),
                'learning_feedback_ready': True,
                'loss_analysis': loss_analysis,
            }
            if signal_id:
                await self.db.record_signal_outcome(
                    int(signal_id),
                    target_hit=(reason == 'take_profit'),
                    was_correct=was_correct,
                    pnl_pct=pnl_pct,
                    outcome_details=outcome_details,
                )

            await self.event_bus.publish(Event(
                type=EventType.SIM_CLOSED,
                source='ghost_simulator',
                data={
                    'id': sim_id,
                    'signal_id': signal_id,
                    'symbol': symbol,
                    'side': side,
                    'market_type': sim_data.get('market_type', 'spot'),
                    'entry_price': float(entry_price),
                    'exit_price': float(exit_price),
                    'pnl': float(pnl),
                    'pnl_pct': float(pnl_pct),
                    'reason': reason,
                    'was_correct': bool(was_correct),
                    'target_hit': bool(reason == 'take_profit'),
                    'closed_at': exit_time.isoformat() + 'Z',
                    'signal_type': sim_data.get('metadata', {}).get('signal_type', 'unknown'),
                    'metadata': sim_data.get('metadata', {}),
                    'loss_analysis': loss_analysis,
                },
            ))

            # LLM post-trade analysis (background priority)
            bridge = _get_llm_bridge()
            if bridge:
                try:
                    llm_analysis = await bridge.ghost_post_trade_analysis({
                        "symbol": symbol,
                        "side": side,
                        "entry_price": entry_price,
                        "exit_price": exit_price,
                        "pnl_pct": pnl_pct,
                        "close_reason": reason,
                        "metadata": sim_data.get("metadata", {}),
                        "holding_time_min": int(
                            (datetime.utcnow() - (sim_data.get('entry_time') or datetime.utcnow())).total_seconds() / 60
                        ) if isinstance(sim_data.get('entry_time'), datetime) else 0,
                    })
                    if llm_analysis and llm_analysis.get("_parsed"):
                        lesson = llm_analysis.get("lesson", "")
                        if lesson:
                            logger.info(f"🤖 LLM Ghost [{symbol}]: {lesson[:120]}")
                except Exception as e:
                    logger.debug(f"LLM post-trade analysis skipped: {e}")

        except Exception as e:
            logger.error(f"Error closing simulation {sim_id}: {e}")

    async def _analyze_loss(self, sim_id: int, sim_data: Dict[str, Any], reason: str,
                            exit_price: float, pnl_pct: float) -> Dict[str, Any] | None:
        if not self.rca_engine:
            return None

        try:
            simulation_payload = {
                'id': sim_id,
                'symbol': sim_data.get('symbol'),
                'side': sim_data.get('side'),
                'entry_price': sim_data.get('entry_price'),
                'exit_price': exit_price,
                'entry_time': sim_data.get('entry_time'),
                'exit_time': datetime.utcnow(),
                'pnl_pct': pnl_pct,
                'metadata': sim_data.get('metadata', {}),
            }
            analysis = await self.rca_engine.analyze_failure(simulation_payload)
            if not analysis:
                return None

            await self.db.insert_rca_result({
                'simulation_id': sim_id,
                'failure_type': analysis.get('failure_type', 'UNKNOWN'),
                'confidence': analysis.get('confidence', 0),
                'explanation': analysis.get('explanation'),
                'recommendations': analysis.get('recommendations', []),
                'context': {
                    **(analysis.get('context', {}) or {}),
                    'close_reason': reason,
                    'entry_time': sim_data.get('entry_time'),
                    'exit_time': simulation_payload['exit_time'],
                },
            })

            signal_type = sim_data.get('metadata', {}).get('signal_type', 'unknown')
            await self.db.insert_failure_analysis({
                'timestamp': datetime.utcnow(),
                'signal_type': signal_type,
                'failure_count': 1,
                'avg_loss_pct': abs(pnl_pct),
                'recommendation': '; '.join(analysis.get('recommendations', [])[:2]),
                'metadata': {
                    'simulation_id': sim_id,
                    'symbol': sim_data.get('symbol'),
                    'failure_type': analysis.get('failure_type', 'UNKNOWN'),
                    'close_reason': reason,
                    'rca_confidence': analysis.get('confidence', 0),
                    'rca_explanation': analysis.get('explanation', ''),
                },
            })

            await self._write_immediate_correction_note(sim_data, sim_id, analysis)
            return analysis
        except Exception as e:
            logger.debug(f"Immediate loss RCA skipped: {e}")
            return None

    async def _write_immediate_correction_note(self, sim_data: Dict[str, Any], sim_id: int,
                                               rca_result: Dict[str, Any]) -> None:
        failure_type = rca_result.get('failure_type', 'UNKNOWN')
        confidence = float(rca_result.get('confidence', 0) or 0)
        if confidence < 0.4:
            return

        signal_type = sim_data.get('metadata', {}).get('signal_type', 'unknown')
        corrections = []
        if failure_type == 'FALSE_BREAKOUT':
            corrections.append(('similarity_threshold', 0.05, 'FALSE_BREAKOUT: increase similarity threshold to be more selective'))
        elif failure_type == 'STOP_HUNT':
            corrections.append(('stop_loss_pct', 0.005, 'STOP_HUNT: widen stop loss to avoid wick traps'))
        elif failure_type == 'OVEREXTENDED':
            corrections.append(('take_profit_pct', -0.005, 'OVEREXTENDED: tighten take profit for earlier exit'))
        elif failure_type == 'LOW_VOLUME_NOISE':
            corrections.append(('price_movement_threshold', 0.005, 'LOW_VOLUME_NOISE: raise movement threshold to filter noise'))
        elif failure_type in ('BAD_TIMING', 'TREND_REVERSAL'):
            corrections.append(('min_confidence', 0.05, f'{failure_type}: raise min confidence for {signal_type}'))

        for adjustment_key, adjustment_value, reason in corrections:
            await self.db.insert_correction_note({
                'signal_type': signal_type,
                'failure_type': failure_type,
                'adjustment_key': adjustment_key,
                'adjustment_value': adjustment_value,
                'reason': reason,
                'simulation_id': sim_id,
            })

    async def _send_feedback_to_brain(self, sim_data: Dict, was_correct: bool, pnl_pct: float,
                                      reason: str, exit_price: float,
                                      loss_analysis: Dict[str, Any] | None = None,
                                      simulation_id: int | None = None):
        """Sonucu Brain'e ve DB'ye bildir - öğrenme döngüsü"""
        try:
            metadata = sim_data.get('metadata', {}) or {}
            signal_type = metadata.get('signal_type', 'unknown')
            recommendations = list((loss_analysis or {}).get('recommendations', [])[:3])
            failure_type = (loss_analysis or {}).get('failure_type')
            explanation = (loss_analysis or {}).get('explanation', '')
            lesson_summary = ' | '.join(filter(None, [failure_type, explanation])) or reason
            learning_context = {
                'symbol': sim_data.get('symbol'),
                'side': sim_data.get('side'),
                'market_type': sim_data.get('market_type'),
                'timeframe': metadata.get('timeframe'),
                'confidence': metadata.get('signal_confidence'),
                'entry_price': sim_data.get('entry_price'),
                'exit_price': exit_price,
                'close_reason': reason,
                'simulation_id': simulation_id,
                'failure_type': failure_type,
                'loss_explanation': explanation,
                'recommendations': recommendations,
            }

            # Brain modülüne bildir
            if self.brain:
                self.brain.update_learning(signal_type, was_correct, pnl_pct)

            # DB'ye öğrenme kaydı yaz
            await self.db.insert_learning_log(
                signal_type=signal_type,
                was_correct=was_correct,
                pnl_pct=pnl_pct,
                context=learning_context
            )

            if self.decision_core:
                side = str(sim_data.get('side', 'long')).lower()
                action = CommandAction.LONG if side == 'long' else CommandAction.SHORT
                lessons = recommendations or [lesson_summary]
                await self.decision_core.record_execution_feedback(ExecutionFeedback(
                    symbol=str(sim_data.get('symbol', '?')),
                    action=action,
                    status='paper_closed',
                    pnl_pct=float(pnl_pct or 0.0),
                    error_message=None if was_correct else lesson_summary,
                    details={
                        **learning_context,
                        'reasoning': lesson_summary,
                        'confidence': float(metadata.get('signal_confidence', 0.0) or 0.0),
                        'lessons': lessons,
                    },
                ))
        except Exception as e:
            logger.debug(f"Feedback error: {e}")

    async def _get_current_prices(self) -> Dict[str, float]:
        """In-memory price cache from MarketActivityTracker — zero DB queries."""
        tracker = get_market_tracker()
        tracker_prices = tracker.get_all_prices()

        # Aktif simülasyonlardaki semboller için fiyat kontrol
        prices = {}
        for sim in self.active_simulations.values():
            symbol = sim['symbol']
            cached = tracker_prices.get(symbol, 0.0)
            if cached > 0:
                prices[symbol] = cached
            else:
                # Tracker'da yoksa DB fallback (nadir durum)
                try:
                    recent = await self.db.get_recent_trades(symbol, limit=1)
                    if recent:
                        prices[symbol] = float(recent[0]['price'])
                except Exception:
                    pass
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
