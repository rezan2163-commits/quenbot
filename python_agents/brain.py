"""
Brain Module - QuenBot AI Zeka Merkezi
=======================================
Tüm botların ortak zeka katmanı. Pattern eşleştirme, öğrenme, 
çoklu zaman dilimi analizi ve kendi kendini geliştirme.
"""
import asyncio
import json
import logging
import math
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity

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

# Intelligence Core lazy import (circular import koruması)
_intelligence_core_module = None

def _get_intelligence_core():
    global _intelligence_core_module
    if _intelligence_core_module is None:
        from intelligence_core import IntelligenceCore
        _intelligence_core_module = IntelligenceCore
    return _intelligence_core_module

# ─── Zaman Dilimleri ───
TIMEFRAMES = {
    '15m': 15,
    '1h': 60,
    '4h': 240,
    '1d': 1440
}


class TradeSnapshot:
    """Belirli bir zaman dilimindeki trade verilerinin özeti"""
    def __init__(self, symbol: str, exchange: str, market_type: str,
                 start_time: datetime, end_time: datetime,
                 buy_count: int, sell_count: int,
                 buy_volume: float, sell_volume: float,
                 avg_price: float, price_start: float, price_end: float,
                 high: float, low: float):
        self.symbol = symbol
        self.exchange = exchange
        self.market_type = market_type
        self.start_time = start_time
        self.end_time = end_time
        self.buy_count = buy_count
        self.sell_count = sell_count
        self.buy_volume = buy_volume
        self.sell_volume = sell_volume
        self.avg_price = avg_price
        self.price_start = price_start
        self.price_end = price_end
        self.high = high
        self.low = low

    @property
    def total_volume(self): return self.buy_volume + self.sell_volume
    @property
    def total_trades(self): return self.buy_count + self.sell_count
    @property
    def buy_ratio(self): return self.buy_volume / max(self.total_volume, 1e-8)
    @property
    def price_change_pct(self): return (self.price_end - self.price_start) / max(self.price_start, 1e-8)
    @property
    def volatility(self): return (self.high - self.low) / max(self.avg_price, 1e-8)

    def to_vector(self) -> np.ndarray:
        """Snapshot'ı karşılaştırılabilir vektöre çevir"""
        return np.array([
            self.price_change_pct,
            self.buy_ratio,
            self.volatility,
            math.log1p(self.total_volume),
            math.log1p(self.total_trades),
            self.buy_count / max(self.sell_count, 1),
        ], dtype=np.float64)

    def to_dict(self) -> dict:
        return {
            'symbol': self.symbol, 'exchange': self.exchange,
            'market_type': self.market_type,
            'start_time': self.start_time.isoformat(),
            'end_time': self.end_time.isoformat(),
            'buy_count': self.buy_count, 'sell_count': self.sell_count,
            'buy_volume': self.buy_volume, 'sell_volume': self.sell_volume,
            'avg_price': self.avg_price,
            'price_start': self.price_start, 'price_end': self.price_end,
            'high': self.high, 'low': self.low,
            'price_change_pct': self.price_change_pct,
            'buy_ratio': self.buy_ratio, 'volatility': self.volatility,
        }


class PatternRecord:
    """Geçmiş pattern kaydı + sonrasında fiyatın ne yaptığı"""
    def __init__(self, snapshot: TradeSnapshot,
                 outcome_15m: Optional[float] = None,
                 outcome_1h: Optional[float] = None,
                 outcome_4h: Optional[float] = None,
                 outcome_1d: Optional[float] = None):
        self.snapshot = snapshot
        self.outcomes = {
            '15m': outcome_15m,
            '1h': outcome_1h,
            '4h': outcome_4h,
            '1d': outcome_1d,
        }

    @property
    def vector(self): return self.snapshot.to_vector()


class BrainModule:
    """
    Merkezi AI Zeka Modülü — Katman 2+3 Köprüsü
    ==============================================
    Katman 2 (Cognition/Memory): Pattern hafızası, öğrenme, kalibrasyon
    Katman 3 Entegrasyonu: GemmaDecisionCore üzerinden nihai karar

    FELSEFE: Brain verileri toplar ve sentezler, GEMMA karar verir.
    """

    MAX_PATTERN_MEMORY = 500
    AUTO_CALIBRATE_INTERVAL = 50  # Her 50 öğrenme sonrasında otomatik kalibrasyon

    def __init__(self, db):
        self.db = db
        self.pattern_memory: List[PatternRecord] = []
        self.learning_weights = {
            'similarity': 0.35,
            'volume_match': 0.25,
            'direction_match': 0.2,
            'confidence_history': 0.2,
        }
        # Öğrenme metrikleri
        self.prediction_accuracy = {'correct': 0, 'total': 0}
        self.signal_type_scores: Dict[str, Dict] = {}
        self.last_learning_update = None
        # Intelligence Core (gelişmiş zeka katmanı)
        self.intelligence_core = None
        # Otomatik kalibrasyon
        self._calibration_counter = 0
        self._calibration_log: List[Dict] = []
        # Pattern matcher değerlendirme istatistikleri
        self._pattern_match_stats = {
            'total_evaluated': 0,
            'total_approved': 0,
            'total_vetoed': 0,
            'last_match': None,
        }

    async def initialize(self):
        """Geçmiş pattern'ları yükle"""
        try:
            patterns = await self.db.get_pattern_records(limit=500)
            for p in patterns:
                snap_data = p.get('snapshot_data', {})
                if not snap_data:
                    continue
                snap = TradeSnapshot(
                    symbol=snap_data.get('symbol', ''),
                    exchange=snap_data.get('exchange', ''),
                    market_type=snap_data.get('market_type', 'spot'),
                    start_time=datetime.fromisoformat(snap_data['start_time']) if snap_data.get('start_time') else datetime.utcnow(),
                    end_time=datetime.fromisoformat(snap_data['end_time']) if snap_data.get('end_time') else datetime.utcnow(),
                    buy_count=snap_data.get('buy_count', 0),
                    sell_count=snap_data.get('sell_count', 0),
                    buy_volume=snap_data.get('buy_volume', 0),
                    sell_volume=snap_data.get('sell_volume', 0),
                    avg_price=snap_data.get('avg_price', 0),
                    price_start=snap_data.get('price_start', 0),
                    price_end=snap_data.get('price_end', 0),
                    high=snap_data.get('high', 0),
                    low=snap_data.get('low', 0),
                )
                record = PatternRecord(
                    snapshot=snap,
                    outcome_15m=p.get('outcome_15m'),
                    outcome_1h=p.get('outcome_1h'),
                    outcome_4h=p.get('outcome_4h'),
                    outcome_1d=p.get('outcome_1d'),
                )
                self.pattern_memory.append(record)
            logger.info(f"🧠 Brain: Loaded {len(self.pattern_memory)} historical patterns")
        except Exception as e:
            logger.warning(f"Brain: Could not load patterns: {e}")

        # Intelligence Core başlat
        try:
            ICClass = _get_intelligence_core()
            self.intelligence_core = ICClass(self)
            await self.intelligence_core.initialize()
        except Exception as e:
            logger.warning(f"Brain: Intelligence Core init failed (degraded mode): {e}")
            self.intelligence_core = None

    def build_snapshot_from_trades(self, trades: List[Dict], symbol: str,
                                    exchange: str, market_type: str) -> Optional[TradeSnapshot]:
        """Trade listesinden snapshot oluştur"""
        if not trades or len(trades) < 5:
            return None
        try:
            buy_trades = [t for t in trades if t.get('side') == 'buy']
            sell_trades = [t for t in trades if t.get('side') == 'sell']
            prices = [float(t['price']) for t in trades]
            timestamps = [t['timestamp'] if isinstance(t['timestamp'], datetime) else datetime.fromisoformat(str(t['timestamp'])) for t in trades]

            return TradeSnapshot(
                symbol=symbol, exchange=exchange, market_type=market_type,
                start_time=min(timestamps), end_time=max(timestamps),
                buy_count=len(buy_trades), sell_count=len(sell_trades),
                buy_volume=sum(float(t['quantity']) * float(t['price']) for t in buy_trades),
                sell_volume=sum(float(t['quantity']) * float(t['price']) for t in sell_trades),
                avg_price=sum(prices) / len(prices),
                price_start=prices[0], price_end=prices[-1],
                high=max(prices), low=min(prices),
            )
        except Exception as e:
            logger.debug(f"Error building snapshot: {e}")
            return None

    def find_matching_patterns(self, current: TradeSnapshot,
                                min_similarity: float = 0.7,
                                top_k: int = 10) -> List[Tuple[PatternRecord, float]]:
        """Mevcut snapshot'a benzeyen geçmiş pattern'ları bul"""
        if not self.pattern_memory:
            return []
        try:
            current_vec = current.to_vector().reshape(1, -1)
            same_symbol = [p for p in self.pattern_memory if p.snapshot.symbol == current.symbol]
            if len(same_symbol) < 3:
                same_symbol = self.pattern_memory  # yeterli veri yoksa tümünü kullan

            if not same_symbol:
                return []

            hist_vecs = np.array([p.vector for p in same_symbol])
            # Normalize
            norms_c = np.linalg.norm(current_vec, axis=1, keepdims=True)
            norms_h = np.linalg.norm(hist_vecs, axis=1, keepdims=True)
            current_normed = current_vec / np.maximum(norms_c, 1e-8)
            hist_normed = hist_vecs / np.maximum(norms_h, 1e-8)

            sims = cosine_similarity(current_normed, hist_normed)[0]
            results = []
            for i, sim in enumerate(sims):
                if sim >= min_similarity:
                    results.append((same_symbol[i], float(sim)))
            results.sort(key=lambda x: x[1], reverse=True)
            return results[:top_k]
        except Exception as e:
            logger.debug(f"Pattern matching error: {e}")
            return []

    def predict_direction(self, matches: List[Tuple[PatternRecord, float]]) -> Dict[str, Any]:
        """Eşleşen pattern'lardan yön ve güven tahmini yap"""
        if not matches:
            return {'direction': None, 'confidence': 0, 'timeframes': {}}

        timeframe_predictions = {}
        for tf_key in TIMEFRAMES:
            outcomes = []
            weights = []
            for pattern, sim in matches:
                outcome = pattern.outcomes.get(tf_key)
                if outcome is not None:
                    outcomes.append(outcome)
                    weights.append(sim)
            if outcomes:
                weighted_avg = sum(o * w for o, w in zip(outcomes, weights)) / sum(weights)
                direction = 'long' if weighted_avg > 0 else 'short'
                strength = abs(weighted_avg)
                timeframe_predictions[tf_key] = {
                    'direction': direction,
                    'avg_change_pct': weighted_avg,
                    'strength': strength,
                    'sample_count': len(outcomes),
                }

        # Çoğunluk yönünü belirle
        directions = [v['direction'] for v in timeframe_predictions.values()]
        long_count = directions.count('long')
        short_count = directions.count('short')
        primary_dir = 'long' if long_count >= short_count else 'short'

        avg_sim = sum(s for _, s in matches) / len(matches)
        confidence = min(avg_sim * 0.6 + (len(matches) / 20) * 0.4, 1.0)

        return {
            'direction': primary_dir,
            'confidence': confidence,
            'timeframes': timeframe_predictions,
            'match_count': len(matches),
            'avg_similarity': avg_sim,
        }

    def record_outcome(self, pattern_id: int, timeframe: str, actual_change_pct: float):
        """Gerçekleşen sonucu kaydet, öğrenme için"""
        for record in self.pattern_memory:
            if id(record) == pattern_id:
                record.outcomes[timeframe] = actual_change_pct
                break
        self.prediction_accuracy['total'] += 1

    def update_learning(self, signal_type: str, was_correct: bool, pnl_pct: float):
        """Sinyal sonuçlarından öğren"""
        if signal_type not in self.signal_type_scores:
            self.signal_type_scores[signal_type] = {
                'correct': 0, 'total': 0, 'total_pnl': 0
            }
        stats = self.signal_type_scores[signal_type]
        stats['total'] += 1
        stats['total_pnl'] += pnl_pct
        if was_correct:
            stats['correct'] += 1
            self.prediction_accuracy['correct'] += 1
        self.prediction_accuracy['total'] += 1
        self.last_learning_update = datetime.utcnow()

        # Adaptive learning weights based on signal performance
        self._update_adaptive_weights()

        # Otomatik kalibrasyon kontrolü
        self._calibration_counter += 1
        if self._calibration_counter >= self.AUTO_CALIBRATE_INTERVAL:
            self._auto_calibrate()
            self._calibration_counter = 0

        logger.info(f"🧠 Brain learning: {signal_type} {'✓' if was_correct else '✗'} | "
                     f"Accuracy: {self.get_accuracy():.1%}")

    def _update_adaptive_weights(self):
        """Signal performance'a göre learning_weights ayarla."""
        if self.prediction_accuracy['total'] < 10:
            return  # Yeterli veri yok

        accuracy = self.get_accuracy()

        # Düşük accuracy → similarity ağırlığını artır (daha sıkı matching)
        if accuracy < 0.4:
            self.learning_weights['similarity'] = min(self.learning_weights['similarity'] + 0.02, 0.5)
            self.learning_weights['confidence_history'] = max(self.learning_weights['confidence_history'] - 0.01, 0.1)
        # Yüksek accuracy → daha dengeli ağırlıklar
        elif accuracy > 0.6:
            self.learning_weights['similarity'] = max(self.learning_weights['similarity'] - 0.01, 0.25)
            self.learning_weights['volume_match'] = min(self.learning_weights['volume_match'] + 0.01, 0.35)

    def _auto_calibrate(self):
        """Otomatik kalibrasyon: zayıf sinyal tiplerini bastır, güçlüleri teşvik et."""
        if not self.signal_type_scores:
            return

        calibration = {"timestamp": datetime.utcnow().isoformat(), "actions": []}

        for sig_type, stats in self.signal_type_scores.items():
            if stats['total'] < 5:
                continue

            accuracy = stats['correct'] / stats['total']
            avg_pnl = stats['total_pnl'] / stats['total']

            # Başarısız sinyal tipi: eşiği yükselt
            if accuracy < 0.35 and stats['total'] >= 10:
                calibration["actions"].append({
                    "type": "suppress",
                    "signal_type": sig_type,
                    "reason": f"Düşük doğruluk ({accuracy:.0%}, n={stats['total']})",
                    "accuracy": accuracy,
                })

            # Başarılı sinyal tipi: güven artır
            if accuracy > 0.65 and avg_pnl > 0:
                calibration["actions"].append({
                    "type": "boost",
                    "signal_type": sig_type,
                    "reason": f"Yüksek doğruluk ({accuracy:.0%}, avg PnL={avg_pnl:.2f}%)",
                    "accuracy": accuracy,
                })

        # Pattern memory temizliği: düşük kaliteli eski pattern'ları çıkar
        if len(self.pattern_memory) > self.MAX_PATTERN_MEMORY * 0.9:
            old_count = len(self.pattern_memory)
            # Outcome'u olmayan eski pattern'ları sil
            self.pattern_memory = [
                p for p in self.pattern_memory
                if any(v is not None for v in p.outcomes.values())
                or (datetime.utcnow() - p.snapshot.end_time).days < 3
            ]
            pruned = old_count - len(self.pattern_memory)
            if pruned > 0:
                calibration["actions"].append({
                    "type": "prune",
                    "pruned_patterns": pruned,
                    "remaining": len(self.pattern_memory),
                })

        # Öğrenme ağırlık normalizasyonu
        total_w = sum(self.learning_weights.values())
        if abs(total_w - 1.0) > 0.01:
            for k in self.learning_weights:
                self.learning_weights[k] /= total_w
            calibration["actions"].append({"type": "normalize_weights"})

        if calibration["actions"]:
            self._calibration_log.append(calibration)
            if len(self._calibration_log) > 20:
                self._calibration_log = self._calibration_log[-20:]
            logger.info(f"🧠 Auto-calibration: {len(calibration['actions'])} actions — "
                         f"accuracy={self.get_accuracy():.1%}, patterns={len(self.pattern_memory)}")

    def get_accuracy(self) -> float:
        if self.prediction_accuracy['total'] == 0:
            return 0.0
        return self.prediction_accuracy['correct'] / self.prediction_accuracy['total']

    # ─── Pattern Match (Euclidean Distance) Integration — GEMMA DECISION CORE ───

    async def evaluate_pattern_match(self, match_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        PatternMatcherAgent'tan gelen Euclidean distance eşleşmesini değerlendir.

        ★ GEMMA-CENTRIC KARAR MEKANİZMASI ★
        Brain artık nihai kararı kendisi vermez; GemmaDecisionCore'a delege eder.
        Brain'in rolü:
          1. Eşleşme kalitesini ön-filtre et (min similarity)
          2. Öğrenme verileriyle ön kontrol yap
          3. GemmaDecisionCore'a tüm verileri sentezlesin diye gönder
          4. Gemma'nın nihai kararını döndür
        """
        from gemma_decision_core import get_decision_core

        symbol = match_data.get('symbol', '')
        similarity = match_data.get('similarity', 0)
        direction = match_data.get('predicted_direction', 'neutral')
        magnitude = match_data.get('predicted_magnitude', 0)
        confidence = match_data.get('confidence', 0)

        self._pattern_match_stats['total_evaluated'] += 1
        self._pattern_match_stats['last_match'] = {
            'symbol': symbol,
            'timeframe': match_data.get('timeframe'),
            'similarity': similarity,
            'timestamp': datetime.utcnow().isoformat(),
        }

        # ─── Ön filtre: minimum kalite ───
        if similarity < 0.50:
            self._pattern_match_stats['total_vetoed'] += 1
            return {
                'symbol': symbol, 'approved': False,
                'direction': direction, 'magnitude': magnitude,
                'confidence': confidence,
                'reasoning': f"Ön-filtre: Benzerlik eşik altı {similarity:.4f} < 0.50",
            }

        # ─── Ön filtre: Brain öğrenme verisi ───
        should_signal = self.should_generate_signal(symbol, direction, confidence)
        if not should_signal:
            self._pattern_match_stats['total_vetoed'] += 1
            return {
                'symbol': symbol, 'approved': False,
                'direction': direction, 'magnitude': magnitude,
                'confidence': confidence,
                'reasoning': f"Brain öğrenme verileri ({direction}) yönünde sinyal bastırıyor",
            }

        # ─── ★ GEMMA DECISION CORE — NİHAİ KARAR ★ ───
        decision_core = get_decision_core(
            brain=self,
            risk_manager=None,  # Orchestrator tarafından inject edilecek
            state_tracker=None,
        )

        gemma_decision = await decision_core.evaluate(
            pattern_data=match_data,
            scout_data=match_data.get('scout_context'),
            strategist_data=match_data.get('strategist_context'),
        )

        # Öğrenme kaydı
        self._record_pattern_match_prediction(symbol, direction, confidence)

        if gemma_decision.approved:
            self._pattern_match_stats['total_approved'] += 1
        else:
            self._pattern_match_stats['total_vetoed'] += 1

        return {
            'symbol': symbol,
            'approved': gemma_decision.approved,
            'direction': gemma_decision.direction,
            'magnitude': gemma_decision.magnitude,
            'confidence': gemma_decision.confidence,
            'reasoning': gemma_decision.reasoning,
            'risk_level': gemma_decision.risk_level,
            'action': gemma_decision.action,
            'llm_analysis': gemma_decision.raw_response[:300] if gemma_decision.raw_response else None,
            'gemma_latency_ms': gemma_decision.latency_ms,
        }

    # Not: _llm_evaluate_pattern_match artık GemmaDecisionCore tarafından yönetilmektedir.
    # Brain sadece ön-filtreleme yapıp GemmaDecisionCore.evaluate()'e delege eder.

    def _record_pattern_match_prediction(self, symbol: str, direction: str,
                                          confidence: float):
        """Pattern match tahminini öğrenme sistemine kaydet"""
        sig_type = f"pattern_match_{direction}"
        if sig_type not in self.signal_type_scores:
            self.signal_type_scores[sig_type] = {
                'correct': 0, 'total': 0, 'total_pnl': 0
            }
        # Tahmin kaydı (sonuç henüz bilinmiyor)
        self.signal_type_scores[sig_type]['total'] += 1
        self.last_learning_update = datetime.utcnow()

    def get_pattern_match_stats(self) -> Dict[str, Any]:
        """Pattern match istatistiklerini döndür"""
        return dict(self._pattern_match_stats)

    def should_generate_signal(self, symbol: str, direction: str,
                                confidence: float, min_return: float = 0.02,
                                min_confidence: float = 0.5) -> bool:
        """Sinyal üretilmeli mi? Öğrenme verileriyle karar ver"""
        if confidence < min_confidence:
            return False

        # Geçmiş benzer sinyallerin başarı oranını kontrol et
        for sig_type, stats in self.signal_type_scores.items():
            if direction in sig_type and stats['total'] > 5:
                accuracy = stats['correct'] / stats['total']
                if accuracy < 0.3:
                    logger.debug(f"Brain: Suppressing {direction} signal for {symbol} "
                                  f"(historical accuracy {accuracy:.1%})")
                    return False
        return True

    def get_brain_status(self) -> Dict[str, Any]:
        """Brain durumunu döndür"""
        status = {
            'total_patterns': len(self.pattern_memory),
            'accuracy': self.get_accuracy(),
            'prediction_stats': dict(self.prediction_accuracy),
            'signal_type_scores': {k: {
                'accuracy': v['correct'] / max(v['total'], 1),
                'total': v['total'],
                'avg_pnl': v['total_pnl'] / max(v['total'], 1),
            } for k, v in self.signal_type_scores.items()},
            'last_learning_update': self.last_learning_update.isoformat() if self.last_learning_update else None,
            'learning_weights': self.learning_weights,
        }
        # Intelligence Core durumu
        if self.intelligence_core:
            status['intelligence_core'] = self.intelligence_core.get_status()
        # Kalibrasyon durumu
        status['calibration_count'] = len(self._calibration_log)
        if self._calibration_log:
            status['last_calibration'] = self._calibration_log[-1].get('timestamp')
        # Pattern match stats
        status['pattern_match'] = self.get_pattern_match_stats()
        return status

    def enhanced_analyze(self, snapshot, indicators: Optional[Dict] = None) -> Optional[Dict[str, Any]]:
        """
        Intelligence Core ile gelişmiş analiz.
        Core yoksa None döner (degraded mode — mevcut brain yoluna fall back).
        """
        if not self.intelligence_core:
            return None
        try:
            return self.intelligence_core.analyze(snapshot, indicators)
        except Exception as e:
            logger.debug(f"Enhanced analyze error: {e}")
            return None

    def record_enriched_pattern(self, snapshot, indicators: Optional[Dict] = None):
        """Intelligence Core pattern library'sine yeni pattern ekle."""
        if self.intelligence_core:
            try:
                self.intelligence_core.record_pattern(snapshot, indicators)
            except Exception as e:
                logger.debug(f"Record enriched pattern error: {e}")

    async def refresh_patterns(self):
        """Yeni pattern'ları DB'den memory'ye yükle"""
        try:
            current_count = len(self.pattern_memory)
            patterns = await self.db.get_pattern_records(limit=500)
            if len(patterns) > current_count:
                new_records = []
                for p in patterns[:(len(patterns) - current_count)]:
                    snap_data = p.get('snapshot_data', {})
                    if not snap_data:
                        continue
                    snap = TradeSnapshot(
                        symbol=snap_data.get('symbol', ''),
                        exchange=snap_data.get('exchange', ''),
                        market_type=snap_data.get('market_type', 'spot'),
                        start_time=datetime.fromisoformat(snap_data['start_time']) if snap_data.get('start_time') else datetime.utcnow(),
                        end_time=datetime.fromisoformat(snap_data['end_time']) if snap_data.get('end_time') else datetime.utcnow(),
                        buy_count=snap_data.get('buy_count', 0),
                        sell_count=snap_data.get('sell_count', 0),
                        buy_volume=snap_data.get('buy_volume', 0),
                        sell_volume=snap_data.get('sell_volume', 0),
                        avg_price=snap_data.get('avg_price', 0),
                        price_start=snap_data.get('price_start', 0),
                        price_end=snap_data.get('price_end', 0),
                        high=snap_data.get('high', 0),
                        low=snap_data.get('low', 0),
                    )
                    record = PatternRecord(
                        snapshot=snap,
                        outcome_15m=p.get('outcome_15m'),
                        outcome_1h=p.get('outcome_1h'),
                        outcome_4h=p.get('outcome_4h'),
                        outcome_1d=p.get('outcome_1d'),
                    )
                    new_records.append(record)
                if new_records:
                    self.pattern_memory.extend(new_records)
                    # Bellek sınırı: eski pattern'ları at
                    if len(self.pattern_memory) > self.MAX_PATTERN_MEMORY:
                        self.pattern_memory = self.pattern_memory[-self.MAX_PATTERN_MEMORY:]
                    # Intelligence Core'a da ekle
                    if self.intelligence_core:
                        for rec in new_records:
                            base_vec = rec.snapshot.to_vector()
                            import numpy as _np
                            enriched = _np.concatenate([base_vec, _np.zeros(12)])
                            self.intelligence_core.pattern_library.add_pattern(rec, enriched)
                    logger.info(f"🧠 Brain: Loaded {len(new_records)} new patterns (total: {len(self.pattern_memory)})")
        except Exception as e:
            logger.debug(f"Brain refresh error: {e}")

    def get_learning_context(self) -> str:
        """
        LLM promptlarına enjekte edilecek öğrenme özeti.
        Her karar çağrısında Brain'in birikimli bilgisi modele beslenir.
        """
        lines = []
        accuracy = self.get_accuracy()
        total = self.prediction_accuracy['total']
        lines.append(f"BRAIN OGRENME DURUMU: Toplam {total} tahmin, dogruluk {accuracy:.1%}")
        lines.append(f"Pattern hafiza: {len(self.pattern_memory)} kayit")

        # Sinyal tipi performansları
        if self.signal_type_scores:
            lines.append("SINYAL TIPI PERFORMANSLARI:")
            for sig_type, stats in sorted(self.signal_type_scores.items(),
                                           key=lambda x: x[1]['total'], reverse=True)[:8]:
                if stats['total'] < 2:
                    continue
                acc = stats['correct'] / stats['total']
                avg_pnl = stats['total_pnl'] / stats['total']
                lines.append(f"  {sig_type}: {acc:.0%} dogruluk ({stats['total']}x), ort PnL {avg_pnl:+.2f}%")

        # Ağırlıklar
        w = self.learning_weights
        lines.append(f"AGIRLIKLAR: similarity={w['similarity']:.2f} volume={w['volume_match']:.2f} "
                     f"direction={w['direction_match']:.2f} confidence={w['confidence_history']:.2f}")

        # Son kalibrasyon
        if self._calibration_log:
            last = self._calibration_log[-1]
            actions = last.get('actions', [])
            if actions:
                lines.append(f"SON KALIBRASYON ({last.get('timestamp', '?')[:16]}):")
                for a in actions[:3]:
                    lines.append(f"  {a.get('type')}: {a.get('reason', a.get('signal_type', ''))}")

        return "\n".join(lines)


class ChatHandler:
    """Bot ile sohbet sistemi"""

    def __init__(self, db, brain: BrainModule):
        self.db = db
        self.brain = brain
        self.agents = {}

    def register_agent(self, name: str, agent):
        self.agents[name] = agent

    async def process_message(self, message: str) -> str:
        """Kullanıcı mesajını işle ve yanıt döndür"""
        msg = message.strip().lower()
        try:
            # Durum sorguları
            if any(w in msg for w in ['durum', 'status', 'nasıl', 'naber']):
                return await self._get_system_status()

            # Fiyat sorguları
            if any(w in msg for w in ['fiyat', 'price', 'kaç']):
                return await self._get_price_info(msg)

            # Sinyal sorguları
            if any(w in msg for w in ['sinyal', 'signal']):
                return await self._get_signal_info()

            # Simülasyon sorguları
            if any(w in msg for w in ['simülasyon', 'simulation', 'sim', 'trade', 'pozisyon']):
                return await self._get_sim_info()

            # Brain / öğrenme sorguları
            if any(w in msg for w in ['öğren', 'learn', 'brain', 'beyin', 'zeka', 'akıl']):
                return await self._get_brain_info()

            # Order flow
            if any(w in msg for w in ['order flow', 'akış', 'alış satış', 'baskı']):
                return await self._get_flow_info(msg)

            # Watchlist
            if any(w in msg for w in ['watchlist', 'izleme', 'liste', 'coin']):
                return await self._get_watchlist_info()

            # Agent health
            if any(w in msg for w in ['agent', 'bot', 'sağlık', 'health']):
                return await self._get_agent_health()

            # Performans
            if any(w in msg for w in ['performans', 'başarı', 'accuracy', 'doğruluk']):
                return await self._get_performance_info()

            # Default
            return (
                "🤖 QuenBot AI ile konuşuyorsunuz. Şu komutları deneyebilirsiniz:\n\n"
                "• **durum** - Sistem genel durumu\n"
                "• **fiyat BTC** - Anlık fiyat bilgisi\n"
                "• **sinyal** - Aktif sinyaller\n"
                "• **simülasyon** - Açık pozisyonlar\n"
                "• **beyin** - AI öğrenme durumu\n"
                "• **order flow** - Alış/satış baskısı\n"
                "• **performans** - Bot başarı oranı\n"
                "• **watchlist** - İzleme listesi\n"
                "• **agent** - Bot sağlık durumları\n"
            )

        except Exception as e:
            return f"⚠ Bir hata oluştu: {str(e)}"

    async def _get_system_status(self) -> str:
        try:
            summary = await self.db.get_dashboard_summary()
            brain_status = self.brain.get_brain_status()
            return (
                f"📊 **QuenBot Sistem Durumu**\n\n"
                f"• Toplam Trade: **{summary['total_trades']:,}**\n"
                f"• Aktif Sinyal: **{summary['active_signals']}**\n"
                f"• Açık Simülasyon: **{summary['open_simulations']}**\n"
                f"• Toplam PnL: **${summary['total_pnl']:.2f}**\n"
                f"• Öğrenilen Pattern: **{brain_status['total_patterns']}**\n"
                f"• AI Doğruluk: **{brain_status['accuracy']:.1%}**\n"
                f"• Tüm botlar çalışıyor ✅"
            )
        except Exception as e:
            return f"⚠ Durum alınamadı: {e}"

    async def _get_price_info(self, msg: str) -> str:
        try:
            symbols = ['BTCUSDT', 'ETHUSDT', 'BNBUSDT', 'SOLUSDT', 'ADAUSDT',
                        'DOTUSDT', 'LINKUSDT', 'LTCUSDT', 'XRPUSDT', 'BCHUSDT']
            target_symbol = None
            for sym in symbols:
                short = sym.replace('USDT', '').lower()
                if short in msg:
                    target_symbol = sym
                    break

            if target_symbol:
                trades = await self.db.get_recent_trades(target_symbol, limit=1)
                if trades:
                    price = float(trades[0]['price'])
                    return f"💰 **{target_symbol}**: ${price:,.2f} ({trades[0]['exchange']})"
                return f"❌ {target_symbol} için veri bulunamadı."

            # Tüm fiyatları göster
            lines = ["💰 **Güncel Fiyatlar**\n"]
            for sym in symbols:
                trades = await self.db.get_recent_trades(sym, limit=1)
                if trades:
                    lines.append(f"• {sym}: **${float(trades[0]['price']):,.2f}**")
            return "\n".join(lines)
        except Exception as e:
            return f"⚠ Fiyat bilgisi alınamadı: {e}"

    async def _get_signal_info(self) -> str:
        try:
            signals = await self.db.get_pending_signals()
            if not signals:
                return "📡 Şu an aktif sinyal bulunmuyor. Stratejist bot yeni pattern'lar arıyor..."
            lines = [f"📡 **{len(signals)} Aktif Sinyal**\n"]
            for s in signals[:5]:
                conf = float(s.get('confidence', 0)) * 100
                lines.append(f"• {s['symbol']} | {s['signal_type']} | Güven: %{conf:.0f} | ${float(s['price']):,.2f}")
            return "\n".join(lines)
        except Exception as e:
            return f"⚠ Sinyal bilgisi alınamadı: {e}"

    async def _get_sim_info(self) -> str:
        try:
            sims = await self.db.get_open_simulations()
            if not sims:
                return "👻 Şu an açık simülasyon yok. Ghost bot sinyal bekliyor..."
            lines = [f"👻 **{len(sims)} Açık Simülasyon**\n"]
            for s in sims[:5]:
                entry = float(s.get('entry_price', 0))
                lines.append(f"• {s['symbol']} | {s['side']} | Giriş: ${entry:,.2f}")
            return "\n".join(lines)
        except Exception as e:
            return f"⚠ Simülasyon bilgisi alınamadı: {e}"

    async def _get_brain_info(self) -> str:
        status = self.brain.get_brain_status()
        lines = [
            "🧠 **AI Beyin Durumu**\n",
            f"• Öğrenilen Pattern: **{status['total_patterns']}**",
            f"• Tahmin Doğruluğu: **{status['accuracy']:.1%}**",
            f"• Toplam Tahmin: **{status['prediction_stats']['total']}**",
            f"• Doğru Tahmin: **{status['prediction_stats']['correct']}**",
        ]
        if status['signal_type_scores']:
            lines.append("\n**Sinyal Tipi Başarıları:**")
            for sig_type, scores in status['signal_type_scores'].items():
                lines.append(f"  • {sig_type}: %{scores['accuracy']*100:.0f} ({scores['total']} sinyal)")
        return "\n".join(lines)

    async def _get_flow_info(self, msg: str) -> str:
        try:
            from config import Config
            lines = ["⚡ **Order Flow (Son 30dk)**\n"]
            for sym in Config.WATCHLIST[:5]:
                trades = await self.db.get_recent_trades(sym, limit=200, market_type='spot')
                if not trades:
                    continue
                recent = [t for t in trades if (datetime.utcnow() - t['timestamp']).seconds < 1800]
                if not recent:
                    continue
                buy_vol = sum(float(t['quantity']) * float(t['price']) for t in recent if t['side'] == 'buy')
                sell_vol = sum(float(t['quantity']) * float(t['price']) for t in recent if t['side'] == 'sell')
                total = buy_vol + sell_vol
                if total > 0:
                    ratio = buy_vol / total * 100
                    pressure = "🟢 Alış" if ratio > 55 else "🔴 Satış" if ratio < 45 else "⚪ Dengeli"
                    lines.append(f"• {sym}: {pressure} ({ratio:.0f}% alış)")
            return "\n".join(lines)
        except Exception as e:
            return f"⚠ Order flow alınamadı: {e}"

    async def _get_watchlist_info(self) -> str:
        from config import Config
        watchlist = await self.db.get_watchlist()
        if watchlist:
            lines = ["📋 **İzleme Listesi (DB)**\n"]
            for w in watchlist:
                lines.append(f"• {w['symbol']} ({w['market_type']})")
        else:
            lines = ["📋 **İzleme Listesi (Config)**\n"]
            for sym in Config.WATCHLIST:
                lines.append(f"• {sym} (spot + futures)")
        return "\n".join(lines)

    async def _get_agent_health(self) -> str:
        lines = ["🤖 **Agent Durumları**\n"]
        for name, agent in self.agents.items():
            try:
                health = await agent.health_check()
                status = "✅" if health.get('healthy', True) else "❌"
                lines.append(f"• {name}: {status}")
            except:
                lines.append(f"• {name}: ❓ Bilinmiyor")
        return "\n".join(lines)

    async def _get_performance_info(self) -> str:
        try:
            closed = await self.db.get_closed_simulations(limit=100)
            if not closed:
                return "📈 Henüz kapatılmış simülasyon yok. Performans verisi birikmesi bekleniyor..."
            wins = [s for s in closed if float(s.get('pnl', 0)) > 0]
            losses = [s for s in closed if float(s.get('pnl', 0)) <= 0]
            win_rate = len(wins) / len(closed) * 100
            avg_pnl = sum(float(s.get('pnl_pct', 0)) for s in closed) / len(closed)
            return (
                f"📈 **Bot Performansı**\n\n"
                f"• Toplam: **{len(closed)}** simülasyon\n"
                f"• Kazanç: **{len(wins)}** ✅\n"
                f"• Kayıp: **{len(losses)}** ❌\n"
                f"• Win Rate: **{win_rate:.1f}%**\n"
                f"• Ort. PnL: **{avg_pnl:.2f}%**"
            )
        except Exception as e:
            return f"⚠ Performans bilgisi alınamadı: {e}"
