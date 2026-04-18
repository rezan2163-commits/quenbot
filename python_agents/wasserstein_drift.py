"""
wasserstein_drift.py — Distribütif sürüklenme dedektörü (§4)
==============================================================
Mevcut 1-saatlik trade-size dağılımını (P_t) 24-saatlik baseline (P_24h) ile
Wasserstein-2 mesafesi üzerinden karşılaştırır. 1D empirik CDF inverse yöntemi
(O(n log n)), harici bağımlılıksız:

    W_2(P_t, P_{24h}) = ( ∫₀¹ (F_t⁻¹(u) - F_{24h}⁻¹(u))² du )^(1/2)

Eşit uzunlukta sıralı örneklerde bu L2 mesafesidir. 7 günlük W2 ortalaması ve
std'sine göre z-score hesaplanıp `wasserstein_drift_zscore` kanalına basılır.

Operasyonel rol:
    EventType.DISTRIBUTION_SHIFT yayınlar (|z| ≥ 3.0).
    OracleSignalBus kanalı: 'wasserstein_drift_zscore' ∈ [-1,+1] (tanh-clamp).

Graceful degradation:
    numpy yoksa pure-python. Window örneği azsa 'insufficient' döner.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    import numpy as np  # type: ignore
    _NP_OK = True
except Exception:  # pragma: no cover
    np = None  # type: ignore
    _NP_OK = False


def _wasserstein2(a: List[float], b: List[float], grid: int = 256) -> float:
    """W2 via quantile L2 distance. O((|a|+|b|) log) + O(grid)."""
    if not a or not b:
        return 0.0
    sa = sorted(a)
    sb = sorted(b)
    # Equal-size quantile grid
    n = max(16, int(grid))
    qa = [sa[min(len(sa) - 1, int(len(sa) * i / n))] for i in range(n)]
    qb = [sb[min(len(sb) - 1, int(len(sb) * i / n))] for i in range(n)]
    s = 0.0
    for x, y in zip(qa, qb):
        s += (float(x) - float(y)) ** 2
    return math.sqrt(s / n)


@dataclass
class _SymbolState:
    recent: Deque[Tuple[float, float]] = field(default_factory=lambda: deque(maxlen=20000))
    # rolling W2 samples (ts, w2) for z-score baseline
    w2_history: Deque[Tuple[float, float]] = field(default_factory=lambda: deque(maxlen=2048))
    last_publish_ts: float = 0.0
    last_w2: float = 0.0
    last_zscore: float = 0.0


class WassersteinDrift:
    PUBLISH_HZ_DEFAULT = 0.2
    ORACLE_CHANNEL_NAME = "wasserstein_drift_zscore"

    def __init__(
        self,
        event_bus: Any = None,
        feature_store: Any = None,
        signal_bus: Any = None,
        baseline_hours: int = 24,
        window_min: int = 60,
        publish_hz: float = 0.2,
        drift_sigma: float = 3.0,
    ) -> None:
        self.event_bus = event_bus
        self.feature_store = feature_store
        self.signal_bus = signal_bus
        self.baseline_sec = int(baseline_hours) * 3600
        self.window_sec = int(window_min) * 60
        self.publish_interval = 1.0 / max(0.01, publish_hz)
        self.drift_sigma = float(drift_sigma)
        self._states: Dict[str, _SymbolState] = {}
        self._stats = {"observes": 0, "publishes": 0, "drift_events": 0}
        self._initialized = False

    async def initialize(self) -> None:
        self._initialized = True
        logger.info(
            "WassersteinDrift ready (baseline=%dh, window=%dm, drift_sigma=%.1f)",
            self.baseline_sec // 3600, self.window_sec // 60, self.drift_sigma,
        )

    def observe(self, symbol: str, trade_size: float, ts: Optional[float] = None) -> None:
        if not symbol:
            return
        try:
            v = float(trade_size)
            if v <= 0 or v != v:
                return
        except (TypeError, ValueError):
            return
        ts = float(ts) if ts is not None else time.time()
        st = self._states.setdefault(symbol, _SymbolState())
        st.recent.append((ts, v))
        # Bounded: drop older than baseline
        cutoff = ts - self.baseline_sec
        while st.recent and st.recent[0][0] < cutoff:
            st.recent.popleft()
        self._stats["observes"] += 1

    def _compute(self, symbol: str, ts: float) -> Optional[Tuple[float, float]]:
        st = self._states.get(symbol)
        if st is None or len(st.recent) < 100:
            return None
        win_lo = ts - self.window_sec
        base_lo = ts - self.baseline_sec
        window = [v for (t, v) in st.recent if t >= win_lo]
        baseline = [v for (t, v) in st.recent if base_lo <= t < win_lo]
        if len(window) < 50 or len(baseline) < 100:
            return None
        w2 = _wasserstein2(window, baseline)
        st.w2_history.append((ts, w2))
        # z-score vs last 7d history of W2 (here approximated by in-memory history)
        vals = [v for (_, v) in list(st.w2_history)[-512:]]
        if len(vals) < 8:
            z = 0.0
        else:
            mu = sum(vals) / len(vals)
            var = sum((x - mu) ** 2 for x in vals) / max(1, len(vals) - 1)
            sd = math.sqrt(var) if var > 0 else 1e-9
            z = (w2 - mu) / sd
        return w2, z

    def maybe_publish(self, symbol: str, ts: Optional[float] = None) -> Optional[Dict[str, Any]]:
        st = self._states.get(symbol)
        if st is None:
            return None
        ts = float(ts) if ts is not None else time.time()
        if (ts - st.last_publish_ts) < self.publish_interval:
            return None
        out = self._compute(symbol, ts)
        if out is None:
            return None
        w2, z = out
        st.last_publish_ts = ts
        st.last_w2 = w2
        st.last_zscore = z
        # channel value: tanh(z/3) → [-1,+1]
        ch_val = math.tanh(z / 3.0)
        if self.signal_bus is not None:
            try:
                self.signal_bus.publish(
                    symbol=symbol,
                    channel=self.ORACLE_CHANNEL_NAME,
                    value=ch_val,
                    source="wasserstein_drift",
                    extra={"w2": w2, "zscore": z},
                )
            except Exception as e:
                logger.debug("Wasserstein signal_bus skip: %s", e)
        self._stats["publishes"] += 1
        evt: Optional[Dict[str, Any]] = None
        if abs(z) >= self.drift_sigma:
            self._stats["drift_events"] += 1
            evt = {"symbol": symbol, "ts": ts, "w2": w2, "zscore": z}
            if self.event_bus is not None:
                try:
                    from event_bus import EventType, Event
                    asyncio.create_task(
                        self.event_bus.publish(
                            Event(type=EventType.DISTRIBUTION_SHIFT, source="wasserstein_drift", data=evt)
                        )
                    )
                except Exception as e:
                    logger.debug("Wasserstein event publish skip: %s", e)
        if self.feature_store is not None:
            try:
                from datetime import datetime, timezone
                self.feature_store.write(
                    symbol=symbol,
                    ts=datetime.fromtimestamp(ts, tz=timezone.utc),
                    features={"wasserstein_w2": w2, "wasserstein_zscore": z},
                )
            except Exception as e:
                logger.debug("Wasserstein fs skip: %s", e)
        return evt or {"symbol": symbol, "ts": ts, "w2": w2, "zscore": z, "drift": False}

    def snapshot(self, symbol: str) -> Optional[Dict[str, Any]]:
        st = self._states.get(symbol)
        if st is None:
            return None
        return {
            "symbol": symbol,
            "w2": st.last_w2,
            "zscore": st.last_zscore,
            "samples": len(st.recent),
        }

    def all_snapshots(self) -> Dict[str, Dict[str, Any]]:
        return {sym: self.snapshot(sym) for sym in self._states}

    def oracle_channel_value(self, symbol: str) -> Optional[float]:
        st = self._states.get(symbol)
        return None if st is None else math.tanh(st.last_zscore / 3.0)

    async def health_check(self) -> Dict[str, Any]:
        return {"healthy": self._initialized, "symbols": len(self._states), **self._stats}

    def metrics(self) -> Dict[str, Any]:
        return {
            "wasserstein_observes_total": self._stats["observes"],
            "wasserstein_publishes_total": self._stats["publishes"],
            "wasserstein_drift_events_total": self._stats["drift_events"],
            "wasserstein_symbols_active": len(self._states),
        }


_instance: Optional[WassersteinDrift] = None


def get_wasserstein_drift(
    event_bus: Any = None, feature_store: Any = None, signal_bus: Any = None, **kwargs: Any,
) -> WassersteinDrift:
    global _instance
    if _instance is None:
        _instance = WassersteinDrift(
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
