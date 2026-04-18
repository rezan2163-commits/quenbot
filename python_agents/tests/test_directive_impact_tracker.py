"""test_directive_impact_tracker.py — Aşama 2 impact formulae."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest

from directive_impact_tracker import (
    DirectiveImpactTracker, DIRECTIVE_TYPE_METRICS, _reset_for_tests,
)


@pytest.fixture(autouse=True)
def _reset():
    _reset_for_tests()
    yield
    _reset_for_tests()


class _Directive:
    def __init__(self, did: str, action: str, symbol: str, ts: float, params=None) -> None:
        self.directive_id = did
        self.action = action
        self.symbol = symbol
        self.ts = ts
        self.params = params or {}


class _Provider:
    def __init__(self) -> None:
        self.pool: dict = {}

    def add(self, symbol: str, ts: float, **fields):
        self.pool.setdefault(symbol, []).append({"ts": ts, **fields})

    def fetch_signals(self, symbol, start_ts, end_ts):
        rows = self.pool.get(symbol, [])
        return [r for r in rows if start_ts <= r["ts"] < end_ts]


def _mk_tracker(provider=None, clock=None, **kw):
    return DirectiveImpactTracker(
        enabled=True, baseline_window_sec=60, measure_window_sec=60,
        check_interval_sec=1, data_provider=provider, clock=clock, **kw,
    )


def test_measure_ready_skips_pending_before_window():
    prov = _Provider()
    t0 = 1_000_000.0
    clk = {"t": t0}
    tracker = _mk_tracker(provider=prov, clock=lambda: clk["t"])
    d = _Directive("d1", "ADJUST_CONFIDENCE_THRESHOLD", "BTCUSDT", t0)
    asyncio.run(tracker.register_directive(d))
    # advance only 30s — still inside measure window
    clk["t"] = t0 + 30
    results = asyncio.run(tracker.measure_ready())
    assert results == []
    clk["t"] = t0 + 61
    results = asyncio.run(tracker.measure_ready())
    assert len(results) == 1
    assert results[0].directive_id == "d1"


def test_adjust_confidence_threshold_positive_impact():
    prov = _Provider()
    t0 = 2_000_000.0
    clk = {"t": t0}
    # Before: 5 signals, 1 win  => signal_quality = 5 * 0.2 = 1.0
    for i in range(5):
        prov.add("BTCUSDT", t0 - 30 - i, win=(i == 0))
    # After: 5 signals, 4 wins => signal_quality = 5 * 0.8 = 4.0
    for i in range(5):
        prov.add("BTCUSDT", t0 + i + 1, win=(i < 4))
    tracker = _mk_tracker(provider=prov, clock=lambda: clk["t"])
    d = _Directive("d_pos", "ADJUST_CONFIDENCE_THRESHOLD", "BTCUSDT", t0)
    asyncio.run(tracker.register_directive(d))
    clk["t"] = t0 + 61
    (m,) = asyncio.run(tracker.measure_ready())
    assert m.impact_score > 0.2, m


def test_pause_symbol_zero_signals_gives_positive_impact():
    prov = _Provider()
    t0 = 3_000_000.0
    clk = {"t": t0}
    tracker = _mk_tracker(provider=prov, clock=lambda: clk["t"])
    d = _Directive("d_pause", "PAUSE_SYMBOL", "ETHUSDT", t0)
    asyncio.run(tracker.register_directive(d))
    clk["t"] = t0 + 61
    (m,) = asyncio.run(tracker.measure_ready())
    assert m.directive_type == "PAUSE_SYMBOL"
    assert m.impact_score >= 0.5  # no new signals → good


def test_resume_symbol_measures_signal_presence():
    prov = _Provider()
    t0 = 4_000_000.0
    clk = {"t": t0}
    for i in range(3):
        prov.add("SOLUSDT", t0 + i + 1, win=True)
    tracker = _mk_tracker(provider=prov, clock=lambda: clk["t"])
    d = _Directive("d_res", "RESUME_SYMBOL", "SOLUSDT", t0)
    asyncio.run(tracker.register_directive(d))
    clk["t"] = t0 + 61
    (m,) = asyncio.run(tracker.measure_ready())
    assert m.impact_score > 0.5


def test_change_strategy_weight_uses_confluence_delta():
    prov = _Provider()
    t0 = 5_000_000.0
    clk = {"t": t0}
    for i in range(4):
        prov.add("BNBUSDT", t0 - 40 + i, confluence=0.40)
    for i in range(4):
        prov.add("BNBUSDT", t0 + 10 + i, confluence=0.60)
    tracker = _mk_tracker(provider=prov, clock=lambda: clk["t"])
    d = _Directive("d_csw", "CHANGE_STRATEGY_WEIGHT", "BNBUSDT", t0)
    asyncio.run(tracker.register_directive(d))
    clk["t"] = t0 + 61
    (m,) = asyncio.run(tracker.measure_ready())
    assert m.impact_score > 0.1


def test_adjust_tp_sl_ratio_positive_when_tp_rate_rises():
    prov = _Provider()
    t0 = 6_000_000.0
    clk = {"t": t0}
    for i in range(4):
        prov.add("XRPUSDT", t0 - 50 + i, tp_hit=(i == 0), sl_hit=(i > 0))
    for i in range(4):
        prov.add("XRPUSDT", t0 + 5 + i, tp_hit=(i < 3), sl_hit=(i == 3))
    tracker = _mk_tracker(provider=prov, clock=lambda: clk["t"])
    d = _Directive("d_tp", "ADJUST_TP_SL_RATIO", "XRPUSDT", t0)
    asyncio.run(tracker.register_directive(d))
    clk["t"] = t0 + 61
    (m,) = asyncio.run(tracker.measure_ready())
    assert m.impact_score > 0.2


def test_untracked_directive_type_not_registered():
    prov = _Provider()
    tracker = _mk_tracker(provider=prov)
    d = _Directive("x", "HOLD_OFF", "BTCUSDT", time.time())
    pending = asyncio.run(tracker.register_directive(d))
    assert pending is None


def test_disabled_tracker_is_noop():
    prov = _Provider()
    tracker = DirectiveImpactTracker(
        enabled=False, baseline_window_sec=60, measure_window_sec=60,
        data_provider=prov,
    )
    d = _Directive("d", "ADJUST_CONFIDENCE_THRESHOLD", "BTCUSDT", time.time())
    assert asyncio.run(tracker.register_directive(d)) is None


def test_rolling_mean_and_synthetic_baseline():
    prov = _Provider()
    tracker = _mk_tracker(provider=prov)
    # Seed synthetic measurements.
    for i in range(10):
        d = _Directive(f"s{i}", "ADJUST_CONFIDENCE_THRESHOLD", "BTCUSDT", time.time())
        asyncio.run(tracker.measure_synthetic(
            d, baseline={"signal_quality": 0.1}, after={"signal_quality": 0.1 + (i - 5) * 0.05},
        ))
    bs = tracker.synthetic_baseline()
    assert bs["count"] == 10
    assert bs["std"] > 0


def test_aggregate_by_type_separates_live_and_synthetic():
    prov = _Provider()
    t0 = 7_000_000.0
    clk = {"t": t0}
    for i in range(2):
        prov.add("BTCUSDT", t0 + 1 + i, win=True)
    tracker = _mk_tracker(provider=prov, clock=lambda: clk["t"])
    live = _Directive("dl", "ADJUST_CONFIDENCE_THRESHOLD", "BTCUSDT", t0)
    asyncio.run(tracker.register_directive(live))
    clk["t"] = t0 + 61
    asyncio.run(tracker.measure_ready())
    syn = _Directive("ds", "ADJUST_CONFIDENCE_THRESHOLD", "BTCUSDT", t0)
    asyncio.run(tracker.measure_synthetic(syn, baseline={"signal_quality": 0.0}, after={"signal_quality": 0.3}))
    agg = tracker.aggregate_by_type()
    assert "ADJUST_CONFIDENCE_THRESHOLD" in agg
    row = agg["ADJUST_CONFIDENCE_THRESHOLD"]
    assert row["live_count"] == 1.0
    assert row["synthetic_count"] == 1.0
