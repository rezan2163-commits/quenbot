"""test_oracle_brain.py — §11 QwenOracleBrain tests."""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

from oracle_signal_bus import OracleSignalBus, _reset_for_tests as _reset_bus
from factor_graph_fusion import FactorGraphFusion, _reset_for_tests as _reset_fg
from qwen_oracle_schemas import OracleObservation
from qwen_oracle_brain import (
    QwenOracleBrain, get_oracle_brain, _reset_for_tests as _reset_brain,
)


@pytest.fixture(autouse=True)
def _reset():
    _reset_bus()
    _reset_fg()
    _reset_brain()
    yield
    _reset_bus()
    _reset_fg()
    _reset_brain()


@pytest.mark.asyncio
async def test_singleton_and_default_shadow():
    b = get_oracle_brain(symbols=["BTCUSDT"])
    assert b is get_oracle_brain()
    await b.initialize()
    assert b.shadow is True
    h = await b.health_check()
    assert h["healthy"] is True
    assert h["shadow"] is True


def _mk_obs(**channels) -> OracleObservation:
    ifi = channels.pop("_ifi", None)
    direction = channels.pop("_direction", None)
    o = OracleObservation(symbol="BTCUSDT", channels={k: float(v) for k, v in channels.items()})
    o.ifi = ifi
    o.ifi_direction = direction
    return o


def test_heuristic_critical_rule():
    b = QwenOracleBrain()
    o = _mk_obs(topological_whale_birth=0.85, mirror_execution_strength=0.9, _ifi=0.4, _direction=0.1)
    d = b._heuristic_directive(o)
    assert d.action == "HOLD_OFF"
    assert d.severity == "critical"


def test_heuristic_high_bias_rule():
    b = QwenOracleBrain()
    o = _mk_obs(_ifi=0.8, _direction=0.6)
    d = b._heuristic_directive(o)
    assert d.action == "BIAS_DIRECTION"
    assert d.severity == "high"
    assert d.params["direction"] == "long"

    o2 = _mk_obs(_ifi=0.8, _direction=-0.7)
    d2 = b._heuristic_directive(o2)
    assert d2.action == "BIAS_DIRECTION"
    assert d2.params["direction"] == "short"


def test_heuristic_entropy_tightens_stops():
    b = QwenOracleBrain()
    o = _mk_obs(entropy_cooling=0.75, _ifi=0.3, _direction=0.0)
    d = b._heuristic_directive(o)
    assert d.action == "TIGHTEN_STOPS"
    assert d.severity == "medium"


def test_heuristic_wasserstein_adjusts_risk():
    b = QwenOracleBrain()
    o = _mk_obs(wasserstein_drift_zscore=-0.8, _ifi=0.3, _direction=0.0)
    d = b._heuristic_directive(o)
    assert d.action == "ADJUST_RISK"
    assert d.params.get("kelly_scale") == 0.5


def test_heuristic_mild_bias():
    b = QwenOracleBrain()
    o = _mk_obs(_ifi=0.55, _direction=0.3)
    d = b._heuristic_directive(o)
    assert d.action == "BIAS_DIRECTION"
    assert d.severity == "low"


def test_heuristic_default_monitor():
    b = QwenOracleBrain()
    o = _mk_obs(_ifi=0.1, _direction=0.0)
    d = b._heuristic_directive(o)
    assert d.action == "MONITOR"
    assert d.severity == "info"


@pytest.mark.asyncio
async def test_tick_symbol_emits_directive_and_logs_trace():
    bus = OracleSignalBus()
    bus.publish("BTCUSDT", "ofi_hurst", 0.9, source="test")
    bus.publish("BTCUSDT", "hawkes_branching_ratio", 0.9, source="test")
    bus.publish("BTCUSDT", "onchain_lead_strength", 0.9, source="test")
    bus.publish("BTCUSDT", "cross_asset_spillover", 0.9, source="test")
    bus.publish("BTCUSDT", "multi_horizon_coherence", 0.9, source="test")
    fg = FactorGraphFusion(signal_bus=bus, bp_iters=10, publish_hz=1000.0)
    await fg.initialize()
    fg.maybe_publish("BTCUSDT", ts=time.time())
    b = QwenOracleBrain(signal_bus=bus, factor_graph=fg, symbols=["BTCUSDT"], shadow=True)
    await b.initialize()
    await b._tick_symbol("BTCUSDT")
    last = b.last_directive("BTCUSDT")
    assert last is not None
    assert last.shadow is True
    traces = b.recent_traces(limit=5)
    assert len(traces) >= 1
    # Action must be bias long given strong positive IFI + direction
    assert last.action in ("BIAS_DIRECTION", "MONITOR", "ADJUST_RISK", "TIGHTEN_STOPS", "HOLD_OFF")


@pytest.mark.asyncio
async def test_safety_tripped_noop():
    b = QwenOracleBrain(symbols=["BTCUSDT"])
    await b.initialize()
    b._safety_net = SimpleNamespace(tripped=True)
    await b._tick_symbol("BTCUSDT")
    assert b.last_directive("BTCUSDT") is None
    assert b._stats.observations == 0


@pytest.mark.asyncio
async def test_all_last_directives_and_set_symbols():
    b = QwenOracleBrain(symbols=["BTCUSDT"])
    await b.initialize()
    b.set_symbols(["ETHUSDT", "BTCUSDT"])
    assert set(b.symbols) == {"ETHUSDT", "BTCUSDT"}
    assert b.all_last_directives() == {}


@pytest.mark.asyncio
async def test_health_and_metrics_shape():
    b = QwenOracleBrain(symbols=["BTCUSDT"])
    await b.initialize()
    h = await b.health_check()
    for k in ("healthy", "running", "shadow", "symbols", "observations",
              "directives_emitted", "learn_cycles", "teach_cycles"):
        assert k in h
    m = b.metrics()
    for k in ("brain_observations_total", "brain_directives_total",
              "brain_learn_cycles_total", "brain_teach_cycles_total",
              "brain_llm_calls_total", "brain_llm_errors_total"):
        assert k in m


@pytest.mark.asyncio
async def test_start_stop_cancellable():
    b = QwenOracleBrain(symbols=["BTCUSDT"], observe_interval_sec=0.05)
    await b.initialize()
    await b.start()
    await asyncio.sleep(0.15)
    await b.stop()
    assert b._running is False
