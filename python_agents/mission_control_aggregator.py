"""
mission_control_aggregator.py — Unified system health snapshot.
================================================================
Polls every observability surface exactly once, assembles a single snapshot
dict consumable by the Mission Control dashboard.

Does NOT modify any existing module. Read-only aggregation. Cached for
``SNAPSHOT_TTL_SEC`` (default 1s) to keep the dashboard snappy without
hammering the event bus.

Design principles:
  * Never crash. Any unreachable subsystem degrades to ``status="unknown"``.
  * No synthetic data. If a value can't be computed, it's ``None``.
  * Side-effect free. All reads are best-effort.
"""

from __future__ import annotations

import logging
import math
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

try:  # pragma: no cover
    from event_bus import EventType, get_event_bus
except Exception:  # pragma: no cover
    from python_agents.event_bus import EventType, get_event_bus  # type: ignore

try:  # pragma: no cover
    from module_registry import (
        MODULE_REGISTRY,
        ModuleSpec,
        list_modules,
        list_by_organ,
    )
except Exception:  # pragma: no cover
    from python_agents.module_registry import (  # type: ignore
        MODULE_REGISTRY,
        ModuleSpec,
        list_modules,
        list_by_organ,
    )

logger = logging.getLogger("quenbot.mission_control")


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class ModuleStatus:
    id: str
    display_name: str
    description: str
    organ: str
    health_score: int
    status: str
    heartbeat_age_sec: Optional[float]
    throughput_per_min: float
    error_rate_5min: float
    latency_p95_ms: Optional[float]
    last_event_type: Optional[str]
    last_event_at: Optional[float]
    flag_enabled: bool
    dependencies: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EdgeActivity:
    frm: str
    to: str
    events_per_sec_1min: float
    activity: str


# ---------------------------------------------------------------------------
# Health scoring
# ---------------------------------------------------------------------------

def _score_module(
    spec: ModuleSpec,
    heartbeat_age: Optional[float],
    throughput_pm: float,
    error_rate: float,
    latency_p95: Optional[float],
    flag_enabled: bool,
) -> Tuple[int, str]:
    # Flag-gated & disabled → dark grey
    if spec.default_state == "flag_gated" and not flag_enabled:
        return 0, "disabled"

    # Dormant module that never emitted anything → dormant (grey)
    if heartbeat_age is None and spec.default_state == "dormant":
        return 0, "dormant"

    h = 100

    if heartbeat_age is None:
        # Active module that should emit but hasn't → treat as very stale.
        h -= 50
    else:
        if heartbeat_age > spec.expected_period_sec * 2:
            h -= 40
        if heartbeat_age > spec.expected_period_sec * 5:
            h -= 30

    if error_rate > 0.05:
        h -= 20

    if latency_p95 is not None:
        # Generic threshold: penalise > 500ms at p95 regardless of module.
        if latency_p95 > 500.0:
            h -= 15

    if throughput_pm <= 0.0 and spec.organ in {"agent", "brain"}:
        h -= 25

    h = max(0, min(100, int(h)))

    if h >= 90:
        status = "healthy"
    elif h >= 60:
        status = "slow"
    else:
        status = "unhealthy"
    return h, status


# ---------------------------------------------------------------------------
# Event bus introspection helpers
# ---------------------------------------------------------------------------

def _event_bus_snapshot() -> Dict[str, Any]:
    try:
        bus = get_event_bus()
        hist = list(getattr(bus, "_history", []) or [])
        hb = dict(getattr(bus, "_latest_heartbeats", {}) or {})
        return {"history": hist, "heartbeats": hb, "bus": bus}
    except Exception as e:  # pragma: no cover
        logger.debug("event bus snapshot failed: %s", e)
        return {"history": [], "heartbeats": {}, "bus": None}


def _latest_event_for_signatures(
    history: List[dict], signatures: Tuple[str, ...]
) -> Tuple[Optional[str], Optional[float], int]:
    """Return (last_event_type, last_event_ts, count_last_60s)."""
    if not signatures:
        return None, None, 0
    sig_set = set(signatures)
    last_type: Optional[str] = None
    last_ts: Optional[float] = None
    count = 0
    now = time.time()
    for entry in reversed(history):
        etype = entry.get("type")
        if etype in sig_set:
            ts = float(entry.get("timestamp") or 0.0)
            if last_type is None:
                last_type = etype
                last_ts = ts
            if now - ts <= 60.0:
                count += 1
    return last_type, last_ts, count


