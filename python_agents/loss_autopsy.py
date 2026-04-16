"""
loss_autopsy.py — Zarar sinyalleri için derin post-mortem
============================================================
Her kaybeden sinyali otopsi eder ve beyne yapılandırılmış ders çıkarır.

Analiz boyutları:
  1. Mikroyapı tetikleyicileri: entry anında OBI / VPIN / spread / Kyle λ
  2. Rejim uyumsuzluğu: strateji rejime uymuyordu mu?
  3. Counter-flow: fiyat MFE'ye ulaştı mı, sonra geri mi döndü? (scraping risk)
  4. Fingerprint: iceberg/spoof yoğunluğu (manipülasyon ihtimali)
  5. Temporal pattern: günün saati / gün, geçmiş kayıpların zaman dağılımı
  6. Benzerlik: bu kaybın geçmişteki en benzer 3 kaybı
  7. Actionable insight: "gelecek sefere X ise bu sinyali atla" kuralı

Her otopsi `loss_autopsies` tablosuna yazılır ve brain içine "kural" olarak beslenir.
"""
from __future__ import annotations

import json
import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class AutopsyRecord:
    signal_id: Optional[int]
    symbol: str
    signal_type: str
    direction: str
    entry_price: float
    exit_price: float
    loss_pct: float
    barrier_hit: str
    duration_s: float
    root_causes: List[str]
    microstructure_verdict: Dict[str, Any]
    regime_verdict: Dict[str, Any]
    fingerprint_verdict: Dict[str, Any]
    temporal_verdict: Dict[str, Any]
    lesson_rule: Dict[str, Any]  # avoid_if: {feature, op, threshold}
    score: float                 # 0..1 öğrenme değeri

    def to_dict(self) -> Dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "symbol": self.symbol,
            "signal_type": self.signal_type,
            "direction": self.direction,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "loss_pct": self.loss_pct,
            "barrier_hit": self.barrier_hit,
            "duration_s": self.duration_s,
            "root_causes": self.root_causes,
            "microstructure_verdict": self.microstructure_verdict,
            "regime_verdict": self.regime_verdict,
            "fingerprint_verdict": self.fingerprint_verdict,
            "temporal_verdict": self.temporal_verdict,
            "lesson_rule": self.lesson_rule,
            "score": self.score,
        }


class LossAutopsyEngine:
    """Zarar sinyalleri için post-mortem + kural çıkarımı."""

    HIGH_VPIN = 0.55
    WIDE_SPREAD_BPS = 8.0
    HIGH_FINGERPRINT = 0.4
    LOW_CONFIDENCE = 0.45

    def __init__(self, db=None) -> None:
        self.db = db
        self._recent: List[AutopsyRecord] = []

    async def autopsy(
        self,
        *,
        signal: Dict[str, Any],
        barrier_result: Dict[str, Any],
        entry_context: Dict[str, Any],
        current_context: Dict[str, Any],
    ) -> Optional[AutopsyRecord]:
        """Yalnızca zarar/timeout sonuçları için çağır."""
        barrier_hit = str(barrier_result.get("barrier_hit", "timeout"))
        final_ret = float(barrier_result.get("final_return_pct", 0.0)) / 100.0
        if barrier_hit == "tp" and final_ret > 0:
            return None  # kazanan; otopsi gerekmez

        symbol = signal.get("symbol", "?")
        metadata = signal.get("metadata") or {}
        if isinstance(metadata, str):
            try: metadata = json.loads(metadata)
            except Exception: metadata = {}

        direction = metadata.get("position_bias", "long")
        entry_price = float(metadata.get("entry_price", 0) or signal.get("price", 0))
        exit_price = float(current_context.get("price", entry_price))

        root_causes: List[str] = []

        # ── 1. Microstructure verdict ──
        ms_entry = entry_context.get("microstructure") or {}
        ms_now = current_context.get("microstructure") or {}
        ms_verdict = {
            "entry_obi": ms_entry.get("obi"),
            "entry_vpin": ms_entry.get("vpin"),
            "entry_spread_bps": ms_entry.get("spread_bps"),
            "entry_kyle_lambda": ms_entry.get("kyle_lambda"),
            "entry_aggressor_buy_ratio": ms_entry.get("aggressor_buy_ratio"),
        }
        if (ms_entry.get("vpin") or 0) > self.HIGH_VPIN:
            root_causes.append(
                f"Toxic flow (VPIN={ms_entry['vpin']:.2f} > {self.HIGH_VPIN}) — bilgilendirilmiş işlemcilerin olduğu anda girdik."
            )
        if (ms_entry.get("spread_bps") or 0) > self.WIDE_SPREAD_BPS:
            root_causes.append(
                f"Geniş spread ({ms_entry['spread_bps']:.1f} bps) — fill kalitesi düşüktü."
            )
        if direction == "long" and (ms_entry.get("obi") or 0) < -0.15:
            root_causes.append("Long girildi ama order book ask-heavy (OBI negatif).")
        if direction == "short" and (ms_entry.get("obi") or 0) > 0.15:
            root_causes.append("Short girildi ama order book bid-heavy (OBI pozitif).")

        # ── 2. Regime verdict ──
        regime_entry = entry_context.get("regime") or {}
        regime_name = regime_entry.get("regime", "unknown")
        trend = float(regime_entry.get("trend_prob", 0.0))
        reg_verdict = {"regime": regime_name, "trend_prob": trend,
                       "vol_prob": regime_entry.get("vol_prob"),
                       "confidence": regime_entry.get("confidence")}
        if direction == "long" and trend < -0.2:
            root_causes.append(f"Long sinyal bear rejimde ({regime_name}, trend={trend:+.2f}).")
        if direction == "short" and trend > 0.2:
            root_causes.append(f"Short sinyal bull rejimde ({regime_name}, trend={trend:+.2f}).")

        # ── 3. Fingerprint (iceberg/spoof) verdict ──
        fp_entry = entry_context.get("fingerprint") or {}
        fp_score = float(fp_entry.get("fingerprint_score", 0.0))
        fp_verdict = {"score_at_entry": fp_score,
                      "iceberg_5m": fp_entry.get("iceberg_5m", 0),
                      "spoof_5m": fp_entry.get("spoof_5m", 0)}
        if fp_score > self.HIGH_FINGERPRINT:
            root_causes.append(
                f"Yüksek manipülasyon izi (fingerprint={fp_score:.2f}, spoof={fp_entry.get('spoof_5m', 0)} / 5dk)."
            )

        # ── 4. Temporal ──
        sig_ts = signal.get("timestamp")
        if isinstance(sig_ts, str):
            try: sig_ts = datetime.fromisoformat(sig_ts.replace("Z", "+00:00")).replace(tzinfo=None)
            except Exception: sig_ts = None
        hour = sig_ts.hour if isinstance(sig_ts, datetime) else None
        weekday = sig_ts.weekday() if isinstance(sig_ts, datetime) else None
        temp_verdict = {"hour_utc": hour, "weekday": weekday}
        if hour is not None and hour in (0, 1, 2, 22, 23):
            root_causes.append(f"Likitidenin düşük olduğu saatte girdik (UTC {hour}).")

        # ── 5. Confidence ──
        conf = float(signal.get("confidence", 0) or 0)
        if conf < self.LOW_CONFIDENCE:
            root_causes.append(f"Düşük güven skoru ({conf:.2f}) — bu bölgede geçmişte benzer sinyaller başarısız.")

        # ── 6. Lesson rule ────
        lesson = self._derive_rule(
            signal_type=signal.get("signal_type", "unknown"),
            direction=direction,
            ms_entry=ms_entry,
            regime=regime_entry,
            fingerprint=fp_entry,
            conf=conf,
            hour=hour,
        )

        # ── 7. Score (ders değeri) ──
        score = min(1.0, 0.25 + 0.15 * len(root_causes) + 0.2 * (abs(final_ret) > 0.005))

        rec = AutopsyRecord(
            signal_id=signal.get("id"),
            symbol=symbol,
            signal_type=signal.get("signal_type", "unknown"),
            direction=direction,
            entry_price=entry_price,
            exit_price=exit_price,
            loss_pct=final_ret * 100,
            barrier_hit=barrier_hit,
            duration_s=float(barrier_result.get("barrier_time_s", 0.0)),
            root_causes=root_causes or ["Belirgin mikroyapı sinyali yok; genel piyasa dalgalanması."],
            microstructure_verdict=ms_verdict,
            regime_verdict=reg_verdict,
            fingerprint_verdict=fp_verdict,
            temporal_verdict=temp_verdict,
            lesson_rule=lesson,
            score=score,
        )

        await self._persist(rec)
        self._recent.append(rec)
        if len(self._recent) > 200: self._recent = self._recent[-200:]
        logger.info(
            f"🔬 Loss autopsy {symbol} {direction} loss={final_ret*100:+.2f}% "
            f"causes={len(root_causes)} score={score:.2f}"
        )
        return rec

    def _derive_rule(
        self, *, signal_type: str, direction: str,
        ms_entry: Dict[str, Any], regime: Dict[str, Any],
        fingerprint: Dict[str, Any], conf: float, hour: Optional[int],
    ) -> Dict[str, Any]:
        """En güçlü tetikleyiciyi alıp 'avoid_if' kuralı üret."""
        candidates: List[Tuple[float, Dict[str, Any]]] = []
        if (ms_entry.get("vpin") or 0) > self.HIGH_VPIN:
            candidates.append((ms_entry["vpin"], {"feature": "vpin", "op": ">", "threshold": self.HIGH_VPIN}))
        if (ms_entry.get("spread_bps") or 0) > self.WIDE_SPREAD_BPS:
            candidates.append((ms_entry["spread_bps"], {"feature": "spread_bps", "op": ">", "threshold": self.WIDE_SPREAD_BPS}))
        if fingerprint.get("fingerprint_score", 0) > self.HIGH_FINGERPRINT:
            candidates.append((fingerprint["fingerprint_score"],
                              {"feature": "fingerprint_score", "op": ">", "threshold": self.HIGH_FINGERPRINT}))
        if direction == "long" and (ms_entry.get("obi") or 0) < -0.15:
            candidates.append((-ms_entry.get("obi", 0), {"feature": "obi", "op": "<", "threshold": -0.15}))

        candidates.sort(key=lambda x: x[0], reverse=True)
        best = candidates[0][1] if candidates else {"feature": "confidence", "op": "<", "threshold": self.LOW_CONFIDENCE}
        best["signal_type"] = signal_type
        best["direction"] = direction
        return {"avoid_if": best}

    async def _persist(self, rec: AutopsyRecord) -> None:
        if self.db is None:
            return
        try:
            async with self.db.pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO loss_autopsies
                        (signal_id, symbol, signal_type, direction, entry_price, exit_price,
                         loss_pct, barrier_hit, duration_s, root_causes, microstructure,
                         regime, fingerprint, temporal, lesson_rule, score, created_at)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16, NOW())
                    """,
                    rec.signal_id, rec.symbol, rec.signal_type, rec.direction,
                    rec.entry_price, rec.exit_price, rec.loss_pct, rec.barrier_hit,
                    rec.duration_s, json.dumps(rec.root_causes),
                    json.dumps(rec.microstructure_verdict),
                    json.dumps(rec.regime_verdict),
                    json.dumps(rec.fingerprint_verdict),
                    json.dumps(rec.temporal_verdict),
                    json.dumps(rec.lesson_rule),
                    float(rec.score),
                )
        except Exception as e:
            logger.debug(f"loss_autopsy persist skipped: {e}")

    def recent(self, limit: int = 20) -> List[Dict[str, Any]]:
        return [r.to_dict() for r in self._recent[-limit:][::-1]]

    async def active_rules(self, limit: int = 50) -> List[Dict[str, Any]]:
        """En sık tekrarlanan avoid_if kuralları."""
        if self.db is None:
            return []
        try:
            async with self.db.pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT lesson_rule, COUNT(*) AS n, AVG(score) AS avg_score
                    FROM loss_autopsies
                    WHERE created_at > NOW() - INTERVAL '7 days'
                    GROUP BY lesson_rule
                    ORDER BY n DESC LIMIT $1
                    """,
                    limit,
                )
            out = []
            for r in rows:
                lr = r["lesson_rule"]
                if isinstance(lr, str):
                    try: lr = json.loads(lr)
                    except Exception: lr = {}
                out.append({"rule": lr, "frequency": int(r["n"]), "avg_score": float(r["avg_score"] or 0)})
            return out
        except Exception as e:
            logger.debug(f"loss_autopsy active_rules skipped: {e}")
            return []

    async def health_check(self) -> Dict[str, Any]:
        count = len(self._recent)
        return {"healthy": True, "recent_autopsies": count,
                "message": f"{count} zarar otopsi kaydı in-memory"}


_engine: Optional[LossAutopsyEngine] = None


def get_loss_autopsy(db=None) -> LossAutopsyEngine:
    global _engine
    if _engine is None:
        _engine = LossAutopsyEngine(db=db)
    elif db is not None and _engine.db is None:
        _engine.db = db
    return _engine
