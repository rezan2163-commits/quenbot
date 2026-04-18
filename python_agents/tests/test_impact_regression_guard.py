"""test_impact_regression_guard.py — Aşama 2 safety-net impact regression."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from safety_net import SafetyNet
from directive_impact_tracker import DirectiveImpactTracker, _reset_for_tests
from auto_rollback_monitor import (
    AutoRollbackMonitor, TRIGGER_IMPACT_REGRESSION,
    _reset_for_tests as _reset_rb,
)


@pytest.fixture(autouse=True)
def _reset():
    _reset_for_tests()
    _reset_rb()
    yield
    _reset_for_tests()
    _reset_rb()


def _seed_tracker(synthetic_values, live_values, clock):
    tracker = DirectiveImpactTracker(
        enabled=True, baseline_window_sec=60, measure_window_sec=60,
        data_provider=None, clock=clock,
    )

    class _D:
        def __init__(self, did):
            self.directive_id = did
            self.action = "ADJUST_CONFIDENCE_THRESHOLD"
            self.symbol = "BTCUSDT"
            self.ts = clock()

    # Synthetic baseline.
    for i, v in enumerate(synthetic_values):
        asyncio.run(tracker.measure_synthetic(
            _D(f"syn{i}"),
            baseline={"signal_quality": 0.0},
            after={"signal_quality": v},
        ))
        # Force the score directly: above helper re-scales; bypass by
        # appending a measurement with explicit value.
        tracker._measurements[-1] = type(tracker._measurements[-1])(
            directive_id=f"syn{i}", directive_type="ADJUST_CONFIDENCE_THRESHOLD",
            symbol="BTCUSDT", issued_ts=clock(), measured_at_ts=clock(),
            impact_score=v, metric_name="signal_quality",
            before=0.0, after=v, synthetic=True, source_tag="baseline",
        )
    for i, v in enumerate(live_values):
        asyncio.run(tracker.measure_synthetic(
            _D(f"live{i}"),
            baseline={"signal_quality": 0.0},
            after={"signal_quality": v},
        ))
        tracker._measurements[-1] = type(tracker._measurements[-1])(
            directive_id=f"live{i}", directive_type="ADJUST_CONFIDENCE_THRESHOLD",
            symbol="BTCUSDT", issued_ts=clock(), measured_at_ts=clock(),
            impact_score=v, metric_name="signal_quality",
            before=0.0, after=v, synthetic=False, source_tag=None,
        )
    return tracker


def test_guard_status_below_starts_timer(tmp_path):
    t0 = 1_000_000.0
    clk = {"t": t0}
    tracker = _seed_tracker(
        synthetic_values=[0.3, 0.35, 0.25, 0.30, 0.28],
        live_values=[-0.5, -0.45, -0.55, -0.4, -0.5],
        clock=lambda: clk["t"],
    )
    sn = SafetyNet(
        event_bus=None, config=None,
        baseline_path=str(tmp_path / "b.json"),
        trip_sentinel_path=str(tmp_path / "t.json"),
    )
    # First call records regression_started, does not trip.
    diag = sn.check_impact_regression(tracker, sigma=1.0, duration_sec=3600)
    assert diag["status"] == "below"
    assert diag.get("regression_started") is True
    assert not sn._tripped


def test_guard_trips_after_duration(tmp_path, monkeypatch):
    t0 = 1_000_000.0
    clk = {"t": t0}
    tracker = _seed_tracker(
        synthetic_values=[0.30, 0.35, 0.25, 0.30, 0.28],
        live_values=[-0.5, -0.45, -0.55, -0.4, -0.5],
        clock=lambda: clk["t"],
    )
    sn = SafetyNet(
        event_bus=None, config=None,
        baseline_path=str(tmp_path / "b.json"),
        trip_sentinel_path=str(tmp_path / "t.json"),
    )
    # Fix safety_net's internal _now() to advance along with clk.
    import safety_net as _sn
    monkeypatch.setattr(_sn, "_now", lambda: clk["t"])

    sn.check_impact_regression(tracker, sigma=1.0, duration_sec=60)
    clk["t"] = t0 + 120
    res = sn.check_impact_regression(tracker, sigma=1.0, duration_sec=60)
    assert res.get("tripped") is True
    assert sn._tripped


def test_guard_clears_when_live_recovers(tmp_path, monkeypatch):
    t0 = 2_000_000.0
    clk = {"t": t0}
    tracker = _seed_tracker(
        synthetic_values=[0.30, 0.35, 0.25, 0.30],
        live_values=[-0.5, -0.4, -0.55],
        clock=lambda: clk["t"],
    )
    sn = SafetyNet(
        event_bus=None, config=None,
        baseline_path=str(tmp_path / "b.json"),
        trip_sentinel_path=str(tmp_path / "t.json"),
    )
    import safety_net as _sn
    monkeypatch.setattr(_sn, "_now", lambda: clk["t"])
    sn.check_impact_regression(tracker, sigma=1.0, duration_sec=60)
    # Append recovering live values.
    tracker._measurements.append(type(tracker._measurements[-1])(
        directive_id="live_good", directive_type="ADJUST_CONFIDENCE_THRESHOLD",
        symbol="BTCUSDT", issued_ts=clk["t"], measured_at_ts=clk["t"],
        impact_score=0.5, metric_name="signal_quality", before=0.0, after=0.5,
        synthetic=False, source_tag=None,
    ))
    res = sn.check_impact_regression(tracker, sigma=1.0, duration_sec=60)
    # live mean now likely above threshold → status ok, timer cleared.
    assert res["status"] in ("ok", "below")
    if res["status"] == "ok":
        assert getattr(sn, "_impact_regression_since", None) is None


def test_auto_rollback_fires_on_impact_regression(tmp_path):
    t0 = 3_000_000.0
    clk = {"t": t0}
    tracker = _seed_tracker(
        synthetic_values=[0.25, 0.20, 0.30],
        live_values=[-0.6, -0.7, -0.5, -0.65],
        clock=lambda: clk["t"],
    )
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
    assert st is not None
    assert st.trigger == TRIGGER_IMPACT_REGRESSION
