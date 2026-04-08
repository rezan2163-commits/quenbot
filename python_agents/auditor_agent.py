import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any

from config import Config
from database import Database
from strategy import StrategyHelper

logger = logging.getLogger(__name__)


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

        try:
            while self.running:
                await self._analyze_failed_signals()
                await self._sync_brain_learning()
                self.last_activity = datetime.utcnow()
                self.audit_count += 1
                # Her 4 saatte bir (başlangıçta daha sık çalışır)
                interval = min(Config.get_agent_config('auditor')['review_interval_hours'] * 3600, 14400)
                await asyncio.sleep(interval)

        except Exception as e:
            logger.error(f"Auditor agent error: {e}")
            raise
        finally:
            await self.stop()

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

                    # Save individual RCA results
                    for sim in failed_simulations[:20]:  # Limit to avoid overload
                        result = await self.rca_engine.analyze_failure(sim)
                        await self.db.insert_rca_result({
                            'simulation_id': sim.get('id'),
                            'failure_type': result['failure_type'],
                            'confidence': result['confidence'],
                            'explanation': result['explanation'],
                            'recommendations': result['recommendations'],
                            'context': result.get('context', {}),
                        })

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

    async def health_check(self) -> Dict[str, Any]:
        try:
            recent_audits = await self.db.get_recent_audits(limit=1)
            latest_audit = recent_audits[0] if recent_audits else None

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
