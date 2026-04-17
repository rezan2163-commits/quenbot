"""
SuperGemma Decision Core — Katman 3: Karar Verme Merkezi
=========================================================
Tüm ajanlardan gelen girdileri sentezleyerek SuperGemma-26B
karar motoruna (GGUF/llama.cpp) gönderir ve nihai stratejik kararı alır.

MİMARİ KONUM: Decision Core (Katman 3)
- INPUT  ← PatternMatcher skoru, Scout verileri, Strategist önerileri,
            RiskManager durumu, StateTracker modu, Brain öğrenme verileri
- OUTPUT → Onay/Red kararı, yön, güven, eylem talimatı

FELSEFE: Ajanlar ÖNERI sunar, SuperGemma NİHAİ KARAR verir.
MODEL: SuperGemma-26B (gemma-2-27b-it Q4_K_M GGUF) — llama-cpp-python
TETİKLEME: Sadece Similarity_Score ≥ %60 olduğunda çağrılır.
"""
import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple

from event_bus import Event, EventType, get_event_bus
from qwen_models import (
    CommandAction,
    CommunicationLogEntry,
    DecisionCommand,
    DecisionEnvelope,
    ErrorObservation,
    ExecutionFeedback,
    LearningExperience,
)
from vector_memory import get_vector_store
from market_activity_tracker import get_market_tracker
from systematic_trade_detector import get_systematic_detector, SystematicActivityReport

logger = logging.getLogger(__name__)

# Lazy LLM imports
_llm_client = None
_llm_bridge = None

def _get_llm_client():
    global _llm_client
    if _llm_client is None:
        from llm_client import get_llm_client
        _llm_client = get_llm_client()
    return _llm_client

def _get_llm_bridge():
    global _llm_bridge
    if _llm_bridge is None:
        from llm_bridge import get_llm_bridge
        _llm_bridge = get_llm_bridge()
    return _llm_bridge


# ─── SuperGemma Decision Prompt Templates ───

DECISION_SYSTEM_PROMPT = """Sen QuenBot trading sisteminin merkezi karar verme motorusun (SuperGemma Decision Core).

GÖREV: Ajanlardan gelen tüm verileri sentezleyerek nihai stratejik karar ver.

KURALLAR:
1. Tüm ajan raporlarını dikkatlice analiz et
2. Pattern eşleşme skoru, piyasa rejimi, risk durumu, öğrenme geçmişini dengele
3. MAMIS mikro-yapı sinyali varsa OFI, VPIN, CVD ve pattern sınıflandırmasını kararın içine kat
3. Kararını JSON formatında ver
4. Onaylamadığın sinyalleri net gerekçeyle reddet
5. Güven seviyeni %0-100 olarak belirt
6. Türkçe gerekçe yaz

CEVAP FORMATI (sadece JSON):
{
    "decision": "APPROVE" veya "REJECT",
    "direction": "long" veya "short" veya "neutral",
    "confidence": 0.0-1.0,
    "magnitude": beklenen hareket yüzdesi (ör: 0.03),
    "reasoning": "Türkçe gerekçe",
    "risk_level": "low" veya "medium" veya "high",
    "action": "eylem talimatı (ör: LONG pozisyon aç)",
    "priority": "critical" veya "high" veya "normal" veya "low"
}"""

SYNTHESIS_PROMPT_TEMPLATE = """## KARAR İSTEĞİ — {symbol} ({timeframe})

### 📊 PATTERN MATCH VERİSİ
- Euclidean Benzerlik: {similarity:.4f}
- Eşleşme Sayısı: {match_count}
- Tahmin Yönü: {predicted_direction}
- Tahmin Büyüklüğü: {predicted_magnitude:+.2%}
- Geçmiş Eşleşme Sonucu: {matched_change:+.2%}

### 📈 PİYASA DURUMU
- Mevcut Fiyat: ${current_price:,.2f}
- Piyasa Rejimi: {market_regime}
- Volatilite: {volatility}
- Trend Gücü: {trend_strength}

### 🧠 BRAIN ÖĞRENMESİ
- Genel Doğruluk: {brain_accuracy:.1%}
- Bu Yön ({predicted_direction}) Başarı Oranı: {direction_accuracy:.1%}
- Toplam Pattern: {total_patterns}
- Son Kalibrasyon: {last_calibration}

### 🛡 RİSK DURUMU
- Bot Modu: {bot_mode}
- Günlük PnL: {daily_pnl:+.2f}%
- Drawdown: {drawdown:.2f}%
- Açık Pozisyon: {open_positions}/{max_positions}
- Ardışık Kayıp: {consecutive_losses}

### 📡 SCOUT VERİSİ
- Alış/Satış Oranı: {buy_ratio:.1%}
- Son 1s Trade Sayısı: {recent_trade_count}
- Hacim Değişimi: {volume_change:+.1%}

### 💡 STRATEGIST ÖNERİSİ
- Öneri: {strategist_recommendation}
- Momentum Skoru: {momentum_score:.2f}

### 🔬 MAMIS MİKRO-YAPI
- Bias: {mamis_bias}
- Güven: {mamis_confidence:.1%}
- Pattern: {mamis_pattern}
- Tahmini Volatilite: {mamis_volatility:.5f}

### 📚 ÖĞRENME BİRİKİMİ
{learning_context}

### 🔍 İMZA EŞLEŞMESİ (Signature Match)
- İmza Eşleşme Sayısı: {signature_match_count}
- En Yüksek Benzerlik: {signature_top_similarity:.1%}
- İmza Yönü: {signature_direction}
{signature_provenance}

### ⚡ PİYASA AKTİVİTE
- Piyasa Modu: {market_mode}
- Aktif Sembol Sayısı: {active_symbol_count}

### 🤖 SİSTEMATİK TİCARET ANALİZİ (Bot Detection)
- Sistematik Trade Oranı: {systematic_trade_ratio:.1%}
- Dominant Bot Tipi: {dominant_bot_type}
- Bot Yön Tahmini: {bot_predicted_direction}
- Bot Yön Güveni: {bot_direction_confidence:.1%}
- Akümülasyon Skoru: {accumulation_score:+.2f} (pozitif=birikim, negatif=dağıtım)
- Smart Money Flow: {smart_money_flow:+.2f}
- Kurumsal vs Retail: {retail_vs_institutional:.1%} (yüksek=kurumsal)
- Tahmini Fiyat Etkisi: {estimated_price_impact_bps:.1f} bps
{bot_signatures_summary}
{confluence_block}
---
Tüm verileri (özellikle imza eşleşmesi ve sistematik ticaret analizini) sentezle ve nihai kararını JSON formatında ver.
Bot tespit verilerini kullanarak "büyük oyuncular ne yapıyor" sorusuna yanıt ver.
Kararında "neden girdik" sorusuna imza eşleşmesi ve bot aktivite verilerinden yanıt ver."""


class AdaptiveEngine:
    """
    Dinamik Eşik & Öğrenme Motoru
    ==============================
    Son N işlemin başarı oranına (Win Rate) göre karar parametrelerini
    yumuşak geçişlerle (learning rate) ayarlar.

    Üç ana mekanizma:
    1. Dynamic Thresholding — similarity/confidence eşiklerini Win Rate'e göre ayarla
    2. Experience-Augmented Decision — RAG ile geçmiş benzer deneyimleri çekerek güven ayarla
    3. Feedback Loop — Her işlem sonucunda iç parametreleri güncelle
    """

    # Öğrenme oranı — parametre değişimleri bu oranda uygulanır (oscillation önleme)
    LEARNING_RATE = 0.05
    # Minimum gözlem sayısı — bu kadar veri olmadan adaptasyon yapma
    MIN_OBSERVATIONS = 10
    # Target win rate — sistem bu orana yakınsama hedefler
    TARGET_WIN_RATE = 0.55
    # Parametre sınırları — aşırı uçlara kaymayı önle
    BOUNDS = {
        'similarity_threshold': (0.45, 0.85),
        'confidence_weight_similarity': (0.20, 0.60),
        'confidence_weight_brain': (0.10, 0.40),
        'confidence_weight_mamis': (0.10, 0.35),
        'confidence_weight_direction': (0.05, 0.25),
        'magnitude_min': (0.005, 0.03),
        'risk_appetite': (0.3, 1.0),
        'sensitivity': (0.3, 1.0),
    }

    def __init__(self):
        # Dinamik parametreler — başlangıç değerleri mevcut sabitlerle uyumlu
        self._params: Dict[str, float] = {
            'similarity_threshold': 0.60,
            'confidence_weight_similarity': 0.40,
            'confidence_weight_brain': 0.25,
            'confidence_weight_mamis': 0.20,
            'confidence_weight_direction': 0.15,
            'magnitude_min': 0.01,
            'risk_appetite': 0.7,     # 1.0 = agresif, 0.3 = muhafazakâr
            'sensitivity': 0.7,       # 1.0 = fazla sinyal, 0.3 = az sinyal
        }
        # Kayan pencere istatistikleri
        self._outcome_window: List[str] = []  # son N sonuç: "success" / "failure"
        self._window_size = 50
        self._total_adjustments = 0
        self._last_adjustment_time: float = 0
        self._adjustment_cooldown = 30.0  # saniye — çok sık güncellemeyi önle

    @property
    def params(self) -> Dict[str, float]:
        return dict(self._params)

    @property
    def win_rate(self) -> float:
        if not self._outcome_window:
            return 0.5
        successes = sum(1 for o in self._outcome_window if o == 'success')
        return successes / len(self._outcome_window)

    @property
    def false_positive_rate(self) -> float:
        """Onay verilip kaybedilen işlem oranı."""
        if not self._outcome_window:
            return 0.0
        failures = sum(1 for o in self._outcome_window if o == 'failure')
        return failures / len(self._outcome_window)

    def get_similarity_threshold(self) -> float:
        return self._params['similarity_threshold']

    def get_magnitude_min(self) -> float:
        return self._params['magnitude_min']

    def compute_confidence(self, similarity: float, brain_accuracy: float,
                           mamis_alignment: float, direction_accuracy: float) -> float:
        """Dinamik ağırlıklarla güven skoru hesapla."""
        w = self._params
        confidence = (
            similarity * w['confidence_weight_similarity'] +
            brain_accuracy * w['confidence_weight_brain'] +
            mamis_alignment * w['confidence_weight_mamis'] +
            (direction_accuracy if direction_accuracy > 0 else 0.5) * w['confidence_weight_direction']
        )
        # Risk appetite modülatörü
        confidence *= (0.7 + 0.3 * w['risk_appetite'])
        return min(max(confidence, 0.0), 1.0)

    def adjust_confidence_from_experiences(self, confidence: float,
                                            past_experiences: List[Dict]) -> Tuple[float, str]:
        """
        RAG-Based Learning: Geçmiş benzer deneyimlerden güven ayarla.
        Eğer geçmişte benzer paternler başarısızsa, güveni düşür.
        """
        if not past_experiences:
            return confidence, ""

        successes = sum(1 for e in past_experiences if e.get('outcome') == 'success')
        failures = sum(1 for e in past_experiences if e.get('outcome') == 'failure')
        total = successes + failures

        if total == 0:
            return confidence, ""

        historical_win_rate = successes / total
        avg_pnl = sum(float(e.get('pnl_pct', 0) or 0) for e in past_experiences) / len(past_experiences)

        adjustment_reason = ""

        if historical_win_rate < 0.35 and total >= 3:
            # Geçmişte benzer sinyaller çoğunlukla başarısız — ghost risk
            penalty = (0.35 - historical_win_rate) * self._params['sensitivity']
            confidence *= (1.0 - penalty)
            adjustment_reason = (
                f"⚠️ Geçmiş benzer deneyimler olumsuz "
                f"(win={historical_win_rate:.0%}, n={total}, avg_pnl={avg_pnl:+.2f}%) "
                f"→ güven {penalty:.0%} düşürüldü"
            )
        elif historical_win_rate > 0.70 and total >= 3:
            # Geçmişte başarılı pattern — güven artır
            boost = (historical_win_rate - 0.70) * 0.3 * self._params['sensitivity']
            confidence = min(1.0, confidence * (1.0 + boost))
            adjustment_reason = (
                f"✅ Geçmiş benzer deneyimler olumlu "
                f"(win={historical_win_rate:.0%}, n={total}, avg_pnl={avg_pnl:+.2f}%) "
                f"→ güven {boost:.0%} artırıldı"
            )

        return min(max(confidence, 0.0), 1.0), adjustment_reason

    def record_outcome(self, outcome: str):
        """Bir işlem sonucunu kaydet ve pencereyi sınırla."""
        normalized = 'success' if outcome in ('success',) else 'failure' if outcome in ('failure', 'error') else None
        if normalized is None:
            return
        self._outcome_window.append(normalized)
        if len(self._outcome_window) > self._window_size:
            self._outcome_window = self._outcome_window[-self._window_size:]

    def _adjust_internal_parameters(self, outcome: str):
        """
        Feedback Loop: İşlem sonucuna göre iç parametreleri yumuşakça güncelle.
        Win Rate'e göre eşikleri daralt veya genişlet.
        """
        now = time.monotonic()
        if now - self._last_adjustment_time < self._adjustment_cooldown:
            return
        if len(self._outcome_window) < self.MIN_OBSERVATIONS:
            return

        self._last_adjustment_time = now
        wr = self.win_rate
        fpr = self.false_positive_rate
        lr = self.LEARNING_RATE
        delta = wr - self.TARGET_WIN_RATE  # pozitif = iyi, negatif = kötü

        # ─── False Positive yüksek → eşikleri sıkılaştır ───
        if fpr > 0.50:
            tighten = lr * (fpr - 0.50)
            self._nudge('similarity_threshold', +tighten)
            self._nudge('magnitude_min', +tighten * 0.5)
            self._nudge('risk_appetite', -tighten)
            self._nudge('sensitivity', -tighten * 0.5)
        # ─── Win Rate çok düşük → muhafazakâr ol ───
        elif delta < -0.10:
            conserve = lr * abs(delta)
            self._nudge('similarity_threshold', +conserve)
            self._nudge('magnitude_min', +conserve * 0.3)
            self._nudge('risk_appetite', -conserve)
        # ─── Win Rate yeterince yüksek → fırsatları artır (False Negative azalt) ───
        elif delta > 0.10:
            relax = lr * delta * 0.5  # yarı hızda gevşet (asimetrik)
            self._nudge('similarity_threshold', -relax)
            self._nudge('magnitude_min', -relax * 0.3)
            self._nudge('risk_appetite', +relax)
            self._nudge('sensitivity', +relax * 0.3)

        # ─── Confidence ağırlıklarını normalleştir ───
        self._normalize_confidence_weights()
        self._total_adjustments += 1

        logger.info(
            f"🔧 AdaptiveEngine güncelleme #{self._total_adjustments}: "
            f"WR={wr:.0%} FPR={fpr:.0%} "
            f"sim_th={self._params['similarity_threshold']:.3f} "
            f"risk_app={self._params['risk_appetite']:.2f} "
            f"sens={self._params['sensitivity']:.2f}"
        )

    def _nudge(self, param: str, delta: float):
        """Parametreyi sınırlar içinde yumuşakça kaydır."""
        lo, hi = self.BOUNDS[param]
        self._params[param] = min(hi, max(lo, self._params[param] + delta))

    def _normalize_confidence_weights(self):
        """Confidence ağırlıklarının toplamını 1.0'a normalleştir."""
        keys = ['confidence_weight_similarity', 'confidence_weight_brain',
                'confidence_weight_mamis', 'confidence_weight_direction']
        total = sum(self._params[k] for k in keys)
        if total > 0:
            for k in keys:
                self._params[k] = self._params[k] / total

    def get_stats(self) -> Dict[str, Any]:
        return {
            'params': dict(self._params),
            'win_rate': self.win_rate,
            'false_positive_rate': self.false_positive_rate,
            'observation_count': len(self._outcome_window),
            'total_adjustments': self._total_adjustments,
            'window_size': self._window_size,
        }