def _heartbeat_age(
    spec: ModuleSpec, history: List[dict], heartbeats: Dict[str, dict]
) -> Optional[float]:
    now = time.time()
    if spec.heartbeat_source == "event_bus":
        # Prefer explicit heartbeat pin keyed by source, else most recent signature event.
        hb = heartbeats.get(spec.heartbeat_key)
        if hb and hb.get("timestamp"):
            return max(0.0, now - float(hb["timestamp"]))
        _, ts, _ = _latest_event_for_signatures(history, spec.event_signatures)
        if ts is not None:
            return max(0.0, now - ts)
        return None
    if spec.heartbeat_source == "callable":
        # Callable sources are filled in by the orchestrator via register_callable;
        # if nothing registered we fall back to event-bus signatures.
        reg = _CallableRegistry.get(spec.heartbeat_key)
        if reg is not None:
            try:
                ts = reg()
                if ts is not None:
                    return max(0.0, now - float(ts))
            except Exception:  # pragma: no cover
                pass
        _, ts, _ = _latest_event_for_signatures(history, spec.event_signatures)
        if ts is not None:
            return max(0.0, now - ts)
        return None
    if spec.heartbeat_source == "db_heartbeat":
        ts = _DbHeartbeatCache.get(spec.heartbeat_key)
        if ts is not None:
            return max(0.0, now - ts)
        return None
    if spec.heartbeat_source == "runtime_supervisor":
        ts = _SupervisorCache.get(spec.heartbeat_key)
        if ts is not None:
            return max(0.0, now - ts)
        return None
    # flag_only → no heartbeat concept
    return None


# ---------------------------------------------------------------------------
# External source caches (filled by orchestrator hooks)
# ---------------------------------------------------------------------------

class _CallableRegistry:
    """Orchestrator registers ``last_heartbeat_ts`` callables per module id."""
    _registry: Dict[str, Any] = {}

    @classmethod
    def register(cls, key: str, fn) -> None:
        cls._registry[key] = fn

    @classmethod
    def get(cls, key: str):
        return cls._registry.get(key)

    @classmethod
    def clear(cls) -> None:
        cls._registry.clear()


class _DbHeartbeatCache:
    _data: Dict[str, float] = {}

    @classmethod
    def set(cls, key: str, ts: float) -> None:
        cls._data[key] = ts

    @classmethod
    def get(cls, key: str) -> Optional[float]:
        return cls._data.get(key)

    @classmethod
    def clear(cls) -> None:
        cls._data.clear()


class _SupervisorCache:
    _data: Dict[str, float] = {}

    @classmethod
    def set(cls, key: str, ts: float) -> None:
        cls._data[key] = ts

    @classmethod
    def get(cls, key: str) -> Optional[float]:
        return cls._data.get(key)

    @classmethod
    def clear(cls) -> None:
        cls._data.clear()


# ---------------------------------------------------------------------------
# Flag resolution
# ---------------------------------------------------------------------------

def _flag_enabled(spec: ModuleSpec) -> bool:
    if spec.default_state != "flag_gated":
        return True
    env = spec.flag_env
    if not env:
        return True
    raw = os.environ.get(env, "")
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# Edge activity
# ---------------------------------------------------------------------------

def _bucket_activity(rate_per_sec: float) -> str:
    if rate_per_sec > 10.0:
        return "hot"
    if rate_per_sec >= 1.0:
        return "warm"
    if rate_per_sec >= 0.1:
        return "cool"
    return "silent"


def _compute_edges(modules: List[ModuleStatus], history: List[dict]) -> List[EdgeActivity]:
    """For each declared dependency (A -> B), rate events from A seen in last 60s."""
    # Pre-count events per module id by matching signatures.
    now = time.time()
    window_start = now - 60.0
    per_module_count: Dict[str, int] = {m.id: 0 for m in modules}
    sig_to_ids: Dict[str, List[str]] = {}
    for m in list_modules():
        for sig in m.event_signatures:
            sig_to_ids.setdefault(sig, []).append(m.id)

    for entry in history:
        ts = float(entry.get("timestamp") or 0.0)
        if ts < window_start:
            continue
        etype = entry.get("type")
        # Prefer mapping by source when the source matches a registered key.
        src = str(entry.get("source") or "")
        if src in per_module_count:
            per_module_count[src] += 1
            continue
        for mid in sig_to_ids.get(etype, []):
            per_module_count[mid] += 1

    edges: List[EdgeActivity] = []
    for m in list_modules():
        for dep in m.dependencies:
            if dep not in MODULE_REGISTRY:
                continue
            count = per_module_count.get(dep, 0)
            rate = count / 60.0
            edges.append(EdgeActivity(
                frm=dep,
                to=m.id,
                events_per_sec_1min=round(rate, 4),
                activity=_bucket_activity(rate),
            ))
    return edges


# ---------------------------------------------------------------------------
# Vital signs
# ---------------------------------------------------------------------------

def _count_events(history: List[dict], types: set, window_sec: float) -> int:
    now = time.time()
    return sum(
        1 for e in history
        if e.get("type") in types
        and (now - float(e.get("timestamp") or 0.0)) <= window_sec
    )


def _latest_ifi(history: List[dict]) -> Optional[float]:
    for e in reversed(history):
        if e.get("type") == EventType.INVISIBLE_FOOTPRINT_INDEX.value:
            preview = e.get("data_preview") or {}
            for k in ("ifi", "score", "value"):
                v = preview.get(k)
                if isinstance(v, (int, float)) and not math.isnan(v):
                    return float(v)
    return None


def _compute_vital_signs(
    history: List[dict],
    heartbeats: Dict[str, dict],
    module_statuses: List[ModuleStatus],
) -> Dict[str, Any]:
    # Scout throughput: count scout events in last 60s, convert to "per minute".
    scout_types = {
        EventType.SCOUT_PRICE_UPDATE.value,
        EventType.ORDER_BOOK_UPDATE.value,
        EventType.SCOUT_ANOMALY.value,
    }
    scout_tps = _count_events(history, scout_types, 60.0)

    qwen_types = {EventType.ORACLE_DIRECTIVE_ISSUED.value}
    qwen_per_hour = _count_events(history, qwen_types, 3600.0)

    warn_types = {
        EventType.SYSTEM_DEGRADED.value,
        EventType.RESOURCE_WARNING.value,
        EventType.SYSTEM_ALERT.value,
        EventType.RISK_ALERT.value,
        EventType.SAFETY_NET_TRIPPED.value,
        EventType.SAFETY_NET_DRIFT_ALERT.value,
    }
    warnings_last_hour = _count_events(history, warn_types, 3600.0)

    # Safety net state.
    safety_net_state = _ExternalSignals.get("safety_net_state", "unknown")
    active_signals = _ExternalSignals.get("active_signals", None)
    ghost_pnl_24h = _ExternalSignals.get("ghost_pnl_24h_pct", None)
    ws_uptime = _ExternalSignals.get("ws_uptime_pct_24h", None)
    test_status = _ExternalSignals.get("test_suite_status", "unknown")

    return {
        "scout_tps": int(scout_tps),
        "qwen_directives_per_hour": int(qwen_per_hour),
        "safety_net_state": safety_net_state,
        "active_signals": active_signals,
        "ghost_pnl_24h_pct": ghost_pnl_24h,
        "ws_uptime_pct_24h": ws_uptime,
        "warnings_last_hour": int(warnings_last_hour),
        "ifi_current": _latest_ifi(history),
        "test_suite_status": test_status,
    }


class _ExternalSignals:
    """Registry for vital-sign values the orchestrator pushes in."""
    _data: Dict[str, Any] = {}

    @classmethod
    def set(cls, key: str, value: Any) -> None:
        cls._data[key] = value

    @classmethod
    def get(cls, key: str, default: Any = None) -> Any:
        return cls._data.get(key, default)

    @classmethod
    def clear(cls) -> None:
        cls._data.clear()


