"""
QuenBot V2 — Event Bus
========================
Central asynchronous event system for inter-agent communication.
Agents publish events, other agents subscribe to event types.
Decoupled, non-blocking, with optional priority and TTL.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine, Optional

logger = logging.getLogger("quenbot.event_bus")


class EventType(str, Enum):
    # Scout events
    SCOUT_ANOMALY = "scout.anomaly"
    SCOUT_PRICE_UPDATE = "scout.price_update"
    SCOUT_DATA_GAP = "scout.data_gap"

    # Strategist events
    SIGNAL_GENERATED = "strategist.signal"
    SIGNAL_APPROVED = "strategist.signal_approved"
    SIGNAL_REJECTED = "strategist.signal_rejected"

    # Risk events
    RISK_APPROVED = "risk.approved"
    RISK_REJECTED = "risk.rejected"
    RISK_ALERT = "risk.alert"

    # Ghost Simulator events
    SIM_OPENED = "ghost.sim_opened"
    SIM_CLOSED = "ghost.sim_closed"
    SIM_UPDATE = "ghost.sim_update"

    # Auditor events
    AUDIT_COMPLETE = "auditor.audit_complete"
    CORRECTION_APPLIED = "auditor.correction"

    # System events
    SYSTEM_DEGRADED = "system.degraded"
    SYSTEM_HEALTHY = "system.healthy"
    RESOURCE_WARNING = "system.resource_warning"
    LLM_STATUS_CHANGE = "system.llm_status"
    HEALTH_REPORT = "system.health_report"

    # Pattern Matcher events
    PATTERN_MATCH = "pattern.match"
    PATTERN_NO_MATCH = "pattern.no_match"

    # Directive events
    DIRECTIVE_UPDATED = "directive.updated"


@dataclass
class Event:
    type: EventType
    source: str
    data: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    priority: int = 0  # higher = more important


class EventBus:
    """Lock-free async event bus with topic-based pub/sub."""

    def __init__(self, max_history: int = 200):
        self._subscribers: dict[str, list[Callable]] = {}
        self._history: list[dict] = []
        self._max_history = max_history
        self._event_count = 0

    def subscribe(self, event_type: EventType, handler: Callable[..., Coroutine]):
        """Subscribe a coroutine handler to an event type."""
        key = event_type.value
        if key not in self._subscribers:
            self._subscribers[key] = []
        self._subscribers[key].append(handler)
        logger.debug(f"Subscribed to {key}: {handler.__qualname__}")

    def unsubscribe(self, event_type: EventType, handler: Callable):
        key = event_type.value
        if key in self._subscribers:
            self._subscribers[key] = [h for h in self._subscribers[key] if h != handler]

    async def publish(self, event: Event):
        """Publish an event to all subscribers. Non-blocking fire-and-forget."""
        key = event.type.value
        self._event_count += 1

        # Store in history (ring buffer)
        self._history.append({
            "type": key,
            "source": event.source,
            "data_keys": list(event.data.keys()),
            "timestamp": event.timestamp,
            "data_summary": self._summarize_data(event),
        })
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        handlers = self._subscribers.get(key, [])
        if not handlers:
            return

        for handler in handlers:
            try:
                asyncio.create_task(handler(event))
            except Exception as e:
                logger.error(f"Event handler error for {key}: {e}")

    def get_stats(self) -> dict:
        return {
            "total_events": self._event_count,
            "subscriber_count": sum(len(v) for v in self._subscribers.values()),
            "topics": {k: len(v) for k, v in self._subscribers.items() if v},
            "recent_events": self._history[-50:],
        }

    @staticmethod
    def _summarize_data(event: Event) -> str:
        """Create a concise Turkish summary of the event data."""
        d = event.data
        t = event.type.value
        src = event.source

        if t == "scout.price_update":
            return f"{d.get('symbol', '?')} fiyat güncellendi: {d.get('price', '?')}"
        elif t == "scout.anomaly":
            return f"⚠ {d.get('symbol', '?')} anomali tespit edildi — {d.get('reason', '?')}"
        elif t == "scout.data_gap":
            return f"Veri boşluğu: {d.get('exchange', '?')} {d.get('symbol', '?')}"
        elif t == "strategist.signal":
            sym = d.get("symbol", "?")
            sig_type = d.get("signal_type", "?")
            direction = d.get("direction", d.get("side", "?"))
            conf = d.get("confidence", 0)
            if isinstance(conf, float) and conf < 1:
                conf = round(conf * 100)
            return f"📡 {sym} — {sig_type} sinyali üretildi [{direction}] güven: %{conf}"
        elif t == "strategist.signal_approved":
            return f"✅ Sinyal onaylandı: {d.get('symbol', '?')} {d.get('signal_type', '?')}"
        elif t == "strategist.signal_rejected":
            return f"❌ Sinyal reddedildi: {d.get('symbol', '?')} — {d.get('reason', '?')}"
        elif t == "risk.approved":
            return f"🛡 Risk onayı: {d.get('symbol', '?')} {d.get('side', '?')} giriş izni verildi"
        elif t == "risk.rejected":
            reason = d.get("reason", d.get("rejection_reason", "?"))
            return f"🛡 Risk reddi: {d.get('symbol', '?')} — {reason}"
        elif t == "ghost.sim_opened":
            sym = d.get("symbol", "?")
            side = d.get("side", "?")
            price = d.get("entry_price", d.get("price", "?"))
            lev = d.get("leverage_x", d.get("leverage", 1))
            return f"👻 Simülasyon açıldı: {sym} {side} @ {price} ({lev}x kaldıraç)"
        elif t == "ghost.sim_closed":
            sym = d.get("symbol", "?")
            pnl = d.get("pnl_pct", d.get("pnl", 0))
            result = "✅ KAZANÇ" if float(pnl or 0) >= 0 else "❌ KAYIP"
            return f"👻 Simülasyon kapandı: {sym} {result} %{pnl}"
        elif t == "ghost.sim_update":
            return f"👻 Simülasyon güncellendi: {d.get('symbol', '?')} anlık K/Z: %{d.get('current_pnl_pct', '?')}"
        elif t == "auditor.audit_complete":
            total = d.get("total", d.get("total_simulations", 0))
            success = d.get("success_rate", 0)
            return f"📋 Denetim tamamlandı: {total} simülasyon, başarı oranı: %{success}"
        elif t == "auditor.correction":
            return f"🔧 Oto-düzeltme uygulandı: {d.get('signal_type', '?')} — {d.get('adjustment_key', '?')}"
        elif t == "system.resource_warning":
            warns = d.get("warnings", [])
            cnt = len(warns)
            comp = warns[0].get("component", "?") if warns else "?"
            return f"⚠ Kaynak uyarısı: {cnt} uyarı — {comp}"
        elif t == "system.llm_status":
            avail = d.get("available", False)
            model = d.get("model", "?")
            return f"🧠 Gemma durumu değişti: {'AKTİF' if avail else 'KAPALI'} — model: {model}"
        elif t == "system.health_report":
            mode = d.get("system_mode", "?")
            agents = d.get("agents", {})
            aktif = sum(1 for v in agents.values() if v)
            toplam = len(agents)
            ram = d.get("ram_percent", 0)
            return f"📊 Sağlık raporu [{mode}]: {aktif}/{toplam} ajan aktif, Bellek: %{ram:.0f}"
        elif t == "pattern.match":
            sym = d.get("symbol", "?")
            sim = d.get("similarity", d.get("score", 0))
            return f"🧬 Örüntü eşleşmesi: {sym} benzerlik: {sim:.4f}" if isinstance(sim, float) else f"🧬 Örüntü eşleşmesi: {sym}"
        elif t == "pattern.no_match":
            return f"🧬 Örüntü eşleşmedi: {d.get('symbol', '?')} — yeterli benzerlik yok"
        elif t == "directive.updated":
            return f"📝 Yönerge güncellendi: {str(d.get('directive', '?'))[:60]}"
        else:
            return f"{src}: {t} — {', '.join(f'{k}={v}' for k,v in list(d.items())[:3])}"


# Singleton
_bus: Optional[EventBus] = None


def get_event_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus
