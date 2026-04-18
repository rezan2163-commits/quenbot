"""
topological_lob_analyzer.py — LOB topolojik dayanıklılık (§7)
===============================================================
LOB heatmap (5-dk pencere) üzerinde persistent homology. gudhi/ripser
opsiyonel — yoksa alpha-shape yaklaşımıyla β0/β1 sayıcı fallback.

Oracle kanalı: 'topological_whale_birth' ∈ [0,1]
(büyük-ömürlü 1-dim persistence feature varlığı).
"""
from __future__ import annotations

import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    import gudhi as _gudhi  # type: ignore
    _HAS_GUDHI = True
except Exception:
    _gudhi = None
    _HAS_GUDHI = False

try:
    from ripser import ripser as _ripser  # type: ignore
    _HAS_RIPSER = True
except Exception:
    _ripser = None
    _HAS_RIPSER = False


def _persistence_diagrams(points: List[Tuple[float, float]], max_edge: float = 5.0) -> Tuple[List[float], List[float]]:
    """Return (H0 lifetimes, H1 lifetimes)."""
    if len(points) < 3:
        return [], []
    if _HAS_RIPSER:
        try:
            import numpy as _np
            arr = _np.asarray(points, dtype=float)
            res = _ripser(arr, maxdim=1, thresh=max_edge)
            diags = res.get("dgms", [])
            h0 = [float(d[1] - d[0]) for d in diags[0] if d[1] != float("inf")]
            h1 = [float(d[1] - d[0]) for d in diags[1]] if len(diags) > 1 else []
            return h0, h1
        except Exception as e:
            logger.debug("ripser failed: %s", e)
    if _HAS_GUDHI:
        try:
            rips = _gudhi.RipsComplex(points=points, max_edge_length=max_edge)
            st = rips.create_simplex_tree(max_dimension=2)
            diag = st.persistence()
            h0 = [d[1][1] - d[1][0] for d in diag if d[0] == 0 and d[1][1] != float("inf")]
            h1 = [d[1][1] - d[1][0] for d in diag if d[0] == 1]
            return h0, h1
        except Exception as e:
            logger.debug("gudhi failed: %s", e)
    # Fallback: alpha-shape benzeri basit sayıcı
    # β0 ≈ gevşek bağlı küme sayısı; β1 ≈ 0 (ölçülemez) → sabit 0.
    if not points:
        return [], []
    # Union-Find basit bağlı bileşen (eşik max_edge/3)
    n = len(points)
    parent = list(range(n))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    thr = max_edge / 3.0
    thr_sq = thr * thr
    for i in range(n):
        for j in range(i + 1, n):
            dx = points[i][0] - points[j][0]
            dy = points[i][1] - points[j][1]
            if dx * dx + dy * dy <= thr_sq:
                union(i, j)
    roots = {find(i) for i in range(n)}
    h0 = [1.0] * max(0, len(roots) - 1)
    return h0, []


@dataclass
class _SymbolState:
    snapshots: Deque[Tuple[float, List[Tuple[float, float]]]] = field(default_factory=lambda: deque(maxlen=512))
    last_publish_ts: float = 0.0
    last_birth_score: float = 0.0
    last_h1_max: float = 0.0
    last_h0_count: int = 0


class TopologicalLOBAnalyzer:
    PUBLISH_HZ_DEFAULT = 0.1
    ORACLE_CHANNEL_NAME = "topological_whale_birth"

    def __init__(
        self,
        event_bus: Any = None,
        feature_store: Any = None,
        signal_bus: Any = None,
        window_sec: int = 300,
        max_edge: float = 5.0,
        publish_hz: float = 0.1,
    ) -> None:
        self.event_bus = event_bus
        self.feature_store = feature_store
        self.signal_bus = signal_bus
        self.window_sec = int(window_sec)
        self.max_edge = float(max_edge)
        self.publish_interval = 1.0 / max(0.001, publish_hz)
        self._states: Dict[str, _SymbolState] = {}
        self._stats = {"observes": 0, "computes": 0, "anomalies": 0}
        self._initialized = False

    async def initialize(self) -> None:
        self._initialized = True
        backend = "ripser" if _HAS_RIPSER else ("gudhi" if _HAS_GUDHI else "fallback_uf")
        logger.info("TopologicalLOBAnalyzer ready (backend=%s, window=%ds)", backend, self.window_sec)

    def observe(self, symbol: str, levels: List[Tuple[float, float]], ts: Optional[float] = None) -> None:
        """levels: [(price_offset_bps, size_normalized), ...]"""
        if not symbol or not levels:
            return
        try:
            pts = [(float(p), float(s)) for (p, s) in levels if s > 0]
        except (TypeError, ValueError):
            return
        if not pts:
            return
        ts = float(ts) if ts is not None else time.time()
        st = self._states.setdefault(symbol, _SymbolState())
        st.snapshots.append((ts, pts))
        cutoff = ts - self.window_sec
        while st.snapshots and st.snapshots[0][0] < cutoff:
            st.snapshots.popleft()
        self._stats["observes"] += 1

    def maybe_publish(self, symbol: str, ts: Optional[float] = None) -> Optional[Dict[str, Any]]:
        st = self._states.get(symbol)
        if st is None or not st.snapshots:
            return None
        ts = float(ts) if ts is not None else time.time()
        if (ts - st.last_publish_ts) < self.publish_interval:
            return None
        # Aggregate points across window (last 20 snapshots max)
        recent = list(st.snapshots)[-20:]
        points: List[Tuple[float, float]] = []
        for _, pts in recent:
            points.extend(pts)
        if len(points) < 8:
            return None
        h0, h1 = _persistence_diagrams(points, max_edge=self.max_edge)
        h1_max = max(h1) if h1 else 0.0
        h0_count = len(h0)
        # Normalize birth score
        birth = 0.0
        if h1_max > 0:
            birth = 1.0 - math.exp(-h1_max / max(self.max_edge * 0.5, 1e-6))
        elif h0_count > 1:
            birth = min(1.0, (h0_count - 1) / 10.0) * 0.3
        st.last_publish_ts = ts
        st.last_birth_score = birth
        st.last_h1_max = h1_max
        st.last_h0_count = h0_count
        self._stats["computes"] += 1
        if self.signal_bus is not None:
            try:
                self.signal_bus.publish(
                    symbol=symbol, channel=self.ORACLE_CHANNEL_NAME, value=birth,
                    source="topological_lob_analyzer",
                    extra={"h1_max": h1_max, "h0_count": h0_count},
                )
            except Exception as e:
                logger.debug("Topology signal_bus skip: %s", e)
        if birth >= 0.6:
            self._stats["anomalies"] += 1
            if self.event_bus is not None:
                try:
                    from event_bus import EventType, Event
                    import asyncio
                    asyncio.create_task(
                        self.event_bus.publish(
                            Event(type=EventType.TOPOLOGICAL_ANOMALY, source="topological_lob_analyzer",
                                  data={"symbol": symbol, "ts": ts, "birth": birth, "h1_max": h1_max})
                        )
                    )
                except Exception as e:
                    logger.debug("Topology event skip: %s", e)
        return {"symbol": symbol, "ts": ts, "birth": birth, "h1_max": h1_max, "h0_count": h0_count}

    def snapshot(self, symbol: str) -> Optional[Dict[str, Any]]:
        st = self._states.get(symbol)
        if st is None:
            return None
        return {"symbol": symbol, "birth": st.last_birth_score, "h1_max": st.last_h1_max, "h0_count": st.last_h0_count, "snapshots": len(st.snapshots)}

    def all_snapshots(self) -> Dict[str, Dict[str, Any]]:
        return {sym: self.snapshot(sym) for sym in self._states}

    def oracle_channel_value(self, symbol: str) -> Optional[float]:
        st = self._states.get(symbol)
        return None if st is None else st.last_birth_score

    async def health_check(self) -> Dict[str, Any]:
        return {"healthy": self._initialized, "symbols": len(self._states), "backend": "ripser" if _HAS_RIPSER else ("gudhi" if _HAS_GUDHI else "fallback"), **self._stats}

    def metrics(self) -> Dict[str, Any]:
        return {
            "topology_observes_total": self._stats["observes"],
            "topology_computes_total": self._stats["computes"],
            "topology_anomalies_total": self._stats["anomalies"],
        }


_instance: Optional[TopologicalLOBAnalyzer] = None


def get_topology(
    event_bus: Any = None, feature_store: Any = None, signal_bus: Any = None, **kwargs: Any,
) -> TopologicalLOBAnalyzer:
    global _instance
    if _instance is None:
        _instance = TopologicalLOBAnalyzer(
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
