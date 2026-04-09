"""
Intelligence Core - QuenBot AI Zeka Çekirdeği
===============================================
Feature Engineering + Pattern Library + Inference Engine + Market Regime Detection

Mevcut BrainModule'ün üstüne eklenen gelişmiş zeka katmanı.
Hiçbir mevcut fonksiyonu değiştirmez, sadece YENİ yetenek ekler.

Bileşenler:
  1. FeatureEngine — 6-dim base vektörü → 18-dim zenginleştirilmiş vektöre çevirir
  2. MarketRegimeDetector — Piyasa rejimi tespiti (trending, ranging, volatile, quiet)
  3. PatternLibrary — Hybrid similarity (DTW+FFT+Cosine) ile gelişmiş pattern araması
  4. InferenceEngine — Multi-timeframe agreement + regime adjustment + calibration
  5. IntelligenceCore — Hepsini birleştiren ana sınıf
"""
import logging
import math
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# 1. FEATURE ENGINE — Raw Data → Rich Mathematical Vectors
# ═══════════════════════════════════════════════════════════

class FeatureEngine:
    """
    TradeSnapshot (6-dim) + Technical Indicators → 18-dim enriched vector.
    
    Vektör yapısı:
    [0-5]   Base: price_change_pct, buy_ratio, volatility, log_volume, log_trades, buy_sell_ratio
    [6]     RSI normalized (0-1)
    [7]     MACD histogram (tanh normalized, -1 to 1)
    [8]     MACD crossover signal (-1, 0, 1)
    [9]     Bollinger %B (0-1)
    [10]    Bollinger squeeze flag (0 or 1)
    [11]    ATR ratio (volatility measure)
    [12]    OBV trend direction (-1 or 1)
    [13]    VWAP deviation (normalized)
    [14]    Overall trend strength (0-1)
    [15]    Trend direction (-1, 0, 1)
    [16]    Trade intensity (log trades per minute)
    [17]    Buy pressure momentum (-1 to 1)
    """

    ENRICHED_DIM = 18
    BASE_DIM = 6
    TECH_DIM = 12

    @staticmethod
    def build_enriched_vector(snapshot, indicators: Optional[Dict] = None) -> np.ndarray:
        """
        Base snapshot vektörü + teknik indikatörler → 18-dim zenginleştirilmiş vektör.
        indicators yoksa teknik kısım sıfır olur (backward-compatible).
        """
        base = snapshot.to_vector()  # 6-dim

        if indicators and isinstance(indicators, dict):
            # RSI normalized (0-100 → 0-1)
            rsi = indicators.get('rsi')
            rsi_norm = (rsi / 100.0) if rsi is not None else 0.5

            # MACD histogram (tanh ile -1,1 arası normalize)
            macd_data = indicators.get('macd')
            macd_hist = 0.0
            macd_cross = 0.0
            if macd_data and isinstance(macd_data, dict):
                macd_hist = float(np.tanh(macd_data.get('histogram', 0) * 100))
                crossover = macd_data.get('crossover', '')
                if crossover == 'bullish':
                    macd_cross = 1.0
                elif crossover == 'bearish':
                    macd_cross = -1.0

            # Bollinger Bands
            bb = indicators.get('bollinger')
            bb_pctb = 0.5
            bb_squeeze = 0.0
            if bb and isinstance(bb, dict):
                bb_pctb = float(bb.get('pct_b', 0.5))
                bb_squeeze = 1.0 if bb.get('squeeze') else 0.0

            # ATR ratio
            atr_ratio = float(indicators.get('atr_ratio') or 0.02)

            # OBV trend
            obv_data = indicators.get('obv')
            obv_trend = 0.0
            if obv_data and isinstance(obv_data, dict):
                obv_trend = 1.0 if obv_data.get('trend') == 'bullish' else -1.0

            # VWAP deviation
            vwap_val = indicators.get('vwap')
            vwap_dev = 0.0
            if vwap_val and snapshot.avg_price > 0:
                vwap_dev = float(np.tanh(
                    (snapshot.avg_price - vwap_val) / max(snapshot.avg_price, 1e-8) * 50
                ))

            # Trend summary
            trend = indicators.get('trend_summary', {})
            if not isinstance(trend, dict):
                trend = {}
            trend_strength = float(trend.get('strength', 0))
            trend_dir_str = trend.get('trend', 'neutral')
            if trend_dir_str == 'bullish':
                trend_dir = 1.0
            elif trend_dir_str == 'bearish':
                trend_dir = -1.0
            else:
                trend_dir = 0.0

            # Trade intensity (trades per minute, log-scaled)
            duration_sec = (snapshot.end_time - snapshot.start_time).total_seconds()
            duration_min = max(duration_sec / 60.0, 1.0)
            trade_intensity = math.log1p(snapshot.total_trades / duration_min)

            # Buy pressure momentum (-1 to 1)
            total_vol = max(snapshot.total_volume, 1e-8)
            buy_pressure = (snapshot.buy_volume - snapshot.sell_volume) / total_vol

            enriched = np.array([
                # Base (6)
                base[0], base[1], base[2], base[3], base[4], base[5],
                # Technical (12)
                rsi_norm,           # [6]  RSI
                macd_hist,          # [7]  MACD histogram
                macd_cross,         # [8]  MACD crossover
                bb_pctb,            # [9]  Bollinger %B
                bb_squeeze,         # [10] Bollinger squeeze
                atr_ratio,          # [11] ATR ratio
                obv_trend,          # [12] OBV trend
                vwap_dev,           # [13] VWAP deviation
                trend_strength,     # [14] Trend strength
                trend_dir,          # [15] Trend direction
                trade_intensity,    # [16] Trade intensity
                buy_pressure,       # [17] Buy pressure
            ], dtype=np.float64)

            return enriched

        # Fallback: base + sıfır teknik kısım
        return np.concatenate([base, np.zeros(FeatureEngine.TECH_DIM)])

    @staticmethod
    def normalize_vector(vec: np.ndarray) -> np.ndarray:
        """Z-score normalization for comparison."""
        std = np.std(vec)
        if std < 1e-10:
            return np.zeros_like(vec)
        return (vec - np.mean(vec)) / std


# ═══════════════════════════════════════════════════════════
# 2. MARKET REGIME DETECTOR
# ═══════════════════════════════════════════════════════════

class MarketRegimeDetector:
    """
    Indikatörler + snapshot'tan piyasa rejimini tespit eder.
    
    Rejimler:
      TRENDING_UP   — Güçlü yükseliş trendi
      TRENDING_DOWN — Güçlü düşüş trendi
      RANGING       — Konsolidasyon / yatay hareket
      VOLATILE      — Yüksek volatilite, belirsiz yön
      QUIET         — Düşük volatilite, düşük hacim
    """

    REGIMES = ('TRENDING_UP', 'TRENDING_DOWN', 'RANGING', 'VOLATILE', 'QUIET', 'UNKNOWN')

    @staticmethod
    def detect(indicators: Optional[Dict], snapshot=None) -> Dict[str, Any]:
        """Rejim tespiti. Returns {'regime': str, 'confidence': float}"""
        if not indicators or not isinstance(indicators, dict):
            return {'regime': 'UNKNOWN', 'confidence': 0.0}

        rsi = indicators.get('rsi')
        atr_ratio = float(indicators.get('atr_ratio') or 0.02)

        trend = indicators.get('trend_summary', {})
        if not isinstance(trend, dict):
            trend = {}
        trend_strength = float(trend.get('strength', 0))
        trend_dir = trend.get('trend', 'neutral')

        bb = indicators.get('bollinger', {})
        if not isinstance(bb, dict):
            bb = {}
        bb_bandwidth = float(bb.get('bandwidth', 0.04))

        volatility = snapshot.volatility if snapshot else 0.0

        # Scoring hiyerarşisi
        if trend_strength > 0.5 and trend_dir == 'bullish':
            return {'regime': 'TRENDING_UP', 'confidence': min(trend_strength, 0.95)}

        if trend_strength > 0.5 and trend_dir == 'bearish':
            return {'regime': 'TRENDING_DOWN', 'confidence': min(trend_strength, 0.95)}

        if atr_ratio > 0.04 and trend_strength < 0.3:
            conf = min(atr_ratio * 10, 0.9)
            return {'regime': 'VOLATILE', 'confidence': conf}

        if bb_bandwidth < 0.02 and volatility < 0.01:
            return {'regime': 'QUIET', 'confidence': 0.7}

        return {'regime': 'RANGING', 'confidence': max(1.0 - trend_strength, 0.4)}


# ═══════════════════════════════════════════════════════════
# 3. PATTERN LIBRARY — Enhanced storage + hybrid retrieval
# ═══════════════════════════════════════════════════════════

class PatternLibrary:
    """
    Enriched vektörlerle pattern deposu.
    - Küçük setlerde hybrid similarity (DTW+FFT+Cosine)
    - Büyük setlerde fast batch cosine
    """

    def __init__(self, max_patterns: int = 2000):
        self.max_patterns = max_patterns
        self.patterns: List[Tuple[Any, np.ndarray]] = []  # (PatternRecord, enriched_vector)
        self._cache_dirty = True

    @property
    def count(self) -> int:
        return len(self.patterns)

    def add_pattern(self, pattern_record, enriched_vector: np.ndarray):
        """Pattern + enriched vector ekle."""
        self.patterns.append((pattern_record, enriched_vector))
        self._cache_dirty = True

        # Kapasite aşılırsa en eskilerden at
        if len(self.patterns) > self.max_patterns:
            self.patterns = self.patterns[-self.max_patterns:]
            self._cache_dirty = True

    def find_similar(self, current_vector: np.ndarray,
                     symbol: Optional[str] = None,
                     min_similarity: float = 0.5,
                     top_k: int = 15,
                     use_hybrid: bool = True) -> List[Tuple[Any, float]]:
        """
        Hybrid veya cosine similarity ile en benzer pattern'ları bul.
        """
        if not self.patterns:
            return []

        # Symbol filtreleme (yetersizse tümünü kullan)
        if symbol:
            candidates = [(p, v) for p, v in self.patterns
                          if p.snapshot.symbol == symbol]
            if len(candidates) < 5:
                candidates = self.patterns  # referans, kopya değil
        else:
            candidates = self.patterns  # referans, kopya değil

        if not candidates:
            return []

        results = []
        vec_len = len(current_vector)

        if use_hybrid and len(candidates) <= 300:
            # Hybrid similarity — daha doğru ama yavaş
            from similarity_engine import hybrid_similarity
            for pattern, vec in candidates:
                if len(vec) != vec_len:
                    continue
                sim = hybrid_similarity(current_vector, vec)
                if sim >= min_similarity:
                    results.append((pattern, float(sim)))
        else:
            # Fast batch cosine — büyük setler için
            cand_vecs = np.array([v for _, v in candidates if len(v) == vec_len])
            cand_patterns = [p for p, v in candidates if len(v) == vec_len]

            if len(cand_vecs) == 0:
                return []

            from sklearn.metrics.pairwise import cosine_similarity as sk_cos
            current_r = current_vector.reshape(1, -1)
            norms_c = np.linalg.norm(current_r, axis=1, keepdims=True)
            norms_h = np.linalg.norm(cand_vecs, axis=1, keepdims=True)
            current_normed = current_r / np.maximum(norms_c, 1e-8)
            hist_normed = cand_vecs / np.maximum(norms_h, 1e-8)
            sims = sk_cos(current_normed, hist_normed)[0]

            for j, sim in enumerate(sims):
                if sim >= min_similarity:
                    results.append((cand_patterns[j], float(sim)))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]


# ═══════════════════════════════════════════════════════════
# 4. INFERENCE ENGINE — Probability / Signal üretimi
# ═══════════════════════════════════════════════════════════

