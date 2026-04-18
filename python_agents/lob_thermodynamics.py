"""
lob_thermodynamics.py — Emir defteri termodinamiği (§3)
========================================================
Top-N level LOB kütleleri üzerinde Shannon entropisi ve entropi üretim hızı:

    H(t) = -Σ p_i log p_i   (p_i = q_i / Σ q_j, top-N=20 default)
    σ̇(t) = (H(t) - H(t-Δ)) / Δ   (10 sn fark)

Sürdürülen negatif σ̇ > ε_threshold süresi ≥ window_sec ise "soğuma rejimi"
(bilgili emilim). 1-saatlik rolling baseline ile Jensen-Shannon diverjansı.

Operasyonel rol:
    EventType.LOB_THERMODYNAMIC_STATE yayınlar.
    OracleSignalBus kanalı: 'entropy_cooling' ∈ [0,1] yoğunluk.

Graceful degradation:
    numpy yoksa pure-python. Top-N yetmeyen snapshot'lar atlanır.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


def _shannon(levels: Sequence[float]) -> float:
    total = sum(abs(float(x)) for x in levels)
    if total <= 0:
        return 0.0
    h = 0.0
    for q in levels:
        p = abs(float(q)) / total
        if p > 0:
            h -= p * math.log(p)
    return h


def _js_divergence(p: Sequence[float], q: Sequence[float]) -> float:
    n = min(len(p), len(q))
    if n == 0:
        return 0.0
    sp = sum(abs(float(x)) for x in p[:n]) or 1.0
    sq = sum(abs(float(x)) for x in q[:n]) or 1.0
    pp = [abs(float(x)) / sp for x in p[:n]]
    qq = [abs(float(x)) / sq for x in q[:n]]
    mm = [(a + b) / 2.0 for a, b in zip(pp, qq)]
    def kl(a: List[float], m: List[float]) -> float:
        s = 0.0
        for ai, mi in zip(a, m):
            if ai > 0 and mi > 0:
                s += ai * math.log(ai / mi)
        return s
    return 0.5 * (kl(pp, mm) + kl(qq, mm))


@dataclass
class _SymbolState:
    # (ts, H, top_levels[:N]) ring
    history: Deque[Tuple[float, float, List[float]]] = field(default_factory=lambda: deque(maxlen=4096))
    last_publish_ts: float = 0.0
    last_entropy: float = 0.0
    last_sigma_dot: float = 0.0
    last_js: float = 0.0
    cooling_since_ts: Optional[float] = None
    last_intensity: float = 0.0


class LOBThermodynamics:
    PUBLISH_HZ_DEFAULT = 0.5
    ORACLE_CHANNEL_NAME = "entropy_cooling"

    def __init__(
        self,
        event_bus: Any = None,
        feature_store: Any = None,
        signal_bus: Any = None,
        cooling_window_sec: int = 180,
        cooling_threshold: float = 1e-4,
        levels: int = 20,
        publish_hz: float = 0.5,
        baseline_sec: int = 3600,
        dt_sec: float = 10.0,
    ) -> None:
        self.event_bus = event_bus
        self.feature_store = feature_store
        self.signal_bus = signal_bus
        self.cooling_window_sec = int(cooling_window_sec)
        self.cooling_threshold = float(cooling_threshold)
        self.levels = int(levels)
        self.publish_interval = 1.0 / max(0.01, publish_hz)
        self.baseline_sec = int(baseline_sec)
        self.dt_sec = float(dt_sec)
        self._states: Dict[str, _SymbolState] = {}
        self._stats = {"snapshots": 0, "publishes": 0, "cooling_events": 0}
        self._initialized = False

    async def initialize(self) -> None:
        self._initialized = True
        logger.info(
            "LOBThermodynamics ready (N=%d, cooling_window=%ds, threshold=%.2e)",
            self.levels, self.cooling_window_sec, self.cooling_threshold,
        )

    def observe(
        self,
        symbol: str,
        bids: Sequence[float],
        asks: Sequence[float],
        ts: Optional[float] = None,
    ) -> None:
        """Top-N bid + ask hacimlerinden birleşik entropy ölç."""
        if not symbol:
            return
        ts = float(ts) if ts is not None else time.time()
        combined = list(bids[: self.levels]) + list(asks[: self.levels])
        if len(combined) < self.levels:
            return
        H = _shannon(combined)
        st = self._states.setdefault(symbol, _SymbolState())
        st.history.append((ts, H, combined))
        self._stats["snapshots"] += 1

    def maybe_publish(self, symbol: str, ts: Optional[float] = None) -> Optional[Dict[str, Any]]:
        st = self._states.get(symbol)
        if st is None or len(st.history) < 4:
            return None
        ts = float(ts) if ts is not None else time.time()
        if (ts - st.last_publish_ts) < self.publish_interval:
            return None
        # current + lagged reference (≥ dt_sec ago)
        cur_ts, cur_H, cur_lvls = st.history[-1]
        ref = None
        for t_r, H_r, _ in reversed(st.history):
            if cur_ts - t_r >= self.dt_sec:
                ref = (t_r, H_r)
                break
        if ref is None:
            return None
        sigma_dot = (cur_H - ref[1]) / max(1e-6, cur_ts - ref[0])
        # baseline avg levels over last baseline_sec
        base_lo = cur_ts - self.baseline_sec
        base_snapshots = [lvls for (t, _, lvls) in st.history if t >= base_lo]
        if base_snapshots:
            n = len(base_snapshots[0])
            base_avg = [0.0] * n
            for lvls in base_snapshots:
                for i in range(n):
                    base_avg[i] += float(lvls[i])
            base_avg = [x / len(base_snapshots) for x in base_avg]
            js = _js_divergence(cur_lvls, base_avg)
        else:
            js = 0.0
        # cooling detection
        if sigma_dot < -self.cooling_threshold:
            st.cooling_since_ts = st.cooling_since_ts or cur_ts
        else:
            st.cooling_since_ts = None
        cooling_duration = (cur_ts - st.cooling_since_ts) if st.cooling_since_ts else 0.0
        intensity = min(1.0, cooling_duration / max(1.0, self.cooling_window_sec))
        st.last_publish_ts = ts
        st.last_entropy = cur_H
        st.last_sigma_dot = sigma_dot
        st.last_js = js
        st.last_intensity = intensity
        if self.signal_bus is not None:
            try:
                self.signal_bus.publish(
                    symbol=symbol,
                    channel=self.ORACLE_CHANNEL_NAME,
                    value=intensity,
                    source="lob_thermodynamics",
                    extra={"H": cur_H, "sigma_dot": sigma_dot, "js": js, "cooling_duration": cooling_duration},
                )
            except Exception as e:
                logger.debug("LOBThermo signal_bus skip: %s", e)
        self._stats["publishes"] += 1
        evt: Optional[Dict[str, Any]] = None
        if intensity >= 1.0:
            self._stats["cooling_events"] += 1
            evt = {"symbol": symbol, "ts": cur_ts, "H": cur_H, "sigma_dot": sigma_dot, "cooling_duration": cooling_duration, "js": js}
            if self.event_bus is not None:
                try:
                    from event_bus import EventType, Event
                    asyncio.create_task(
                        self.event_bus.publish(
                            Event(type=EventType.LOB_THERMODYNAMIC_STATE, source="lob_thermodynamics", data=evt)
                        )
                    )
                except Exception as e:
                    logger.debug("LOBThermo event skip: %s", e)
        if self.feature_store is not None:
            try:
                from datetime import datetime, timezone
                self.feature_store.write(
                    symbol=symbol,
                    ts=datetime.fromtimestamp(cur_ts, tz=timezone.utc),
                    features={
                        "lob_entropy": cur_H,
                        "lob_sigma_dot": sigma_dot,
                        "lob_js": js,
                        "lob_cooling_intensity": intensity,
                    },
                )
            except Exception as e:
                logger.debug("LOBThermo fs skip: %s", e)
        return evt or {"symbol": symbol, "ts": cur_ts, "intensity": intensity}

    def snapshot(self, symbol: str) -> Optional[Dict[str, Any]]:
        st = self._states.get(symbol)
        if st is None:
            return None
        return {
            "symbol": symbol,
            "entropy": st.last_entropy,
            "sigma_dot": st.last_sigma_dot,
            "js_vs_baseline": st.last_js,
            "cooling_intensity": st.last_intensity,
            "history": len(st.history),
        }

    def all_snapshots(self) -> Dict[str, Dict[str, Any]]:
        return {sym: self.snapshot(sym) for sym in self._states}

    def oracle_channel_value(self, symbol: str) -> Optional[float]:
        st = self._states.get(symbol)
        return None if st is None else st.last_intensity

    async def health_check(self) -> Dict[str, Any]:
        return {"healthy": self._initialized, "symbols": len(self._states), **self._stats}

    def metrics(self) -> Dict[str, Any]:
        return {
            "lob_thermo_snapshots_total": self._stats["snapshots"],
            "lob_thermo_publishes_total": self._stats["publishes"],
            "lob_thermo_cooling_events_total": self._stats["cooling_events"],
            "lob_thermo_symbols_active": len(self._states),
        }


_instance: Optional[LOBThermodynamics] = None


def get_lob_thermodynamics(
    event_bus: Any = None, feature_store: Any = None, signal_bus: Any = None, **kwargs: Any,
) -> LOBThermodynamics:
    global _instance
    if _instance is None:
        _instance = LOBThermodynamics(
            event_bus=event_bus, feature_store=feature_store, signal_bus=signal_bus, **kwargs,
        )
    else:
        if event_bus is not None and _instance.event_bus is None:
            _instance.event_bus = event_bus
        if feature_store is not None and _instance.feature_store is None:
            _instance.feature_store = feature_store
        if signal_bus is not None and _instance.signal_bus is None:
            _instance.signal_bus = signal_bus
    return _instance


def _reset_for_tests() -> None:
    global _instance
    _instance = None
