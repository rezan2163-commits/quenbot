"""
meta_labeler.py — Meta-Labeling (López de Prado, 2018)
=======================================================
İkinci aşama sınıflandırıcı: Strategist primer sinyali üretir, meta-labeler
"Bu sinyali tutmalı mıyız?" sorusuna evet/hayır döner. Yanlış pozitifleri
(false alarm) keser, recall'u hedeflenen seviyede tutarken precision'u yükseltir.

Modelin tek bağımlılığı sklearn (requirements'te var). Offline öğrenme:
`brain_learning_log` tablosundan okur, `context` JSONB içine gömülmüş
microstructure özellikleriyle birlikte barrier-hit etiketini kullanır.
"""
from __future__ import annotations

import logging
import math
import pickle
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_MODEL_PATH = Path(__file__).parent / "data" / "meta_labeler.pkl"
_FEATURE_KEYS = [
    "confidence", "obi", "vpin", "kyle_lambda", "aggressor_buy_ratio",
    "spread_bps", "trade_intensity", "regime_trend_prob",
    "regime_vol_prob", "hist_accuracy", "hist_avg_pnl",
]


class MetaLabeler:
    """Gradient Boosted Trees ikili sınıflandırıcı (barrier_hit == 'tp')."""

    def __init__(self) -> None:
        self._model = None
        self._fitted_at: Optional[float] = None
        self._fit_samples: int = 0
        self._version: int = 0
        self._min_accept_proba: float = 0.55
        self._degenerate: bool = False
        self._degenerate_reason: Optional[str] = None
        self._load()

    # ─────────── Inference ───────────
    def predict(self, features: Dict[str, float]) -> Dict[str, Any]:
        """Return {'accept': bool, 'proba': float, 'reason': str}."""
        if self._model is None:
            return {"accept": True, "proba": 0.5, "reason": "meta_labeler_untrained", "version": 0}
        if self._degenerate:
            # Keep meta-labeler advisory-only when model carries no information.
            return {
                "accept": True,
                "proba": 0.5,
                "reason": f"meta_labeler_degenerate:{self._degenerate_reason or 'unknown'}",
                "version": self._version,
            }
        try:
            x = [[float(features.get(k, 0.0) or 0.0) for k in _FEATURE_KEYS]]
            proba = float(self._model.predict_proba(x)[0][1])
        except Exception as e:
            logger.debug(f"meta_labeler predict skipped: {e}")
            return {"accept": True, "proba": 0.5, "reason": f"predict_error:{e}", "version": self._version}
        accept = proba >= self._min_accept_proba
        return {
            "accept": accept,
            "proba": proba,
            "reason": "meta_ok" if accept else "meta_low_prob",
            "version": self._version,
        }

    # ─────────── Training ───────────
    def fit(self, samples: List[Tuple[Dict[str, float], int]]) -> Dict[str, Any]:
        """
        samples: [(features_dict, label_0_or_1), ...]
        label = 1 if barrier_hit == 'tp' else 0
        """
        try:
            from sklearn.ensemble import GradientBoostingClassifier
        except ImportError:
            return {"ok": False, "reason": "sklearn_missing"}

        if len(samples) < 40:
            return {"ok": False, "reason": f"insufficient_samples:{len(samples)}"}

        X = [[float(s[0].get(k, 0.0) or 0.0) for k in _FEATURE_KEYS] for s in samples]
        y = [int(s[1]) for s in samples]
        pos = sum(y); neg = len(y) - pos
        if pos < 5 or neg < 5:
            return {"ok": False, "reason": f"imbalanced:{pos}/{neg}"}

        model = GradientBoostingClassifier(
            n_estimators=60,
            max_depth=3,
            learning_rate=0.05,
            subsample=0.8,
            random_state=42,
        )
        try:
            model.fit(X, y)
        except Exception as e:
            return {"ok": False, "reason": f"fit_error:{e}"}

        self._model = model
        self._fitted_at = time.time()
        self._fit_samples = len(samples)
        self._version += 1
        self._evaluate_model_health()
        # calibrate accept threshold to target ~0.35 precision lift
        try:
            probs = model.predict_proba(X)[:, 1]
            pairs = sorted(zip(probs, y), reverse=True)
            # pick threshold with top-50% precision
            cut = int(len(pairs) * 0.5)
            top = pairs[:cut] if cut >= 5 else pairs
            if top:
                thr = min(p for p, _ in top)
                self._min_accept_proba = float(max(0.5, min(0.8, thr)))
        except Exception:
            self._min_accept_proba = 0.55

        self._save()
        logger.info(
            f"🧪 MetaLabeler fit v{self._version} | n={self._fit_samples} "
            f"thr={self._min_accept_proba:.3f} (pos={pos}, neg={neg})"
        )
        return {
            "ok": True, "version": self._version, "samples": self._fit_samples,
            "threshold": self._min_accept_proba, "pos": pos, "neg": neg,
        }

    # ─────────── Persistence ───────────
    def _save(self) -> None:
        try:
            _MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(_MODEL_PATH, "wb") as f:
                pickle.dump({
                    "model": self._model,
                    "version": self._version,
                    "threshold": self._min_accept_proba,
                    "fitted_at": self._fitted_at,
                    "samples": self._fit_samples,
                }, f)
        except Exception as e:
            logger.debug(f"meta_labeler save skipped: {e}")

    def _load(self) -> None:
        try:
            if not _MODEL_PATH.exists():
                return
            with open(_MODEL_PATH, "rb") as f:
                d = pickle.load(f)
            self._model = d.get("model")
            self._version = int(d.get("version", 0))
            self._min_accept_proba = float(d.get("threshold", 0.55))
            self._fitted_at = d.get("fitted_at")
            self._fit_samples = int(d.get("samples", 0))
            self._evaluate_model_health()
            logger.info(f"🧪 MetaLabeler loaded v{self._version} (n={self._fit_samples})")
        except Exception as e:
            logger.debug(f"meta_labeler load skipped: {e}")

    def _evaluate_model_health(self) -> None:
        self._degenerate = False
        self._degenerate_reason = None
        if self._model is None:
            return
        try:
            importances = getattr(self._model, "feature_importances_", None)
            if importances is not None and len(importances) > 0:
                total_importance = float(sum(abs(float(v)) for v in importances))
                if total_importance <= 1e-12:
                    self._degenerate = True
                    self._degenerate_reason = "zero_feature_importance"
                    return

            n_features = int(getattr(self._model, "n_features_in_", len(_FEATURE_KEYS)))
            probes = [
                [0.0] * n_features,
                [0.5] * n_features,
                [1.0] * n_features,
            ]
            probs = [float(self._model.predict_proba([row])[0][1]) for row in probes]
            if max(probs) - min(probs) < 1e-6:
                self._degenerate = True
                self._degenerate_reason = "constant_probability_surface"
        except Exception as e:
            logger.debug(f"meta_labeler health check skipped: {e}")

    # ─────────── Health ───────────
    def status(self) -> Dict[str, Any]:
        return {
            "trained": self._model is not None,
            "version": self._version,
            "samples": self._fit_samples,
            "threshold": self._min_accept_proba,
            "fitted_at": self._fitted_at,
            "degenerate": self._degenerate,
            "degenerate_reason": self._degenerate_reason,
        }


_labeler: Optional[MetaLabeler] = None


def get_meta_labeler() -> MetaLabeler:
    global _labeler
    if _labeler is None:
        _labeler = MetaLabeler()
    return _labeler
