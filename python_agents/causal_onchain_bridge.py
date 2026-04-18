"""
causal_onchain_bridge.py — On-chain → CEX nedensel köprü (§8)
================================================================
Convergent Cross Mapping (Sugihara 2012) pure-python yaklaşık uygulama.
Zaman gecikmeli embedding (E=3, τ=1) üzerinden cross-map skill ölçer.
ρ(X→Y) - ρ(Y→X) > 0 ise X, Y'yi yönlendiriyor demektir.

Oracle kanalı: 'onchain_lead_strength' ∈ [-1, +1].
"""
from __future__ import annotations

import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _embed(series: List[float], E: int, tau: int) -> List[List[float]]:
    n = len(series)
    pts: List[List[float]] = []
    for t in range((E - 1) * tau, n):
        pts.append([series[t - i * tau] for i in range(E)])
    return pts


def _pearson(x: List[float], y: List[float]) -> float:
    n = len(x)
    if n < 2:
        return 0.0
    mx = sum(x) / n
    my = sum(y) / n
    vx = sum((v - mx) ** 2 for v in x) / n
    vy = sum((v - my) ** 2 for v in y) / n
    if vx <= 0 or vy <= 0:
        return 0.0
    cov = sum((a - mx) * (b - my) for a, b in zip(x, y)) / n
    return cov / math.sqrt(vx * vy)


def _ccm_rho(X: List[float], Y: List[float], E: int = 3, tau: int = 1, lib: int = 100) -> float:
    """X'in shadow manifoldundan Y'yi cross-map et, ρ döndür."""
    if len(X) < lib + E * tau + 5 or len(Y) < lib + E * tau + 5:
        return 0.0
    Mx = _embed(X[-lib:], E, tau)
    n = len(Mx)
    if n < E + 2:
        return 0.0
    y_target = Y[-lib + (E - 1) * tau:][: n]
    preds: List[float] = []
    truth: List[float] = []
    k = E + 1
    for i in range(n):
        # En yakın k komşu (i hariç)
        qp = Mx[i]
        dists: List[Tuple[float, int]] = []
        for j in range(n):
            if j == i:
                continue
            d = math.sqrt(sum((a - b) ** 2 for a, b in zip(qp, Mx[j])))
            dists.append((d, j))
        dists.sort()
        nn = dists[:k]
        d_min = max(nn[0][0], 1e-9)
        weights = [math.exp(-d / d_min) for (d, _) in nn]
        ws = sum(weights) or 1.0
        w_norm = [w / ws for w in weights]
        pred = sum(w * y_target[nn[idx][1]] for idx, w in enumerate(w_norm))
        preds.append(pred)
        truth.append(y_target[i])
    return _pearson(preds, truth)


@dataclass
class _SymbolState:
    onchain_series: Deque[Tuple[float, float]] = field(default_factory=lambda: deque(maxlen=2048))
    cex_series: Deque[Tuple[float, float]] = field(default_factory=lambda: deque(maxlen=2048))
    last_publish_ts: float = 0.0
    last_rho_on_to_cex: float = 0.0
    last_rho_cex_to_on: float = 0.0
    last_lead: float = 0.0