class InferenceEngine:
    """
    Pattern eşleşmeleri + rejim + calibration → kalibre edilmiş tahmin.
    
    Pipeline:
    1. Pattern outcomes → ağırlıklı yön tahmini (similarity² weighting)
    2. Multi-timeframe agreement skoru
    3. Regime-based confidence çarpanı
    4. Tutarlılık (variance) bonusu/cezası
    5. Tarihsel signal_type performansı blend
    """

    REGIME_MULTIPLIERS = {
        'TRENDING_UP':   {'long': 1.2, 'short': 0.7},
        'TRENDING_DOWN': {'long': 0.7, 'short': 1.2},
        'RANGING':       {'long': 0.85, 'short': 0.85},
        'VOLATILE':      {'long': 0.6, 'short': 0.6},
        'QUIET':         {'long': 0.8, 'short': 0.8},
        'UNKNOWN':       {'long': 1.0, 'short': 1.0},
    }

    TIMEFRAMES = ('15m', '1h', '4h', '1d')

    def infer(self, matches: List[Tuple[Any, float]],
              regime: Dict[str, Any],
              signal_type_scores: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Tam inference pipeline → kalibre edilmiş tahmin döndür.
        """
        if not matches:
            return {
                'direction': None, 'confidence': 0.0,
                'regime': regime, 'timeframes': {},
                'match_count': 0,
            }

        # ── Step 1: Her timeframe için ağırlıklı outcome tahmini ──
        timeframe_predictions = {}
        for tf_key in self.TIMEFRAMES:
            outcomes = []
            weights = []
            for pattern, sim in matches:
                outcome = pattern.outcomes.get(tf_key)
                if outcome is not None:
                    outcomes.append(outcome)
                    weights.append(sim ** 2)  # Similarity² → güçlü matchlere daha çok ağırlık

            if outcomes:
                total_w = sum(weights)
                weighted_avg = sum(o * w for o, w in zip(outcomes, weights)) / max(total_w, 1e-8)
                variance = sum(w * (o - weighted_avg) ** 2 for o, w in zip(outcomes, weights)) / max(total_w, 1e-8)

                direction = 'long' if weighted_avg > 0 else 'short'
                # Tutarlılık: variance düşükse outcomes birbirine yakın → yüksek güven
                consistency = 1.0 - min(math.sqrt(variance) / max(abs(weighted_avg), 1e-8), 1.0)
                consistency = max(consistency, 0.0)

                timeframe_predictions[tf_key] = {
                    'direction': direction,
                    'avg_change_pct': weighted_avg,
                    'strength': abs(weighted_avg),
                    'consistency': consistency,
                    'sample_count': len(outcomes),
                    'variance': variance,
                }

        if not timeframe_predictions:
            return {
                'direction': None, 'confidence': 0.0,
                'regime': regime, 'timeframes': {},
                'match_count': len(matches),
            }

        # ── Step 2: Multi-timeframe agreement ──
        directions = [v['direction'] for v in timeframe_predictions.values()]
        long_count = directions.count('long')
        short_count = directions.count('short')
        primary_dir = 'long' if long_count >= short_count else 'short'
        tf_agreement = max(long_count, short_count) / len(directions)

        # ── Step 3: Similarity-weighted base confidence ──
        avg_sim = sum(s for _, s in matches) / len(matches)
        top3_sim = sum(s for _, s in matches[:3]) / min(len(matches), 3)
        sample_factor = min(len(matches) / 15.0, 1.0)

        base_confidence = (
            top3_sim * 0.35 +       # En iyi eşleşmelerin kalitesi
            avg_sim * 0.15 +         # Genel eşleşme kalitesi
            tf_agreement * 0.25 +    # Timeframe uyumu
            sample_factor * 0.25     # Örneklem büyüklüğü
        )

        # ── Step 4: Regime adjustment ──
        regime_name = regime.get('regime', 'UNKNOWN') if isinstance(regime, dict) else 'UNKNOWN'
        regime_mult = self.REGIME_MULTIPLIERS.get(regime_name, {}).get(primary_dir, 1.0)
        adjusted = base_confidence * regime_mult

        # ── Step 5: Consistency bonus/penalty ──
        consistencies = [v['consistency'] for v in timeframe_predictions.values()]
        avg_consistency = sum(consistencies) / len(consistencies)
        adjusted *= (0.7 + 0.3 * avg_consistency)  # 0.7x → 1.0x çarpan

        # ── Step 6: Historical signal_type calibration ──
        if signal_type_scores:
            for sig_type, stats in signal_type_scores.items():
                if primary_dir in sig_type and stats.get('total', 0) > 5:
                    accuracy = stats.get('correct', 0) / stats['total']
                    adjusted = adjusted * 0.7 + accuracy * 0.3
                    break  # İlk uygun sig_type yeterli

        # Clamp [0, 0.95]
        final_confidence = max(0.0, min(adjusted, 0.95))

        return {
            'direction': primary_dir,
            'confidence': final_confidence,
            'base_confidence': base_confidence,
            'regime': regime,
            'regime_multiplier': regime_mult,
            'tf_agreement': tf_agreement,
            'avg_similarity': avg_sim,
            'top3_similarity': top3_sim,
            'match_count': len(matches),
            'timeframes': timeframe_predictions,
            'avg_consistency': avg_consistency,
        }


# ═══════════════════════════════════════════════════════════
# 5. INTELLIGENCE CORE — Ana Sınıf
# ═══════════════════════════════════════════════════════════

class IntelligenceCore:
    """
    Merkezi Zeka Çekirdeği — tüm bileşenleri birleştirir.
    BrainModule'e drop-in enhancement olarak eklenir.
    
    Usage:
        core = IntelligenceCore(brain_module)
        await core.initialize()
        result = core.analyze(snapshot, indicators)
    """

    VERSION = '1.0'

    def __init__(self, brain_module):
        self.brain = brain_module
        self.feature_engine = FeatureEngine()
        self.regime_detector = MarketRegimeDetector()
        self.inference_engine = InferenceEngine()
        self.pattern_library = PatternLibrary(max_patterns=2000)
        self._initialized = False

    async def initialize(self):
        """Mevcut Brain pattern'larını enriched library'ye yükle."""
        loaded = 0
        if self.brain and self.brain.pattern_memory:
            for record in self.brain.pattern_memory:
                base_vec = record.snapshot.to_vector()
                # Tarihi pattern'lar için indikatör yok → teknik kısım sıfır
                enriched = np.concatenate([base_vec, np.zeros(FeatureEngine.TECH_DIM)])
                self.pattern_library.add_pattern(record, enriched)
                loaded += 1

        self._initialized = True
        logger.info(f"🧠 Intelligence Core v{self.VERSION} initialized: "
                     f"{loaded} patterns loaded into enriched library")

    def analyze(self, snapshot, indicators: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Tam zeka pipeline'ı:
          1. FeatureEngine → 18-dim enriched vector
          2. MarketRegimeDetector → rejim tespiti
          3. PatternLibrary.find_similar → hybrid match
          4. InferenceEngine.infer → kalibre edilmiş tahmin
        
        Returns dict with direction, confidence, regime, timeframes, etc.
        """
        # Step 1: Enriched feature vector
        enriched_vec = self.feature_engine.build_enriched_vector(snapshot, indicators)

        # Step 2: Regime detection
        regime = self.regime_detector.detect(indicators, snapshot)

        # Step 3: Pattern matching (hybrid for small sets, cosine for large)
        use_hybrid = self.pattern_library.count <= 500
        matches = self.pattern_library.find_similar(
            enriched_vec,
            symbol=snapshot.symbol,
            min_similarity=0.3,
            top_k=15,
            use_hybrid=use_hybrid,
        )

        # Step 4: Inference
        prediction = self.inference_engine.infer(
            matches,
            regime,
            signal_type_scores=self.brain.signal_type_scores if self.brain else None,
        )

        # Ek meta bilgiler
        prediction['enriched_dim'] = len(enriched_vec)
        prediction['regime'] = regime
        prediction['intelligence_version'] = self.VERSION
        prediction['pattern_library_size'] = self.pattern_library.count

        return prediction

    def record_pattern(self, snapshot, indicators: Optional[Dict] = None):
        """Yeni pattern'ı enriched vektörüyle birlikte kaydet."""
        from brain import PatternRecord
        enriched_vec = self.feature_engine.build_enriched_vector(snapshot, indicators)
        record = PatternRecord(snapshot=snapshot)
        self.pattern_library.add_pattern(record, enriched_vec)
        return record

    def get_status(self) -> Dict[str, Any]:
        """Intelligence Core durumu."""
        return {
            'initialized': self._initialized,
            'version': self.VERSION,
            'pattern_library_size': self.pattern_library.count,
            'max_patterns': self.pattern_library.max_patterns,
            'feature_dim': FeatureEngine.ENRICHED_DIM,
        }
