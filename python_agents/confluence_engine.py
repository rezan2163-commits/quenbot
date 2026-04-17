"""
confluence_engine.py — Bayesçi kanıt füzyonu (pre-move fingerprint)
====================================================================
Intel Upgrade Phase 1. Tüm pre-move sinyallerini log-odds alanında
toplayarak `confluence_score ∈ [0,1]` üretir.

    log_odds(move ≥ 2%) = Σ_i w_i · z_i + bias
    confluence_score    = σ(log_odds)

Varsayılan ağırlıklar konservatif. Faz 3 sonrasında
`confluence_weight_learner` gece SGD ile güncelleyecek.

Giriş sinyalleri (en son snapshot'tan):
    microstructure.vpin, kyle_lambda, obi, aggressor_buy_ratio
    order_flow_imbalance.ofi_1m, ofi_hurst_2h, ofi_zscore_24h
    iceberg_detector.fingerprint_score
    multi_horizon_signatures.coherence + accumulation
    hmm_regime.current_state (opsiyonel)

Yön çıkarımı:
    score_long  = σ( + weighted pozitif işaretli sinyaller )
    score_short = σ( − weighted pozitif işaretli sinyaller )
    direction   = {up|down|neutral}  (neutral bandı [0.45, 0.55])
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ──────────── varsayılan ağırlıklar ────────────
DEFAULT_WEIGHTS: Dict[str, float] = {
    "ofi_hurst_2h": 0.9,
    "ofi_zscore_24h": 0.7,
    "vpin_zscore": 0.6,
    "kyle_lambda_zscore": 0.5,
    "iceberg_fingerprint": 0.8,
    "signature_coherence": 0.7,
    "obi_drift_vs_price": 0.6,
    "aggressor_divergence": 0.5,
    "bias": -1.2,
}

NEUTRAL_BAND: Tuple[float, float] = (0.45, 0.55)


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _safe_z(x: Optional[float], default: float = 0.0, clip: float = 6.0) -> float:
    if x is None:
        return default
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    if math.isnan(v) or math.isinf(v):
        return default
    return max(-clip, min(clip, v))


@dataclass
class Contribution:
    feature: str
    z: float
    weight: float
    log_odds: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "feature": self.feature,
            "z": round(self.z, 4),
            "weight": round(self.weight, 4),
            "log_odds": round(self.log_odds, 4),
        }


@dataclass
class ConfluenceResult:
    symbol: str
    ts: float
    score: float
    direction: str   # "up" | "down" | "neutral"
    log_odds: float
    top_contributors: List[Contribution] = field(default_factory=list)
    missing_signals: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "ts": self.ts,
            "confluence_score": round(self.score, 4),
            "direction": self.direction,
            "log_odds": round(self.log_odds, 4),
            "top_contributors": [c.to_dict() for c in self.top_contributors],
            "missing_signals": self.missing_signals,
        }


def load_weights(path: str) -> Dict[str, float]:
    """Ağırlıkları JSON'dan yükle, yoksa default yaz."""
    p = Path(path)
    if not p.exists():
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(DEFAULT_WEIGHTS, indent=2), encoding="utf-8")
            logger.info("🧭 Confluence default weights yazıldı → %s", p)
        except Exception as e:
            logger.warning("confluence weights yazılamadı: %s", e)
        return dict(DEFAULT_WEIGHTS)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        # eksik anahtarları default'tan doldur
        out = dict(DEFAULT_WEIGHTS)
        for k, v in (data or {}).items():
            try:
                out[k] = float(v)
            except (TypeError, ValueError):
                continue
        return out
    except Exception as e:
        logger.warning("confluence weights okunamadı (%s), default kullanılıyor", e)
        return dict(DEFAULT_WEIGHTS)


def save_weights(path: str, weights: Dict[str, float]) -> bool:
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(weights, indent=2), encoding="utf-8")
        return True
    except Exception as e:
        logger.warning("confluence weights kaydedilemedi: %s", e)
        return False


class ConfluenceEngine:
    """Pre-move evidence fusion (Naive Bayes-style log-odds)."""

    def __init__(
        self,
        event_bus=None,
        feature_store=None,
        weights_path: str = "python_agents/.confluence_weights.json",
        publish_hz: float = 1.0,
    ) -> None:
        self.event_bus = event_bus
        self.feature_store = feature_store
        self.weights_path = weights_path
        self.weights: Dict[str, float] = load_weights(weights_path)
        self.publish_hz = max(0.1, float(publish_hz))
        self._min_publish_interval = 1.0 / self.publish_hz
        self._last_publish: Dict[str, float] = {}
        self._cache: Dict[str, ConfluenceResult] = {}
        self._total_computed = 0

    # ──────────── public API ────────────
    async def compute(self, symbol: str) -> ConfluenceResult:
        """Bu sembol için en son tüm snapshot'ları toplayıp confluence hesapla."""
        now = time.time()
        sigs, missing = self._collect_signals(symbol)
        log_odds = float(self.weights.get("bias", 0.0))
        contribs: List[Contribution] = []
        for feat, z in sigs.items():
            w = float(self.weights.get(feat, 0.0))
            if w == 0.0:
                continue
            term = w * z
            log_odds += term
            contribs.append(Contribution(feature=feat, z=z, weight=w, log_odds=term))

        score = _sigmoid(log_odds)
        if score >= NEUTRAL_BAND[1]:
            direction = "up"
        elif score <= NEUTRAL_BAND[0]:
            direction = "down"
        else:
            direction = "neutral"

        # magnitude'a göre sırala
        contribs.sort(key=lambda c: abs(c.log_odds), reverse=True)
        result = ConfluenceResult(
            symbol=symbol,
            ts=now,
            score=score,
            direction=direction,
            log_odds=log_odds,
            top_contributors=contribs[:5],
            missing_signals=missing,
        )
        self._cache[symbol] = result
        self._total_computed += 1
        return result

    async def maybe_publish(self, symbol: str) -> Optional[ConfluenceResult]:
        """Publish frekansına göre hesapla + event bus + feature_store'a yaz."""
        now = time.time()
        if now - self._last_publish.get(symbol, 0.0) < self._min_publish_interval:
            return None
        self._last_publish[symbol] = now
        res = await self.compute(symbol)

        if self.feature_store is not None:
            try:
                asyncio.create_task(self.feature_store.write(
                    symbol=symbol,
                    ts=datetime.fromtimestamp(now, tz=timezone.utc),
                    features={
                        "confluence.score": res.score,
                        "confluence.log_odds": res.log_odds,
                        "confluence.direction": res.direction,
                    },
                ))
            except Exception as e:
                logger.debug("confluence→feature_store skip: %s", e)

        if self.event_bus is not None:
            try:
                from event_bus import Event, EventType
                if hasattr(EventType, "CONFLUENCE_SCORE"):
                    await self.event_bus.publish(Event(
                        type=EventType.CONFLUENCE_SCORE,
                        source="confluence_engine",
                        data=res.to_dict(),
                    ))
            except Exception as e:
                logger.debug("confluence publish skip: %s", e)
        return res

    def snapshot(self, symbol: str) -> Optional[Dict[str, Any]]:
        r = self._cache.get(symbol)
        return r.to_dict() if r else None

    def explain(self, symbol: str) -> Optional[Dict[str, Any]]:
        """LLM prompt enjeksiyonu için kısa özet."""
        r = self._cache.get(symbol)
        if not r:
            return None
        top3 = r.top_contributors[:3]
        return {
            "symbol": symbol,
            "score": round(r.score, 3),
            "direction": r.direction,
            "top": [
                f"{c.feature}:{c.z:+.2f}σ→{c.log_odds:+.2f}" for c in top3
            ],
        }

    def reload_weights(self) -> None:
        self.weights = load_weights(self.weights_path)
        logger.info("🔁 Confluence weights yeniden yüklendi")

    async def health_check(self) -> Dict[str, Any]:
        return {
            "healthy": True,
            "tracked_symbols": len(self._cache),
            "total_computed": self._total_computed,
            "weights_keys": len(self.weights),
            "weights_path": self.weights_path,
            "message": f"{len(self._cache)} sembolde confluence aktif",
        }

    def metrics(self) -> Dict[str, Any]:
        scores = [r.score for r in self._cache.values()]
        return {
            "confluence_computed_total": self._total_computed,
            "confluence_tracked_symbols": len(self._cache),
            "confluence_score_mean": sum(scores) / len(scores) if scores else 0.0,
        }

    # ──────────── internals ────────────
    def _collect_signals(self, symbol: str) -> Tuple[Dict[str, float], List[str]]:
        """Her alt-modülün singleton'unu sorgula, z-score'a çevir.

        Eksik modüller listede `missing` olarak raporlanır; hesaplama devam eder.
        """
        z: Dict[str, float] = {}
        missing: List[str] = []

        # microstructure
        try:
            from microstructure import get_microstructure_engine
            snap = get_microstructure_engine().snapshot(symbol)
            if snap:
                z["vpin_zscore"] = _safe_z(self._vpin_to_z(snap.get("vpin")))
                z["kyle_lambda_zscore"] = _safe_z(self._kyle_to_z(snap.get("kyle_lambda")))
                # obi drift vs price (proxy: obi * sign(mid - micro))
                mid = snap.get("mid_price") or 0.0
                micro = snap.get("micro_price") or 0.0
                obi = float(snap.get("obi") or 0.0)
                drift = obi * (1.0 if micro >= mid else -1.0)
                z["obi_drift_vs_price"] = _safe_z(drift * 2.0)  # [-1,1] → rough σ scale
                buy_ratio = float(snap.get("aggressor_buy_ratio") or 0.5)
                z["aggressor_divergence"] = _safe_z((buy_ratio - 0.5) * 4.0)
            else:
                missing.append("microstructure")
        except Exception as e:
            missing.append("microstructure")
            logger.debug("confluence microstructure skip: %s", e)

        # ofi
        try:
            from order_flow_imbalance import get_ofi_engine
            snap = get_ofi_engine().snapshot(symbol)
            if snap:
                z["ofi_hurst_2h"] = _safe_z(self._hurst_to_z(snap.get("ofi_hurst_2h")))
                z["ofi_zscore_24h"] = _safe_z(snap.get("ofi_zscore_24h"))
            else:
                missing.append("ofi")
        except Exception as e:
            missing.append("ofi")
            logger.debug("confluence ofi skip: %s", e)

        # iceberg
        try:
            from iceberg_detector import get_iceberg_detector
            fp = get_iceberg_detector().fingerprint(symbol)
            if fp:
                z["iceberg_fingerprint"] = _safe_z((fp.get("fingerprint_score") or 0.0) * 3.0)
            else:
                missing.append("iceberg")
        except Exception as e:
            missing.append("iceberg")
            logger.debug("confluence iceberg skip: %s", e)

        # multi horizon
        try:
            from multi_horizon_signatures import get_multi_horizon_engine
            mh = get_multi_horizon_engine().snapshot(symbol)
            if mh:
                z["signature_coherence"] = _safe_z(float(mh.get("coherence") or 0.0) * 3.0)
            else:
                missing.append("multi_horizon")
        except Exception as e:
            missing.append("multi_horizon")
            logger.debug("confluence multi_horizon skip: %s", e)

        return z, missing

    @staticmethod
    def _vpin_to_z(vpin: Optional[float]) -> float:
        """VPIN ∈ [0,1]. 0.5 nötr, >0.7 toxic. Lineer → σ eşdeğeri."""
        if vpin is None:
            return 0.0
        return (float(vpin) - 0.5) * 4.0  # 0.75 → 1σ, 1.0 → 2σ

    @staticmethod
    def _kyle_to_z(kyle: Optional[float]) -> float:
        """Kyle lambda pozitif/büyük → toxic fiyat etkisi. Tanh ölçekleme."""
        if kyle is None:
            return 0.0
        return math.tanh(float(kyle) * 1e6) * 2.0  # scale-invariant-ish

    @staticmethod
    def _hurst_to_z(h: Optional[float]) -> float:
        """H > 0.5 persistent footprint. 0.5 → 0σ; 0.7 → +2σ; 0.3 → -2σ."""
        if h is None:
            return 0.0
        return (float(h) - 0.5) * 10.0


# ─────────── singleton ───────────
_engine: Optional[ConfluenceEngine] = None


def get_confluence_engine(
    event_bus=None,
    feature_store=None,
    weights_path: str = "python_agents/.confluence_weights.json",
    publish_hz: float = 1.0,
) -> ConfluenceEngine:
    global _engine
    if _engine is None:
        _engine = ConfluenceEngine(
            event_bus=event_bus,
            feature_store=feature_store,
            weights_path=weights_path,
            publish_hz=publish_hz,
        )
    else:
        if event_bus is not None and _engine.event_bus is None:
            _engine.event_bus = event_bus
        if feature_store is not None and _engine.feature_store is None:
            _engine.feature_store = feature_store
    return _engine
