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
from event_bus import Event, EventType, get_event_bus

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
from market_activity_tracker import get_market_tracker

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

TARGET_HORIZONS = [
    ('15m', 15, 1.0),
    ('1h', 60, 1.3),
    ('4h', 240, 1.8),
    ('24h', 1440, 2.5),
]


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
        self._signal_cooldown_seconds = int(os.getenv("QUENBOT_SIGNAL_COOLDOWN_SECONDS", "900"))
        self._signal_window_seconds = int(os.getenv("QUENBOT_SIGNAL_WINDOW_SECONDS", "900"))
        self._min_signal_confidence = float(os.getenv("QUENBOT_MIN_SIGNAL_CONFIDENCE", "0.62"))
        self._min_quality_score = float(os.getenv("QUENBOT_MIN_QUALITY_SCORE", "0.68"))
        self._last_signal_emit: Dict[str, float] = {}
        self._last_signal_window: Dict[str, int] = {}
        # Günlük sinyal limiti 
        self._max_daily_signals_per_symbol = int(os.getenv("QUENBOT_MAX_DAILY_SIGNALS_PER_SYMBOL", "4"))
        self._daily_signal_timestamps: Dict[str, list] = {}  # {symbol: [timestamp1, ...]}
        self._event_bus = get_event_bus()
        self._mamis_signal_ttl_seconds = int(os.getenv("QUENBOT_MAMIS_SIGNAL_TTL_SECONDS", "1200"))
        self._mamis_weight = float(os.getenv("QUENBOT_MAMIS_ENSEMBLE_WEIGHT", "0.35"))
        self._strategist_weight = float(os.getenv("QUENBOT_STRATEGIST_ENSEMBLE_WEIGHT", "0.65"))
        self._mamis_signal_cache: Dict[str, Dict[str, Any]] = {}
        self._mamis_fusions = 0
        self._historical_lookback_hours = max(24, int(os.getenv("QUENBOT_HISTORICAL_LOOKBACK_HOURS", str(Config.HISTORICAL_LOOKBACK_HOURS))))
        self._signature_limit = max(200, int(os.getenv("QUENBOT_SIGNATURE_CACHE_LIMIT", str(Config.SIGNATURE_CACHE_LIMIT))))

    async def initialize(self):
        logger.info("Initializing Strategist Agent...")
        self._event_bus.subscribe(EventType.MICROSTRUCTURE_SIGNAL, self._on_mamis_signal)

    async def _on_mamis_signal(self, event: Event):
        signal = event.data or {}
        symbol = str(signal.get("symbol", "")).upper()
        if not symbol:
            return
        self._mamis_signal_cache[symbol] = {
            **signal,
            "_cached_at": time.time(),
        }

    def _get_mamis_signal(self, symbol: str) -> Optional[Dict[str, Any]]:
        cached = self._mamis_signal_cache.get(symbol.upper())
        if not cached:
            return None
        age = time.time() - float(cached.get("_cached_at", 0) or 0)
        if age > self._mamis_signal_ttl_seconds:
            self._mamis_signal_cache.pop(symbol.upper(), None)
            return None
        return cached

    def _apply_mamis_ensemble(
        self,
        *,
        symbol: str,
        direction: str,
        confidence: float,
        target_pct: float,
    ) -> Dict[str, Any]:
        mamis = self._get_mamis_signal(symbol)
        if not mamis:
            return {
                "direction": direction,
                "confidence": float(confidence),
                "target_pct": float(target_pct),
                "ensemble": None,
            }

        mamis_dir = str(mamis.get("signal_direction", "neutral")).lower()
        mamis_conf = float(mamis.get("confidence_score", 0) or 0)
        strategist_score = float(confidence)
        aligned = mamis_dir == direction
        opposite = mamis_dir in {"long", "short"} and mamis_dir != direction

        if aligned:
            fused_conf = self._strategist_weight * strategist_score + self._mamis_weight * mamis_conf
            fused_target = float(target_pct) * 1.12
        elif opposite:
            conflict_penalty = 1.0 - min(0.75, self._mamis_weight * mamis_conf)
            fused_conf = strategist_score * conflict_penalty
            fused_target = float(target_pct) * 0.8
        else:
            fused_conf = self._strategist_weight * strategist_score + self._mamis_weight * mamis_conf * 0.5
            fused_target = float(target_pct)

        self._mamis_fusions += 1
        return {
            "direction": direction,
            "confidence": float(min(max(fused_conf, 0.0), 1.0)),
            "target_pct": float(max(fused_target, 0.005)),
            "ensemble": {
                "mamis_direction": mamis_dir,
                "mamis_confidence": mamis_conf,
                "aligned": aligned,
                "opposite": opposite,
                "weights": {
                    "strategist": self._strategist_weight,
                    "mamis": self._mamis_weight,
                },
                "pattern_type": mamis.get("detected_pattern_type"),
                "estimated_volatility": float(mamis.get("estimated_volatility", 0) or 0),
            },
        }

    async def start(self):
        self.running = True
        logger.info("Starting Strategist Agent...")
        tracker = get_market_tracker()

        while self.running:
            try:
                # Piyasa aktif olana kadar bekle (max 60s), düşük güçte idle
                is_active = await tracker.wait_for_activity(timeout=60)
                if not is_active:
                    # Low-power: sadece correction notes ve outcome güncelleme
                    await self._apply_correction_notes()
                    await self._update_pattern_outcomes()
                    continue

                await self._apply_correction_notes()
                await self._analyze_strategies()
                await self._signature_matching()
                await self._multi_timeframe_analysis()
                await self._update_pattern_outcomes()
            except Exception as e:
                logger.error(f"Strategist cycle error: {e}")
            await asyncio.sleep(15)  # Aktif modda 15s döngü

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

    def _signal_quality_score(self, confidence: float, target_pct: float) -> float:
        # 15m+ horizon quality: only high-conviction >=2% moves should pass.
        c = min(max(float(confidence), 0.0), 1.0)
        tp = abs(float(target_pct))
        ideal = 0.025
        target_component = 1.0 - min(abs(tp - ideal) / 0.03, 1.0)
        return min(max(c * 0.8 + target_component * 0.2, 0.0), 1.0)

    def _normalize_target_pct(self, value: float) -> float:
        numeric = abs(float(value or 0.0))
        if numeric > 0.5:
            numeric /= 100.0
        return numeric

    def _estimate_data_density(self, metadata: Optional[Dict[str, Any]] = None) -> float:
        meta = metadata or {}
        explicit = meta.get('data_density')
        if explicit is not None:
            return min(max(float(explicit), 0.0), 1.0)
        sample_count = float(meta.get('sample_count') or meta.get('trade_count') or 0)
        if sample_count <= 0:
            return 0.45
        return min(max(sample_count / 80.0, 0.0), 1.0)

    def _build_target_horizons(
        self,
        *,
        entry_price: float,
        direction: str,
        target_pct: float,
        confidence: float,
        data_density: float,
    ) -> List[Dict[str, Any]]:
        base_target = max(self._normalize_target_pct(target_pct), 0.02)
        strength = min(max((float(confidence) * 0.7) + (float(data_density) * 0.3), 0.0), 1.0)
        horizons: List[Dict[str, Any]] = []

        for label, eta_minutes, multiplier in TARGET_HORIZONS:
            if eta_minutes > 60:
                # 15m and 1h are ALWAYS emitted so every signal card has a
                # one-hour close/learning resolution. Longer horizons remain
                # strength-gated.
                required_strength = {
                    240: 0.40,
                    1440: 0.50,
                }.get(eta_minutes, 0.40)
                if strength < required_strength:
                    continue

            horizon_target = min(max(base_target * multiplier, 0.02), 0.25)
            target_price = entry_price * (1.0 + horizon_target) if direction == 'long' else entry_price * (1.0 - horizon_target)
            horizons.append({
                'label': label,
                'eta_minutes': eta_minutes,
                'target_pct': float(horizon_target),
                'target_price': float(target_price),
                'strength': round(strength, 4),
            })

        return horizons or [{
            'label': '15m',
            'eta_minutes': 15,
            'target_pct': float(base_target),
            'target_price': float(entry_price * (1.0 + base_target) if direction == 'long' else entry_price * (1.0 - base_target)),
            'strength': round(strength, 4),
        }]

    def _can_emit_signal(
        self,
        symbol: str,
        market_type: str,
        direction: str,
        confidence: float,
        target_pct: float,
    ) -> bool:
        fusion = self._apply_mamis_ensemble(
            symbol=symbol,
            direction=direction,
            confidence=confidence,
            target_pct=target_pct,
        )
        confidence = float(fusion["confidence"])
        target_pct = float(fusion["target_pct"])

        if self._normalize_target_pct(target_pct) < 0.02:
            return False

        if confidence < self._min_signal_confidence:
            return False

        quality = self._signal_quality_score(confidence, target_pct)
        if quality < self._min_quality_score:
            return False

        # Kilit symbol seviyesinde — aynı coin için spot/futures veya farklı borsalar
        # ayrı ayrı sinyal yaratmasın, tek bir havuzda yarışsın.
        key = symbol.upper()
        now = time.time()
        last = self._last_signal_emit.get(key, 0.0)
        if now - last < self._signal_cooldown_seconds:
            return False

        window_id = int(now // max(self._signal_window_seconds, 1))
        if self._last_signal_window.get(key) == window_id:
            return False

        # Günlük sinyal limiti (symbol bazında, market_type'tan bağımsız)
        one_day_ago = now - 86400
        timestamps = [
            ts for ts in self._daily_signal_timestamps.get(key, [])
            if ts > one_day_ago
        ]
        if len(timestamps) >= self._max_daily_signals_per_symbol:
            logger.info(
                f"🚫 Daily signal limit (strategist): {key} "
                f"{len(timestamps)}/{self._max_daily_signals_per_symbol} bugün"
            )
            self._daily_signal_timestamps[key] = timestamps
            return False

        timestamps.append(now)
        self._daily_signal_timestamps[key] = timestamps
        self._last_signal_emit[key] = now
        self._last_signal_window[key] = window_id
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
        fusion = self._apply_mamis_ensemble(
            symbol=symbol,
            direction=direction,
            confidence=confidence,
            target_pct=target_pct,
        )
        confidence = float(fusion["confidence"])
        target_pct = float(fusion["target_pct"])
        safe_target_pct = self._normalize_target_pct(target_pct)
        safe_target_pct = max(safe_target_pct, 0.02)
        max_target_15m = float(os.getenv("QUENBOT_15M_MAX_TARGET_PCT", "0.03"))
        safe_target_pct = min(safe_target_pct, min(float(Config.STRATEGY_MAX_TARGET_PCT), max_target_15m))
        if direction == 'short':
            # Prevent impossible negative target prices on short signals.
            safe_target_pct = min(safe_target_pct, 0.95)
        safe_entry = float(entry_price)
        density = self._estimate_data_density(metadata)
        target_horizons = list((metadata or {}).get('target_horizons') or [])
        if not target_horizons:
            target_horizons = self._build_target_horizons(
                entry_price=safe_entry,
                direction=direction,
                target_pct=safe_target_pct,
                confidence=confidence,
                data_density=density,
            )
        selected_horizon = max(target_horizons, key=lambda item: int(item.get('eta_minutes', 15) or 15)) if target_horizons else None
        effective_eta = int((selected_horizon or {}).get('eta_minutes', eta_minutes) or eta_minutes)
        target_price = float((selected_horizon or {}).get('target_price') or (safe_entry * (1.0 + safe_target_pct) if direction == 'long' else safe_entry * (1.0 - safe_target_pct)))
        safe_target_pct = float((selected_horizon or {}).get('target_pct') or safe_target_pct)
        ts = datetime.utcnow()
        # Ensure each horizon has initial 'active' status
        for h in target_horizons:
            if 'status' not in h:
                h['status'] = 'active'
        # Expires based on longest horizon + 5 min buffer
        max_eta = max((int(h.get('eta_minutes', 15)) for h in target_horizons), default=240)
        expires_at = ts + timedelta(minutes=max_eta + 5)
        base_meta = {
            'position_bias': direction,
            'market_type': market_type,
            'target_pct': safe_target_pct,
            'signal_time': ts.isoformat() + 'Z',
            'entry_price': safe_entry,
            'current_price_at_signal': safe_entry,
            'target_price': float(target_price),
            'estimated_duration_to_target_minutes': int(max(15, effective_eta)),
            'target_horizons': target_horizons,
            'selected_horizon': (selected_horizon or {}).get('label', '15m'),
            'data_density': density,
            'quality_score': round(self._signal_quality_score(confidence, safe_target_pct), 4),
            'strategy_approved': True,
            'dashboard_candidate': True,
            'target_candidate': True,
            'expires_at': expires_at.isoformat() + 'Z',
        }
        if fusion.get("ensemble"):
            base_meta['mamis_ensemble'] = fusion["ensemble"]
            base_meta['mamis_context'] = {
                'direction': fusion['ensemble'].get('mamis_direction', 'neutral'),
                'confidence': fusion['ensemble'].get('mamis_confidence', 0),
                'pattern_type': fusion['ensemble'].get('pattern_type', 'none'),
                'volatility': float(fusion['ensemble'].get('estimated_volatility', 0) or 0),
            }
        if metadata:
            base_meta.update(metadata)

        # ─── Enhanced intelligence snapshot (microstructure + regime + fingerprint) ───
        try:
            from enhanced_features import build_feature_snapshot, feature_vector_for_meta_labeler
            snapshot = build_feature_snapshot(symbol)
            if snapshot and (snapshot.get('microstructure') or snapshot.get('regime') or snapshot.get('fingerprint')):
                base_meta['entry_features'] = snapshot
            # Meta-labeler advisory (log-only; sinyal gating'i bozulmaz)
            try:
                from meta_labeler import get_meta_labeler
                fv = feature_vector_for_meta_labeler(confidence=float(confidence), snapshot=snapshot)
                adv = get_meta_labeler().predict(fv)
                base_meta['meta_labeler'] = {
                    'proba': adv.get('proba'),
                    'accept': adv.get('accept'),
                    'version': adv.get('version'),
                    'reason': adv.get('reason'),
                }
            except Exception:
                pass
            # Conformal confidence interval
            try:
                from conformal import get_conformal
                lo, hi, q = get_conformal().predict_interval(float(confidence))
                base_meta['confidence_band'] = {'lo': round(lo, 4), 'hi': round(hi, 4), 'q': round(q, 4)}
            except Exception:
                pass
            # Thompson-bandit EV
            try:
                from thompson_bandit import get_thompson_bandit
                base_meta['bandit_ev'] = round(get_thompson_bandit().expected_value(signal_type), 4)
            except Exception:
                pass
        except Exception:
            pass

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
                                        best_tf_change = abs(prediction['timeframes'][best_tf].get('avg_change_pct', 0.02))
                                        target_pct = max(best_tf_change, 0.005)
                                        signal_type = f'brain_{direction}_{best_tf}'
                                        if self._can_emit_signal(symbol, market_type, direction, confidence, target_pct):
                                            signal_payload = self._build_signal_payload(
                                                market_type=market_type,
                                                symbol=symbol,
                                                signal_type=signal_type,
                                                direction=direction,
                                                confidence=confidence,
                                                entry_price=last_price,
                                                target_pct=target_pct,
                                                eta_minutes=15,
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
                                                    if self._can_emit_signal(symbol, market_type, intel_dir, float(intel_conf), max(itf_change, 0.005)):
                                                        signal_payload = self._build_signal_payload(
                                                            market_type=market_type,
                                                            symbol=symbol,
                                                            signal_type=signal_type,
                                                            direction=intel_dir,
                                                            confidence=float(min(intel_conf, 0.95)),
                                                            entry_price=last_price,
                                                            target_pct=max(itf_change, 0.005),
                                                            eta_minutes=15,
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

                        historical_profiles = await self.db.get_movement_profiles(
                            symbol,
                            hours=self._historical_lookback_hours,
                            market_type=market_type,
                            limit=200,
                        )
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
                            target_pct = max(float(mean_profit), 0.005)
                            if self._can_emit_signal(symbol, market_type, direction, float(min(evo_conf, 1.0)), target_pct):
                                signal_payload = self._build_signal_payload(
                                    market_type=market_type,
                                    symbol=symbol,
                                    signal_type=signal_type,
                                    direction=direction,
                                    confidence=float(min(evo_conf, 1.0)),
                                    entry_price=last_price,
                                    target_pct=target_pct,
                                    eta_minutes=15,
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
                            target_pct = max(float(mean_profit), 0.005)
                            if self._can_emit_signal(symbol, market_type, direction, float(momentum_conf), target_pct):
                                signal_payload = self._build_signal_payload(
                                    market_type=market_type,
                                    symbol=symbol,
                                    signal_type=signal_type,
                                    direction=direction,
                                    confidence=float(momentum_conf),
                                    entry_price=last_price,
                                    target_pct=target_pct,
                                    eta_minutes=15,
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
                            if (abs(price_change_pct) > 0.005 and trend_strength > 0.20 and
                                    rsi_val is not None and 20 < rsi_val < 80):
                                pa_direction = 'long' if price_change_pct > 0 and trend_dir == 'bullish' else (
                                    'short' if price_change_pct < 0 and trend_dir == 'bearish' else None
                                )
                                if pa_direction:
                                    pa_conf = min(abs(price_change_pct) * 18 + trend_strength * 0.35, 0.85)
                                    target_pct = max(abs(price_change_pct) * 1.2, 0.005)
                                    if self._can_emit_signal(symbol, market_type, pa_direction, float(pa_conf), target_pct):
                                        signal_payload = self._build_signal_payload(
                                            market_type=market_type,
                                            symbol=symbol,
                                            signal_type=f'price_action_{pa_direction}',
                                            direction=pa_direction,
                                            confidence=float(pa_conf),
                                            entry_price=last_price,
                                            target_pct=target_pct,
                                            eta_minutes=15,
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
                        signatures = await self.db.get_historical_signatures(
                            symbol=symbol,
                            limit=self._signature_limit,
                            lookback_hours=self._historical_lookback_hours,
                        )
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
                            target_pct = max(abs(sig_change), 0.005)

                            if self._can_emit_signal(symbol, market_type, sig_direction, confidence, target_pct):
                                signal_payload = self._build_signal_payload(
                                    market_type=market_type,
                                    symbol=symbol,
                                    signal_type=signal_type,
                                    direction=sig_direction,
                                    confidence=confidence,
                                    entry_price=last_price,
                                    target_pct=target_pct,
                                    eta_minutes=15,
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
                "mamis_fusions": self._mamis_fusions,
                "brain_connected": self.brain is not None,
                "recent_movements_analyzed": len(recent_movements),
                "similarity_threshold": Config.SIMILARITY_THRESHOLD
            }
        except Exception as e:
            return {"healthy": False, "error": str(e)}
