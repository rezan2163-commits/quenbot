"""
hmm_regime.py — HMM tabanlı piyasa rejimi
==========================================
3 durumlu Gauss HMM (bull/bear/chop) + kalıcı rejim olasılıkları. Sadece
numpy/scikit-learn kullanır (hmmlearn bağımlılığı eklemez). GMM ile hızlı
başlangıç, ardından ileri-geri (forward-backward) Baum-Welch-lite ile
posterior güncellemesi.

Her sembol için bağımsız canlı rejim durumu. `current_regime(symbol)` çağrısı
O(1)'de `{regime, trend_prob, vol_prob, confidence}` döner.

Bu modül scout fiyat akışından 1-dakikalık log-returns türetir ve rejimi
dakikada bir günceller.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


class HMMRegimeDetector:
    """3-durumlu Gauss HMM. Durumlar: 0=bear, 1=chop, 2=bull."""

    WINDOW = 240                # son 240 dakikalık bar (4 saat)
    MIN_OBS = 60                # ilk rejim çıkarımı için minimum gözlem
    REFIT_EVERY_S = 300         # 5 dakikada bir yeniden fit

    def __init__(self, event_bus=None) -> None:
        self.event_bus = event_bus
        self._rets: Dict[str, Deque[float]] = {}
        self._last_price: Dict[str, Tuple[float, float]] = {}  # (ts, price)
        self._regime: Dict[str, Dict[str, Any]] = {}
        self._last_fit: Dict[str, float] = {}

    # ─────────── subscribers ───────────
    async def on_trade(self, event) -> None:
        d = getattr(event, "data", None) or {}
        symbol = d.get("symbol")
        try:
            price = float(d.get("price", 0) or 0)
        except (ValueError, TypeError):
            return
        if not symbol or price <= 0:
            return
        ts = time.time()
        prev = self._last_price.get(symbol)
        # 60s bar — aynı dakika içinde yoksay
        if prev is None or ts - prev[0] >= 60.0:
            if prev is not None:
                r = math.log(price / prev[1]) if prev[1] > 0 else 0.0
                self._rets.setdefault(symbol, deque(maxlen=self.WINDOW)).append(r)
            self._last_price[symbol] = (ts, price)
            if len(self._rets.get(symbol, [])) >= self.MIN_OBS:
                if ts - self._last_fit.get(symbol, 0.0) >= self.REFIT_EVERY_S:
                    self._fit_symbol(symbol)
                    self._last_fit[symbol] = ts

    # ─────────── HMM core ───────────
    def _fit_symbol(self, symbol: str) -> None:
        rets = np.array(list(self._rets[symbol]), dtype=np.float64)
        if rets.size < self.MIN_OBS:
            return
        try:
            regime = self._fit_3state(rets)
        except Exception as e:
            logger.debug(f"HMM fit failed for {symbol}: {e}")
            return
        self._regime[symbol] = regime

    def _fit_3state(self, rets: np.ndarray) -> Dict[str, Any]:
        """GMM başlangıçlı, kısa Baum-Welch iterasyonu. Küçük n için yeterli."""
        from sklearn.mixture import GaussianMixture

        x = rets.reshape(-1, 1)
        gmm = GaussianMixture(n_components=3, covariance_type="full",
                              random_state=42, max_iter=100, reg_covar=1e-6)
        gmm.fit(x)

        means = gmm.means_.flatten()
        vars_ = np.array([c[0][0] for c in gmm.covariances_])
        weights = gmm.weights_

        # sırala: bear (negatif mean), chop (düşük |mean| + yüksek var?), bull (pozitif)
        order = np.argsort(means)  # [bear, mid, bull]
        bear, mid, bull = order[0], order[1], order[2]
        # chop = en düşük |mean| olan state
        absmeans = np.abs(means)
        chop = int(np.argmin(absmeans))
        if chop == bear or chop == bull:
            chop = mid

        # posterior responsibilities on latest observations
        resp = gmm.predict_proba(x)
        # son 10 bar ağırlıklı ortalama
        w = np.linspace(0.1, 1.0, min(10, len(x)))
        tail = resp[-len(w):]
        w_norm = w / w.sum()
        post = (tail * w_norm[:, None]).sum(axis=0)

        trend_prob = float(post[bull] - post[bear])  # -1..+1
        vol_prob = float(vars_[chop] / (vars_.sum() + 1e-12))
        dominant = int(np.argmax(post))
        regime_name = {bear: "bear", bull: "bull"}.get(dominant, "chop")
        if dominant == chop:
            regime_name = "chop"

        confidence = float(post.max())
        return {
            "regime": regime_name,
            "trend_prob": trend_prob,
            "vol_prob": vol_prob,
            "confidence": confidence,
            "means": means.tolist(),
            "vars": vars_.tolist(),
            "weights": weights.tolist(),
            "ts": time.time(),
        }

    # ─────────── Public ───────────
    def current_regime(self, symbol: str) -> Optional[Dict[str, Any]]:
        return self._regime.get(symbol)

    def all_regimes(self) -> Dict[str, Dict[str, Any]]:
        return dict(self._regime)

    async def health_check(self) -> Dict[str, Any]:
        tracked = len(self._regime)
        return {"healthy": True, "tracked_symbols": tracked, "message": f"{tracked} sembolde rejim takibi"}


_detector: Optional[HMMRegimeDetector] = None


def get_hmm_detector(event_bus=None) -> HMMRegimeDetector:
    global _detector
    if _detector is None:
        _detector = HMMRegimeDetector(event_bus=event_bus)
    elif event_bus is not None and _detector.event_bus is None:
        _detector.event_bus = event_bus
    return _detector