# ---------------------------------------------------------------------------
# Qwen pulse
# ---------------------------------------------------------------------------

def _qwen_pulse(history: List[dict]) -> Dict[str, Any]:
    directives = [
        e for e in history
        if e.get("type") == EventType.ORACLE_DIRECTIVE_ISSUED.value
    ]
    now = time.time()
    in_last_hour = [
        e for e in directives
        if (now - float(e.get("timestamp") or 0.0)) <= 3600.0
    ]
    rejected_last_hour = _count_events(
        history, {EventType.DIRECTIVE_REJECTED.value}, 3600.0
    )
    accepted_last_hour = _count_events(
        history, {EventType.DIRECTIVE_ACCEPTED.value}, 3600.0
    )
    total_routed = rejected_last_hour + accepted_last_hour
    rejection_rate = (
        round(rejected_last_hour / total_routed, 4)
        if total_routed > 0 else 0.0
    )

    last_directive: Optional[Dict[str, Any]] = None
    if directives:
        d = directives[-1]
        preview = d.get("data_preview") or {}
        last_directive = {
            "type": preview.get("type") or preview.get("action"),
            "symbol": preview.get("symbol"),
            "confidence": preview.get("confidence"),
            "ts": d.get("timestamp"),
        }

    shadow = bool(os.environ.get("QUENBOT_ORACLE_BRAIN_SHADOW", "0") in {"1", "true", "True"})
    # Phase detection: Aşama 3 supersedes 2 supersedes 1 when env flags are set.
    if os.environ.get("QUENBOT_EMERGENCY_LOCKDOWN_ENABLED", "").strip().lower() in {"1", "true"}:
        phase = "3"
    elif os.environ.get("QUENBOT_DIRECTIVE_IMPACT_TRACKING", "").strip().lower() in {"1", "true"}:
        phase = "2"
    elif os.environ.get("QUENBOT_DIRECTIVE_GATEKEEPER_ENABLED", "").strip().lower() in {"1", "true"}:
        phase = "1"
    else:
        phase = "0"

    return {
        "shadow": shadow,
        "directives_last_hour": len(in_last_hour),
        "rejection_rate_1h": rejection_rate,
        "last_directive": last_directive,
        "asama": phase,
    }


# ---------------------------------------------------------------------------
# Snapshot cache + main API
# ---------------------------------------------------------------------------

class _SnapshotCache:
    _last_ts: float = 0.0
    _last_value: Optional[Dict[str, Any]] = None

    @classmethod
    def get(cls, ttl: float) -> Optional[Dict[str, Any]]:
        if cls._last_value is None:
            return None
        if (time.time() - cls._last_ts) <= ttl:
            return cls._last_value
        return None

    @classmethod
    def set(cls, value: Dict[str, Any]) -> None:
        cls._last_value = value
        cls._last_ts = time.time()

    @classmethod
    def clear(cls) -> None:
        cls._last_value = None
        cls._last_ts = 0.0


def _snapshot_ttl_sec() -> float:
    raw = os.environ.get("MISSION_CONTROL_SNAPSHOT_TTL_SEC")
    if raw:
        try:
            return max(0.0, float(raw))
        except Exception:
            pass
    return 1.0


def _build_module_status(
    spec: ModuleSpec,
    history: List[dict],
    heartbeats: Dict[str, dict],
) -> ModuleStatus:
    flag_ok = _flag_enabled(spec)
    hb_age = _heartbeat_age(spec, history, heartbeats) if flag_ok else None

    last_type, last_ts, count_60s = _latest_event_for_signatures(
        history, spec.event_signatures
    )
    throughput_pm = (count_60s / 60.0) * 60.0  # events per minute == count in last 60s

    # Error rate: fraction of spec events marked error-ish in the last 5 minutes.
    error_rate = _ErrorRateCache.get(spec.id, 0.0)
    latency_p95 = _LatencyCache.get(spec.id)

    score, status = _score_module(
        spec, hb_age, throughput_pm, error_rate, latency_p95, flag_ok
    )

    return ModuleStatus(
        id=spec.id,
        display_name=spec.display_name,
        description=spec.description,
        organ=spec.organ,
        health_score=score,
        status=status,
        heartbeat_age_sec=round(hb_age, 2) if hb_age is not None else None,
        throughput_per_min=round(throughput_pm, 3),
        error_rate_5min=round(error_rate, 4),
        latency_p95_ms=latency_p95,
        last_event_type=last_type,
        last_event_at=last_ts,
        flag_enabled=flag_ok,
        dependencies=list(spec.dependencies),
        details={
            "heartbeat_source": spec.heartbeat_source,
            "heartbeat_key": spec.heartbeat_key,
            "expected_period_sec": spec.expected_period_sec,
            "default_state": spec.default_state,
        },
    )


