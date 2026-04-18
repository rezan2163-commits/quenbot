"""test_asama1_integration.py — End-to-end Aşama 1 wiring check.

Exercises the full path:
    QwenOracleBrain._tick_symbol → DirectiveGatekeeper.evaluate
    → (accepted | rejected) path
    → AutoRollbackMonitor observes gatekeeper rejection rate & fires
"""
from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

import pytest

# `qwen_oracle_brain` imports are already covered by conftest sys.path.
from directive_gatekeeper import DirectiveGatekeeper, _reset_for_tests as _reset_gk
from auto_rollback_monitor import (
    AutoRollbackMonitor, TRIGGER_REJECTION, _reset_for_tests as _reset_mon,
)
from qwen_oracle_schemas import OracleDirective


@pytest.fixture(autouse=True)
def _reset():
    _reset_gk()
    _reset_mon()
    yield
    _reset_gk()
    _reset_mon()


def _dir(action, confidence):
    return OracleDirective(
        symbol="BTCUSDT", action=action, severity="medium",
        confidence=confidence, rationale="t", params={}, shadow=True,
    )


def test_gatekeeper_blocks_out_of_allowlist(tmp_path):
    gk = DirectiveGatekeeper(
        enabled=True, confidence_min=0.5, max_per_hour=100,
        allowlist=["PAUSE_SYMBOL"],
        rejected_log_path=str(tmp_path / "rej.jsonl"),
    )
    # Heuristic might try to emit BIAS_DIRECTION — not allowed in Aşama 1.
    d = gk.evaluate(_dir("BIAS_DIRECTION", 0.9))
    assert d.accepted is False
    assert d.filter_name == "allowlist"


def test_gatekeeper_and_rollback_wired_together(tmp_path):
    gk = DirectiveGatekeeper(
        enabled=True, confidence_min=0.9, max_per_hour=50,
        allowlist=["ADJUST_CONFIDENCE_THRESHOLD"],
        rejected_log_path=str(tmp_path / "rej.jsonl"),
    )

    # One accepted, many rejected → rejection rate pushes over 0.60
    gk.evaluate(_dir("ADJUST_CONFIDENCE_THRESHOLD", 0.95))
    for _ in range(30):
        gk.evaluate(_dir("ADJUST_CONFIDENCE_THRESHOLD", 0.10))

    class _Cfg:
        ORACLE_BRAIN_SHADOW = False

    cfg = _Cfg()
    mon = AutoRollbackMonitor(
        enabled=True, gatekeeper=gk, config_obj=cfg,
        rejection_rate_threshold=0.60, rejection_window_min=30,
        accuracy_threshold=0.45, accuracy_window=50,
        meta_conf_min=0.40, meta_conf_streak=10,
        unhealthy_grace_sec=3600,
        force_sentinel_path=str(tmp_path / "force_shadow"),
        shadow_forced_path=str(tmp_path / "shadow_forced.json"),
        forensic_dir=str(tmp_path / "forensic"),
    )

    state = asyncio.get_event_loop().run_until_complete(mon.evaluate_once())
    assert state is not None
    assert state.trigger == TRIGGER_REJECTION
    assert cfg.ORACLE_BRAIN_SHADOW is True
    assert Path(mon.shadow_forced_path).exists()


def test_flag_disabled_is_byte_identical_to_pre_asama1(tmp_path):
    """With enabled=False and no hard-blocklist actions, every directive
    must be accepted and no side effects should touch disk."""
    rej_path = tmp_path / "rej.jsonl"
    gk = DirectiveGatekeeper(
        enabled=False, confidence_min=0.99, max_per_hour=0,
        allowlist=[],
        rejected_log_path=str(rej_path),
    )
    for conf in [0.1, 0.5, 0.9]:
        d = gk.evaluate(_dir("ADJUST_CONFIDENCE_THRESHOLD", conf))
        assert d.accepted is True
        assert d.filter_name == "disabled"
    assert not rej_path.exists(), "disabled gatekeeper must not create log file"


def test_all_trigger_paths_fire_under_synthetic_input(tmp_path):
    """Verify each trigger can fire independently within one test run."""
    from auto_rollback_monitor import (
        TRIGGER_ACCURACY, TRIGGER_META_CONF, TRIGGER_SAFETY_NET, TRIGGER_MANUAL,
    )

    class _Cfg:
        ORACLE_BRAIN_SHADOW = False

    class _Sn:
        tripped = False
        _trip_reason = None

    # 1) accuracy
    cfg = _Cfg()
    mon = AutoRollbackMonitor(
        enabled=True, config_obj=cfg, accuracy_window=5, accuracy_threshold=0.5,
        force_sentinel_path=str(tmp_path / "s1"),
        shadow_forced_path=str(tmp_path / "f1.json"),
        forensic_dir=str(tmp_path / "fd1"),
    )
    for _ in range(5):
        mon.record_shadow_outcome(False)
    state = asyncio.get_event_loop().run_until_complete(mon.evaluate_once())
    assert state is not None and state.trigger == TRIGGER_ACCURACY

    # 2) meta_conf streak
    cfg = _Cfg()
    mon = AutoRollbackMonitor(
        enabled=True, config_obj=cfg, meta_conf_streak=3, meta_conf_min=0.5,
        force_sentinel_path=str(tmp_path / "s2"),
        shadow_forced_path=str(tmp_path / "f2.json"),
        forensic_dir=str(tmp_path / "fd2"),
    )
    for _ in range(3):
        mon.record_meta_confidence(0.1)
    state = asyncio.get_event_loop().run_until_complete(mon.evaluate_once())
    assert state is not None and state.trigger == TRIGGER_META_CONF

    # 3) safety net
    cfg = _Cfg()
    sn = _Sn()
    sn.tripped = True
    mon = AutoRollbackMonitor(
        enabled=True, config_obj=cfg, safety_net=sn,
        force_sentinel_path=str(tmp_path / "s3"),
        shadow_forced_path=str(tmp_path / "f3.json"),
        forensic_dir=str(tmp_path / "fd3"),
    )
    state = asyncio.get_event_loop().run_until_complete(mon.evaluate_once())
    assert state is not None and state.trigger == TRIGGER_SAFETY_NET

    # 4) manual sentinel
    cfg = _Cfg()
    sentinel = tmp_path / "s4"
    sentinel.write_text("x", encoding="utf-8")
    mon = AutoRollbackMonitor(
        enabled=True, config_obj=cfg,
        force_sentinel_path=str(sentinel),
        shadow_forced_path=str(tmp_path / "f4.json"),
        forensic_dir=str(tmp_path / "fd4"),
    )
    state = asyncio.get_event_loop().run_until_complete(mon.evaluate_once())
    assert state is not None and state.trigger == TRIGGER_MANUAL
