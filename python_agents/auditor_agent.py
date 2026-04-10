import asyncio
import logging
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Any

from config import Config
from database import Database
from strategy import StrategyHelper

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


class AuditorAgent:
    def __init__(self, db: Database, brain=None, rca_engine=None):
        self.db = db
        self.brain = brain
        self.rca_engine = rca_engine
        self.running = False
        self.last_activity = None
        self.strategy_helper = StrategyHelper()
        self.audit_count = 0
        self.last_accuracy = 0.0

    async def initialize(self):
        logger.info("Initializing Auditor Agent...")

    async def start(self):
        self.running = True
        logger.info("Starting Auditor Agent...")

        while self.running:
            try:
                await self._analyze_failed_signals()
                await self._sync_brain_learning()
                await self._auto_rca_recent_failures()
                self.last_activity = datetime.utcnow()
                self.audit_count += 1
            except Exception as e:
                logger.error(f"Auditor cycle error: {e}")
            # Her 2 saatte bir (bootstrap'ta daha sık)
            interval = min(Config.get_agent_config('auditor')['review_interval_hours'] * 3600, 7200)
            await asyncio.sleep(interval)

    async def stop(self):
        self.running = False
        logger.info("Stopping Auditor Agent...")

    async def _sync_brain_learning(self):
        """Brain öğrenme verilerini senkronize et"""
        if not self.brain:
            return
        try:
            learning_stats = await self.db.get_learning_stats()
            brain_status = self.brain.get_brain_status()
            self.last_accuracy = brain_status['accuracy']

            logger.info(
                f"🧠 Brain Sync | Patterns: {brain_status['total_patterns']} | "
                f"Accuracy: {brain_status['accuracy']:.1%} | "
                f"DB Learning: {learning_stats['total']} records ({learning_stats['accuracy']:.1f}%)"
            )

            # Doğruluk çok düşükse Brain parametrelerini ayarla
            if learning_stats['total'] > 20 and learning_stats['accuracy'] < 40:
                logger.warning("⚠ Brain accuracy too low (<40%), adjusting thresholds...")
                # Similarity threshold'u yükselt (daha seçici ol)
                Config.SIMILARITY_THRESHOLD = min(Config.SIMILARITY_THRESHOLD + 0.05, 0.95)
                logger.info(f"Similarity threshold increased to {Config.SIMILARITY_THRESHOLD}")

        except Exception as e:
            logger.debug(f"Brain sync error: {e}")

    async def _analyze_failed_signals(self):
        """Analyze failed signals and update strategy thresholds."""
        try:
            auditor_config = Config.get_agent_config('auditor')
            min_samples = auditor_config.get('min_audit_samples', 100)

            closed_sims = await self.db.get_closed_simulations(limit=min_samples)
            if len(closed_sims) < 10:  # Daha az sample ile de çalışsın
                logger.debug(f"Not enough closed simulations ({len(closed_sims)}) for audit")
                return

            successful = [s for s in closed_sims if float(s.get('pnl', 0)) > 0]
            failed = [s for s in closed_sims if float(s.get('pnl', 0)) <= 0]

            success_rate = len(successful) / len(closed_sims) if closed_sims else 0.0
            avg_win = sum(float(s.get('pnl_pct', 0)) for s in successful) / len(successful) if successful else 0.0
            avg_loss = sum(float(s.get('pnl_pct', 0)) for s in failed) / len(failed) if failed else 0.0

            logger.info(f"📋 Audit #{self.audit_count}: {len(closed_sims)} sims | "
                         f"Win Rate: {success_rate:.1%} | Avg Win: {avg_win:.2f}% | Avg Loss: {avg_loss:.2f}%")

            if success_rate < auditor_config.get('false_positive_threshold', 0.7):
                await self._investigate_false_positives(failed)

            # LLM-powered failure analysis
            bridge = _get_llm_bridge()
            if bridge and len(closed_sims) >= 20:
                try:
                    # Build failure type summary
                    failure_types = {}
                    for f in failed:
                        meta = f.get('metadata', {})
                        if isinstance(meta, str):
                            try:
                                meta = json.loads(meta)
                            except (json.JSONDecodeError, TypeError):
                                meta = {}
                        st = meta.get('signal_type', 'unknown')
                        failure_types[st] = failure_types.get(st, 0) + 1

                    llm_analysis = await bridge.auditor_analyze_failures(
                        failure_summary={"total_failed": len(failed), "total_success": len(successful)},
                        win_rate=success_rate,
                        avg_win_pct=avg_win,
                        avg_loss_pct=avg_loss,
                        top_failure_types=failure_types,
                    )
                    if llm_analysis and llm_analysis.get("_parsed"):
                        correction = llm_analysis.get("correction", "")
                        if correction:
                            logger.info(f"🤖 LLM Auditor recommendation: {correction[:150]}")
                except Exception as e:
                    logger.debug(f"LLM auditor analysis skipped: {e}")

            await self.db.insert_audit_record({
                'timestamp': datetime.utcnow(),
                'total_simulations': len(closed_sims),
                'successful_simulations': len(successful),
                'failed_simulations': len(failed),
                'success_rate': success_rate,
                'avg_win_pct': avg_win,
                'avg_loss_pct': avg_loss,
                'metadata': {
                    'analysis_type': 'periodic_review',
                    'sample_count': len(closed_sims),
                    'audit_number': self.audit_count,
                    'brain_accuracy': self.last_accuracy,
                }
            })

        except Exception as e:
            logger.error(f"Error analyzing failed signals: {e}")

    async def _investigate_false_positives(self, failed_simulations: List[Dict[str, Any]]):
        """Investigate why signals are failing using RCA engine."""
        try:
            logger.info(f"Investigating {len(failed_simulations)} failed signals for false positives...")

            # RCA batch analysis
            if self.rca_engine:
                rca_report = await self.rca_engine.batch_analyze(failed_simulations)
                if rca_report['total_analyzed'] > 0:
                    logger.info(f"🔍 RCA Report: {rca_report['total_analyzed']} failures analyzed")
                    for ftype, stats in rca_report.get('categories', {}).items():
                        logger.info(f"  → {ftype}: {stats['count']} cases, avg loss: {stats.get('avg_loss', 0):.2f}%")
                    for rec in rca_report.get('top_recommendations', [])[:3]:
                        logger.info(f"  💡 {rec}")

                    # Save individual RCA results + write correction notes
                    for sim in failed_simulations[:20]:
                        result = await self.rca_engine.analyze_failure(sim)
                        await self.db.insert_rca_result({
                            'simulation_id': sim.get('id'),
                            'failure_type': result['failure_type'],
                            'confidence': result['confidence'],
                            'explanation': result['explanation'],
                            'recommendations': result['recommendations'],
                            'context': result.get('context', {}),
                        })

                        # Write correction note for strategist
                        await self._write_correction_note(sim, result)

            failures_by_type = {}
            for sim in failed_simulations:
                meta = sim.get('metadata', {})
                if isinstance(meta, str):
                    import json
                    meta = json.loads(meta)
                signal_type = meta.get('signal_type', 'unknown')
                if signal_type not in failures_by_type:
                    failures_by_type[signal_type] = []
                failures_by_type[signal_type].append(sim)

            for signal_type, failures in failures_by_type.items():
                avg_loss_pct = sum(float(s.get('pnl_pct', 0)) for s in failures) / len(failures)
                logger.warning(f"Signal type '{signal_type}': {len(failures)} failures, avg loss {avg_loss_pct:.2f}%")

                # Brain'e başarısız sinyal tipini bildir
                if self.brain:
                    for f in failures:
                        self.brain.update_learning(signal_type, False, avg_loss_pct)

                await self.db.insert_failure_analysis({
                    'timestamp': datetime.utcnow(),
                    'signal_type': signal_type,
                    'failure_count': len(failures),
                    'avg_loss_pct': avg_loss_pct,
                    'recommendation': f"Consider reducing threshold or adjusting parameters for {signal_type}",
                    'metadata': {
                        'sample_failures': [s['id'] for s in failures[:5]]
                    }
                })

        except Exception as e:
            logger.error(f"Error investigating false positives: {e}")

    async def _write_correction_note(self, simulation: Dict[str, Any],
                                       rca_result: Dict[str, Any]):
        """Write a correction note based on RCA analysis for Strategist to apply."""
        try:
            failure_type = rca_result['failure_type']
            confidence = rca_result.get('confidence', 0)
            meta = simulation.get('metadata', {})
            if isinstance(meta, str):
                import json
                meta = json.loads(meta)
            signal_type = meta.get('signal_type', 'unknown')

            # Only write corrections with decent confidence
            if confidence < 0.4:
                return

            corrections = []

            if failure_type == 'FALSE_BREAKOUT':
                corrections.append({
                    'adjustment_key': 'similarity_threshold',
                    'adjustment_value': 0.05,
                    'reason': f'FALSE_BREAKOUT: increase similarity threshold to be more selective',
                })
            elif failure_type == 'STOP_HUNT':
                corrections.append({
                    'adjustment_key': 'stop_loss_pct',
                    'adjustment_value': 0.005,
                    'reason': f'STOP_HUNT: widen stop loss to avoid wick traps',
                })
            elif failure_type == 'OVEREXTENDED':
                corrections.append({
                    'adjustment_key': 'take_profit_pct',
                    'adjustment_value': -0.005,
                    'reason': f'OVEREXTENDED: tighten take profit for earlier exit',
                })
            elif failure_type == 'LOW_VOLUME_NOISE':
                corrections.append({
                    'adjustment_key': 'price_movement_threshold',
                    'adjustment_value': 0.005,
                    'reason': f'LOW_VOLUME_NOISE: raise movement threshold to filter noise',
                })
            elif failure_type in ('BAD_TIMING', 'TREND_REVERSAL'):
                corrections.append({
                    'adjustment_key': 'min_confidence',
                    'adjustment_value': 0.05,
                    'reason': f'{failure_type}: raise min confidence for {signal_type}',
                })

            for corr in corrections:
                await self.db.insert_correction_note({
                    'signal_type': signal_type,
                    'failure_type': failure_type,
                    'adjustment_key': corr['adjustment_key'],
                    'adjustment_value': corr['adjustment_value'],
                    'reason': corr['reason'],
                    'simulation_id': simulation.get('id'),
                })
                logger.info(f"📝 Correction note: {corr['adjustment_key']} "
                             f"{corr['adjustment_value']:+.3f} for {signal_type} "
                             f"({failure_type})")

        except Exception as e:
            logger.error(f"Error writing correction note: {e}")

    async def perform_root_cause_analysis(self, failed_trade_id: int) -> Dict[str, Any]:
        """
        Full RCA for a specific failed simulation.
        1. Look up the failed trade
        2. Compare predicted vs actual volatility
        3. Write Correction Note for Strategist
        """
        try:
            # Fetch the simulation
            sims = await self.db.get_closed_simulations(limit=500)
            simulation = None
            for s in sims:
                if s.get('id') == failed_trade_id:
                    simulation = s
                    break

            if not simulation:
                return {'error': f'Simulation {failed_trade_id} not found'}

            pnl = float(simulation.get('pnl', 0))
            if pnl >= 0:
                return {'error': f'Simulation {failed_trade_id} is not a failure (PnL={pnl:.2f})'}

            # Run RCA analysis
            result = await self.rca_engine.analyze_failure(simulation)

            # Get the signal's predicted volatility from metadata
            meta = simulation.get('metadata', {})
            if isinstance(meta, str):
                import json
                meta = json.loads(meta)

            predicted_atr = meta.get('atr_ratio', 0)

            # Actual volatility: get trades during simulation
            entry_time = simulation.get('entry_time')
            exit_time = simulation.get('exit_time')
            symbol = simulation.get('symbol', '')
            actual_atr = 0.0

            if entry_time and exit_time:
                sim_trades = await self.db.get_trades_in_range(
                    symbol, entry_time, exit_time)
                if len(sim_trades) >= 10:
                    sim_prices = [float(t['price']) for t in sim_trades]
                    price_range = max(sim_prices) - min(sim_prices)
                    avg_price = sum(sim_prices) / len(sim_prices)
                    actual_atr = price_range / max(avg_price, 1e-8)

            result['volatility_comparison'] = {
                'predicted_atr_ratio': predicted_atr,
                'actual_atr_ratio': actual_atr,
                'deviation': abs(actual_atr - predicted_atr),
                'under_estimated': actual_atr > predicted_atr,
            }

            # Save RCA result
            await self.db.insert_rca_result({
                'simulation_id': failed_trade_id,
                'failure_type': result['failure_type'],
                'confidence': result['confidence'],
                'explanation': result['explanation'],
                'recommendations': result['recommendations'],
                'context': {
                    **result.get('context', {}),
                    'volatility_comparison': result['volatility_comparison'],
                },
            })

            # Write correction note
            await self._write_correction_note(simulation, result)

            logger.info(f"🔍 Full RCA for sim #{failed_trade_id}: {result['failure_type']} "
                         f"(conf={result['confidence']:.2f}) "
                         f"predicted_vol={predicted_atr:.4f} actual_vol={actual_atr:.4f}")

            return result

        except Exception as e:
            logger.error(f"Error in perform_root_cause_analysis: {e}")
            return {'error': str(e)}

    async def _auto_rca_recent_failures(self):
        """Automatically run RCA on recent failed simulations that haven't been analyzed."""
        if not self.rca_engine:
            return
        try:
            closed = await self.db.get_closed_simulations(limit=50)
            failed = [s for s in closed if float(s.get('pnl', 0)) < 0]

            # Check which ones already have RCA results
            for sim in failed[:10]:
                sim_id = sim.get('id')
                if sim_id is None:
                    continue
                # Run full RCA (it will write correction notes automatically)
                await self.perform_root_cause_analysis(sim_id)

        except Exception as e:
            logger.error(f"Auto RCA error: {e}")

    async def health_check(self) -> Dict[str, Any]:
        try:
            recent_audits = await self.db.get_recent_audits(limit=1)
            latest_audit = recent_audits[0] if recent_audits else None
            # Sanitize datetime/Decimal fields for JSON serialization
            if latest_audit:
                for k, v in list(latest_audit.items()):
                    if hasattr(v, 'isoformat'):
                        latest_audit[k] = v.isoformat()
                    elif isinstance(v, Decimal):
                        latest_audit[k] = float(v)

            return {
                "healthy": True,
                "last_activity": self.last_activity.isoformat() if self.last_activity else None,
                "latest_audit": latest_audit,
                "audit_count": self.audit_count,
                "last_accuracy": self.last_accuracy,
                "brain_connected": self.brain is not None,
            }
        except Exception as e:
            return {"healthy": False, "error": str(e)}