class _ErrorRateCache:
    _data: Dict[str, float] = {}

    @classmethod
    def set(cls, key: str, value: float) -> None:
        cls._data[key] = float(value)

    @classmethod
    def get(cls, key: str, default: float = 0.0) -> float:
        return cls._data.get(key, default)

    @classmethod
    def clear(cls) -> None:
        cls._data.clear()


class _LatencyCache:
    _data: Dict[str, float] = {}

    @classmethod
    def set(cls, key: str, value: float) -> None:
        cls._data[key] = float(value)

    @classmethod
    def get(cls, key: str) -> Optional[float]:
        return cls._data.get(key)

    @classmethod
    def clear(cls) -> None:
        cls._data.clear()


def _overall_score(modules: List[ModuleStatus]) -> int:
    visible = [m for m in modules if m.status != "disabled"]
    if not visible:
        return 0
    avg = sum(m.health_score for m in visible) / len(visible)
    return max(0, min(100, int(round(avg))))


def snapshot(force: bool = False) -> Dict[str, Any]:
    """Return the unified mission-control snapshot dict (cached)."""
    ttl = _snapshot_ttl_sec()
    if not force:
        cached = _SnapshotCache.get(ttl)
        if cached is not None:
            return cached

    bus_info = _event_bus_snapshot()
    history: List[dict] = bus_info["history"]
    heartbeats: Dict[str, dict] = bus_info["heartbeats"]

    module_statuses: List[ModuleStatus] = [
        _build_module_status(m, history, heartbeats) for m in list_modules()
    ]
    edges = _compute_edges(module_statuses, history)

    vital = _compute_vital_signs(history, heartbeats, module_statuses)
    qwen_pulse = _qwen_pulse(history)

    snap = {
        "generated_at": time.time(),
        "overall_health_score": _overall_score(module_statuses),
        "vital_signs": vital,
        "modules": [
            {
                "id": m.id,
                "display_name": m.display_name,
                "description": m.description,
                "organ": m.organ,
                "health_score": m.health_score,
                "status": m.status,
                "heartbeat_age_sec": m.heartbeat_age_sec,
                "throughput_per_min": m.throughput_per_min,
                "error_rate_5min": m.error_rate_5min,
                "latency_p95_ms": m.latency_p95_ms,
                "last_event_type": m.last_event_type,
                "last_event_at": m.last_event_at,
                "flag_enabled": m.flag_enabled,
                "dependencies": m.dependencies,
                "details": m.details,
            }
            for m in module_statuses
        ],
        "edges": [
            {
                "from": e.frm,
                "to": e.to,
                "events_per_sec_1min": e.events_per_sec_1min,
                "activity": e.activity,
            }
            for e in edges
        ],
        "qwen_pulse": qwen_pulse,
    }
    _SnapshotCache.set(snap)
    return snap


# ---------------------------------------------------------------------------
# Autopsy — per-module timeline + recent logs + diagnosis hook
# ---------------------------------------------------------------------------