class CausalOnChainBridge:
    PUBLISH_HZ_DEFAULT = 0.05
    ORACLE_CHANNEL_NAME = "onchain_lead_strength"

    def __init__(
        self,
        event_bus: Any = None,
        feature_store: Any = None,
        signal_bus: Any = None,
        onchain_client: Any = None,
        lib_size: int = 200,
        embed_dim: int = 3,
        publish_hz: float = 0.05,
    ) -> None:
        self.event_bus = event_bus
        self.feature_store = feature_store
        self.signal_bus = signal_bus
        self.onchain_client = onchain_client
        self.lib_size = int(lib_size)
        self.embed_dim = int(embed_dim)
        self.publish_interval = 1.0 / max(0.001, publish_hz)
        self._states: Dict[str, _SymbolState] = {}
        self._stats = {"observes": 0, "computes": 0, "causal_events": 0}
        self._initialized = False

    async def initialize(self) -> None:
        self._initialized = True
        logger.info("CausalOnChainBridge ready (lib=%d, E=%d)", self.lib_size, self.embed_dim)

    def observe_cex(self, symbol: str, value: float, ts: Optional[float] = None) -> None:
        try:
            v = float(value)
            if v != v:
                return
        except (TypeError, ValueError):
            return
        ts = float(ts) if ts is not None else time.time()
        st = self._states.setdefault(symbol, _SymbolState())
        st.cex_series.append((ts, v))
        self._stats["observes"] += 1

    def observe_onchain(self, symbol: str, value: float, ts: Optional[float] = None) -> None:
        try:
            v = float(value)
            if v != v:
                return
        except (TypeError, ValueError):
            return
        ts = float(ts) if ts is not None else time.time()
        st = self._states.setdefault(symbol, _SymbolState())
        st.onchain_series.append((ts, v))

    def maybe_publish(self, symbol: str, ts: Optional[float] = None) -> Optional[Dict[str, Any]]:
        st = self._states.get(symbol)
        if st is None:
            return None
        ts = float(ts) if ts is not None else time.time()
        if (ts - st.last_publish_ts) < self.publish_interval:
            return None
        if len(st.onchain_series) < self.lib_size or len(st.cex_series) < self.lib_size:
            return {"symbol": symbol, "ts": ts, "disabled_reason": "insufficient_history"}
        X = [v for (_, v) in list(st.onchain_series)]
        Y = [v for (_, v) in list(st.cex_series)]
        # Align to same length (tail)
        L = min(len(X), len(Y), self.lib_size)
        X = X[-L:]
        Y = Y[-L:]
        rho_xy = _ccm_rho(X, Y, E=self.embed_dim, tau=1, lib=L)
        rho_yx = _ccm_rho(Y, X, E=self.embed_dim, tau=1, lib=L)
        lead = max(-1.0, min(1.0, rho_xy - rho_yx))
        st.last_publish_ts = ts
        st.last_rho_on_to_cex = rho_xy
        st.last_rho_cex_to_on = rho_yx
        st.last_lead = lead
        self._stats["computes"] += 1
        if self.signal_bus is not None:
            try:
                self.signal_bus.publish(
                    symbol=symbol, channel=self.ORACLE_CHANNEL_NAME, value=lead,
                    source="causal_onchain_bridge",
                    extra={"rho_onchain_to_cex": rho_xy, "rho_cex_to_onchain": rho_yx},
                )
            except Exception as e:
                logger.debug("CCM signal_bus skip: %s", e)
        if abs(lead) >= 0.2:
            self._stats["causal_events"] += 1
            if self.event_bus is not None:
                try:
                    from event_bus import EventType, Event
                    import asyncio
                    asyncio.create_task(
                        self.event_bus.publish(
                            Event(type=EventType.ONCHAIN_CAUSAL_SIGNAL, source="causal_onchain_bridge",
                                  data={"symbol": symbol, "ts": ts, "lead": lead})
                        )
                    )
                except Exception as e:
                    logger.debug("CCM event skip: %s", e)
        return {"symbol": symbol, "ts": ts, "lead": lead, "rho_on_to_cex": rho_xy, "rho_cex_to_on": rho_yx}

    def snapshot(self, symbol: str) -> Optional[Dict[str, Any]]:
        st = self._states.get(symbol)
        if st is None:
            return None
        return {"symbol": symbol, "lead": st.last_lead, "rho_on_to_cex": st.last_rho_on_to_cex,
                "rho_cex_to_on": st.last_rho_cex_to_on,
                "onchain_points": len(st.onchain_series), "cex_points": len(st.cex_series)}

    def all_snapshots(self) -> Dict[str, Dict[str, Any]]:
        return {sym: self.snapshot(sym) for sym in self._states}

    def oracle_channel_value(self, symbol: str) -> Optional[float]:
        st = self._states.get(symbol)
        return None if st is None else st.last_lead

    async def health_check(self) -> Dict[str, Any]:
        return {"healthy": self._initialized, "symbols": len(self._states), **self._stats}

    def metrics(self) -> Dict[str, Any]:
        return {
            "ccm_observes_total": self._stats["observes"],
            "ccm_computes_total": self._stats["computes"],
            "ccm_causal_events_total": self._stats["causal_events"],
        }


_instance: Optional[CausalOnChainBridge] = None


def get_causal_onchain(
    event_bus: Any = None, feature_store: Any = None, signal_bus: Any = None,
    onchain_client: Any = None, **kwargs: Any,
) -> CausalOnChainBridge:
    global _instance
    if _instance is None:
        _instance = CausalOnChainBridge(
            event_bus=event_bus, feature_store=feature_store, signal_bus=signal_bus,
            onchain_client=onchain_client, **kwargs,
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
