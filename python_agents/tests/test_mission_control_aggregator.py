"""Tests for mission_control_aggregator — health scoring, edges, cache, flags."""

from __future__ import annotations

import os
import time

import pytest

import mission_control_aggregator as mc
from event_bus import EventType, get_event_bus
from module_registry import MODULE_REGISTRY


@pytest.fixture(autouse=True)
def _reset_all_state():
    mc._reset_all_caches_for_tests()
    bus = get_event_bus()
    bus._history.clear()
    bus._significant_history.clear()
    bus._latest_heartbeats.clear()
    bus._pinned_meta.clear()
    # Clear phase / shadow env so tests are deterministic.
    for env in (
        "QUENBOT_ORACLE_BRAIN_SHADOW",
        "QUENBOT_EMERGENCY_LOCKDOWN_ENABLED",
        "QUENBOT_DIRECTIVE_IMPACT_TRACKING",
        "QUENBOT_DIRECTIVE_GATEKEEPER_ENABLED",
        "QUENBOT_ONCHAIN_BRIDGE_ENABLED",
        "QUENBOT_GEMMA_ENABLED",
        "MISSION_CONTROL_SNAPSHOT_TTL_SEC",
    ):
        os.environ.pop(env, None)
    yield


def _emit(bus, etype: EventType, source: str, data=None, when: float | None = None):
    entry = {
        "type": etype.value,
        "source": source,
        "data_keys": list((data or {}).keys()),
        "data_preview": dict(data or {}),
        "timestamp": float(when if when is not None else time.time()),
        "priority": 0,
    }
    bus._history.append(entry)
    bus._significant_history.append(entry)
    if etype == EventType.AGENT_HEARTBEAT:
        bus._latest_heartbeats[source] = entry
    return entry


# ---------------------------------------------------------------------------
# Snapshot shape & module coverage
# ---------------------------------------------------------------------------

def test_snapshot_returns_all_registered_modules():
    snap = mc.snapshot(force=True)
    returned_ids = {m["id"] for m in snap["modules"]}
    assert returned_ids == set(MODULE_REGISTRY.keys())


def test_snapshot_has_top_level_keys():
    snap = mc.snapshot(force=True)
    for key in ("generated_at", "overall_health_score", "vital_signs",
                "modules", "edges", "qwen_pulse"):
        assert key in snap


def test_edge_count_matches_total_dependencies():
    snap = mc.snapshot(force=True)
    expected = sum(len(m.dependencies) for m in MODULE_REGISTRY.values()
                   if all(d in MODULE_REGISTRY for d in m.dependencies))
    assert len(snap["edges"]) == expected


# ---------------------------------------------------------------------------
# Health scoring
# ---------------------------------------------------------------------------

def test_fresh_heartbeat_yields_healthy_status():
    bus = get_event_bus()
    _emit(bus, EventType.SCOUT_PRICE_UPDATE, "scout_agent")
    snap = mc.snapshot(force=True)
    scout = next(m for m in snap["modules"] if m["id"] == "scout_agent")
    assert scout["status"] == "healthy"
    assert scout["health_score"] >= 90


def test_stale_heartbeat_degrades_health():
    bus = get_event_bus()
    spec = MODULE_REGISTRY["scout_agent"]
    # Older than 5x expected period → should drop below 60 (unhealthy).
    _emit(
        bus, EventType.SCOUT_PRICE_UPDATE, "scout_agent",
        when=time.time() - spec.expected_period_sec * 6.0,
    )
    snap = mc.snapshot(force=True)
    scout = next(m for m in snap["modules"] if m["id"] == "scout_agent")
    assert scout["status"] == "unhealthy"
    assert scout["health_score"] < 60


def test_never_seen_active_module_is_unhealthy_not_crash():
    # No events at all — every module should still appear but be degraded.
    snap = mc.snapshot(force=True)
    scout = next(m for m in snap["modules"] if m["id"] == "scout_agent")
    assert scout["status"] in {"unhealthy", "slow"}
    assert scout["heartbeat_age_sec"] is None


def test_flag_gated_module_with_flag_off_is_disabled():
    snap = mc.snapshot(force=True)
    onchain = next(m for m in snap["modules"] if m["id"] == "causal_onchain_bridge")
    assert onchain["status"] == "disabled"
    assert onchain["flag_enabled"] is False


def test_flag_gated_module_with_flag_on_is_considered():
    os.environ["QUENBOT_ONCHAIN_BRIDGE_ENABLED"] = "1"
    snap = mc.snapshot(force=True)
    onchain = next(m for m in snap["modules"] if m["id"] == "causal_onchain_bridge")
    assert onchain["status"] != "disabled"
    assert onchain["flag_enabled"] is True


# ---------------------------------------------------------------------------
# Edge activity
# ---------------------------------------------------------------------------

def test_edge_activity_bucket_math():
    # 120 scout price updates in the last 60s → rate 2.0/s → "warm".
    bus = get_event_bus()
    now = time.time()
    for i in range(120):
        _emit(bus, EventType.SCOUT_PRICE_UPDATE, "scout_agent", when=now - (i % 60))
    snap = mc.snapshot(force=True)
    # scout_agent → strategist_agent edge should be warm.
    edge = next(e for e in snap["edges"]
                if e["from"] == "scout_agent" and e["to"] == "strategist_agent")
    assert edge["activity"] in {"warm", "hot"}
    assert edge["events_per_sec_1min"] > 0.5


def test_silent_edges_when_no_traffic():
    snap = mc.snapshot(force=True)
    for e in snap["edges"]:
        assert e["activity"] == "silent"
        assert e["events_per_sec_1min"] == 0.0


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------

def test_snapshot_cache_respects_ttl():
    os.environ["MISSION_CONTROL_SNAPSHOT_TTL_SEC"] = "60"
    mc._SnapshotCache.clear()
    first = mc.snapshot()
    t1 = first["generated_at"]
    time.sleep(0.05)
    second = mc.snapshot()
    assert second["generated_at"] == t1, "cache should return same object within TTL"


def test_snapshot_force_bypasses_cache():
    os.environ["MISSION_CONTROL_SNAPSHOT_TTL_SEC"] = "60"
    mc._SnapshotCache.clear()
    first = mc.snapshot()
    time.sleep(0.02)
    second = mc.snapshot(force=True)
    assert second["generated_at"] >= first["generated_at"]


# ---------------------------------------------------------------------------
# Qwen pulse + vital signs
# ---------------------------------------------------------------------------

def test_qwen_pulse_counts_directives_last_hour():
    bus = get_event_bus()
    now = time.time()
    for i in range(3):
        _emit(bus, EventType.ORACLE_DIRECTIVE_ISSUED, "qwen_oracle_brain",
              data={"type": "ADJUST", "symbol": "BTCUSDT", "confidence": 0.8},
              when=now - 10)
    # one very old → outside window
    _emit(bus, EventType.ORACLE_DIRECTIVE_ISSUED, "qwen_oracle_brain",
          when=now - 7200)
    snap = mc.snapshot(force=True)
    assert snap["qwen_pulse"]["directives_last_hour"] == 3
    assert snap["qwen_pulse"]["last_directive"] is not None


def test_qwen_pulse_rejection_rate():
    bus = get_event_bus()
    now = time.time()
    for _ in range(2):
        _emit(bus, EventType.DIRECTIVE_REJECTED, "directive_gatekeeper", when=now - 5)
    for _ in range(3):
        _emit(bus, EventType.DIRECTIVE_ACCEPTED, "directive_gatekeeper", when=now - 5)
    snap = mc.snapshot(force=True)
    # 2/(2+3) = 0.4
    assert snap["qwen_pulse"]["rejection_rate_1h"] == pytest.approx(0.4, abs=1e-3)


def test_phase_detection_from_env():
    os.environ["QUENBOT_DIRECTIVE_GATEKEEPER_ENABLED"] = "1"
    assert mc.snapshot(force=True)["qwen_pulse"]["asama"] == "1"
    os.environ["QUENBOT_DIRECTIVE_IMPACT_TRACKING"] = "1"
    assert mc.snapshot(force=True)["qwen_pulse"]["asama"] == "2"
    os.environ["QUENBOT_EMERGENCY_LOCKDOWN_ENABLED"] = "1"
    assert mc.snapshot(force=True)["qwen_pulse"]["asama"] == "3"


def test_vital_sign_external_push():
    mc.set_vital_sign("safety_net_state", "ok")
    mc.set_vital_sign("active_signals", 17)
    mc.set_vital_sign("ghost_pnl_24h_pct", 2.3)
    snap = mc.snapshot(force=True)
    assert snap["vital_signs"]["safety_net_state"] == "ok"
    assert snap["vital_signs"]["active_signals"] == 17
    assert snap["vital_signs"]["ghost_pnl_24h_pct"] == 2.3


def test_warnings_count_in_window():
    bus = get_event_bus()
    now = time.time()
    _emit(bus, EventType.RISK_ALERT, "risk_manager", when=now - 30)
    _emit(bus, EventType.SAFETY_NET_TRIPPED, "safety_net", when=now - 120)
    _emit(bus, EventType.RISK_ALERT, "risk_manager", when=now - 7200)  # out of window
    snap = mc.snapshot(force=True)
    assert snap["vital_signs"]["warnings_last_hour"] == 2


def test_ifi_current_extracted_from_event_preview():
    bus = get_event_bus()
    _emit(bus, EventType.INVISIBLE_FOOTPRINT_INDEX, "factor_graph_fusion",
          data={"ifi": 0.73})
    snap = mc.snapshot(force=True)
    assert snap["vital_signs"]["ifi_current"] == pytest.approx(0.73)


# ---------------------------------------------------------------------------
# Autopsy
# ---------------------------------------------------------------------------

def test_autopsy_bundle_unknown_module():
    bundle = mc.autopsy_bundle("__nope__")
    assert "error" in bundle


def test_autopsy_bundle_reports_dependencies():
    bundle = mc.autopsy_bundle("strategist_agent")
    assert bundle["module_id"] == "strategist_agent"
    dep_ids = {d["id"] for d in bundle["dependencies_status"]}
    # Upstream deps are declared on strategist; downstream is ghost_simulator_agent.
    assert "scout_agent" in dep_ids
    assert "ghost_simulator_agent" in dep_ids
    # qwen_diagnosis is null until the route fills it in.
    assert bundle["qwen_diagnosis"] is None
    assert "restart" in bundle["operator_actions_available"]


def test_timeline_returns_throughput_buckets():
    bus = get_event_bus()
    now = time.time()
    for i in range(10):
        _emit(bus, EventType.SCOUT_PRICE_UPDATE, "scout_agent", when=now - i)
    tl = mc.timeline("scout_agent")
    assert tl["module_id"] == "scout_agent"
    assert len(tl["throughput"]) == 20
    # Some buckets must have non-zero throughput.
    assert any(p["v"] > 0 for p in tl["throughput"])


# ---------------------------------------------------------------------------
# Overall score + graceful degradation
# ---------------------------------------------------------------------------

def test_overall_score_bounded_0_100():
    snap = mc.snapshot(force=True)
    assert 0 <= snap["overall_health_score"] <= 100


def test_error_rate_and_latency_hooks_affect_score():
    bus = get_event_bus()
    _emit(bus, EventType.SCOUT_PRICE_UPDATE, "scout_agent")
    mc.set_error_rate("scout_agent", 0.2)
    mc.set_latency_p95("scout_agent", 1200.0)
    snap = mc.snapshot(force=True)
    scout = next(m for m in snap["modules"] if m["id"] == "scout_agent")
    # Fresh heartbeat but high error + latency → should drop from 100.
    assert scout["health_score"] <= 65
