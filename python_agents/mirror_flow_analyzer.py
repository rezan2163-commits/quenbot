"""
mirror_flow_analyzer.py — Borsalar arası senkron emir akışı (§6)
==================================================================
Aynı sembol için Binance & Bybit trade akışları arasında sliding 30-dk
pencere üzerinde normalize DTW mesafesi. p-value < 0.01 & süre ≥ 10 dk
→ "mirror execution". FastDTW (Salvador-Chan 2007) pure-python radius=10.

Oracle kanalı: 'mirror_execution_strength' ∈ [0,1].

Graceful degradation:
    Tek borsa akışı varsa 'disabled_for_symbol' raporu.
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


def _dtw_band(a: List[float], b: List[float], radius: int = 10) -> float:
    """Sakoe-Chiba band ile DTW. O(min(|a|,|b|) * radius)."""
    n, m = len(a), len(b)
    if n == 0 or m == 0:
        return float("inf")
    inf = float("inf")
    r = max(1, int(radius))
    # prev row length = m+1
    prev = [inf] * (m + 1)
    prev[0] = 0.0
    for i in range(1, n + 1):
        curr = [inf] * (m + 1)
        j_lo = max(1, i - r)
        j_hi = min(m, i + r)
        for j in range(j_lo, j_hi + 1):
            cost = (a[i - 1] - b[j - 1]) ** 2
            curr[j] = cost + min(prev[j], curr[j - 1] if j - 1 >= 0 else inf, prev[j - 1])
        prev = curr
    return math.sqrt(prev[m]) if prev[m] != inf else float("inf")


def _bucketize(trades: List[Tuple[float, float]], start: float, end: float, bucket_sec: float) -> List[float]:
    """Return aggregated signed volume per bucket in [start, end)."""
    n = max(1, int((end - start) / bucket_sec))
    out = [0.0] * n
    for (t, v) in trades:
        if t < start or t >= end:
            continue
        idx = min(n - 1, int((t - start) / bucket_sec))
        out[idx] += v
    return out


@dataclass
class _SymbolState:
    binance: Deque[Tuple[float, float]] = field(default_factory=lambda: deque(maxlen=50000))
    bybit: Deque[Tuple[float, float]] = field(default_factory=lambda: deque(maxlen=50000))
    dtw_history: Deque[Tuple[float, float]] = field(default_factory=lambda: deque(maxlen=2048))
    last_publish_ts: float = 0.0
    last_dtw: float = 0.0
    last_pvalue: float = 1.0
    last_strength: float = 0.0
    sustained_since: Optional[float] = None


class MirrorFlowAnalyzer:
    PUBLISH_HZ_DEFAULT = 0.1
    ORACLE_CHANNEL_NAME = "mirror_execution_strength"

    def __init__(
        self,
        event_bus: Any = None,
        feature_store: Any = None,
        signal_bus: Any = None,
        window_min: int = 30,
        radius: int = 10,
        sig_pvalue: float = 0.01,
        publish_hz: float = 0.1,
        bucket_sec: float = 1.0,
        sustained_window_sec: int = 600,
    ) -> None:
        self.event_bus = event_bus
        self.feature_store = feature_store
        self.signal_bus = signal_bus
        self.window_sec = int(window_min) * 60
        self.radius = int(radius)
        self.sig_pvalue = float(sig_pvalue)
        self.publish_interval = 1.0 / max(0.001, publish_hz)
        self.bucket_sec = float(bucket_sec)
        self.sustained_window_sec = int(sustained_window_sec)
        self._states: Dict[str, _SymbolState] = {}
        self._stats = {"observes": 0, "dtw_computes": 0, "mirror_events": 0}
        self._initialized = False

    async def initialize(self) -> None:
        self._initialized = True
        logger.info(
            "MirrorFlowAnalyzer ready (window=%dm, radius=%d, p<%0.3f)",
            self.window_sec // 60, self.radius, self.sig_pvalue,
        )

    def observe(self, symbol: str, exchange: str, signed_volume: float, ts: Optional[float] = None) -> None:
        if not symbol:
            return
        ex = exchange.lower()
        if ex not in ("binance", "bybit"):
            return
        try:
            v = float(signed_volume)
            if v != v:
                return
        except (TypeError, ValueError):
            return
        ts = float(ts) if ts is not None else time.time()
        st = self._states.setdefault(symbol, _SymbolState())
        (st.binance if ex == "binance" else st.bybit).append((ts, v))
        cutoff = ts - self.window_sec
        for dq in (st.binance, st.bybit):
            while dq and dq[0][0] < cutoff:
                dq.popleft()
        self._stats["observes"] += 1

    def _compute_pvalue(self, dtw: float, history: List[float]) -> float:
        if len(history) < 8:
            return 1.0
        rank = sum(1 for v in history if v <= dtw)
        # one-tailed: lower DTW = more similar = more surprising under null
        return (rank + 1) / (len(history) + 2)

    def maybe_publish(self, symbol: str, ts: Optional[float] = None) -> Optional[Dict[str, Any]]:
        st = self._states.get(symbol)
        if st is None:
            return None
        ts = float(ts) if ts is not None else time.time()
        if (ts - st.last_publish_ts) < self.publish_interval:
            return None
        if len(st.binance) < 20 or len(st.bybit) < 20:
            return {"symbol": symbol, "ts": ts, "disabled_reason": "insufficient_dual_feed"}
        end = ts
        start = end - self.window_sec
        a = _bucketize(list(st.binance), start, end, self.bucket_sec)
        b = _bucketize(list(st.bybit), start, end, self.bucket_sec)
        dtw = _dtw_band(a, b, radius=self.radius)
        if dtw == float("inf"):
            return None
        st.dtw_history.append((ts, dtw))
        pvalue = self._compute_pvalue(dtw, [v for (_, v) in list(st.dtw_history)[-512:]])
        strength = max(0.0, min(1.0, 1.0 - pvalue / max(self.sig_pvalue, 1e-6)))
        # sustained detection
        if pvalue < self.sig_pvalue:
            st.sustained_since = st.sustained_since or ts
        else:
            st.sustained_since = None
        sustained = (ts - st.sustained_since) if st.sustained_since else 0.0
        mirror_detected = sustained >= self.sustained_window_sec
        st.last_publish_ts = ts
        st.last_dtw = dtw
        st.last_pvalue = pvalue
        st.last_strength = strength if not mirror_detected else 1.0
        self._stats["dtw_computes"] += 1
        if self.signal_bus is not None:
            try:
                self.signal_bus.publish(
                    symbol=symbol, channel=self.ORACLE_CHANNEL_NAME, value=st.last_strength,
                    source="mirror_flow_analyzer",
                    extra={"dtw": dtw, "pvalue": pvalue, "sustained_sec": sustained},
                )
            except Exception as e:
                logger.debug("Mirror signal_bus skip: %s", e)
        out = {"symbol": symbol, "ts": ts, "dtw": dtw, "pvalue": pvalue, "strength": st.last_strength, "sustained_sec": sustained}
        if mirror_detected:
            self._stats["mirror_events"] += 1
            if self.event_bus is not None:
                try:
                    from event_bus import EventType, Event
                    asyncio.create_task(
                        self.event_bus.publish(
                            Event(type=EventType.MIRROR_EXECUTION_DETECTED, source="mirror_flow_analyzer", data=out)
                        )
                    )
                except Exception as e:
                    logger.debug("Mirror event skip: %s", e)
        return out

    def snapshot(self, symbol: str) -> Optional[Dict[str, Any]]:
        st = self._states.get(symbol)
        if st is None:
            return None
        return {
            "symbol": symbol, "dtw": st.last_dtw, "pvalue": st.last_pvalue,
            "strength": st.last_strength,
            "binance_trades": len(st.binance), "bybit_trades": len(st.bybit),
        }

    def all_snapshots(self) -> Dict[str, Dict[str, Any]]:
        return {sym: self.snapshot(sym) for sym in self._states}

    def oracle_channel_value(self, symbol: str) -> Optional[float]:
        st = self._states.get(symbol)
        return None if st is None else st.last_strength

    async def health_check(self) -> Dict[str, Any]:
        return {"healthy": self._initialized, "symbols": len(self._states), **self._stats}

    def metrics(self) -> Dict[str, Any]:
        return {
            "mirror_observes_total": self._stats["observes"],
            "mirror_dtw_computes_total": self._stats["dtw_computes"],
            "mirror_events_total": self._stats["mirror_events"],
            "mirror_symbols_active": len(self._states),
        }


_instance: Optional[MirrorFlowAnalyzer] = None


def get_mirror_flow(
    event_bus: Any = None, feature_store: Any = None, signal_bus: Any = None, **kwargs: Any,
) -> MirrorFlowAnalyzer:
    global _instance
    if _instance is None:
        _instance = MirrorFlowAnalyzer(
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
