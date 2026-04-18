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
    ORDER_BOOK_UPDATE = "scout.order_book_update"

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
    PATTERN_DETECTED = "pattern.detected"
    SIGNATURE_MATCH = "signature.match"

    # Directive events
    DIRECTIVE_UPDATED = "directive.updated"

    # LLM events
    LLM_REQUEST = "llm.request"
    LLM_RESPONSE = "llm.response"
    LLM_ERROR = "llm.error"

    # Command routing events (user -> qwen -> agents)
    COMMAND_RECEIVED = "command.received"
    COMMAND_ROUTED = "command.routed"
    COMMAND_EXECUTED = "command.executed"
    COMMAND_FAILED = "command.failed"

    # Decision core / learning events
    DECISION_MADE = "decision.made"
    EXPERIENCE_RECORDED = "learning.experience_recorded"
    ERROR_OBSERVED = "learning.error_observed"
    CLEANUP_COMPLETED = "system.cleanup_completed"
    REDIS_MESSAGE = "system.redis_message"
    SYSTEM_ALERT = "system.alert"
    STORAGE_PRUNING_STARTED = "storage.pruning_started"
    STORAGE_PRUNING_COMPLETED = "storage.pruning_completed"

    # MAMIS microstructure events
    MICROSTRUCTURE_BAR = "mamis.bar"
    MICROSTRUCTURE_ALERT = "mamis.alert"
    MICROSTRUCTURE_CLASSIFIED = "mamis.classified"
    MICROSTRUCTURE_SIGNAL = "mamis.signal"

    # Agent activity broadcast (for inter-agent terminal visibility)
    AGENT_HEARTBEAT = "agent.heartbeat"
    HORIZON_RESOLVED = "signal.horizon_resolved"

    # Microstructure & regime intelligence
    MICROSTRUCTURE_FEATURES = "microstructure.features"
    REGIME_CHANGE = "regime.change"
    ICEBERG_DETECTED = "fingerprint.iceberg"
    SPOOF_DETECTED = "fingerprint.spoof"

    # Learning pipeline
    BARRIER_LABELED = "learning.barrier_labeled"
    META_LABEL_DECISION = "learning.meta_label"
    LOSS_AUTOPSY = "learning.loss_autopsy"
    BANDIT_UPDATED = "learning.bandit_update"
    META_MODEL_REFIT = "learning.meta_refit"
    DRIFT_ALERT = "learning.drift_alert"

    # ── Intel Upgrade (Phase 1+) — ADDITIVE, never rename ──
    ORDER_FLOW_IMBALANCE = "intel.ofi"
    MULTI_HORIZON_SIGNATURE = "intel.mh_signature"
    CONFLUENCE_SCORE = "intel.confluence"
    # Phase 2
    LEAD_LAG_ALERT = "intel.lead_lag"
    CROSS_ASSET_GRAPH_UPDATED = "intel.cross_asset_updated"
    # Phase 3
    FAST_BRAIN_PREDICTION = "intel.fast_brain"
    FINAL_DECISION = "intel.final_decision"
    DECISION_SHADOW = "intel.decision_shadow"
    # Phase 4
    COUNTERFACTUAL_UPDATE = "intel.counterfactual"
    CONFLUENCE_WEIGHTS_ROTATED = "intel.weights_rotated"

    # Phase 5 Finalization — Safety Net (ADDITIVE, never rename)
    SAFETY_NET_TRIPPED = "intel.safety_net_tripped"
    SAFETY_NET_RESET = "intel.safety_net_reset"
    SAFETY_NET_DRIFT_ALERT = "intel.safety_net_drift"
    SAFETY_NET_FS_DEGRADED = "intel.safety_net_fs_degraded"

    # ── Phase 6: Oracle Stack — ADDITIVE only, never rename ──────
    # Detector events (§1–§8 of phase 6 plan)
    BOCPD_CONSENSUS_CHANGEPOINT = "oracle.bocpd_consensus"
    HAWKES_KERNEL_UPDATE = "oracle.hawkes_kernel"
    LOB_THERMODYNAMIC_STATE = "oracle.lob_thermodynamics"
    DISTRIBUTION_SHIFT = "oracle.wasserstein_drift"
    PATH_SIGNATURE_MATCH = "oracle.path_signature"
    MIRROR_EXECUTION_DETECTED = "oracle.mirror_flow"
    TOPOLOGICAL_ANOMALY = "oracle.topology"
    ONCHAIN_CAUSAL_SIGNAL = "oracle.onchain_causal"
    # Fusion + brain (§10–§11)
    INVISIBLE_FOOTPRINT_INDEX = "oracle.ifi"
    ORACLE_DIRECTIVE_ISSUED = "oracle.directive"
    ORACLE_REASONING_TRACE = "oracle.reasoning_trace"


@dataclass
class Event:
    type: EventType
    source: str
    data: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    priority: int = 0  # higher = more important


class EventBus:
    """Lock-free async event bus with topic-based pub/sub."""

    def __init__(self, max_history: int = 400):
        self._subscribers: dict[str, list[Callable]] = {}
        self._mirrors: list[Callable[..., Coroutine]] = []
        self._history: list[dict] = []
        self._significant_history: list[dict] = []
        # Pinned per-agent heartbeat snapshots so the Inter-Agent Terminal
        # always shows every agent regardless of ring-buffer churn.
        self._latest_heartbeats: dict[str, dict] = {}
        # Pinned recent meta-events (horizons, llm status, resource warnings)
        # that are sparse but important for the terminal feed.
        self._pinned_meta: list[dict] = []
        self._max_history = max_history
        self._event_count = 0
        # High-frequency event types that drown out terminal visibility.
        # They still land in _history but are excluded from the default feed.
        self._spam_types = {
            "scout.order_book_update",
            "scout.price_update",
        }
        self._meta_types = {
            "signal.horizon_resolved",
            "llm.status_change",
            "system.resource_warning",
            "health.report",
            "brain.learning_update",
            "regime.change",
            "fingerprint.iceberg",
            "fingerprint.spoof",
            "learning.loss_autopsy",
            "learning.meta_refit",
            "learning.drift_alert",
            "learning.barrier_labeled",
            "learning.bandit_update",
        }

    def _safe_preview(self, data: dict) -> dict:
        preview: dict[str, Any] = {}
        for k, v in (data or {}).items():
            if isinstance(v, (int, float, bool)) or v is None:
                preview[k] = v
            elif isinstance(v, str):
                preview[k] = v[:200]
            elif isinstance(v, list):
                preview[k] = f"list[{len(v)}]"
            elif isinstance(v, dict):
                preview[k] = {ik: str(iv)[:100] for ik, iv in list(v.items())[:8]}
            else:
                preview[k] = str(v)[:100]
        return preview

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

    def register_mirror(self, handler: Callable[..., Coroutine]):
        if handler not in self._mirrors:
            self._mirrors.append(handler)

    async def publish(self, event: Event):
        """Publish an event to all subscribers. Non-blocking fire-and-forget."""
        key = event.type.value
        self._event_count += 1

        # Store in history (ring buffer)
        entry = {
            "type": key,
            "source": event.source,
            "data_keys": list(event.data.keys()),
            "data_preview": self._safe_preview(event.data),
            "timestamp": event.timestamp,
            "priority": event.priority,
        }
        self._history.append(entry)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]
        if key not in self._spam_types:
            self._significant_history.append(entry)
            if len(self._significant_history) > self._max_history:
                self._significant_history = self._significant_history[-self._max_history:]
        # Pin per-agent heartbeat snapshot (always latest one per agent).
        if key == "agent.heartbeat":
            self._latest_heartbeats[str(event.source)] = entry
        # Pin sparse meta events so they aren't washed out.
        if key in self._meta_types:
            self._pinned_meta.append(entry)
            if len(self._pinned_meta) > 100:
                self._pinned_meta = self._pinned_meta[-100:]

        handlers = self._subscribers.get(key, [])
        for handler in handlers:
            try:
                asyncio.create_task(handler(event))
            except Exception as e:
                logger.error(f"Event handler error for {key}: {e}")

        for mirror in self._mirrors:
            try:
                asyncio.create_task(mirror(event))
            except Exception as e:
                logger.error(f"Event mirror error for {key}: {e}")

    def get_stats(self, recent_limit: int = 200, include_spam: bool = False) -> dict:
        try:
            limit = max(1, min(int(recent_limit), self._max_history))
        except Exception:
            limit = 200
        source = self._history if include_spam else self._significant_history
        heartbeats = list(self._latest_heartbeats.values())
        pinned = list(self._pinned_meta[-30:])
        # Build feed: recent slice of main history, then ensure every pinned
        # heartbeat + meta event is present (appended at tail so they are
        # never clipped by high-volume topics).
        feed = list(source[-limit:])
        seen = {(e.get("type"), e.get("source"), e.get("timestamp")) for e in feed}
        for extra in heartbeats + pinned:
            k = (extra.get("type"), extra.get("source"), extra.get("timestamp"))
            if k not in seen:
                feed.append(extra)
                seen.add(k)
        return {
            "total_events": self._event_count,
            "subscriber_count": sum(len(v) for v in self._subscribers.values()),
            "topics": {k: len(v) for k, v in self._subscribers.items() if v},
            "recent_events": feed,
            "latest_heartbeats": heartbeats,
        }


# Singleton
_bus: Optional[EventBus] = None


def get_event_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus
