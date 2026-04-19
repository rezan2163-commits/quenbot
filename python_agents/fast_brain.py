"""
Fast Brain — Phase 3 Intel Upgrade
===================================
LightGBM tabanlı, kalibre edilmiş **hızlı tahmin motoru**.

Amaç: <5 ms içinde yönsel güvenli bir olasılık üret. LLM'e (Gemma ~200-800 ms)
her işlem için gitmek yerine, yüksek güvenli sinyallerde LLM'i atlamak
(DecisionRouter tarafından yönetilir).

Mimari:
  - Model dosyası `.lgb` (LightGBM booster) + `.calib.json` (kalibrasyon).
  - Feature vektörü canlı olarak confluence/ofi/mh/cross-asset singleton'larından
    toplanır (feature_store'u beklemez — hot path).
  - Kalibrasyon: Platt-style `σ(a·score + b)` veya isotonic pickled lookup
    desteklenir; kalibrasyon yoksa raw booster çıktısı döner.

Güvenlik:
  - LightGBM veya model dosyası yoksa motor `enabled=False` döner, hiçbir
    tahmin yapmaz. Decision router bu durumda klasik LLM yoluna düşer.
  - Her predict çağrısı try/except ile sarılı; hata sinyali sessizce yutulur.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    import lightgbm as lgb  # type: ignore
    _HAS_LGB = True
except Exception:
    _HAS_LGB = False

try:
    import numpy as np  # type: ignore
    _HAS_NUMPY = True
except Exception:
    _HAS_NUMPY = False

logger = logging.getLogger(__name__)

# ─────────── default feature order (training & inference must agree) ───────────
DEFAULT_FEATURE_ORDER: Tuple[str, ...] = (
    "ofi_hurst_2h",
    "ofi_zscore_24h",
    "vpin_zscore",
    "kyle_lambda_zscore",
    "iceberg_fingerprint",
    "signature_coherence",
    "obi_drift_vs_price",
    "aggressor_divergence",
    "cross_asset_spillover",
    "confluence_score",
    "confluence_log_odds",
    "mh_coherence",
)


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    z = math.exp(x)
    return z / (1.0 + z)


@dataclass
class FastBrainPrediction:
    symbol: str
    probability: float       # kalibrasyon sonrası "yukarı" olasılığı [0,1]
    direction: str           # up|down|neutral
    raw_score: float         # kalibrasyondan önce
    confidence: float        # |p - 0.5| * 2 ∈ [0,1]
    features_used: int
    missing_features: List[str] = field(default_factory=list)
    ts: float = 0.0
    latency_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "probability": round(self.probability, 4),
            "direction": self.direction,
            "raw_score": round(self.raw_score, 4),
            "confidence": round(self.confidence, 4),
            "features_used": self.features_used,
            "missing_features": self.missing_features,
            "ts": self.ts,
            "latency_ms": round(self.latency_ms, 3),
        }


@dataclass
class _Calibration:
    method: str = "none"     # "platt" | "isotonic" | "none"
    a: float = 1.0
    b: float = 0.0
    isotonic_x: List[float] = field(default_factory=list)
    isotonic_y: List[float] = field(default_factory=list)

    def apply(self, raw: float) -> float:
        if self.method == "platt":
            return _sigmoid(self.a * raw + self.b)
        if self.method == "isotonic" and self.isotonic_x:
            # linear interpolation
            xs, ys = self.isotonic_x, self.isotonic_y
            if raw <= xs[0]:
                return ys[0]
            if raw >= xs[-1]:
                return ys[-1]
            # binary search
            lo, hi = 0, len(xs) - 1
            while lo < hi - 1:
                mid = (lo + hi) // 2
                if xs[mid] <= raw:
                    lo = mid
                else:
                    hi = mid
            x0, x1 = xs[lo], xs[hi]
            y0, y1 = ys[lo], ys[hi]
            if x1 == x0:
                return y0
            return y0 + (y1 - y0) * (raw - x0) / (x1 - x0)
        # default: clamp raw into [0,1] (if it's already a probability)
        if 0.0 <= raw <= 1.0:
            return raw
        return _sigmoid(raw)


class FastBrainEngine:
    """LightGBM booster + Platt/isotonic kalibrasyon."""

    def __init__(
        self,
        model_path: str,
        calibration_path: Optional[str] = None,
        feature_order: Tuple[str, ...] = DEFAULT_FEATURE_ORDER,
        t_high: float = 0.65,
        t_low: float = 0.45,
        min_features: int = 4,
        event_bus=None,
    ) -> None:
        self.model_path = model_path
        self.calibration_path = calibration_path
        self.feature_order = tuple(feature_order)
        self.t_high = float(t_high)
        self.t_low = float(t_low)
        self.min_features = int(min_features)
        self.event_bus = event_bus

        self._booster = None
        self._calibration = _Calibration()
        self._enabled = False
        self._model_feature_order: Tuple[str, ...] = self.feature_order
        self._model_loaded_ts: float = 0.0
        self._total_predictions = 0
        self._total_errors = 0
        self._last_prediction: Dict[str, FastBrainPrediction] = {}

        if not _HAS_LGB:
            logger.warning("FastBrain: lightgbm yüklü değil — motor devre dışı")
            return

        self._load_model()
        self._load_calibration()

    def _load_model(self) -> None:
        try:
            if not Path(self.model_path).exists():
                logger.info("FastBrain: model dosyası yok (%s) — motor dormant", self.model_path)
                return
            self._booster = lgb.Booster(model_file=self.model_path)
            try:
                feature_names = tuple(
                    str(name) for name in (self._booster.feature_name() or [])
                    if str(name) in self.feature_order
                )
                if feature_names:
                    self._model_feature_order = feature_names
                if self._model_feature_order != self.feature_order:
                    logger.info(
                        "FastBrain runtime feature order model ile hizalandi: %d -> %d",
                        len(self.feature_order),
                        len(self._model_feature_order),
                    )
            except Exception as e:
                logger.warning("FastBrain feature order okunamadı: %s", e)
            self._enabled = True
            self._model_loaded_ts = time.time()
            logger.info("🧠 FastBrain modeli yüklendi: %s (features=%d)",
                        self.model_path, self._booster.num_feature())
        except Exception as e:
            logger.warning("FastBrain model yüklenemedi: %s", e)
            self._booster = None
            self._enabled = False

    def _load_calibration(self) -> None:
        if not self.calibration_path or not Path(self.calibration_path).exists():
            return
        try:
            data = json.loads(Path(self.calibration_path).read_text(encoding="utf-8"))
            self._calibration = _Calibration(
                method=str(data.get("method", "none")).lower(),
                a=float(data.get("a", 1.0)),
                b=float(data.get("b", 0.0)),
                isotonic_x=list(map(float, data.get("isotonic_x", []))),
                isotonic_y=list(map(float, data.get("isotonic_y", []))),
            )
            logger.info("FastBrain kalibrasyon: %s", self._calibration.method)
        except Exception as e:
            logger.warning("FastBrain kalibrasyon okunamadı: %s", e)

    def reload(self) -> bool:
        """Dosyadan modeli + kalibrasyonu yeniden yükle (hot swap)."""
        self._booster = None
        self._enabled = False
        self._calibration = _Calibration()
        self._load_model()
        self._load_calibration()
        return self._enabled

    # ──────────── public API ────────────
    @property
    def enabled(self) -> bool:
        return self._enabled

    def collect_features(self, symbol: str) -> Tuple[Dict[str, float], List[str]]:
        """Canlı singleton'lardan feature vektörü topla. Eksik → missing."""
        feats: Dict[str, float] = {}
        missing: List[str] = []

        # Microstructure
        try:
            from microstructure import get_microstructure_engine
            snap = get_microstructure_engine().snapshot(symbol)
            if snap:
                vpin = snap.get("vpin")
                if vpin is not None:
                    feats["vpin_zscore"] = (float(vpin) - 0.5) * 4.0
                kyle = snap.get("kyle_lambda")
                if kyle is not None:
                    try:
                        feats["kyle_lambda_zscore"] = math.tanh(float(kyle) * 1e6) * 2.0
                    except Exception:
                        pass
                mid = float(snap.get("mid_price") or 0.0)
                micro = float(snap.get("micro_price") or 0.0)
                obi = float(snap.get("obi") or 0.0)
                feats["obi_drift_vs_price"] = obi * (1.0 if micro >= mid else -1.0) * 2.0
                br = float(snap.get("aggressor_buy_ratio") or 0.5)
                feats["aggressor_divergence"] = (br - 0.5) * 4.0
        except Exception:
            pass

        # OFI
        try:
            from order_flow_imbalance import get_ofi_engine
            snap = get_ofi_engine().snapshot(symbol)
            if snap:
                h = snap.get("ofi_hurst_2h")
                if h is not None:
                    feats["ofi_hurst_2h"] = (float(h) - 0.5) * 10.0
                z = snap.get("ofi_zscore_24h")
                if z is not None:
                    feats["ofi_zscore_24h"] = float(z)
        except Exception:
            pass

        # Iceberg
        try:
            from iceberg_detector import get_iceberg_detector
            fp = get_iceberg_detector().fingerprint(symbol)
            if fp:
                s = fp.get("fingerprint_score")
                if s is not None:
                    feats["iceberg_fingerprint"] = float(s) * 3.0
        except Exception:
            pass

        # Multi-horizon
        try:
            from multi_horizon_signatures import get_multi_horizon_engine
            mh = get_multi_horizon_engine().snapshot(symbol)
            if mh:
                coh = mh.get("coherence")
                if coh is not None:
                    feats["signature_coherence"] = float(coh) * 3.0
                    feats["mh_coherence"] = float(coh)
        except Exception:
            pass

        # Cross-asset spillover
        try:
            from cross_asset_graph import get_cross_asset_engine
            spill = get_cross_asset_engine().spillover_signal(symbol)
            if spill != 0.0:
                feats["cross_asset_spillover"] = float(spill)
        except Exception:
            pass

        # Confluence (varsa)
        try:
            from confluence_engine import get_confluence_engine
            snap = get_confluence_engine().snapshot(symbol)
            if snap:
                score = snap.get("confluence_score")
                if score is not None:
                    feats["confluence_score"] = float(score)
                lo = snap.get("log_odds")
                if lo is not None:
                    feats["confluence_log_odds"] = float(lo)
        except Exception:
            pass

        for name in self.feature_order:
            if name not in feats:
                missing.append(name)
        return feats, missing

    def predict(self, symbol: str, features: Optional[Dict[str, float]] = None) -> Optional[FastBrainPrediction]:
        """Tek bir sembol için tahmin. Motor kapalıysa None döner."""
        if not self._enabled or self._booster is None:
            return None
        t0 = time.perf_counter()
        try:
            active_feature_order = self._model_feature_order or self.feature_order
            if features is None:
                features, _ = self.collect_features(symbol)
            else:
                features = dict(features)

            missing = [n for n in active_feature_order if n not in features]
            used = sum(1 for n in active_feature_order if n in features)
            if used < self.min_features:
                return None

            if _HAS_NUMPY:
                vec = np.array([[features.get(n, 0.0) for n in active_feature_order]],
                               dtype=np.float32)
            else:
                vec = [[features.get(n, 0.0) for n in active_feature_order]]

            raw = float(self._booster.predict(vec, num_iteration=self._booster.best_iteration)[0])
            prob = self._calibration.apply(raw)
            prob = max(0.0, min(1.0, prob))

            if prob >= self.t_high:
                direction = "up"
            elif prob <= self.t_low:
                direction = "down"
            else:
                direction = "neutral"

            conf = abs(prob - 0.5) * 2.0
            pred = FastBrainPrediction(
                symbol=symbol,
                probability=prob,
                direction=direction,
                raw_score=raw,
                confidence=conf,
                features_used=used,
                missing_features=missing,
                ts=time.time(),
                latency_ms=(time.perf_counter() - t0) * 1000.0,
            )
            self._total_predictions += 1
            self._last_prediction[symbol] = pred
            return pred
        except Exception as e:
            self._total_errors += 1
            logger.debug("FastBrain predict %s hata: %s", symbol, e)
            return None

    async def publish_prediction(self, pred: FastBrainPrediction) -> None:
        if self.event_bus is None:
            return
        try:
            from event_bus import Event, EventType
            if hasattr(EventType, "FAST_BRAIN_PREDICTION"):
                await self.event_bus.publish(Event(
                    type=EventType.FAST_BRAIN_PREDICTION,
                    source="fast_brain",
                    data=pred.to_dict(),
                ))
        except Exception as e:
            logger.debug("fast_brain publish skip: %s", e)

    def snapshot(self, symbol: str) -> Optional[Dict[str, Any]]:
        p = self._last_prediction.get(symbol)
        return p.to_dict() if p else None

    async def health_check(self) -> Dict[str, Any]:
        return {
            "healthy": self._enabled,
            "lightgbm_available": _HAS_LGB,
            "model_loaded": self._booster is not None,
            "model_path": self.model_path,
            "model_loaded_ts": self._model_loaded_ts,
            "calibration": self._calibration.method,
            "feature_order_size": len(self.feature_order),
            "model_feature_order_size": len(self._model_feature_order),
            "total_predictions": self._total_predictions,
            "total_errors": self._total_errors,
            "tracked_symbols": len(self._last_prediction),
            "t_high": self.t_high,
            "t_low": self.t_low,
            "message": "FastBrain aktif" if self._enabled else "FastBrain dormant (model yok)",
        }

    def metrics(self) -> Dict[str, Any]:
        return {
            "fast_brain_predictions_total": self._total_predictions,
            "fast_brain_errors_total": self._total_errors,
            "fast_brain_tracked_symbols": len(self._last_prediction),
            "fast_brain_enabled": 1 if self._enabled else 0,
        }


_engine: Optional[FastBrainEngine] = None


def get_fast_brain_engine(*args, **kwargs) -> FastBrainEngine:
    global _engine
    if _engine is None:
        _engine = FastBrainEngine(*args, **kwargs)
    return _engine


def _reset_fast_brain_engine_for_tests() -> None:
    global _engine
    _engine = None