class GemmaDecision:
    """Gemma'nın tek bir karar çıktısı"""
    __slots__ = ('decision', 'direction', 'confidence', 'magnitude',
                 'reasoning', 'risk_level', 'action', 'priority',
                 'symbol', 'timeframe', 'timestamp', 'latency_ms',
                 'raw_response')

    def __init__(self, *, decision: str, direction: str, confidence: float,
                 magnitude: float, reasoning: str, risk_level: str,
                 action: str, priority: str, symbol: str = '',
                 timeframe: str = '', raw_response: str = ''):
        self.decision = decision
        self.direction = direction
        self.confidence = confidence
        self.magnitude = magnitude
        self.reasoning = reasoning
        self.risk_level = risk_level
        self.action = action
        self.priority = priority
        self.symbol = symbol
        self.timeframe = timeframe
        self.timestamp = datetime.utcnow()
        self.latency_ms = 0
        self.raw_response = raw_response

    @property
    def approved(self) -> bool:
        return self.decision == "APPROVE"

    def to_dict(self) -> dict:
        return {
            'decision': self.decision,
            'direction': self.direction,
            'confidence': self.confidence,
            'magnitude': self.magnitude,
            'reasoning': self.reasoning,
            'risk_level': self.risk_level,
            'action': self.action,
            'priority': self.priority,
            'symbol': self.symbol,
            'timeframe': self.timeframe,
            'timestamp': self.timestamp.isoformat(),
            'latency_ms': self.latency_ms,
        }