def timeline(module_id: str, window_sec: float = 300.0) -> Dict[str, Any]:
    """Return 5-minute metric windows for a module by re-scanning recent history."""
    spec = MODULE_REGISTRY.get(module_id)
    if spec is None:
        return {
            "module_id": module_id,
            "throughput": [],
            "error_rate": [],
            "latency_p95": [],
        }
    bus_info = _event_bus_snapshot()
    history: List[dict] = bus_info["history"]
    sig_set = set(spec.event_signatures)
    now = time.time()
    buckets = 20  # ~15-second buckets over 5 minutes
    bucket_sec = window_sec / buckets
    counts = [0] * buckets
    for e in history:
        ts = float(e.get("timestamp") or 0.0)
        if now - ts > window_sec:
            continue
        etype = e.get("type")
        if etype in sig_set or str(e.get("source") or "") == spec.heartbeat_key:
            idx = int((now - ts) // bucket_sec)
            if 0 <= idx < buckets:
                counts[buckets - 1 - idx] += 1
    throughput_series = [
        {"t": now - (buckets - i) * bucket_sec, "v": c / bucket_sec}
        for i, c in enumerate(counts)
    ]
    return {
        "module_id": module_id,
        "throughput": throughput_series,
        "error_rate": [],
        "latency_p95": [],
    }


def _parse_log_line(line: str) -> Dict[str, Any]:
    """Best-effort parse of the main.py-produced log lines.

    Format produced upstream: ``f"{timestamp:.2f} {type} {preview}"``.
    Returns ``{ts, type, preview}`` with graceful fallbacks.
    """
    try:
        parts = str(line).split(" ", 2)
        ts = float(parts[0]) if parts and parts[0] else 0.0
        typ = parts[1] if len(parts) > 1 else ""
        preview = parts[2] if len(parts) > 2 else ""
        return {"ts": ts, "type": typ, "preview": preview}
    except Exception:
        return {"ts": 0.0, "type": "", "preview": str(line)}


def autopsy_bundle(module_id: str, log_tail: Optional[List[str]] = None) -> Dict[str, Any]:
    """Build the payload consumed by the ``/api/mission-control/autopsy`` route.

    Does NOT call the LLM — that is done separately by the route handler so
    caching and failure-handling stay out of the aggregator.
    """
    spec = MODULE_REGISTRY.get(module_id)
    if spec is None:
        return {"module_id": module_id, "error": "unknown module"}

    snap = snapshot()
    mod_entry = next((m for m in snap["modules"] if m["id"] == module_id), None)
    dependencies_status: List[Dict[str, Any]] = []
    upstream_collab: List[Dict[str, Any]] = []
    downstream_collab: List[Dict[str, Any]] = []
    for dep_id in spec.dependencies:
        dep_entry = next((m for m in snap["modules"] if m["id"] == dep_id), None)
        dep_spec = MODULE_REGISTRY.get(dep_id)
        dep_status_val = dep_entry["status"] if dep_entry else "unknown"
        dependencies_status.append({
            "id": dep_id,
            "status": dep_status_val,
            "impact_direction": "upstream",
        })
        upstream_collab.append({
            "id": dep_id,
            "display_name": dep_spec.display_name if dep_spec else dep_id,
            "status": dep_status_val,
            "health_score": dep_entry["health_score"] if dep_entry else 0,
            "last_event_at": dep_entry.get("last_event_at") if dep_entry else None,
        })
    # Downstream: who depends on this module?
    for m in snap["modules"]:
        if module_id in m.get("dependencies", []):
            dep_status_val = "affected" if mod_entry and mod_entry["status"] in {
                "unhealthy", "slow"
            } else m["status"]
            dependencies_status.append({
                "id": m["id"],
                "status": dep_status_val,
                "impact_direction": "downstream",
            })
            dn_spec = MODULE_REGISTRY.get(m["id"])
            downstream_collab.append({
                "id": m["id"],
                "display_name": dn_spec.display_name if dn_spec else m["id"],
                "status": dep_status_val,
                "health_score": m.get("health_score", 0),
                "last_event_at": m.get("last_event_at"),
            })

    # Parse recent log lines into structured events for the UI
    raw_logs = list(log_tail or [])
    recent_events: List[Dict[str, Any]] = [_parse_log_line(ln) for ln in raw_logs]

    # Derive mission / activity summary from the timeline + recent events
    tl = timeline(module_id)
    tp_series = tl.get("throughput", []) if isinstance(tl, dict) else []
    bucket_values = [float(p.get("v", 0) or 0) for p in tp_series]
    now_ts = time.time()
    total_events_5min = 0.0
    for p in tp_series:
        try:
            total_events_5min += float(p.get("v", 0) or 0) * 15.0  # 15s bucket
        except Exception:
            pass
    events_last_minute = 0.0
    for p in tp_series[-4:]:  # last 4 × 15s = 1 min
        try:
            events_last_minute += float(p.get("v", 0) or 0) * 15.0
        except Exception:
            pass
    avg_throughput = sum(bucket_values) / len(bucket_values) if bucket_values else 0.0
    peak_throughput = max(bucket_values) if bucket_values else 0.0

    last_event = recent_events[-1] if recent_events else None
    last_event_ts = float(last_event["ts"]) if last_event and last_event.get("ts") else None
    seconds_since_last = (now_ts - last_event_ts) if last_event_ts else None
    is_active = bool(seconds_since_last is not None and seconds_since_last <= max(
        30.0, float(spec.expected_period_sec) * 2.0
    ))

    if is_active and last_event:
        activity_desc = f"Aktif — son olay {int(seconds_since_last or 0)}s önce ({last_event.get('type') or 'event'})"
    elif last_event_ts:
        activity_desc = f"Sessiz — son olay {int(seconds_since_last or 0)}s önce"
    else:
        activity_desc = "Son 5 dk'da olay yok"

    mission_summary = {
        "total_events_5min": round(total_events_5min, 1),
        "events_last_minute": round(events_last_minute, 1),
        "avg_throughput_per_sec": round(avg_throughput, 3),
        "peak_throughput_per_sec": round(peak_throughput, 3),
        "dependency_count": len(spec.dependencies),
        "downstream_count": len(downstream_collab),
    }
    current_activity = {
        "is_active": is_active,
        "description_tr": activity_desc,
        "last_event_ts": last_event_ts,
        "last_event_type": last_event.get("type") if last_event else None,
        "last_event_preview": last_event.get("preview") if last_event else None,
        "seconds_since_last_event": round(seconds_since_last, 1) if seconds_since_last is not None else None,
        "expected_period_sec": float(spec.expected_period_sec),
    }

    return {
        "module_id": module_id,
        "display_name": spec.display_name,
        "description_tr": spec.description,
        "organ": spec.organ,
        "current_health": mod_entry["health_score"] if mod_entry else 0,
        "status": mod_entry["status"] if mod_entry else "unknown",
        "timeline_5min": tl,
        "recent_logs": raw_logs,
        "recent_events": recent_events,
        "recent_errors": [],
        "dependencies_status": dependencies_status,
        "collaborators": {
            "upstream": upstream_collab,
            "downstream": downstream_collab,
        },
        "mission_summary": mission_summary,
        "current_activity": current_activity,
        "warnings": [],
        "qwen_diagnosis": None,
        "operator_actions_available": ["restart", "download_logs", "view_source"],
    }


# ---------------------------------------------------------------------------
# Integration hooks for the orchestrator
# ---------------------------------------------------------------------------

def register_callable_heartbeat(module_id: str, fn) -> None:
    """Orchestrator can pass a callable returning a unix-ts last heartbeat."""
    _CallableRegistry.register(module_id, fn)


def set_db_heartbeat(key: str, ts: float) -> None:
    _DbHeartbeatCache.set(key, float(ts))


def set_supervisor_heartbeat(key: str, ts: float) -> None:
    _SupervisorCache.set(key, float(ts))


def set_vital_sign(key: str, value: Any) -> None:
    _ExternalSignals.set(key, value)


def set_error_rate(module_id: str, rate: float) -> None:
    _ErrorRateCache.set(module_id, rate)


def set_latency_p95(module_id: str, ms: float) -> None:
    _LatencyCache.set(module_id, ms)


def _reset_all_caches_for_tests() -> None:
    """Test helper — never call from production code."""
    _SnapshotCache.clear()
    _CallableRegistry.clear()
    _DbHeartbeatCache.clear()
    _SupervisorCache.clear()
    _ExternalSignals.clear()
    _ErrorRateCache.clear()
    _LatencyCache.clear()


__all__ = [
    "snapshot",
    "timeline",
    "autopsy_bundle",
    "register_callable_heartbeat",
    "set_db_heartbeat",
    "set_supervisor_heartbeat",
    "set_vital_sign",
    "set_error_rate",
    "set_latency_p95",
]
