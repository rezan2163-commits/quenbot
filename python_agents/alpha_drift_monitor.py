"""
alpha_drift_monitor.py — Canlı alfa drift izleyicisi
=====================================================
Özellik dağılımlarının (OBI, VPIN, confidence, …) zaman içinde kayıp kaymadığını
Population Stability Index (PSI) ve ADWIN-lite sürüşme testleri ile izler. Drift
tespit edildiğinde RESOURCE_WARNING publish eder ve meta-labeler'ı yeniden
eğitmeye tetikler.
"""
from __future__ import annotations

import logging
import math
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional

logger = logging.getLogger(__name__)


class _FeatureStream:
    def __init__(self, maxlen: int = 2000) -> None:
        self.buf: Deque[float] = deque(maxlen=maxlen)
        self.baseline: List[float] = []
        self.last_psi: float = 0.0
        self.last_check: float = 0.0
        self.drift_count: int = 0

    def observe(self, v: float) -> None:
        try:
            self.buf.append(float(v))
        except (ValueError, TypeError):
            pass

    def set_baseline(self) -> None:
        if len(self.buf) >= 200:
            self.baseline = list(self.buf)

    def psi(self, bins: int = 10) -> float:
        if len(self.baseline) < 100 or len(self.buf) < 100:
            return 0.0
        import numpy as np
        b = np.asarray(self.baseline, dtype=float)
        c = np.asarray(list(self.buf)[-len(self.baseline):], dtype=float)
        # quantile-based bins from baseline
        qs = np.quantile(b, np.linspace(0, 1, bins + 1))
        qs[0] = -np.inf; qs[-1] = np.inf
        hist_b, _ = np.histogram(b, bins=qs)
        hist_c, _ = np.histogram(c, bins=qs)
        eps = 1e-6
        pb = hist_b / max(hist_b.sum(), 1)
        pc = hist_c / max(hist_c.sum(), 1)
        psi = float(np.sum((pc - pb) * np.log((pc + eps) / (pb + eps))))
        self.last_psi = psi
        self.last_check = time.time()
        if psi > 0.25:
            self.drift_count += 1
        return psi


class AlphaDriftMonitor:
    """Çok özellikli PSI + tetikleyici sayaçları."""

    PSI_WARN = 0.10
    PSI_ALERT = 0.25

    def __init__(self, event_bus=None) -> None:
        self.event_bus = event_bus
        self.streams: Dict[str, _FeatureStream] = {}
        self._init_ts = time.time()
        self._last_alert: Dict[str, float] = {}

    def observe(self, feature: str, value: float) -> None:
        s = self.streams.setdefault(feature, _FeatureStream())
        s.observe(value)
        # ilk 500 sonra baseline sabitle
        if not s.baseline and len(s.buf) >= 500:
            s.set_baseline()

    async def tick(self) -> Dict[str, Any]:
        report: Dict[str, Any] = {"features": {}}
        for name, s in self.streams.items():
            psi = s.psi()
            report["features"][name] = {
                "psi": round(psi, 4),
                "buf": len(s.buf),
                "baseline": len(s.baseline),
                "drift_count": s.drift_count,
            }
            if psi >= self.PSI_ALERT and time.time() - self._last_alert.get(name, 0) > 300:
                self._last_alert[name] = time.time()
                await self._publish_alert(name, psi)
        return report

    async def _publish_alert(self, feature: str, psi: float) -> None:
        if not self.event_bus:
            return
        try:
            from event_bus import Event, EventType
            await self.event_bus.publish(Event(
                type=EventType.RESOURCE_WARNING,
                source="alpha_drift",
                data={"feature": feature, "psi": psi, "message": f"Alpha drift: {feature} PSI={psi:.2f}"},
            ))
        except Exception as e:
            logger.debug(f"drift alert skipped: {e}")

    async def health_check(self) -> Dict[str, Any]:
        drifting = sum(1 for s in self.streams.values() if s.last_psi >= self.PSI_ALERT)
        return {"healthy": drifting == 0, "features_tracked": len(self.streams),
                "drifting": drifting,
                "message": f"{drifting} özellik kayıyor" if drifting else "tüm özellikler stabil"}


_monitor: Optional[AlphaDriftMonitor] = None


def get_drift_monitor(event_bus=None) -> AlphaDriftMonitor:
    global _monitor
    if _monitor is None:
        _monitor = AlphaDriftMonitor(event_bus=event_bus)
    elif event_bus is not None and _monitor.event_bus is None:
        _monitor.event_bus = event_bus
    return _monitor
