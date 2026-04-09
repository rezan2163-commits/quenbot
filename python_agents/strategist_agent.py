import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

import numpy as np

from config import Config
from database import Database
from indicators import compute_all_indicators
from strategy import (
    normalize_prices,
    evolutionary_algorithm,
    strategy as evaluate_strategy,
    build_movement_vector,
    compare_similarity
)

logger = logging.getLogger(__name__)

# Çoklu zaman dilimi analizi pencereleri (dakika)
TIMEFRAME_WINDOWS = {
    '15m': 15,
    '1h': 60,
    '4h': 240,
    '1d': 1440,
}


class StrategistAgent:
    def __init__(self, db: Database, brain=None, state_tracker=None, risk_manager=None):
        self.db = db
        self.brain = brain
        self.state_tracker = state_tracker
        self.risk_manager = risk_manager
        self.running = False
        self.last_activity = None
        self.feature_weights = Config.get_agent_config('strategist')['feature_weights']
        self.analysis_count = 0
        self.signals_generated = 0

    async def initialize(self):
        logger.info("Initializing Strategist Agent...")

    async def start(self):
        self.running = True
        logger.info("Starting Strategist Agent...")

        try:
            while self.running:
                await self._analyze_strategies()
                await self._multi_timeframe_analysis()
                await self._update_pattern_outcomes()
                await asyncio.sleep(60)  # Her 1 dakikada bir

        except Exception as e:
            logger.error(f"Strategist agent error: {e}")
            raise
        finally:
            await self.stop()

    async def stop(self):
        self.running = False
        logger.info("Stopping Strategist Agent...")

    async def _get_watchlist(self) -> List[str]:
        """Aktif izleme listesini döndür"""
        try:
            user_wl = await self.db.get_user_watchlist()
            if user_wl:
                return list(set(w['symbol'] for w in user_wl))
        except:
            pass
        return Config.WATCHLIST

    async def _multi_timeframe_analysis(self):
        """Çoklu zaman dilimi analizi - 15m, 1h, 4h, 1d"""
        if not self.brain:
            return
        try:
            watchlist = await self._get_watchlist()
            for symbol in watchlist:
                for market_type in Config.MARKET_TYPES:
                    for tf_key, tf_minutes in TIMEFRAME_WINDOWS.items():
                        try:
                            trades = await self.db.get_trades_for_snapshot(
                                symbol, minutes=tf_minutes, market_type=market_type)
                            if len(trades) < 10:
                                continue

                            snapshot = self.brain.build_snapshot_from_trades(
                                trades, symbol, 'mixed', market_type)
                            if not snapshot:
                                continue

                            # Mode-aware similarity threshold
                            if self.risk_manager:
                                _mode_p = self.risk_manager.get_mode_params()
                                brain_sim_threshold = _mode_p['similarity_threshold']
                            else:
                                brain_sim_threshold = Config.SIMILARITY_THRESHOLD
                            matches = self.brain.find_matching_patterns(
                                snapshot, min_similarity=max(brain_sim_threshold, 0.15))

                            if matches:
                                prediction = self.brain.predict_direction(matches)
                                confidence = prediction['confidence']
                                direction = prediction['direction']

                                # Mode-aware confidence threshold
                                min_conf = 0.3 if (self.state_tracker and 
                                    self.state_tracker.get_mode() in ('BOOTSTRAP', 'LEARNING')) else 0.6
                                if (confidence >= min_conf and
                                        self.brain.should_generate_signal(symbol, direction, confidence)):

                                    # En iyi timeframe'i seç
                                    best_tf = None
                                    best_strength = 0
                                    for tf, tf_data in prediction['timeframes'].items():
                                        if tf_data['strength'] > best_strength:
                                            best_strength = tf_data['strength']
                                            best_tf = tf

                                    if best_tf and abs(prediction['timeframes'].get(best_tf, {}).get('avg_change_pct', 0)) >= 0.02:
                                        last_price = float(trades[-1]['price'])
                                        signal_type = f'brain_{direction}_{best_tf}'
                                        signal_payload = {
                                            'market_type': market_type,
                                            'symbol': symbol,
                                            'signal_type': signal_type,
                                            'confidence': float(min(confidence, 1.0)),
                                            'price': last_price,
                                            'timestamp': datetime.utcnow(),
                                            'metadata': {
                                                'position_bias': direction,
                                                'timeframe': best_tf,
                                                'match_count': prediction['match_count'],
                                                'avg_similarity': prediction['avg_similarity'],
                                                'timeframe_predictions': prediction['timeframes'],
                                                'brain_analysis': True,
                                                'market_type': market_type,
                                            }
                                        }
                                        await self.db.insert_signal(signal_payload)
                                        self.signals_generated += 1
                                        logger.info(
                                            f"🧠 Brain signal [{market_type}] {symbol} {direction} "
                                            f"tf={best_tf} conf={confidence:.2f} matches={prediction['match_count']}")

                                # Pattern'ı kaydet
                                await self.db.insert_pattern_record({
                                    'symbol': symbol,
                                    'exchange': 'mixed',
                                    'market_type': market_type,
                                    'snapshot_data': snapshot.to_dict(),
                                })

                        except Exception as e:
                            logger.debug(f"Multi-TF error {symbol} {tf_key}: {e}")
                            continue

            self.analysis_count += 1

        except Exception as e:
            logger.error(f"Multi-timeframe analysis error: {e}")

    async def _update_pattern_outcomes(self):
        """Geçmiş pattern'ların gerçek sonuçlarını güncelle (öğrenme)"""
        if not self.brain:
            return
        try:
            patterns = await self.db.get_pattern_records(limit=50)
            for p in patterns:
                snap = p.get('snapshot_data', {})
                if not snap.get('end_time'):
                    continue
                end_time = datetime.fromisoformat(snap['end_time']) if isinstance(snap['end_time'], str) else snap['end_time']
                symbol = snap.get('symbol', p.get('symbol'))
                if not symbol:
                    continue

                for tf_key, tf_minutes in TIMEFRAME_WINDOWS.items():
                    col = f'outcome_{tf_key}'
                    if p.get(col) is not None:
                        continue  # Zaten hesaplanmış

                    target_time = end_time + timedelta(minutes=tf_minutes)
                    if target_time > datetime.utcnow():
                        continue  # Henüz zaman gelmedi

                    end_price = float(snap.get('price_end', 0))
                    if end_price <= 0:
                        continue

                    actual_price = await self.db.get_price_at_time(symbol, target_time)
                    if actual_price:
                        change_pct = (actual_price - end_price) / end_price
                        await self.db.update_pattern_outcome(p['id'], tf_key, change_pct)
                        logger.debug(f"Updated outcome {symbol} {tf_key}: {change_pct:.4f}")

        except Exception as e:
            logger.debug(f"Pattern outcome update error: {e}")

    async def _analyze_strategies(self):
        try:
            watchlist = await self._get_watchlist()

            # Mode-aware thresholds
            if self.risk_manager:
                mode_params = self.risk_manager.get_mode_params()
                sim_threshold = mode_params['similarity_threshold']
                min_profit = mode_params['min_mean_profit']
            else:
                sim_threshold = Config.SIMILARITY_THRESHOLD
                min_profit = Config.STRATEGY_MIN_MEAN_PROFIT

            for market_type in Config.MARKET_TYPES:
                for symbol in watchlist:
                    try:
                        trades = await self.db.get_recent_trades(symbol, limit=250, market_type=market_type)
                        if len(trades) < 10:
                            continue

                        prices = np.array([float(row['price']) for row in reversed(trades)], dtype=np.float64)
                        if prices.size == 0:
                            continue
                        
                        normalized = normalize_prices(prices)
                        if normalized is None or normalized.size == 0:
                            continue
                            
                        movement_vector = build_movement_vector(prices)
                        if movement_vector is None or movement_vector.size == 0:
                            continue

                        historical_movements = await self.db.get_recent_movements(symbol, hours=72, market_type=market_type)
                        historical_vectors = []
                        for movement in historical_movements:
                            t10_data = movement.get('t10_data') or {}
                            profile = t10_data.get('price_profile')
                            if isinstance(profile, list) and len(profile) > 2:
                                try:
                                    vec = np.array(profile, dtype=np.float64)
                                    if vec.size > 0:
                                        historical_vectors.append(vec)
                                except:
                                    continue

                        similarities = compare_similarity(movement_vector, historical_vectors) if movement_vector.size > 0 else []
                        best_similarity = max(similarities) if similarities else 0.0

                        if normalized.size < 10:
                            continue
                            
                        result = evolutionary_algorithm(
                            normalized,
                            population_size=Config.STRATEGY_POPULATION_SIZE,
                            generations=Config.STRATEGY_GENERATIONS
                        )

                        mean_profit, risk, score = evaluate_strategy(normalized, result['params'])
                        last_price = float(prices[-1])
                        first_price = float(prices[0])
                        direction = 'long' if last_price > first_price else 'short'

                        # Technical indicators (her durumda hesapla)
                        ind = compute_all_indicators(prices)
                        trend_summary = ind.get('trend_summary', {})
                        atr_ratio = ind.get('atr_ratio', 0.02)

                        # Debug log for analysis
                        mode = self.state_tracker.get_mode() if self.state_tracker else 'PRODUCTION'
                        logger.info(f"\U0001f50d Analysis [{market_type}] {symbol}: trades={len(trades)} "
                                     f"score={score:.4f} profit={mean_profit:.4f} sim={best_similarity:.2f} mode={mode}")

                        if best_similarity >= sim_threshold and score > 0 and mean_profit > min_profit:
                            signal_type = f'evolutionary_similarity_{direction}'
                            # Confidence: en az 0.3, similarity varsa onu kullan, yoksa score-based
                            evo_conf = max(float(best_similarity), min(float(score) * 0.1, 0.85), 0.3)
                            signal_payload = {
                                'market_type': market_type,
                                'symbol': symbol,
                                'signal_type': signal_type,
                                'confidence': float(min(evo_conf, 1.0)),
                                'price': last_price,
                                'timestamp': datetime.utcnow(),
                                'metadata': {
                                    'position_bias': direction,
                                    'similarity_score': float(best_similarity),
                                    'strategy_score': float(score),
                                    'mean_profit': float(mean_profit),
                                    'risk': float(risk),
                                    'upper_threshold': float(result['params'][0]),
                                    'lower_threshold': float(result['params'][1]),
                                    'history_count': len(historical_vectors),
                                    'sample_count': len(prices),
                                    'market_type': market_type,
                                    'rsi': float(ind['rsi']) if ind.get('rsi') is not None else None,
                                    'macd': float(ind['macd']['histogram']) if ind.get('macd') else None,
                                    'trend': trend_summary.get('trend'),
                                    'atr_ratio': float(atr_ratio) if atr_ratio is not None else 0.02,
                                }
                            }
                            await self.db.insert_signal(signal_payload)
                            self.signals_generated += 1
                            logger.info(
                                f"Generated signal [{market_type}] {symbol} dir={direction} sim={best_similarity:.2f} score={score:.4f}"
                            )

                        # Momentum-based signal: evolutionary algo sonuç buldu
                        elif score > 0.05 and mean_profit > min_profit * 0.5:
                            momentum_conf = min(max(score * 0.6, 0.3), 0.85)
                            signal_type = f'momentum_{direction}'
                            signal_payload = {
                                'market_type': market_type,
                                'symbol': symbol,
                                'signal_type': signal_type,
                                'confidence': float(momentum_conf),
                                'price': last_price,
                                'timestamp': datetime.utcnow(),
                                'metadata': {
                                    'position_bias': direction,
                                    'strategy_score': float(score),
                                    'mean_profit': float(mean_profit),
                                    'risk': float(risk),
                                    'sample_count': len(prices),
                                    'market_type': market_type,
                                    'bootstrap': True
                                }
                            }
                            await self.db.insert_signal(signal_payload)
                            self.signals_generated += 1
                            logger.info(
                                f"📈 Momentum signal [{market_type}] {symbol} dir={direction} score={score:.4f} profit={mean_profit:.4f}"
                            )

                        # BOOTSTRAP/LEARNING: Basit fiyat hareketi sinyali
                        elif self.state_tracker and self.state_tracker.get_mode() in ('BOOTSTRAP', 'LEARNING'):
                            price_change_pct = (last_price - first_price) / first_price
                            trend_dir = trend_summary.get('trend', 'neutral')
                            trend_strength = trend_summary.get('strength', 0)

                            # RSI + trend alignment → sinyal
                            rsi_val = ind.get('rsi')
                            if (abs(price_change_pct) > 0.001 and trend_strength > 0.15 and
                                    rsi_val is not None and 20 < rsi_val < 80):
                                pa_direction = 'long' if price_change_pct > 0 and trend_dir == 'bullish' else (
                                    'short' if price_change_pct < 0 and trend_dir == 'bearish' else None
                                )
                                if pa_direction:
                                    pa_conf = min(abs(price_change_pct) * 20 + trend_strength * 0.3, 0.7)
                                    signal_payload = {
                                        'market_type': market_type,
                                        'symbol': symbol,
                                        'signal_type': f'price_action_{pa_direction}',
                                        'confidence': float(pa_conf),
                                        'price': last_price,
                                        'timestamp': datetime.utcnow(),
                                        'metadata': {
                                            'position_bias': pa_direction,
                                            'price_change_pct': float(price_change_pct),
                                            'rsi': float(rsi_val),
                                            'trend': trend_dir,
                                            'trend_strength': float(trend_strength),
                                            'atr_ratio': atr_ratio,
                                            'sample_count': len(prices),
                                            'market_type': market_type,
                                            'bootstrap': True,
                                        }
                                    }
                                    await self.db.insert_signal(signal_payload)
                                    self.signals_generated += 1
                                    logger.info(
                                        f"🔥 Price action [{market_type}] {symbol} {pa_direction} "
                                        f"chg={price_change_pct*100:.2f}% rsi={rsi_val:.0f} trend={trend_dir}"
                                    )

                    except Exception as e:
                        logger.error(f"Error processing {symbol} ({market_type}): {e}")
                        continue

            self.last_activity = datetime.utcnow()

        except Exception as e:
            logger.error(f"Error in strategy analysis: {e}")

    async def health_check(self) -> Dict[str, Any]:
        try:
            recent_movements = await self.db.get_recent_movements(Config.WATCHLIST[0], hours=1, market_type='spot')
            return {
                "healthy": True,
                "last_activity": self.last_activity.isoformat() if self.last_activity else None,
                "analysis_count": self.analysis_count,
                "signals_generated": self.signals_generated,
                "brain_connected": self.brain is not None,
                "recent_movements_analyzed": len(recent_movements),
                "similarity_threshold": Config.SIMILARITY_THRESHOLD
            }
        except Exception as e:
            return {"healthy": False, "error": str(e)}
