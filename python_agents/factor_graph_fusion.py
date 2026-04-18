"""
factor_graph_fusion.py — 12 kanal Oracle birleştirici (§10)
=============================================================
Loopy belief propagation tarzı basit bir factor graph füzyonu. Her kanal
bir gözlem düğümü; IFI (Invisible Footprint Index) gizli düğüm. Damping'li
log-odds toplamı + yön bileşeni üretir.

Pure-python; numpy opsiyonel. Hiçbir karar ayarı tetiklemez; yalnız
oracle_signal_bus'a IFI + direction yayar, event_bus'a ORACLE_REASONING_TRACE
yayar ve feature_store'a yazar.
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


# Channel "polarity" (+1 = bullish kanıt, -1 = bearish, 0 = kindexsel birlikte risk)
# Bilinmeyen kanallar 0 kabul edilir (sadece büyüklük etkili olur).
DEFAULT_CHANNEL_POLARITY: Dict[str, float] = {
    "ofi_hurst": +1.0,
    "multi_horizon_coherence": +1.0,
    "iceberg_fingerprint": 0.0,
    "bocpd_consensus": 0.0,
    "hawkes_branching_ratio": +1.0,
    "entropy_cooling": 0.0,
    "wasserstein_drift_zscore": 0.0,
    "path_signature_similarity": 0.0,
    "mirror_execution_strength": 0.0,
    "topological_whale_birth": 0.0,
    "onchain_lead_strength": +1.0,
    "cross_asset_spillover": +1.0,
}

# İlk prior ağırlıkları. Loglanır, brain update_weights() ile revize edebilir.
DEFAULT_CHANNEL_WEIGHTS: Dict[str, float] = {k: 1.0 for k in DEFAULT_CHANNEL_POLARITY}


def _logit(p: float) -> float:
    p = min(1.0 - 1e-6, max(1e-6, p))
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


@dataclass
class _SymbolState:
    last_ts: float = 0.0
    last_ifi: float = 0.0
    last_direction: float = 0.0
    last_channels: Dict[str, float] = field(default_factory=dict)
    last_marginals: Dict[str, float] = field(default_factory=dict)
    history: Deque[Tuple[float, float, float]] = field(default_factory=lambda: deque(maxlen=2048))


class FactorGraphFusion:
    PUBLISH_HZ_DEFAULT = 0.5
    ORACLE_CHANNEL_NAME = "invisible_footprint_index"

    def __init__(
        self,
        event_bus: Any = None,
        feature_store: Any = None,
        signal_bus: Any = None,
        bp_iters: int = 20,
        damping: float = 0.5,
        publish_hz: float = 0.5,
        channel_weights: Optional[Dict[str, float]] = None,
        channel_polarity: Optional[Dict[str, float]] = None,
    ) -> None:
        self.event_bus = event_bus
        self.feature_store = feature_store
        self.signal_bus = signal_bus
        self.bp_iters = max(1, int(bp_iters))
        self.damping = min(0.99, max(0.0, float(damping)))
        self.publish_interval = 1.0 / max(0.001, float(publish_hz))
        self.weights: Dict[str, float] = dict(channel_weights or DEFAULT_CHANNEL_WEIGHTS)
        self.polarity: Dict[str, float] = dict(channel_polarity or DEFAULT_CHANNEL_POLARITY)
        self._states: Dict[str, _SymbolState] = {}
        self._stats = {"fusions": 0, "publishes": 0, "bp_iters": 0}
        self._initialized = False

    async def initialize(self) -> None:
        self._initialized = True
        logger.info("FactorGraphFusion ready (iters=%d, damping=%.2f, channels=%d)",
                    self.bp_iters, self.damping, len(self.weights))

    def update_weights(self, weights: Dict[str, float]) -> None:
        """Brain'in revize ettiği ağırlıkları güncelle."""
        for k, v in weights.items():
            try:
                self.weights[k] = float(v)
            except (TypeError, ValueError):
                continue

    def _read_channels_for_symbol(self, symbol: str) -> Dict[str, float]:
        """Signal bus'tan tüm kanal değerlerini oku. Eksikler atlanır."""
        if self.signal_bus is None:
            return {}
        try:
            snaps = self.signal_bus.all_snapshots()
        except Exception:
            return {}
        if not isinstance(snaps, dict):
            return {}
        sym_block = snaps.get(symbol) or {}
        if not isinstance(sym_block, dict):
            return {}
        out: Dict[str, float] = {}
        for k, v in sym_block.items():
            try:
                if isinstance(v, dict):
                    val = v.get("value")
                else:
                    val = v
                if val is None:
                    continue
                val = float(val)
                if val != val:
                    continue
                out[k] = max(-1.0, min(1.0, val))
            except (TypeError, ValueError):
                continue
        return out

    def _fuse(self, channels: Dict[str, float]) -> Tuple[float, float, Dict[str, float]]:
        """Damping'li iteratif log-odds füzyonu.
        Returns: (ifi ∈ [0,1], direction ∈ [-1,+1], marginals per channel)
        """
        if not channels:
            return 0.0, 0.0, {}
        # Initial messages: prior = 0 (uninformative); evidence from each channel.
        belief = 0.0           # log-odds IFI (intensity, abs)
        direction = 0.0        # signed combined direction (log-odds-like)
        marginals: Dict[str, float] = {}
        total_w = 0.0
        # Iterative damping: stability under correlated inputs.
        for it in range(self.bp_iters):
            new_belief = 0.0
            new_direction = 0.0
            total_w = 0.0
            for ch, v in channels.items():
                w = float(self.weights.get(ch, 1.0))
                if w <= 0:
                    continue
                total_w += w
                magnitude = abs(v)
                # Normalize magnitude→log-odds contribution
                evidence_intensity = _logit(min(0.999, max(0.001, magnitude)))
                polarity = float(self.polarity.get(ch, 0.0))
                # Yönsel kanallar hem intensity hem direction; nötr kanallar sadece intensity.
                new_belief += w * evidence_intensity
                if polarity != 0.0:
                    new_direction += w * polarity * v
                marginals[ch] = _sigmoid(w * evidence_intensity)
            if total_w > 0:
                new_belief /= total_w
                new_direction /= total_w
            # Damping
            belief = self.damping * belief + (1 - self.damping) * new_belief
            direction = self.damping * direction + (1 - self.damping) * new_direction
            self._stats["bp_iters"] += 1
        ifi = _sigmoid(belief)           # [0,1]
        direction = max(-1.0, min(1.0, direction))
        return ifi, direction, marginals

    def maybe_publish(self, symbol: str, ts: Optional[float] = None) -> Optional[Dict[str, Any]]:
        if not symbol:
            return None
        ts = float(ts) if ts is not None else time.time()
        st = self._states.setdefault(symbol, _SymbolState())
        if (ts - st.last_ts) < self.publish_interval:
            return None
        channels = self._read_channels_for_symbol(symbol)
        ifi, direction, marginals = self._fuse(channels)
        st.last_ts = ts
        st.last_ifi = ifi
        st.last_direction = direction
        st.last_channels = channels
        st.last_marginals = marginals
        st.history.append((ts, ifi, direction))
        self._stats["fusions"] += 1
        self._stats["publishes"] += 1
        if self.signal_bus is not None:
            try:
                self.signal_bus.publish(
                    symbol=symbol, channel=self.ORACLE_CHANNEL_NAME, value=ifi,
                    source="factor_graph_fusion",
                    extra={"direction": direction, "n_channels": len(channels), "marginals": marginals},
                )
            except Exception as e:
                logger.debug("FG signal_bus skip: %s", e)
        if self.event_bus is not None:
            try:
                from event_bus import EventType, Event
                asyncio.create_task(
                    self.event_bus.publish(
                        Event(type=EventType.INVISIBLE_FOOTPRINT_INDEX, source="factor_graph_fusion",
                              data={"symbol": symbol, "ts": ts, "ifi": ifi, "direction": direction,
                                    "n_channels": len(channels)})
                    )
                )
            except Exception as e:
                logger.debug("FG event skip: %s", e)
        if self.feature_store is not None:
            try:
                from datetime import datetime, timezone
                self.feature_store.write(
                    symbol=symbol,
                    ts=datetime.fromtimestamp(ts, tz=timezone.utc),
                    features={"ifi": ifi, "ifi_direction": direction, "ifi_channels": len(channels)},
                )
            except Exception as e:
                logger.debug("FG fs skip: %s", e)
        return {"symbol": symbol, "ts": ts, "ifi": ifi, "direction": direction,
                "channels": channels, "marginals": marginals}

    def snapshot(self, symbol: str) -> Optional[Dict[str, Any]]:
        st = self._states.get(symbol)
        if st is None:
            return None
        return {
            "symbol": symbol,
            "ts": st.last_ts,
            "ifi": st.last_ifi,
            "direction": st.last_direction,
            "channels": dict(st.last_channels),
            "marginals": dict(st.last_marginals),
            "weights": dict(self.weights),
        }

    def all_snapshots(self) -> Dict[str, Dict[str, Any]]:
        return {sym: self.snapshot(sym) for sym in self._states}

    def oracle_channel_value(self, symbol: str) -> Optional[float]:
        st = self._states.get(symbol)
        return None if st is None else st.last_ifi

    async def health_check(self) -> Dict[str, Any]:
        return {"healthy": self._initialized, "symbols": len(self._states), **self._stats}

    def metrics(self) -> Dict[str, Any]:
        return {
            "fg_fusions_total": self._stats["fusions"],
            "fg_publishes_total": self._stats["publishes"],
            "fg_bp_iters_total": self._stats["bp_iters"],
            "fg_symbols_active": len(self._states),
        }


_instance: Optional[FactorGraphFusion] = None


def get_factor_graph(
    event_bus: Any = None, feature_store: Any = None, signal_bus: Any = None, **kwargs: Any,
) -> FactorGraphFusion:
    global _instance
    if _instance is None:
        _instance = FactorGraphFusion(
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
