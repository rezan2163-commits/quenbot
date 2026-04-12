"""
Gemma Decision Core — Katman 3: Karar Verme Merkezi
=====================================================
Tüm ajanlardan gelen girdileri sentezleyerek Gemma 4'e gönderir
ve nihai stratejik kararı alır.

MİMARİ KONUM: Decision Core (Katman 3)
- INPUT  ← PatternMatcher skoru, Scout verileri, Strategist önerileri,
            RiskManager durumu, StateTracker modu, Brain öğrenme verileri
- OUTPUT → Onay/Red kararı, yön, güven, eylem talimatı

FELSEFE: Ajanlar ÖNERI sunar, Gemma NİHAİ KARAR verir.
"""
import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple

logger = logging.getLogger(__name__)
ACTIVE_LLM_MODEL = os.getenv("QUENBOT_LLM_MODEL", "quenbot-brain")

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


# ─── Gemma Decision Prompt Templates ───

DECISION_SYSTEM_PROMPT = """Sen QuenBot trading sisteminin merkezi karar verme motorusun (Gemma Decision Core).

GÖREV: Ajanlardan gelen tüm verileri sentezleyerek nihai stratejik karar ver.

KURALLAR:
1. Tüm ajan raporlarını dikkatlice analiz et
2. Pattern eşleşme skoru, piyasa rejimi, risk durumu, öğrenme geçmişini dengele
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

---
Bu verileri sentezle ve nihai kararını JSON formatında ver."""


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
    Katman 3: Merkezi Karar Verme Motoru
    =====================================
    Tüm ajanlardan gelen girdileri tek bir Gemma promptunda sentezler.
    Gemma nihai kararı verir, ajanlar sadece uygular.

    AKIŞ:
      PatternMatcher → |
      Scout          → | → GemmaDecisionCore.evaluate() → GemmaDecision
      Strategist     → |     ↓ Gemma 4 LLM inference
      Brain          → |
      RiskManager    → |
    """

    # Son N kararı bellekte tut
    MAX_DECISION_HISTORY = 100
    # Gemma'yı aşırı yüklememek için rate limit (saniye)
    MIN_DECISION_INTERVAL = 5
    # Chat trafiğini boğmamak için Gemma karar çağrısı ancak LLM boşsa yapılır.
    MIN_FREE_SLOTS_FOR_GEMMA_DECISION = 1
    # Pattern kararı için Gemma uzun süre bekletilmez; yoğunlukta hızlı fallback gerekir.
    GEMMA_DECISION_TIMEOUT_SECONDS = 12
    STRICT_LLM = os.getenv("QUENBOT_LLM_STRICT", "0").lower() in {"1", "true", "yes", "on"}
    # Gemma bağlanamazsa fallback kurallar
    FALLBACK_MIN_SIMILARITY = 0.92
    FALLBACK_MIN_CONFIDENCE = 0.60

    def __init__(self, brain=None, risk_manager=None, state_tracker=None):
        self.brain = brain
        self.risk_manager = risk_manager
        self.state_tracker = state_tracker
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

        # ─── Gemma çağrısı ───
        try:
            client = _get_llm_client()
            self._ensure_capacity_for_decision(client, context)
            decision = await self._gemma_evaluate(symbol, timeframe, context, client)
            self._stats['gemma_calls'] += 1
        except Exception as e:
            logger.warning(f"Gemma Decision Core fallback: {e}")
            decision = self._fallback_evaluate(symbol, timeframe, context)
            self._stats['fallback_calls'] += 1
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
            f"🎯 GemmaDecision: {symbol} {timeframe} → "
            f"{'✅ APPROVE' if decision.approved else '🚫 REJECT'} "
            f"({decision.direction}, güven={decision.confidence:.0%}, "
            f"{decision.latency_ms}ms)"
        )

        return decision

    def _build_context(self,
                       pattern_data: Dict,
                       scout_data: Optional[Dict],
                       strategist_data: Optional[Dict]) -> Dict[str, Any]:
        """Tüm ajan verilerini tek bir context dict'ine topla."""
        scout = scout_data or {}
        strat = strategist_data or {}

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
        }

    def _ensure_capacity_for_decision(self, client, context: Dict) -> None:
        """Chat'i bloke etmemek için yalnızca LLM gerçekten müsaitse Gemma kararına gir."""
        semaphore = getattr(client, '_semaphore', None)
        free_slots = getattr(semaphore, '_value', None)

        if free_slots is not None and free_slots < self.MIN_FREE_SLOTS_FOR_GEMMA_DECISION:
            raise RuntimeError(f"LLM yoğun, karar fallback'a düşürüldü (boş slot={free_slots})")

        similarity = float(context.get('similarity', 0) or 0)
        magnitude = abs(float(context.get('predicted_magnitude', 0) or 0))
        if similarity < self.FALLBACK_MIN_SIMILARITY:
            raise RuntimeError(
                f"Gemma ön-eleme: similarity {similarity:.4f} < {self.FALLBACK_MIN_SIMILARITY}"
            )
        if magnitude < 0.01:
            raise RuntimeError(
                f"Gemma ön-eleme: magnitude {magnitude:.2%} çok düşük"
            )

    async def _gemma_evaluate(self, symbol: str, timeframe: str,
                               context: Dict, client=None) -> GemmaDecision:
        """Gemma 4'e tam context gönder, karar al."""
        prompt = SYNTHESIS_PROMPT_TEMPLATE.format(**context)

        client = client or _get_llm_client()
        response = await asyncio.wait_for(
            client.generate(
                prompt=prompt,
                system=DECISION_SYSTEM_PROMPT,
                temperature=0.3,
                json_mode=True,
                timeout_override=10,
            ),
            timeout=self.GEMMA_DECISION_TIMEOUT_SECONDS,
        )

        if not response.success or not response.text.strip():
            raise RuntimeError(f"Gemma yanıt vermedi: {response.text[:100]}")

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
        Gemma çalışmadığında kural tabanlı fallback karar.
        Daha muhafazakar eşikler kullanır.
        """
        similarity = context.get('similarity', 0)
        predicted_direction = context.get('predicted_direction', 'neutral')
        predicted_magnitude = context.get('predicted_magnitude', 0)
        brain_accuracy = context.get('brain_accuracy', 0)
        consecutive_losses = context.get('consecutive_losses', 0)
        drawdown = context.get('drawdown', 0)

        # Kural tabanlı karar
        approved = True
        reasons = []

        if similarity < self.FALLBACK_MIN_SIMILARITY:
            approved = False
            reasons.append(f"Benzerlik düşük: {similarity:.4f} < {self.FALLBACK_MIN_SIMILARITY}")

        if abs(predicted_magnitude) < 0.01:
            approved = False
            reasons.append(f"Beklenen hareket çok küçük: {predicted_magnitude:+.2%}")

        if consecutive_losses >= 5:
            approved = False
            reasons.append(f"Ardışık kayıp çok yüksek: {consecutive_losses}")

        if abs(drawdown) > 8:
            approved = False
            reasons.append(f"Drawdown çok yüksek: {drawdown:.2f}%")

        if brain_accuracy < 0.3 and brain_accuracy > 0:
            approved = False
            reasons.append(f"Brain doğruluğu düşük: {brain_accuracy:.1%}")

        confidence = min(similarity * 0.5 + brain_accuracy * 0.3 + 0.2, 1.0)

        return GemmaDecision(
            decision='APPROVE' if approved else 'REJECT',
            direction=predicted_direction,
            confidence=confidence if approved else confidence * 0.5,
            magnitude=predicted_magnitude,
            reasoning=' | '.join(reasons) if reasons else 'Kural tabanlı onay (Gemma offline)',
            risk_level='high' if drawdown < -5 else 'medium',
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
        logger.warning(f"Gemma karar JSON parse hatası: {text[:200]}")
        return {'decision': 'REJECT', 'reasoning': 'JSON parse hatası'}

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
