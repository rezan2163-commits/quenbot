"""test_asama2_integration.py — end-to-end: loosened gatekeeper + impact + rollback."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from directive_gatekeeper import DirectiveGatekeeper, _reset_for_tests as _reset_gk
from directive_impact_tracker import (
    DirectiveImpactTracker, _reset_for_tests as _reset_tr,
)
from auto_rollback_monitor import (
    AutoRollbackMonitor, TRIGGER_IMPACT_REGRESSION, TRIGGER_CASCADE,
    _reset_for_tests as _reset_rb,
)


@pytest.fixture(autouse=True)
def _reset():
    _reset_gk(); _reset_tr(); _reset_rb()
    yield
    _reset_gk(); _reset_tr(); _reset_rb()


class _Dir:
    def __init__(self, did, action, conf, symbol="BTCUSDT"):
        self.directive_id = did
        self.action = action
        self.confidence = conf
        self.symbol = symbol
        self.ts = time.time()
        self.severity = "med"


def test_aşama2_allowlist_accepts_new_types(tmp_path):
    gk = DirectiveGatekeeper(
        enabled=True, confidence_min=0.65, max_per_hour=10,
        allowlist=[
            "ADJUST_CONFIDENCE_THRESHOLD", "ADJUST_POSITION_SIZE_MULT",
            "PAUSE_SYMBOL", "RESUME_SYMBOL", "CHANGE_STRATEGY_WEIGHT",
            "ADJUST_TP_SL_RATIO",
        ],
        rejected_log_path=str(tmp_path / "rej.jsonl"),
    )
    # Each new type at 0.70 confidence passes.
    for a in ("RESUME_SYMBOL", "CHANGE_STRATEGY_WEIGHT", "ADJUST_TP_SL_RATIO"):
        dec = gk.evaluate(_Dir(f"d_{a}", a, 0.70))
        assert dec.accepted, f"{a} should accept: {dec}"


def test_aşama2_hard_blocklist_still_denies_force_trade(tmp_path):
    gk = DirectiveGatekeeper(
        enabled=True, confidence_min=0.65, max_per_hour=10,
        allowlist=["FORCE_TRADE"],  # even if allowlisted
        rejected_log_path=str(tmp_path / "rej.jsonl"),
    )
    dec = gk.evaluate(_Dir("x", "FORCE_TRADE", 0.99))
    assert not dec.accepted


def test_impact_flow_feeds_rollback_detector(tmp_path):
    """Live impact stream below synthetic baseline → rollback fires."""
    t0 = 1_000_000.0
    clk = {"t": t0}
    tracker = DirectiveImpactTracker(
        enabled=True, baseline_window_sec=60, measure_window_sec=60,
        data_provider=None, clock=lambda: clk["t"],
    )

    class _D:
        def __init__(self, did): self.directive_id = did; self.action = "ADJUST_CONFIDENCE_THRESHOLD"; self.symbol = "BTCUSDT"; self.ts = clk["t"]

    Meas = type(tracker._measurements).__mro__[0]
    # Actually it's a deque so get the ImpactMeasurement class:
    from directive_impact_tracker import ImpactMeasurement
    for i, v in enumerate([0.25, 0.30, 0.35, 0.20]):
        tracker._measurements.append(ImpactMeasurement(
            directive_id=f"s{i}", directive_type="ADJUST_CONFIDENCE_THRESHOLD",
            symbol="BTCUSDT", issued_ts=clk["t"], measured_at_ts=clk["t"],
            impact_score=v, metric_name="signal_quality", before=0.0, after=v,
            synthetic=True, source_tag="baseline",
        ))
    for i, v in enumerate([-0.6, -0.7, -0.5, -0.65, -0.55]):
        tracker._measurements.append(ImpactMeasurement(
            directive_id=f"l{i}", directive_type="ADJUST_CONFIDENCE_THRESHOLD",
            symbol="BTCUSDT", issued_ts=clk["t"], measured_at_ts=clk["t"],
            impact_score=v, metric_name="signal_quality", before=0.0, after=v,
            synthetic=False, source_tag=None,
        ))

    m = AutoRollbackMonitor(
        enabled=True,
        rejection_rate_threshold=0.99, rejection_window_min=30,
        accuracy_threshold=0.0, accuracy_window=10_000,
        meta_conf_min=0.0, meta_conf_streak=10_000,
        unhealthy_grace_sec=10_000,
        force_sentinel_path=str(tmp_path / "s"),
        shadow_forced_path=str(tmp_path / "f.json"),
        forensic_dir=str(tmp_path / "fx"),
        check_interval_sec=1,
        impact_tracker=tracker, impact_mean_min=-0.10, impact_window_h=24,
    )
    st = asyncio.run(m.evaluate_once())
    assert st is not None and st.trigger == TRIGGER_IMPACT_REGRESSION


def test_config_defaults_use_aşama2_values():
    # Import config fresh — defaults reflect the loosened Aşama 2 values.
    from config import Config
    # Only assert when env has not overridden; otherwise skip gracefully.
    import os
    if os.getenv("QUENBOT_ORACLE_BRAIN_DIRECTIVE_CONFIDENCE_MIN") is None:
        assert Config.ORACLE_BRAIN_DIRECTIVE_CONFIDENCE_MIN == 0.65
    if os.getenv("QUENBOT_ORACLE_BRAIN_MAX_DIRECTIVES_PER_HOUR") is None:
        assert Config.ORACLE_BRAIN_MAX_DIRECTIVES_PER_HOUR == 10
    if os.getenv("QUENBOT_AUTO_ROLLBACK_ACCURACY_MIN") is None:
        assert Config.AUTO_ROLLBACK_ACCURACY_THRESHOLD == 0.50
    # Hard blocklist always includes FORCE_TRADE & DISABLE_SAFETY_NET.
    assert "FORCE_TRADE" in Config.ORACLE_BRAIN_DIRECTIVE_BLOCKLIST_HARD
    assert "DISABLE_SAFETY_NET" in Config.ORACLE_BRAIN_DIRECTIVE_BLOCKLIST_HARD


def test_impact_tracker_registers_only_allowlisted_types():
    tracker = DirectiveImpactTracker(
        enabled=True, baseline_window_sec=60, measure_window_sec=60,
    )
    pend = asyncio.run(tracker.register_directive(_Dir("d1", "ADJUST_POSITION_SIZE_MULT", 0.9)))
    assert pend is not None
    none = asyncio.run(tracker.register_directive(_Dir("d2", "UNKNOWN_TYPE", 0.9)))
    assert none is None
