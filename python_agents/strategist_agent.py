import asyncio
import ctypes
import gc
import json
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity as sk_cosine

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
from intelligence_core import FeatureEngine, MarketRegimeDetector

# glibc malloc_trim — bellegi OS'a geri ver
try:
    _libc = ctypes.CDLL("libc.so.6")
    def _malloc_trim():
        _libc.malloc_trim(0)
except Exception:
    def _malloc_trim():
        pass

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

# Çoklu zaman dilimi analizi pencereleri (dakika) — bellek-dostu set
TIMEFRAME_WINDOWS = {
    '15m': 15,
    '1h': 60,
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
        self._signal_cooldown_seconds = int(os.getenv("QUENBOT_SIGNAL_COOLDOWN_SECONDS", "300"))
        self._last_signal_emit: Dict[str, float] = {}

    async def initialize(self):
        logger.info("Initializing Strategist Agent...")

    async def start(self):
        self.running = True
        logger.info("Starting Strategist Agent...")

        while self.running:
            try:
                await self._apply_correction_notes()
                await self._analyze_strategies()
                await self._indicator_based_signals()
                await self._signature_matching()
                await self._multi_timeframe_analysis()
                await self._update_pattern_outcomes()
            except Exception as e:
                logger.error(f"Strategist cycle error: {e}")
            await asyncio.sleep(180)  # Dakika bazlı dalgalanmayı azaltmak için 3 dk döngü

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

    def _can_emit_signal(self, symbol: str, market_type: str, direction: str) -> bool:
        key = f"{symbol}:{market_type}:{direction}"
        now = time.time()
        last = self._last_signal_emit.get(key, 0.0)
        if now - last < self._signal_cooldown_seconds:
            return False
        self._last_signal_emit[key] = now
        return True

    def _build_signal_payload(
        self,
        *,
        market_type: str,
        symbol: str,
        signal_type: str,
        direction: str,
        confidence: float,
        entry_price: float,
        target_pct: float,
        eta_minutes: int,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        safe_target_pct = abs(float(target_pct))
        safe_target_pct = max(safe_target_pct, 0.02)
        safe_target_pct = min(safe_target_pct, float(Config.STRATEGY_MAX_TARGET_PCT))
        if direction == 'short':
            # Prevent impossible negative target prices on short signals.
            safe_target_pct = min(safe_target_pct, 0.95)
        safe_entry = float(entry_price)
        target_price = safe_entry * (1.0 + safe_target_pct) if direction == 'long' else safe_entry * (1.0 - safe_target_pct)
        ts = datetime.utcnow()
        base_meta = {
            'position_bias': direction,
            'market_type': market_type,
            'target_pct': safe_target_pct,
            'signal_time': ts.isoformat() + 'Z',
            'entry_price': safe_entry,
            'current_price_at_signal': safe_entry,
            'target_price': float(target_price),
            'estimated_duration_to_target_minutes': int(max(1, eta_minutes)),
        }
        if metadata:
            base_meta.update(metadata)

        return {
            'market_type': market_type,
            'symbol': symbol,
            'signal_type': signal_type,
            'confidence': float(min(max(confidence, 0.0), 1.0)),
            'price': safe_entry,
            'timestamp': ts,
            'metadata': base_meta,
        }

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
                                _cur_mode = self.state_tracker.get_mode() if self.state_tracker else 'PRODUCTION'
                                min_conf = 0.3 if _cur_mode in ('BOOTSTRAP', 'LEARNING') else 0.6
                                if (confidence >= min_conf and
                                        self.brain.should_generate_signal(symbol, direction, confidence,
                                                                          mode=_cur_mode)):

                                    # En iyi timeframe'i seç
                                    best_tf = None
                                    best_strength = 0
                                    for tf, tf_data in prediction['timeframes'].items():
                                        if tf_data['strength'] > best_strength:
                                            best_strength = tf_data['strength']
                                            best_tf = tf

                                    if best_tf and abs(prediction['timeframes'].get(best_tf, {}).get('avg_change_pct', 0)) >= 0.01:
                                        last_price = float(trades[-1]['price'])
                                        best_tf_change = abs(prediction['timeframes'][best_tf].get('avg_change_pct', 0.02))
                                        target_pct = max(best_tf_change, 0.02)
                                        signal_type = f'brain_{direction}_{best_tf}'
                                        if self._can_emit_signal(symbol, market_type, direction):
                                            signal_payload = self._build_signal_payload(
                                                market_type=market_type,
                                                symbol=symbol,
                                                signal_type=signal_type,
                                                direction=direction,
                                                confidence=confidence,
                                                entry_price=last_price,
                                                target_pct=target_pct,
                                                eta_minutes=15 if best_tf == '15m' else 60,
                                                metadata={
                                                    'timeframe': best_tf,
                                                    'match_count': prediction['match_count'],
                                                    'avg_similarity': prediction['avg_similarity'],
                                                    'timeframe_predictions': prediction['timeframes'],
                                                    'brain_analysis': True,
                                                },
                                            )
                                            await self.db.insert_signal(signal_payload)
                                            self.signals_generated += 1
                                            logger.info(
                                                f"🧠 Brain signal [{market_type}] {symbol} {direction} "
                                                f"tf={best_tf} conf={confidence:.2f} matches={prediction['match_count']}")

                                        # LLM-enhanced signal evaluation
                                        bridge = _get_llm_bridge()
                                        if bridge:
                                            try:
                                                llm_eval = await bridge.strategist_evaluate_signal(
                                                    symbol=symbol,
                                                    signal_type=signal_type,
                                                    direction=direction,
                                                    confidence=confidence,
                                                    indicators={},
                                                    regime="UNKNOWN",
                                                    pattern_matches=prediction['match_count'],
                                                    recent_performance=self.brain.signal_type_scores.get(signal_type, {}),
                                                )
                                                if llm_eval and llm_eval.get("_parsed"):
                                                    llm_risk = llm_eval.get("risk_score", 0.5)
                                                    logger.info(
                                                        f"🤖 LLM Strategist [{symbol}]: risk={llm_risk:.2f} "
                                                        f"entry_reason={llm_eval.get('entry_reason', 'N/A')[:50]}"
                                                    )
                                            except Exception as e:
                                                logger.debug(f"LLM signal eval skipped: {e}")

                            # Pattern'ı kaydet (matches olsun olmasın — library'yi doldur)
                            await self.db.insert_pattern_record({
                                'symbol': symbol,
                                'exchange': 'mixed',
                                'market_type': market_type,
                                'snapshot_data': snapshot.to_dict(),
                            })

                            # ── Intelligence Core analizi (mevcut brain'den BAĞIMSIZ) ──
                            if self.brain and self.brain.intelligence_core:
                                try:
                                    prices_arr = np.array([float(t['price']) for t in reversed(trades)], dtype=np.float64)
                                    volumes_arr = np.array([float(t['quantity']) * float(t['price']) for t in reversed(trades)], dtype=np.float64)
                                    ind = compute_all_indicators(prices_arr, volumes=volumes_arr)
                                    if not isinstance(ind, dict):
                                        ind = {}

                                    intel_result = self.brain.enhanced_analyze(snapshot, ind)
                                    if intel_result and intel_result.get('direction') and intel_result.get('confidence', 0) > 0:
                                        intel_conf = intel_result['confidence']
                                        intel_dir = intel_result['direction']
                                        regime = intel_result.get('regime', {})
                                        regime_name = regime.get('regime', 'UNKNOWN') if isinstance(regime, dict) else 'UNKNOWN'

                                        # Mode-aware confidence threshold
                                        min_intel_conf = 0.35 if (self.state_tracker and
                                            self.state_tracker.get_mode() in ('BOOTSTRAP', 'LEARNING')) else 0.55

                                        if intel_conf >= min_intel_conf:
                                            # En iyi timeframe seç
                                            intel_tfs = intel_result.get('timeframes', {})
                                            best_intel_tf = None
                                            best_intel_strength = 0
                                            for itf, itf_data in intel_tfs.items():
                                                if itf_data.get('strength', 0) > best_intel_strength:
                                                    best_intel_strength = itf_data['strength']
                                                    best_intel_tf = itf

                                            if best_intel_tf:
                                                itf_change = abs(intel_tfs[best_intel_tf].get('avg_change_pct', 0))
                                                if itf_change >= 0.005:  # Minimum %0.5 beklenen hareket
                                                    last_price = float(trades[-1]['price'])
                                                    signal_type = f'intel_{intel_dir}_{best_intel_tf}'
                                                    if self._can_emit_signal(symbol, market_type, intel_dir):
                                                        signal_payload = self._build_signal_payload(
                                                            market_type=market_type,
                                                            symbol=symbol,
                                                            signal_type=signal_type,
                                                            direction=intel_dir,
                                                            confidence=float(min(intel_conf, 0.95)),
                                                            entry_price=last_price,
                                                            target_pct=max(itf_change, 0.02),
                                                            eta_minutes=15 if best_intel_tf == '15m' else 60,
                                                            metadata={
                                                                'timeframe': best_intel_tf,
                                                                'match_count': intel_result.get('match_count', 0),
                                                                'avg_similarity': intel_result.get('avg_similarity', 0),
                                                                'top3_similarity': intel_result.get('top3_similarity', 0),
                                                                'regime': regime_name,
                                                                'regime_multiplier': intel_result.get('regime_multiplier', 1.0),
                                                                'tf_agreement': intel_result.get('tf_agreement', 0),
                                                                'avg_consistency': intel_result.get('avg_consistency', 0),
                                                                'enriched_dim': intel_result.get('enriched_dim', 18),
                                                                'intelligence_version': intel_result.get('intelligence_version', '1.0'),
                                                            },
                                                        )
                                                        await self.db.insert_signal(signal_payload)
                                                        self.signals_generated += 1
                                                        logger.info(
                                                            f"🧬 Intel signal [{market_type}] {symbol} {intel_dir} "
                                                            f"tf={best_intel_tf} conf={intel_conf:.2f} "
                                                            f"regime={regime_name} matches={intel_result.get('match_count', 0)}")

                                    # Pattern'ı enriched library'ye kaydet (her durumda)
                                    self.brain.record_enriched_pattern(snapshot, ind)

                                except Exception as e:
                                    logger.debug(f"Intel analysis error {symbol}: {e}")
                            # Bellek temizliği: iterasyon sonrası
                            del trades, snapshot
                            try:
                                del prices_arr, volumes_arr, ind, intel_result
                            except NameError:
                                pass

                        except Exception as e:
                            logger.debug(f"Multi-TF error {symbol} {tf_key}: {e}")
                            continue

            self.analysis_count += 1
            gc.collect()
            _malloc_trim()  # glibc bellegi OS'a geri ver

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
                if isinstance(snap, str):
                    try:
                        snap = json.loads(snap)
                    except (json.JSONDecodeError, TypeError):
                        continue
                if not isinstance(snap, dict) or not snap.get('end_time'):
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
                        trades = await self.db.get_recent_trades(symbol, limit=100, market_type=market_type)
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

                        historical_profiles = await self.db.get_movement_profiles(symbol, hours=24, market_type=market_type, limit=50)
                        historical_vectors = []
                        for profile in historical_profiles:
                            try:
                                vec = np.array(profile, dtype=np.float64)
                                if vec.size > 0:
                                    historical_vectors.append(vec)
                            except Exception:
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
                        if not isinstance(ind, dict):
                            ind = {}
                        trend_summary = ind.get('trend_summary', {})
                        if not isinstance(trend_summary, dict):
                            trend_summary = {}
                        atr_ratio = ind.get('atr_ratio', 0.02)

                        # Debug log for analysis
                        mode = self.state_tracker.get_mode() if self.state_tracker else 'PRODUCTION'
                        logger.info(f"\U0001f50d Analysis [{market_type}] {symbol}: trades={len(trades)} "
                                     f"score={score:.4f} profit={mean_profit:.4f} sim={best_similarity:.2f} mode={mode}")

                        if best_similarity >= sim_threshold and score > 0 and mean_profit > min_profit:
                            signal_type = f'evolutionary_similarity_{direction}'
                            # Confidence: en az 0.3, similarity varsa onu kullan, yoksa score-based
                            evo_conf = max(float(best_similarity), min(float(score) * 0.1, 0.85), 0.3)
                            target_pct = max(float(mean_profit), 0.02)  # ≥2% target
                            if self._can_emit_signal(symbol, market_type, direction):
                                signal_payload = self._build_signal_payload(
                                    market_type=market_type,
                                    symbol=symbol,
                                    signal_type=signal_type,
                                    direction=direction,
                                    confidence=float(min(evo_conf, 1.0)),
                                    entry_price=last_price,
                                    target_pct=target_pct,
                                    eta_minutes=60,
                                    metadata={
                                        'similarity_score': float(best_similarity),
                                        'strategy_score': float(score),
                                        'mean_profit': float(mean_profit),
                                        'risk': float(risk),
                                        'upper_threshold': float(result['params'][0]),
                                        'lower_threshold': float(result['params'][1]),
                                        'history_count': len(historical_vectors),
                                        'sample_count': len(prices),
                                        'rsi': float(ind['rsi']) if ind.get('rsi') is not None else None,
                                        'macd': float(ind['macd']['histogram']) if ind.get('macd') else None,
                                        'trend': trend_summary.get('trend'),
                                        'atr_ratio': float(atr_ratio) if atr_ratio is not None else 0.02,
                                    },
                                )
                                await self.db.insert_signal(signal_payload)
                                self.signals_generated += 1
                                logger.info(
                                    f"Generated signal [{market_type}] {symbol} dir={direction} sim={best_similarity:.2f} score={score:.4f}"
                                )

                        # Momentum-based signal: evolutionary algo sonuç buldu
                        elif score > 0.05 and mean_profit > min_profit * 0.5:
                            momentum_conf = min(max(score * 0.6, 0.3), 0.85)
                            signal_type = f'momentum_{direction}'
                            target_pct = max(float(mean_profit), 0.02)  # ≥2% target
                            if self._can_emit_signal(symbol, market_type, direction):
                                signal_payload = self._build_signal_payload(
                                    market_type=market_type,
                                    symbol=symbol,
                                    signal_type=signal_type,
                                    direction=direction,
                                    confidence=float(momentum_conf),
                                    entry_price=last_price,
                                    target_pct=target_pct,
                                    eta_minutes=45,
                                    metadata={
                                        'strategy_score': float(score),
                                        'mean_profit': float(mean_profit),
                                        'risk': float(risk),
                                        'sample_count': len(prices),
                                        'bootstrap': True,
                                    },
                                )
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
                                    target_pct = max(abs(price_change_pct) * 2, 0.02)  # ≥2% target
                                    if self._can_emit_signal(symbol, market_type, pa_direction):
                                        signal_payload = self._build_signal_payload(
                                            market_type=market_type,
                                            symbol=symbol,
                                            signal_type=f'price_action_{pa_direction}',
                                            direction=pa_direction,
                                            confidence=float(pa_conf),
                                            entry_price=last_price,
                                            target_pct=target_pct,
                                            eta_minutes=30,
                                            metadata={
                                                'price_change_pct': float(price_change_pct),
                                                'rsi': float(rsi_val),
                                                'trend': trend_dir,
                                                'trend_strength': float(trend_strength),
                                                'atr_ratio': atr_ratio,
                                                'sample_count': len(prices),
                                                'bootstrap': True,
                                            },
                                        )
                                        await self.db.insert_signal(signal_payload)
                                        self.signals_generated += 1
                                        logger.info(
                                            f"🔥 Price action [{market_type}] {symbol} {pa_direction} "
                                            f"chg={price_change_pct*100:.2f}% rsi={rsi_val:.0f} trend={trend_dir}"
                                        )

                    except Exception as e:
                        logger.exception(f"Error processing {symbol} ({market_type}): {e}")
                        continue

                # Bellek temizliği: her market_type sonrası
                gc.collect()

            self.last_activity = datetime.utcnow()
            gc.collect()
            _malloc_trim()

        except Exception as e:
            logger.error(f"Error in strategy analysis: {e}")

    async def _signature_matching(self):
        """Compare current market state against stored historical signatures.
        If cosine_similarity > 50%, generate a TradeSignal."""
        try:
            watchlist = await self._get_watchlist()
            for market_type in Config.MARKET_TYPES:
                for symbol in watchlist:
                    try:
                        # Get recent trades to build current vector
                        trades = await self.db.get_recent_trades(symbol, limit=100, market_type=market_type)
                        if len(trades) < 20:
                            continue

                        prices = np.array([float(t['price']) for t in reversed(trades)], dtype=np.float64)
                        current_vector = np.array(
                            [(p - prices[0]) / max(prices[0], 1e-8) for p in prices],
                            dtype=np.float64
                        )
                        if current_vector.size < 4:
                            continue

                        # Fetch historical signatures for this symbol
                        signatures = await self.db.get_historical_signatures(symbol=symbol, limit=100)
                        if not signatures:
                            continue

                        for sig in signatures:
                            sig_vector = sig.get('pre_move_vector', [])
                            if not sig_vector or len(sig_vector) < 4:
                                continue

                            sig_arr = np.array(sig_vector, dtype=np.float64)

                            # Align lengths
                            min_len = min(len(current_vector), len(sig_arr))
                            cv = current_vector[:min_len].reshape(1, -1)
                            sv = sig_arr[:min_len].reshape(1, -1)

                            similarity = float(sk_cosine(cv, sv)[0][0])
                            if similarity < 0.50:
                                continue

                            sig_direction = sig.get('direction', 'long')
                            sig_change = float(sig.get('change_pct', 0))
                            sig_tf = sig.get('timeframe', '15m')

                            confidence = float(min(similarity * 0.9, 0.95))
                            signal_type = f'signature_{sig_direction}'
                            last_price = float(prices[-1])
                            target_pct = max(abs(sig_change), 0.02)  # ≥2% target

                            if self._can_emit_signal(symbol, market_type, sig_direction):
                                signal_payload = self._build_signal_payload(
                                    market_type=market_type,
                                    symbol=symbol,
                                    signal_type=signal_type,
                                    direction=sig_direction,
                                    confidence=confidence,
                                    entry_price=last_price,
                                    target_pct=target_pct,
                                    eta_minutes=15 if sig_tf == '15m' else 60,
                                    metadata={
                                        'cosine_similarity': float(similarity),
                                        'reference_change_pct': float(sig_change),
                                        'reference_timeframe': sig_tf,
                                        'signature_id': sig.get('id'),
                                        'pre_move_indicators': sig.get('pre_move_indicators', {}),
                                    },
                                )
                                await self.db.insert_signal(signal_payload)
                                self.signals_generated += 1
                                logger.info(
                                    f"🔖 Signature match [{market_type}] {symbol} {sig_direction} "
                                    f"sim={similarity:.2f} ref_chg={sig_change:+.2%} tf={sig_tf}"
                                )
                                break  # 1 match per symbol per cycle

                    except Exception as e:
                        logger.error(f"Signature matching error {symbol} ({market_type}): {e}")
                        continue

        except Exception as e:
            logger.error(f"Signature matching global error: {e}")
        finally:
            gc.collect()
            _malloc_trim()

    def _count_indicator_confirmations(self, ind: Dict) -> tuple:
        """
        Count how many indicators confirm the same direction.
        Returns: (direction, confirming_count, base_confidence)
        agent_instructions.py rule: minimum 2 indicator confirmations required for signal.
        """
        bullish = 0
        bearish = 0

        # RSI signal: above 55 = bullish trend, below 45 = bearish trend
        # (55/45 used instead of 70/30 to detect early trend direction, not extremes)
        rsi_val = ind.get('rsi')
        if rsi_val is not None:
            if rsi_val > 55:
                bullish += 1
            elif rsi_val < 45:
                bearish += 1

        # MACD histogram signal: positive = bullish momentum, negative = bearish momentum
        macd_data = ind.get('macd')
        if macd_data and isinstance(macd_data, dict):
            hist = macd_data.get('histogram', 0)
            if hist > 0:
                bullish += 1
            elif hist < 0:
                bearish += 1

        # Bollinger Bands %B signal
        bb = ind.get('bollinger')
        if bb and isinstance(bb, dict):
            pct_b = bb.get('pct_b', 0.5)
            if pct_b > 0.6:
                bullish += 1
            elif pct_b < 0.4:
                bearish += 1

        # Overall trend summary signal
        trend_summary = ind.get('trend_summary', {})
        if isinstance(trend_summary, dict):
            trend_dir = trend_summary.get('trend', 'neutral')
            if trend_dir == 'bullish':
                bullish += 1
            elif trend_dir == 'bearish':
                bearish += 1

        # OBV confirmation signal: only count explicit bullish/bearish, not neutral
        obv_data = ind.get('obv')
        if obv_data and isinstance(obv_data, dict):
            obv_trend = obv_data.get('trend')
            if obv_trend == 'bullish':
                bullish += 1
            elif obv_trend == 'bearish':
                bearish += 1

        if bullish >= 2 and bullish > bearish:
            base_conf = min(0.3 + bullish * 0.08, 0.85)
            return 'long', bullish, base_conf
        elif bearish >= 2 and bearish > bullish:
            base_conf = min(0.3 + bearish * 0.08, 0.85)
            return 'short', bearish, base_conf

        return None, 0, 0.0

    def _get_regime_multiplier(self, regime: str) -> Dict[str, float]:
        """
        Return confidence multipliers per market regime per agent_instructions.py:
        TRENDING_UP: Long +15%, Short -20%
        TRENDING_DOWN: Short +15%, Long -20%
        RANGING: All signals -10%
        VOLATILE: All signals -25%
        QUIET: All signals -15%
        """
        multipliers = {
            'TRENDING_UP':   {'long': 1.15, 'short': 0.80},
            'TRENDING_DOWN': {'long': 0.80, 'short': 1.15},
            'RANGING':       {'long': 0.90, 'short': 0.90},
            'VOLATILE':      {'long': 0.75, 'short': 0.75},
            'QUIET':         {'long': 0.85, 'short': 0.85},
            'UNKNOWN':       {'long': 1.00, 'short': 1.00},
        }
        return multipliers.get(regime, {'long': 1.0, 'short': 1.0})

    async def _indicator_based_signals(self):
        """
        Technical indicator-based signal generation.

        Implements agent_instructions.py rules:
        - Minimum 2 indicator confirmations required (RSI, MACD, BB, OBV, trend)
        - Market regime confidence multiplier applied (TRENDING/RANGING/VOLATILE/QUIET)
        - No signals during low volume periods (< 30% of average volume)
        - Works in all modes (BOOTSTRAP through PRODUCTION)
        """
        try:
            watchlist = await self._get_watchlist()
            mode = self.state_tracker.get_mode() if self.state_tracker else 'PRODUCTION'
            min_conf = 0.25 if mode in ('BOOTSTRAP', 'LEARNING') else 0.35

            for market_type in Config.MARKET_TYPES:
                for symbol in watchlist:
                    try:
                        trades = await self.db.get_recent_trades(
                            symbol, limit=100, market_type=market_type)
                        if len(trades) < 30:
                            continue

                        prices = np.array(
                            [float(row['price']) for row in reversed(trades)],
                            dtype=np.float64)
                        volumes = np.array(
                            [float(row['quantity']) * float(row['price'])
                             for row in reversed(trades)],
                            dtype=np.float64)

                        # Hacim filtresi: son 5 periyot ortalaması < son 20 periyot ortalamasının %30'u ise geç
                        if volumes.size >= 20:
                            avg_vol = float(np.mean(volumes[-20:]))
                            recent_vol = float(np.mean(volumes[-5:])) if volumes.size >= 5 else float(volumes[-1])
                            if avg_vol > 0 and recent_vol < avg_vol * 0.30:
                                logger.debug(f"[{market_type}] {symbol}: low volume, signal generation skipped")
                                continue

                        ind = compute_all_indicators(prices, volumes=volumes)
                        if not isinstance(ind, dict):
                            continue

                        # Piyasa rejimi tespiti
                        regime_result = MarketRegimeDetector.detect(ind)
                        regime_name = regime_result.get('regime', 'UNKNOWN')

                        # QUIET rejimde: frekansı azalt — yalnızca yüksek güvende sinyal üret
                        quiet_mode_min_conf = 0.60

                        # İndikatör teyit sayısını hesapla
                        direction, confirming_count, base_conf = self._count_indicator_confirmations(ind)

                        if confirming_count < 2 or direction is None:
                            continue

                        # Rejim çarpanını uygula
                        regime_mult = self._get_regime_multiplier(regime_name)
                        confidence = float(base_conf * regime_mult.get(direction, 1.0))
                        confidence = max(0.10, min(0.95, confidence))

                        # Minimum güven eşiği kontrolü
                        effective_min_conf = max(min_conf, quiet_mode_min_conf) if regime_name == 'QUIET' else min_conf
                        if confidence < effective_min_conf:
                            continue

                        last_price = float(prices[-1])
                        atr_ratio = float(ind.get('atr_ratio') or 0.02)
                        # Direktif: stop-loss = ATR × 1.5 → R/R 1:1.5 için hedef = ATR × 2.25
                        target_pct = max(atr_ratio * 2.25, 0.02)

                        if self._can_emit_signal(symbol, market_type, direction):
                            signal_payload = self._build_signal_payload(
                                market_type=market_type,
                                symbol=symbol,
                                signal_type=f'indicator_{direction}',
                                direction=direction,
                                confidence=confidence,
                                entry_price=last_price,
                                target_pct=target_pct,
                                eta_minutes=30,
                                metadata={
                                    'confirming_indicators': confirming_count,
                                    'regime': regime_name,
                                    'regime_multiplier': float(regime_mult.get(direction, 1.0)),
                                    'rsi': float(ind['rsi']) if ind.get('rsi') is not None else None,
                                    'macd_histogram': float(ind['macd']['histogram']) if ind.get('macd') else None,
                                    'trend': ind.get('trend_summary', {}).get('trend', 'neutral'),
                                    'trend_strength': float(
                                        ind.get('trend_summary', {}).get('strength', 0)),
                                    'atr_ratio': float(atr_ratio),
                                    'bollinger_pct_b': float(ind['bollinger']['pct_b'])
                                        if ind.get('bollinger') else None,
                                    'sample_count': len(prices),
                                },
                            )
                            await self.db.insert_signal(signal_payload)
                            self.signals_generated += 1
                            logger.info(
                                f"📊 Indicator signal [{market_type}] {symbol} {direction} "
                                f"conf={confidence:.2f} indicators={confirming_count} regime={regime_name}"
                            )
                    except Exception as e:
                        logger.error(f"Indicator signal error {symbol} ({market_type}): {e}")
                        continue

            gc.collect()
        except Exception as e:
            logger.error(f"Indicator signals global error: {e}")

    async def _apply_correction_notes(self):
        """Read pending correction notes from RCA and adjust thresholds."""
        try:
            corrections = await self.db.get_pending_corrections()
            if not corrections:
                return

            for note in corrections:
                adj_key = note['adjustment_key']
                adj_val = float(note['adjustment_value'])
                signal_type = note['signal_type']
                reason = note.get('reason', '')

                if adj_key == 'similarity_threshold':
                    old_val = Config.SIMILARITY_THRESHOLD
                    Config.SIMILARITY_THRESHOLD = max(0.1, min(0.95, old_val + adj_val))
                    logger.info(f"🔧 Correction: SIMILARITY_THRESHOLD {old_val:.3f} → {Config.SIMILARITY_THRESHOLD:.3f} ({reason})")

                elif adj_key == 'price_movement_threshold':
                    old_val = Config.PRICE_MOVEMENT_THRESHOLD
                    Config.PRICE_MOVEMENT_THRESHOLD = max(0.005, min(0.10, old_val + adj_val))
                    logger.info(f"🔧 Correction: PRICE_MOVEMENT_THRESHOLD {old_val:.3f} → {Config.PRICE_MOVEMENT_THRESHOLD:.3f} ({reason})")

                elif adj_key == 'take_profit_pct':
                    old_val = Config.GHOST_TAKE_PROFIT_PCT
                    Config.GHOST_TAKE_PROFIT_PCT = max(0.01, min(0.20, old_val + adj_val))
                    logger.info(f"🔧 Correction: TAKE_PROFIT {old_val:.3f} → {Config.GHOST_TAKE_PROFIT_PCT:.3f} ({reason})")

                elif adj_key == 'stop_loss_pct':
                    old_val = Config.GHOST_STOP_LOSS_PCT
                    Config.GHOST_STOP_LOSS_PCT = max(0.005, min(0.10, old_val + adj_val))
                    logger.info(f"🔧 Correction: STOP_LOSS {old_val:.3f} → {Config.GHOST_STOP_LOSS_PCT:.3f} ({reason})")

                elif adj_key == 'min_confidence':
                    # Store per signal_type adjustment in brain
                    if self.brain:
                        self.brain.adjust_confidence_threshold(signal_type, adj_val)
                        logger.info(f"🔧 Correction: min_confidence for {signal_type}: +{adj_val:.3f}")

                await self.db.mark_correction_applied(note['id'])

            if corrections:
                logger.info(f"🔧 Applied {len(corrections)} correction notes from RCA")

        except Exception as e:
            logger.error(f"Error applying corrections: {e}")

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
