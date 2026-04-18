"""test_auto_rollback_monitor.py — Aşama 1 rollback triggers."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from directive_gatekeeper import DirectiveGatekeeper
from auto_rollback_monitor import (
    AutoRollbackMonitor, TRIGGER_REJECTION, TRIGGER_ACCURACY,
    TRIGGER_SAFETY_NET, TRIGGER_META_CONF, TRIGGER_UNHEALTHY, TRIGGER_MANUAL,
    _reset_for_tests,
)


@pytest.fixture(autouse=True)
def _reset(tmp_path):
    _reset_for_tests()
    yield
    _reset_for_tests()


def _mk_monitor(tmp_path, **overrides):
    defaults = dict(
        enabled=True,
        rejection_rate_threshold=0.60, rejection_window_min=30,
        accuracy_threshold=0.45, accuracy_window=50,
        meta_conf_min=0.40, meta_conf_streak=10,
        unhealthy_grace_sec=1,
        force_sentinel_path=str(tmp_path / "force_shadow"),
        shadow_forced_path=str(tmp_path / "shadow_forced.json"),
        forensic_dir=str(tmp_path / "forensic"),
        check_interval_sec=1,
    )
    defaults.update(overrides)
    return AutoRollbackMonitor(**defaults)


class _StubConfig:
    ORACLE_BRAIN_SHADOW = False


@pytest.mark.asyncio
async def test_rejection_rate_trigger(tmp_path):
    gk = DirectiveGatekeeper(
        enabled=True, confidence_min=0.9, max_per_hour=100,
        allowlist=["ADJUST_CONFIDENCE_THRESHOLD"],
        rejected_log_path=str(tmp_path / "rej.jsonl"),
    )
    # Seed heavy rejection.
    for _ in range(20):
        gk.evaluate({"action": "ADJUST_CONFIDENCE_THRESHOLD", "confidence": 0.1, "symbol": "BTCUSDT"})
    gk.evaluate({"action": "ADJUST_CONFIDENCE_THRESHOLD", "confidence": 0.95, "symbol": "BTCUSDT"})
    cfg = _StubConfig()
    mon = _mk_monitor(tmp_path, gatekeeper=gk, config_obj=cfg)
    state = await mon.evaluate_once()
    assert state is not None
    assert state.rolled_back is True
    assert state.trigger == TRIGGER_REJECTION
    assert cfg.ORACLE_BRAIN_SHADOW is True
    # Forced-shadow persists.
    assert Path(mon.shadow_forced_path).exists()


@pytest.mark.asyncio
async def test_shadow_accuracy_trigger(tmp_path):
    cfg = _StubConfig()
    mon = _mk_monitor(tmp_path, config_obj=cfg, accuracy_window=10, accuracy_threshold=0.5)
    # 10 results, 3 correct = 30% accuracy.
    for i in range(10):
        mon.record_shadow_outcome(i < 3)
    state = await mon.evaluate_once()
    assert state is not None
    assert state.trigger == TRIGGER_ACCURACY
    assert cfg.ORACLE_BRAIN_SHADOW is True


@pytest.mark.asyncio
async def test_safety_net_trip_trigger(tmp_path):
    class _Sn:
        tripped = True
        _trip_reason = "brier regression"
    cfg = _StubConfig()
    mon = _mk_monitor(tmp_path, safety_net=_Sn(), config_obj=cfg)
    state = await mon.evaluate_once()
    assert state is not None
    assert state.trigger == TRIGGER_SAFETY_NET


@pytest.mark.asyncio
async def test_meta_conf_streak_trigger(tmp_path):
    cfg = _StubConfig()
    mon = _mk_monitor(tmp_path, config_obj=cfg, meta_conf_streak=5, meta_conf_min=0.4)
    for _ in range(5):
        mon.record_meta_confidence(0.2)
    state = await mon.evaluate_once()
    assert state is not None
    assert state.trigger == TRIGGER_META_CONF


@pytest.mark.asyncio
async def test_unhealthy_grace_trigger(tmp_path):
    class _Sup:
        def __init__(self):
            self._st = {"components": {"oracle": {"healthy": False}}}
        def status(self):
            return self._st
    cfg = _StubConfig()
    sup = _Sup()
    mon = _mk_monitor(tmp_path, runtime_supervisor=sup, config_obj=cfg, unhealthy_grace_sec=0)
    # First tick records unhealthy_since; second (with elapsed > 0) fires.
    await mon.evaluate_once()
    time.sleep(0.05)
    state = await mon.evaluate_once()
    assert state is not None
    assert state.trigger == TRIGGER_UNHEALTHY


@pytest.mark.asyncio
async def test_manual_sentinel_trigger(tmp_path):
    cfg = _StubConfig()
    mon = _mk_monitor(tmp_path, config_obj=cfg)
    Path(mon.force_sentinel_path).write_text("force", encoding="utf-8")
    state = await mon.evaluate_once()
    assert state is not None
    assert state.trigger == TRIGGER_MANUAL


@pytest.mark.asyncio
async def test_reset_clears_lock(tmp_path):
    cfg = _StubConfig()
    mon = _mk_monitor(tmp_path, config_obj=cfg)
    Path(mon.force_sentinel_path).write_text("x", encoding="utf-8")
    state = await mon.evaluate_once()
    assert state is not None and state.rolled_back
    payload = mon.reset(operator="tester")
    assert payload["operator"] == "tester"
    assert mon.status()["state"]["rolled_back"] is False
    assert not Path(mon.shadow_forced_path).exists()


@pytest.mark.asyncio
async def test_persisted_forced_shadow_is_rehydrated(tmp_path):
    forced = tmp_path / "shadow_forced.json"
    forced.write_text(json.dumps({
        "trigger": TRIGGER_REJECTION, "reason": "prev run", "ts": time.time(),
    }), encoding="utf-8")
    cfg = _StubConfig()
    mon = _mk_monitor(tmp_path, config_obj=cfg, shadow_forced_path=str(forced))
    assert mon.status()["state"]["rolled_back"] is True
    assert cfg.ORACLE_BRAIN_SHADOW is True


@pytest.mark.asyncio
async def test_no_trigger_returns_armed(tmp_path):
    cfg = _StubConfig()
    mon = _mk_monitor(tmp_path, config_obj=cfg)
    out = await mon.evaluate_once()
    assert out is None
    st = mon.status()
    assert st["light"] == "armed"
    assert st["state"]["rolled_back"] is False
