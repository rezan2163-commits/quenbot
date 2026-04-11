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
            "recent_events": self._history[-20:],
        }


# Singleton
_bus: Optional[EventBus] = None


def get_event_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus
