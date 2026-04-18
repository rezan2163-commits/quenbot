"""
oracle_signal_bus.py — Phase 6 sinyal birleştirici otobüs
==========================================================
12 Oracle kanalını tek noktadan okumak için hafif registry. ConfluenceEngine,
FactorGraphFusion ve QwenOracleBrain bu otobüsten okur; N-to-M coupling önlenir.

Matematiksel/operasyonel rol:
    Her kanal -> {value: float in [-1,+1] veya [0,1], updated_at: ts, source: str,
                  quality: float in [0,1]}
    healthy_channels(symbol, max_age_s) güncel kanalları döndürür.

Sözleşme:
    - Default ON (yan etkisiz registry; QUENBOT_ORACLE_BUS_ENABLED=1).
    - Detector publish() çağırır; tüketici read()/read_with_metadata() çağırır.
    - Hot path bloklamaz, kilit minimal (asyncio Lock değil; salt-yazım dict).
    - Kanal ismi globally unique; çakışma -> son yazan kazanır + warn.

Bu modül salt additive; mevcut kanallar üzerinde hiçbir davranışı değiştirmez.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class _ChannelEntry:
    name: str
    source: str
    value: Optional[float] = None
    updated_at: float = 0.0
    quality: float = 1.0
    extra: Dict[str, Any] = field(default_factory=dict)


class OracleSignalBus:
    """Sembol bazında Oracle kanal kayıt ve okuma katmanı."""

    def __init__(self, event_bus: Any = None) -> None:
        self.event_bus = event_bus
        # {symbol: {channel: _ChannelEntry}}
        self._channels: Dict[str, Dict[str, _ChannelEntry]] = {}
        # {channel: source} kayıt sırası takibi (overwrite tespiti)
        self._registry: Dict[str, str] = {}
        self._stats = {"publishes": 0, "reads": 0, "channels_registered": 0}

    # ─── Registry ───────────────────────────────────────────────
    def register_channel(self, name: str, source: str) -> None:
        """Kanal ismini ve sahibini kayıt eder. Idempotent."""
        if not name or not source:
            return
        if name in self._registry and self._registry[name] != source:
            logger.warning(
                "OracleSignalBus channel '%s' previously owned by '%s' overwritten by '%s'",
                name, self._registry[name], source,
            )
        if name not in self._registry:
            self._stats["channels_registered"] += 1
        self._registry[name] = source

    def registered_channels(self) -> List[str]:
        return sorted(self._registry.keys())

    def channel_owner(self, name: str) -> Optional[str]:
        return self._registry.get(name)

    # ─── Publish (detector tarafı) ──────────────────────────────
    def publish(
        self,
        symbol: str,
        channel: str,
        value: float,
        source: Optional[str] = None,
        quality: float = 1.0,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Bir kanalın değerini günceller. Hot path safe (sync, O(1))."""
        if not symbol or not channel:
            return
        try:
            v = float(value)
            if v != v:  # NaN guard
                return
        except (TypeError, ValueError):
            return
        sym_map = self._channels.setdefault(symbol, {})
        entry = sym_map.get(channel)
        if entry is None:
            entry = _ChannelEntry(
                name=channel,
                source=source or self._registry.get(channel, "unknown"),
            )
            sym_map[channel] = entry
        entry.value = v
        entry.updated_at = time.time()
        entry.quality = max(0.0, min(1.0, float(quality)))
        if extra:
            entry.extra = dict(extra)
        if source and entry.source != source:
            entry.source = source
        self._stats["publishes"] += 1

    # ─── Read (consumer tarafı) ─────────────────────────────────
    def read(
        self,
        symbol: str,
        channels: Optional[List[str]] = None,
    ) -> Dict[str, float]:
        """Sembol için kanal değerlerini döndürür (sadece value)."""
        self._stats["reads"] += 1
        sym_map = self._channels.get(symbol) or {}
        if channels is None:
            return {name: e.value for name, e in sym_map.items() if e.value is not None}
        out: Dict[str, float] = {}
        for ch in channels:
            e = sym_map.get(ch)
            if e and e.value is not None:
                out[ch] = e.value
        return out

    def read_with_metadata(self, symbol: str) -> Dict[str, Dict[str, Any]]:
        """Sembol için kanal değerlerini metadata ile birlikte döndürür."""
        self._stats["reads"] += 1
        now = time.time()
        sym_map = self._channels.get(symbol) or {}
        out: Dict[str, Dict[str, Any]] = {}
        for name, e in sym_map.items():
            if e.value is None:
                continue
            out[name] = {
                "value": e.value,
                "age_s": max(0.0, now - e.updated_at),
                "source": e.source,
                "quality": e.quality,
            }
            if e.extra:
                out[name]["extra"] = dict(e.extra)
        return out

    def healthy_channels(self, symbol: str, max_age_s: float = 30.0) -> List[str]:
        """Yaş eşiğini geçmemiş kanal isimleri."""
        now = time.time()
        sym_map = self._channels.get(symbol) or {}
        return sorted(
            name for name, e in sym_map.items()
            if e.value is not None and (now - e.updated_at) <= max_age_s
        )

    def all_snapshots(self) -> Dict[str, Dict[str, float]]:
        """Tüm semboller için kanal değer snapshot'u."""
        return {
            sym: {name: e.value for name, e in m.items() if e.value is not None}
            for sym, m in self._channels.items()
        }

    def stats(self) -> Dict[str, Any]:
        return {
            **self._stats,
            "symbols": len(self._channels),
            "channels_total": sum(len(v) for v in self._channels.values()),
        }

    def metrics(self) -> Dict[str, Any]:
        """Prometheus exporter uyumlu metrik dict."""
        s = self.stats()
        return {
            "oracle_bus_publishes_total": s["publishes"],
            "oracle_bus_reads_total": s["reads"],
            "oracle_bus_channels_registered": s["channels_registered"],
            "oracle_bus_symbols_active": s["symbols"],
            "oracle_bus_channel_values_total": s["channels_total"],
        }


# ─── Singleton ───────────────────────────────────────────────
_instance: Optional[OracleSignalBus] = None


def get_oracle_signal_bus(event_bus: Any = None) -> OracleSignalBus:
    """DI uyumlu singleton accessor."""
    global _instance
    if _instance is None:
        _instance = OracleSignalBus(event_bus=event_bus)
    elif event_bus is not None and _instance.event_bus is None:
        _instance.event_bus = event_bus
    return _instance


def _reset_for_tests() -> None:
    """Yalnızca test ortamı için singleton resetleme."""
    global _instance
    _instance = None
