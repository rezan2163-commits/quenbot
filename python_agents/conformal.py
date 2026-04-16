"""
conformal.py — Split-Conformal Prediction
==========================================
Model-agnostic güven aralığı: geçmiş `|tahmin - gerçek|` hatalarının
ampirik kuantili üzerinden her yeni sinyal için (1-α) kapsama garantili
bant üretir. Brain bunu "bu sinyale ne kadar güvenebilirim" soru­sunda
istatistiksel olarak destekler.

Burada kullanımı: strategist'in `confidence` çıktısı ile gerçek barrier
sonucu (1 = tp, 0 = sl/timeout) arasındaki kalibrasyon hatasını takip eder.
`predict_interval(confidence)` → (lo, hi) probability bandı.
"""
from __future__ import annotations

import logging
import math
from collections import deque
from typing import Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class SplitConformal:
    """Sınıflandırma için kalibrasyon: absolute residuals."""

    def __init__(self, alpha: float = 0.1, max_history: int = 500) -> None:
        self.alpha = alpha
        self._res: Deque[float] = deque(maxlen=max_history)
        self._paired: Deque[Tuple[float, float]] = deque(maxlen=max_history)  # (confidence, outcome)

    def record(self, confidence: float, outcome: int) -> None:
        """outcome: 1 = success, 0 = fail."""
        c = max(0.0, min(1.0, float(confidence)))
        y = 1 if outcome else 0
        self._res.append(abs(y - c))
        self._paired.append((c, float(y)))

    def calibration_error(self) -> float:
        if not self._paired:
            return 0.0
        # expected calibration error (10 bin)
        bins = [[] for _ in range(10)]
        for c, y in self._paired:
            idx = min(9, int(c * 10))
            bins[idx].append((c, y))
        total = 0.0
        n = len(self._paired)
        for b in bins:
            if not b: continue
            conf = sum(x[0] for x in b) / len(b)
            acc = sum(x[1] for x in b) / len(b)
            total += (len(b) / n) * abs(conf - acc)
        return total

    def predict_interval(self, confidence: float) -> Tuple[float, float, float]:
        """Return (lo, hi, quantile_residual). Kapsama (1-alpha)."""
        if len(self._res) < 20:
            return (max(0.0, confidence - 0.2), min(1.0, confidence + 0.2), 0.2)
        sorted_res = sorted(self._res)
        k = int(math.ceil((1 - self.alpha) * (len(sorted_res) + 1))) - 1
        k = max(0, min(len(sorted_res) - 1, k))
        q = sorted_res[k]
        return (max(0.0, confidence - q), min(1.0, confidence + q), q)

    def snapshot(self) -> Dict[str, float]:
        return {
            "n": len(self._res),
            "alpha": self.alpha,
            "calibration_error": round(self.calibration_error(), 4),
            "median_residual": round(sorted(self._res)[len(self._res)//2], 4) if self._res else 0.0,
        }


_conformal: Optional[SplitConformal] = None


def get_conformal(alpha: float = 0.1) -> SplitConformal:
    global _conformal
    if _conformal is None:
        _conformal = SplitConformal(alpha=alpha)
    return _conformal