class GemmaDecisionCore:
    """
    Katman 3: Merkezi Karar Verme Motoru (SuperGemma-26B)
    =====================================================
    Tüm ajanlardan gelen girdileri tek bir SuperGemma promptunda sentezler.
    SuperGemma nihai kararı verir, ajanlar sadece uygular.
    Tetikleme: Sadece Similarity_Score ≥ %60 olduğunda çalışır.

    AKIŞ:
      PatternMatcher → |
            Scout          → | → GemmaDecisionCore.evaluate() → GemmaDecision
            Strategist     → |     ↓ SuperGemma-26B GGUF inference
      Brain          → |
      RiskManager    → |
    """

    # Son N kararı bellekte tut
    MAX_DECISION_HISTORY = 100
    # SuperGemma'yı aşırı yüklememek için rate limit (saniye)
    MIN_DECISION_INTERVAL = float(os.getenv("QUENBOT_DECISION_MIN_INTERVAL", "0.5"))
    # Similarity trigger — sadece ≥%60 eşleşmede SuperGemma çağrılır
    SIMILARITY_TRIGGER_THRESHOLD = 0.60
    # SuperGemma bağlanamazsa fallback kurallar
    FALLBACK_MIN_SIMILARITY = 0.60
    FALLBACK_MIN_CONFIDENCE = 0.50

    def __init__(self, brain=None, risk_manager=None, state_tracker=None, redis_bridge=None):
        self.brain = brain
        self.risk_manager = risk_manager
        self.state_tracker = state_tracker
        self.redis_bridge = redis_bridge
        self.vector_store = get_vector_store()
        self.event_bus = get_event_bus()
        self.adaptive = AdaptiveEngine()
        self._decision_model = os.getenv("QUENBOT_DECISION_MODEL", "gemma-3-12b-it")
        self._decision_timeout = int(os.getenv("QUENBOT_DECISION_TIMEOUT", "120"))
        self._decision_lock = asyncio.Lock()
        self._decision_history: List[GemmaDecision] = []
        self._last_decision_time: float = 0
        self._stats = {
            'total_requests': 0,
            'total_approved': 0,
            'total_rejected': 0,
            'gemma_calls': 0,
            'fallback_calls': 0,
            'avg_latency_ms': 0,
            'total_latency_ms': 0,
        }

    async def evaluate(self,
                       pattern_data: Dict[str, Any],
                       scout_data: Optional[Dict[str, Any]] = None,
                       strategist_data: Optional[Dict[str, Any]] = None,
                       ) -> GemmaDecision:
        """
        Ana karar metodu — tüm ajan verilerini sentezle, Gemma'ya sor.

        Args:
            pattern_data: PatternMatcher'dan gelen eşleşme verisi
            scout_data: Scout'tan gelen piyasa verisi (fiyat, hacim, anomali)
            strategist_data: Strategist'ten gelen öneri ve momentum

        Returns:
            GemmaDecision nesnesi
        """
        self._stats['total_requests'] += 1
        t0 = time.monotonic()

        symbol = pattern_data.get('symbol', 'UNKNOWN')
        timeframe = pattern_data.get('timeframe', '?')

        # ─── Rate limiting ───
        elapsed = time.monotonic() - self._last_decision_time
        if elapsed < self.MIN_DECISION_INTERVAL:
            await asyncio.sleep(self.MIN_DECISION_INTERVAL - elapsed)

        # ─── Context toplama ───
        context = self._build_context(pattern_data, scout_data, strategist_data)

        # ─── Experience-Augmented Decision (RAG) ───
        past_experiences = self.vector_store.query_similar_experiences(
            symbol, max_age_hours=168, limit=10,
        )
        context['_past_experiences'] = past_experiences

        # ─── Similarity Trigger Gate (dinamik eşik) ───
        similarity_score = context.get('similarity', 0)
        dynamic_threshold = self.adaptive.get_similarity_threshold()
        if similarity_score < dynamic_threshold:
            decision = self._fallback_evaluate(symbol, timeframe, context)
            decision.reasoning = f"{decision.reasoning} | Similarity {similarity_score:.1%} < %{dynamic_threshold:.0%} dinamik eşik — SuperGemma tetiklenmedi"
            self._stats['fallback_calls'] += 1
        elif self._decision_lock.locked():
            decision = self._fallback_evaluate(symbol, timeframe, context)
            decision.reasoning = f"{decision.reasoning} | SuperGemma yoğun, hızlı fallback"
            self._stats['fallback_calls'] += 1
        else:
            async with self._decision_lock:
                try:
                    decision = await self._gemma_evaluate(symbol, timeframe, context)
                    self._stats['gemma_calls'] += 1
                except Exception as e:
                    logger.warning(f"SuperGemma Decision Core fallback: {e}")
                    decision = self._fallback_evaluate(symbol, timeframe, context)
                    self._stats['fallback_calls'] += 1

        # ─── Experience-Augmented Confidence Adjustment ───
        adj_confidence, adj_reason = self.adaptive.adjust_confidence_from_experiences(
            decision.confidence, past_experiences,
        )
        if adj_reason:
            decision.confidence = adj_confidence
            decision.reasoning = f"{decision.reasoning} | {adj_reason}"

        # ─── İstatistik güncelle ───
        decision.latency_ms = int((time.monotonic() - t0) * 1000)
        self._stats['total_latency_ms'] += decision.latency_ms
        counted = self._stats['gemma_calls'] + self._stats['fallback_calls']
        if counted > 0:
            self._stats['avg_latency_ms'] = self._stats['total_latency_ms'] / counted

        if decision.approved:
            self._stats['total_approved'] += 1
        else:
            self._stats['total_rejected'] += 1

        # ─── Geçmişe ekle ───
        self._decision_history.append(decision)
        if len(self._decision_history) > self.MAX_DECISION_HISTORY:
            self._decision_history = self._decision_history[-self.MAX_DECISION_HISTORY:]

        self._last_decision_time = time.monotonic()

        logger.info(
            f"🎯 SuperGemmaDecision: {symbol} {timeframe} → "
            f"{'✅ APPROVE' if decision.approved else '🚫 REJECT'} "
            f"(sim={context.get('similarity', 0):.1%}, th={dynamic_threshold:.0%}, "
            f"{decision.direction}, güven={decision.confidence:.0%}, "
            f"{decision.latency_ms}ms, WR={self.adaptive.win_rate:.0%})"
        )

        envelope = self.build_command_envelope(pattern_data, decision)
        await self.event_bus.publish(Event(
            type=EventType.DECISION_MADE,
            source="supergemma_decision_core",
            data={
                "symbol": symbol,
                "timeframe": timeframe,
                "approved": decision.approved,
                "decision": decision.to_dict(),
                "command": envelope.command.model_dump(mode="json"),
                "reasoning_steps": envelope.reasoning_steps,
            },
        ))
        if self.redis_bridge:
            await self.redis_bridge.publish_command(
                CommunicationLogEntry(
                    channel="commands",
                    source="supergemma_decision_core",
                    kind="command",
                    summary=f"{symbol} icin {envelope.command.action.value} komutu üretildi (SuperGemma)",
                    payload={
                        "decision": decision.to_dict(),
                        "command": envelope.command.model_dump(mode="json"),
                    },
                )
            )

        # ── Phase 3: FastBrain + DecisionRouter shadow logging ──
        # Her zaman shadow; "active" mod çıktıyı override etmez, sadece konsey
        # izlemesi için log tutar. Hot-path'i bozmamak için hatalar yutulur.
        try:
            await self._route_through_fast_brain(symbol, decision)
        except Exception as e:
            logger.debug("fast_brain/decision_router hook atlandı: %s", e)

        return decision

    async def _route_through_fast_brain(self, symbol: str, decision: "GemmaDecision") -> None:
        """Phase 3: FastBrain tahmini al, DecisionRouter'a log için ver."""
        fast_engine = getattr(self, "fast_brain_engine", None)
        router = getattr(self, "decision_router", None)
        if fast_engine is None and router is None:
            return

        fast_dict = None
        if fast_engine is not None and getattr(fast_engine, "enabled", False):
            pred = fast_engine.predict(symbol)
            if pred is not None:
                fast_dict = pred.to_dict()
                try:
                    await fast_engine.publish_prediction(pred)
                except Exception:
                    pass

        if router is not None:
            gemma_dict = {
                "action": getattr(decision.recommended_action, "value",
                                  str(decision.recommended_action)),
                "confidence": float(getattr(decision, "confidence", 0.5) or 0.5),
            }
            r_decision = router.route(symbol, gemma_dict, fast_dict)
            try:
                if hasattr(EventType, "DECISION_SHADOW"):
                    await self.event_bus.publish(Event(
                        type=EventType.DECISION_SHADOW,
                        source="decision_router",
                        data={"symbol": symbol, **r_decision.to_dict()},
                    ))
            except Exception:
                pass

    def _get_systematic_context(self, symbol: str) -> Dict[str, Any]:
        """Systematic Trade Detector'dan bot analiz verileri al."""
        detector = get_systematic_detector()
        report = detector.get_last_report(symbol)
        
        if report is None:
            return {
                'systematic_trade_ratio': 0.0,
                'dominant_bot_type': 'tespit yok',
                'bot_predicted_direction': 'neutral',
                'bot_direction_confidence': 0.0,
                'accumulation_score': 0.0,
                'smart_money_flow': 0.0,
                'retail_vs_institutional': 0.0,
                'estimated_price_impact_bps': 0.0,
                'bot_signatures_summary': '- Bot aktivitesi tespit edilemedi',
            }
        
        # Bot imzalarından özet oluştur
        signatures_summary = []
        for sig in report.bot_signatures[:3]:  # En önemli 3 imza
            signatures_summary.append(
                f"- {sig.signature_type.upper()}: güven={sig.confidence:.0%}, "
                f"yön={sig.direction_bias}, etki={sig.price_impact_bps:.1f}bps"
            )
        
        return {
            'systematic_trade_ratio': report.systematic_trade_ratio,
            'dominant_bot_type': report.dominant_bot_type or 'belirsiz',
            'bot_predicted_direction': report.predicted_price_direction,
            'bot_direction_confidence': report.direction_confidence,
            'accumulation_score': report.accumulation_score,
            'smart_money_flow': report.smart_money_flow,
            'retail_vs_institutional': report.retail_vs_institutional,
            'estimated_price_impact_bps': report.estimated_price_impact_bps,
            'bot_signatures_summary': '\n'.join(signatures_summary) if signatures_summary else '- Önemli bot imzası yok',
        }

    def _get_confluence_block(self, symbol: str) -> str:
        """
        Confluence Engine cache'inden LLM prompt bloğu.
        - Flag kapalıysa, cache boşsa veya herhangi bir hata varsa boş string döner.
        - Karar akışı kritik — hiçbir koşulda exception yükseltmez.
        """
        try:
            from config import Config
            if not getattr(Config, 'CONFLUENCE_INJECT_LLM', True):
                return ""
            if not getattr(Config, 'CONFLUENCE_ENABLED', False):
                return ""
            if not symbol:
                return ""
            from confluence_engine import get_confluence_engine
            engine = get_confluence_engine()
            # explain() sync + cache-based — publisher loop bu cache'i doldurur
            info = engine.explain(symbol)
            if not info:
                return ""
            score = float(info.get('score', 0.0) or 0.0)
            direction = str(info.get('direction', 'neutral'))
            top = info.get('top') or []
            top_line = ", ".join(top) if top else "—"
            snap = engine.snapshot(symbol) or {}
            log_odds = float(snap.get('log_odds', 0.0) or 0.0)
            missing = snap.get('missing_signals') or []
            n_active = max(0, len(top))

            # Cross-asset (Phase 2) — en güçlü 2 leader'ı satıra ekle
            cross_line = ""
            try:
                if getattr(Config, 'CROSS_ASSET_ENABLED', False):
                    from cross_asset_graph import get_cross_asset_engine
                    ca = get_cross_asset_engine()
                    leaders = ca.leaders_of(symbol)[:2]
                    spill = ca.spillover_signal(symbol)
                    if leaders:
                        lead_txt = ", ".join(
                            f"{s}(+{l}s,ρ={r:+.2f})" for s, l, r in leaders
                        )
                        cross_line = f"- Cross-Asset Leaders: {lead_txt} | Spillover: {spill:+.2f}σ\n"
                    elif spill != 0.0:
                        cross_line = f"- Cross-Asset Spillover: {spill:+.2f}σ\n"
            except Exception:
                cross_line = ""

            return (
                "\n### 🎯 CONFLUENCE ENGINE (Pre-Move Fingerprint)\n"
                f"- Skor: {score:.3f} | Yön: {direction} | log-odds: {log_odds:+.2f}\n"
                f"- En Güçlü Katkılar: {top_line}\n"
                f"{cross_line}"
                f"- Eksik Sinyaller: {', '.join(missing) if missing else 'yok'} | Aktif top-K: {n_active}\n"
            )
        except Exception:
            return ""


    def _build_context(self,
                       pattern_data: Dict,
                       scout_data: Optional[Dict],
                       strategist_data: Optional[Dict]) -> Dict[str, Any]:
        """Tüm ajan verilerini tek bir context dict'ine topla."""
        scout = scout_data or {}
        strat = strategist_data or {}
        mamis_context = pattern_data.get('mamis_context') or strat.get('mamis_context') or {}

        # Brain öğrenme verileri
        brain_accuracy = 0.0
        direction_accuracy = 0.0
        total_patterns = 0
        last_calibration = "yok"
        predicted_direction = pattern_data.get('predicted_direction', 'neutral')

        if self.brain:
            brain_status = self.brain.get_brain_status()
            brain_accuracy = brain_status.get('accuracy', 0)
            total_patterns = brain_status.get('total_patterns', 0)
            cal = brain_status.get('last_calibration')
            if cal:
                last_calibration = cal

            # Bu yöndeki geçmiş başarı oranı
            sig_scores = brain_status.get('signal_type_scores', {})
            dir_key = f"pattern_match_{predicted_direction}"
            if dir_key in sig_scores:
                direction_accuracy = sig_scores[dir_key].get('accuracy', 0)

        # Risk verileri
        bot_mode = "LEARNING"
        daily_pnl = 0.0
        drawdown = 0.0
        open_positions = 0
        max_positions = 8
        consecutive_losses = 0

        if self.state_tracker:
            st = self.state_tracker.state
            bot_mode = self.state_tracker.get_mode()
            daily_pnl = st.get('daily_pnl', 0)
            drawdown = st.get('current_drawdown', 0)
            open_positions = len(st.get('active_symbols', []))
            consecutive_losses = st.get('consecutive_losses', 0)

        if self.risk_manager:
            from config import Config
            max_positions = Config.RISK_MAX_OPEN_POSITIONS

        return {
            # Pattern
            'symbol': pattern_data.get('symbol', ''),
            'timeframe': pattern_data.get('timeframe', ''),
            'similarity': pattern_data.get('similarity', 0),
            'match_count': pattern_data.get('match_count', 0),
            'predicted_direction': predicted_direction,
            'predicted_magnitude': pattern_data.get('predicted_magnitude', 0),
            'matched_change': pattern_data.get('matched_change_pct', 0),
            'current_price': pattern_data.get('current_price', 0),
            # Market
            'market_regime': scout.get('regime', 'bilinmiyor'),
            'volatility': scout.get('volatility', 'orta'),
            'trend_strength': scout.get('trend_strength', 'orta'),
            'buy_ratio': scout.get('buy_ratio', 0.5),
            'recent_trade_count': scout.get('recent_trade_count', 0),
            'volume_change': scout.get('volume_change_pct', 0),
            # Brain
            'brain_accuracy': brain_accuracy,
            'direction_accuracy': direction_accuracy,
            'total_patterns': total_patterns,
            'last_calibration': last_calibration,
            # Risk
            'bot_mode': bot_mode,
            'daily_pnl': daily_pnl,
            'drawdown': drawdown,
            'open_positions': open_positions,
            'max_positions': max_positions,
            'consecutive_losses': consecutive_losses,
            # Strategist
            'strategist_recommendation': strat.get('recommendation', 'bekle'),
            'momentum_score': strat.get('momentum_score', 0),
            # MAMIS
            'mamis_bias': mamis_context.get('direction', 'neutral'),
            'mamis_confidence': float(mamis_context.get('confidence', 0) or 0),
            'mamis_pattern': mamis_context.get('pattern_type', 'none'),
            'mamis_volatility': float(mamis_context.get('volatility', 0) or 0),
            # Learning context
            'learning_context': self.vector_store.build_learning_context(pattern_data.get('symbol')),
            # Market Activity
            'market_mode': get_market_tracker().mode.value,
            'active_symbol_count': len(get_market_tracker().get_active_symbols()),
            # Signature provenance
            'signature_match_count': pattern_data.get('signature_match_count', 0),
            'signature_top_similarity': pattern_data.get('signature_top_similarity', 0),
            'signature_direction': pattern_data.get('signature_direction', 'neutral'),
            'signature_provenance': pattern_data.get('signature_provenance', 'İmza eşleşmesi bulunamadı.'),
            # Systematic Trade Detection (Bot Analysis)
            **self._get_systematic_context(pattern_data.get('symbol', '')),
            # Confluence Engine (Phase 1 Intel Upgrade) — guarded, boş string ise LLM promptuna hiçbir etki yapmaz
            'confluence_block': self._get_confluence_block(pattern_data.get('symbol', '')),
        }

    async def _gemma_evaluate(self, symbol: str, timeframe: str,
                               context: Dict) -> GemmaDecision:
        """SuperGemma-26B'ye tam context gönder, karar al."""
        prompt = SYNTHESIS_PROMPT_TEMPLATE.format(**context)

        client = _get_llm_client()
        response = await client.generate(
            prompt=prompt,
            system=DECISION_SYSTEM_PROMPT,
            temperature=0.2,
            json_mode=True,
            timeout_override=self._decision_timeout,
            max_tokens_override=256,
            prefer_fast_fail=True,
        )

        if not response.success or not response.text.strip():
            raise RuntimeError(f"SuperGemma yanıt vermedi: {response.text[:100]}")

        # JSON parse
        parsed = self._parse_decision_json(response.text)

        return GemmaDecision(
            decision=parsed.get('decision', 'REJECT'),
            direction=parsed.get('direction', 'neutral'),
            confidence=float(parsed.get('confidence', 0)),
            magnitude=float(parsed.get('magnitude', 0)),
            reasoning=parsed.get('reasoning', ''),
            risk_level=parsed.get('risk_level', 'medium'),
            action=parsed.get('action', ''),
            priority=parsed.get('priority', 'normal'),
            symbol=symbol,
            timeframe=timeframe,
            raw_response=response.text[:500],
        )

    def _fallback_evaluate(self, symbol: str, timeframe: str,
                            context: Dict) -> GemmaDecision:
        """
        SuperGemma çalışmadığında kural tabanlı fallback karar.
        MarketActivityTracker modunu, MAMIS sinyalini ve Brain doğruluğunu kullanır.
        Similarity ≥ %60 gating dahil."""
        similarity = context.get('similarity', 0)
        predicted_direction = context.get('predicted_direction', 'neutral')
        predicted_magnitude = context.get('predicted_magnitude', 0)
        brain_accuracy = context.get('brain_accuracy', 0)
        consecutive_losses = context.get('consecutive_losses', 0)
        drawdown = context.get('drawdown', 0)
        mamis_bias = context.get('mamis_bias', 'neutral')
        mamis_confidence = context.get('mamis_confidence', 0)
        direction_accuracy = context.get('direction_accuracy', 0)
        bot_mode = context.get('bot_mode', 'LEARNING')
        
        # Systematic Trade Detector verileri
        systematic_ratio = context.get('systematic_trade_ratio', 0)
        bot_direction = context.get('bot_predicted_direction', 'neutral')
        bot_confidence = context.get('bot_direction_confidence', 0)
        smart_money_flow = context.get('smart_money_flow', 0)
        accumulation_score = context.get('accumulation_score', 0)

        # Kural tabanlı karar
        approved = True
        reasons = []

        # ─── Hard Gates (dinamik eşiklerle) ───
        dyn_sim_threshold = self.adaptive.get_similarity_threshold()
        dyn_mag_min = self.adaptive.get_magnitude_min()

        if similarity < dyn_sim_threshold:
            approved = False
            reasons.append(f"Benzerlik düşük: {similarity:.4f} < {dyn_sim_threshold:.3f}")

        if abs(predicted_magnitude) < dyn_mag_min:
            approved = False
            reasons.append(f"Beklenen hareket çok küçük: {predicted_magnitude:+.2%} < {dyn_mag_min:.2%}")

        if consecutive_losses >= 5:
            approved = False
            reasons.append(f"Ardışık kayıp çok yüksek: {consecutive_losses}")

        if abs(drawdown) > 8:
            approved = False
            reasons.append(f"Drawdown çok yüksek: {drawdown:.2f}%")

        if brain_accuracy < 0.3 and brain_accuracy > 0:
            approved = False
            reasons.append(f"Brain doğruluğu düşük: {brain_accuracy:.1%}")

        # ─── MAMIS Cross-validation ───
        if mamis_bias != 'neutral' and mamis_bias != predicted_direction and mamis_confidence > 0.6:
            approved = False
            reasons.append(f"MAMIS çelişkisi: {mamis_bias} vs {predicted_direction} (güven={mamis_confidence:.0%})")

        # ─── Systematic Trade (Bot) Cross-validation ───
        if systematic_ratio > 0.4 and bot_direction != 'neutral':
            if bot_direction != predicted_direction and bot_confidence > 0.6:
                approved = False
                reasons.append(
                    f"🤖 Bot aktivitesi zıt yönde: {bot_direction} vs {predicted_direction} "
                    f"(güven={bot_confidence:.0%}, oran={systematic_ratio:.0%})"
                )
            elif bot_direction == predicted_direction and bot_confidence > 0.5:
                # Bot yönü ile uyumlu — güveni artır (sonradan)
                reasons.append(
                    f"🤖 Bot aktivitesi destekliyor: {bot_direction} "
                    f"(güven={bot_confidence:.0%}, smart_money={smart_money_flow:+.2f})"
                )
        
        # ─── Smart Money Flow Gate ───
        if abs(smart_money_flow) > 0.5:
            if (smart_money_flow > 0 and predicted_direction == 'short') or \
               (smart_money_flow < 0 and predicted_direction == 'long'):
                if systematic_ratio > 0.3:
                    approved = False
                    reasons.append(
                        f"💰 Smart money akışı zıt: flow={smart_money_flow:+.2f}, "
                        f"önerilen={predicted_direction}"
                    )

        # ─── Market Activity Mode ───
        tracker = get_market_tracker()
        if not tracker.is_active:
            quiet_threshold = max(0.70, dyn_sim_threshold + 0.10)
            if similarity < quiet_threshold:
                approved = False
                reasons.append(f"Piyasa durgun — yüksek benzerlik gerekli: {similarity:.4f} < {quiet_threshold:.2f}")

        # ─── Direction-specific accuracy gate ───
        if direction_accuracy > 0 and direction_accuracy < 0.35:
            approved = False
            reasons.append(f"Bu yön ({predicted_direction}) başarı oranı düşük: {direction_accuracy:.1%}")

        # ─── Confidence calculation (dinamik ağırlıklar) ───
        mamis_alignment = 0.5
        if mamis_bias == predicted_direction:
            mamis_alignment = mamis_confidence
        elif mamis_bias == 'neutral':
            mamis_alignment = 0.5
        else:
            mamis_alignment = max(0.1, 1.0 - mamis_confidence)

        confidence = self.adaptive.compute_confidence(
            similarity, brain_accuracy, mamis_alignment, direction_accuracy,
        )
        
        # ─── Bot/Systematic Trade Confidence Boost ───
        if systematic_ratio > 0.3 and bot_direction == predicted_direction and bot_confidence > 0.5:
            bot_boost = bot_confidence * systematic_ratio * 0.15  # Max ~%7.5 boost
            confidence = min(1.0, confidence + bot_boost)
            reasons.append(f"🤖 Bot desteği ile güven +{bot_boost:.1%}")
        
        # ─── Smart Money Flow Confidence Adjustment ───
        if abs(smart_money_flow) > 0.3:
            if (smart_money_flow > 0 and predicted_direction == 'long') or \
               (smart_money_flow < 0 and predicted_direction == 'short'):
                smf_boost = abs(smart_money_flow) * 0.1  # Max %10 boost
                confidence = min(1.0, confidence + smf_boost)
                reasons.append(f"💰 Smart money flow uyumlu: +{smf_boost:.1%}")

        # ─── Experience-Augmented (RAG) güven ayarı ───
        past_exp = context.get('_past_experiences', [])
        adj_confidence, adj_reason = self.adaptive.adjust_confidence_from_experiences(confidence, past_exp)
        if adj_reason:
            confidence = adj_confidence
            reasons.append(adj_reason)

        # ─── Risk level determination ───
        if abs(drawdown) > 5 or consecutive_losses >= 3:
            risk_level = 'high'
        elif abs(drawdown) > 2 or consecutive_losses >= 1:
            risk_level = 'medium'
        else:
            risk_level = 'low'

        return GemmaDecision(
            decision='APPROVE' if approved else 'REJECT',
            direction=predicted_direction,
            confidence=confidence if approved else confidence * 0.5,
            magnitude=predicted_magnitude,
            reasoning=' | '.join(reasons) if reasons else 'Kural tabanlı onay (SuperGemma offline)',
            risk_level=risk_level,
            action=f'{predicted_direction.upper()} pozisyon aç' if approved else 'Bekle',
            priority='normal',
            symbol=symbol,
            timeframe=timeframe,
        )

    def _parse_decision_json(self, text: str) -> Dict:
        """Gemma yanıtından JSON parse et."""
        import re
        # Önce düz JSON dene
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # JSON bloğunu bul
        match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        # Think tag'leri arasında JSON ara
        match = re.search(r'```(?:json)?\s*(\{[^`]+\})\s*```', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        logger.warning(f"SuperGemma karar JSON parse hatası: {text[:200]}")
        return {'decision': 'REJECT', 'reasoning': 'JSON parse hatası'}

    def build_command_envelope(self, pattern_data: Dict[str, Any], decision: GemmaDecision) -> DecisionEnvelope:
        raw_direction = (decision.direction or pattern_data.get("predicted_direction") or "neutral").lower()
        if raw_direction in {"up", "long"}:
            action = CommandAction.LONG
        elif raw_direction in {"down", "short"}:
            action = CommandAction.SHORT
        else:
            action = CommandAction.HOLD

        target_profit_pct = abs(float(decision.magnitude or pattern_data.get("predicted_magnitude", 0.02) or 0.02))
        target_profit_pct = max(0.02, min(target_profit_pct, 0.12))
        stop_loss_pct = max(0.01, min(target_profit_pct / 2.0, 0.05))

        command = DecisionCommand(
            action=action,
            symbol=str(pattern_data.get("symbol", "GLOBAL") or "GLOBAL"),
            market_type=str(pattern_data.get("market_type", "spot")),
            exchange=str(pattern_data.get("exchange", "mixed")),
            target_profit_pct=target_profit_pct,
            stop_loss_pct=stop_loss_pct,
            estimated_duration_minutes=int(pattern_data.get("estimated_duration_minutes", 30) or 30),
            confidence=float(decision.confidence or 0.0),
            reasoning=decision.reasoning,
            strategy="pattern_vector_recall",
            execution_mode="paper",
            constraints={
                "match_count": pattern_data.get("match_count", 0),
                "similarity": pattern_data.get("similarity", 0),
                "risk_level": decision.risk_level,
            },
            metadata={
                "source_event": "EVENT_PATTERN_DETECTED",
                "timeframe": pattern_data.get("timeframe", "15m"),
                "current_price": pattern_data.get("current_price", 0),
            },
        )
        steps = [
            f"Pattern similarity {float(pattern_data.get('similarity', 0)):.2f}",
            f"Predicted direction {raw_direction}",
            f"Risk level {decision.risk_level}",
        ]
        return DecisionEnvelope(
            task=f"{command.symbol} icin anlamli pattern hareketini isle",
            goal="Dusuk gecikmeli, yapilandirilmis ve paper-trade uyumlu karar uretmek",
            strategy_summary=decision.reasoning[:240],
            command=command,
            priority=decision.priority if decision.priority in {"low", "normal", "high", "critical"} else "normal",
            source_event="pattern.detected",
            reasoning_steps=steps,
        )

    def build_command_envelope_from_dict(self, pattern_data: Dict[str, Any], decision_data: Dict[str, Any]) -> DecisionEnvelope:
        synthetic_decision = GemmaDecision(
            decision='APPROVE' if decision_data.get('approved') else 'REJECT',
            direction=str(decision_data.get('direction', 'neutral')),
            confidence=float(decision_data.get('confidence', 0.0) or 0.0),
            magnitude=float(decision_data.get('magnitude', 0.0) or 0.0),
            reasoning=str(decision_data.get('reasoning', '')),
            risk_level=str(decision_data.get('risk_level', 'medium')),
            action=str(decision_data.get('action', 'Bekle')),
            priority='high' if float(decision_data.get('confidence', 0.0) or 0.0) >= 0.75 else 'normal',
            symbol=str(pattern_data.get('symbol', 'GLOBAL') or 'GLOBAL'),
            timeframe=str(pattern_data.get('timeframe', '15m') or '15m'),
        )
        return self.build_command_envelope(pattern_data, synthetic_decision)

    async def record_execution_feedback(self, feedback: ExecutionFeedback):
        outcome = "error" if feedback.status == "error" else "success" if (feedback.pnl_pct or 0) > 0 else "failure"
        lessons = [str(item) for item in feedback.details.get("lessons", []) if item]
        if not lessons:
            fallback_lesson = str(feedback.error_message or "paper trade feedback")
            lessons = [fallback_lesson]
        experience = LearningExperience(
            symbol=feedback.symbol,
            action=feedback.action,
            outcome=outcome,
            pnl_pct=float(feedback.pnl_pct or 0.0),
            confidence=float(feedback.details.get("confidence", 0.0) or 0.0),
            reasoning=str(feedback.details.get("reasoning", "")),
            lessons=lessons,
            context=feedback.details,
        )
        exp_id = self.vector_store.record_experience(experience)

        # ─── Feedback Loop: Adaptive Engine güncelle ───
        self.adaptive.record_outcome(outcome)
        self.adaptive._adjust_internal_parameters(outcome)

        await self.event_bus.publish(Event(
            type=EventType.EXPERIENCE_RECORDED,
            source="supergemma_decision_core",
            data={"experience_id": exp_id, **experience.model_dump(mode="json")},
        ))
        return exp_id

    async def record_error_observation(self, source: str, error_type: str, message: str, context: Optional[Dict[str, Any]] = None):
        observation = ErrorObservation(
            source=source,
            error_type=error_type,
            message=message,
            context=context or {},
        )
        err_id = self.vector_store.record_error(observation)
        await self.event_bus.publish(Event(
            type=EventType.ERROR_OBSERVED,
            source=source,
            data={"error_id": err_id, **observation.model_dump(mode="json")},
        ))
        return err_id

    # ─── Query & Stats ───

    def get_recent_decisions(self, symbol: Optional[str] = None,
                             limit: int = 20) -> List[Dict]:
        """Son kararları getir."""
        history = self._decision_history
        if symbol:
            history = [d for d in history if d.symbol == symbol]
        return [d.to_dict() for d in history[-limit:]]

    def get_stats(self) -> Dict[str, Any]:
        """Karar motoru istatistikleri."""
        approval_rate = 0
        if self._stats['total_requests'] > 0:
            approval_rate = self._stats['total_approved'] / self._stats['total_requests']
        return {
            **self._stats,
            'approval_rate': approval_rate,
            'decision_history_count': len(self._decision_history),
            'last_decision': self._decision_history[-1].to_dict() if self._decision_history else None,
            'adaptive': self.adaptive.get_stats(),
        }

    def get_symbol_summary(self, symbol: str) -> Dict[str, Any]:
        """Belirli bir sembol için karar geçmişi özeti."""
        decisions = [d for d in self._decision_history if d.symbol == symbol]
        if not decisions:
            return {'symbol': symbol, 'total': 0}
        approved = sum(1 for d in decisions if d.approved)
        return {
            'symbol': symbol,
            'total': len(decisions),
            'approved': approved,
            'rejected': len(decisions) - approved,
            'avg_confidence': sum(d.confidence for d in decisions) / len(decisions),
            'last_decision': decisions[-1].to_dict(),
        }


# ─── Singleton ───
_decision_core: Optional[GemmaDecisionCore] = None

def get_decision_core(**kwargs) -> GemmaDecisionCore:
    global _decision_core
    if _decision_core is None:
        _decision_core = GemmaDecisionCore(**kwargs)
    return _decision_core
