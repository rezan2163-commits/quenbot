"""test_factor_graph.py — §10 FactorGraphFusion tests."""
from __future__ import annotations

import asyncio
import math

import pytest

from oracle_signal_bus import OracleSignalBus, _reset_for_tests as _reset_bus
from factor_graph_fusion import (
    FactorGraphFusion,
    get_factor_graph,
    _reset_for_tests as _reset_fg,
    DEFAULT_CHANNEL_POLARITY,
)


@pytest.fixture(autouse=True)
def _reset():
    _reset_bus()
    _reset_fg()
    yield
    _reset_bus()
    _reset_fg()


@pytest.mark.asyncio
async def test_singleton_idempotent():
    a = get_factor_graph()
    b = get_factor_graph()
    assert a is b
    await a.initialize()
    h = await a.health_check()
    assert h["healthy"] is True


def test_oracle_channel_value_none_for_unknown():
    fg = FactorGraphFusion()
    assert fg.oracle_channel_value("NOPE") is None
    assert fg.snapshot("NOPE") is None


def test_fuse_empty_returns_zero():
    fg = FactorGraphFusion(bp_iters=5)
    ifi, direction, marginals = fg._fuse({})
    assert ifi == 0.0
    assert direction == 0.0
    assert marginals == {}


def test_fuse_bounded_range():
    fg = FactorGraphFusion(bp_iters=10, damping=0.5)
    channels = {
        "ofi_hurst": 0.8,
        "hawkes_branching_ratio": 0.7,
        "entropy_cooling": 0.6,
        "wasserstein_drift_zscore": -0.4,
        "mirror_execution_strength": 0.9,
        "topological_whale_birth": 0.85,
    }
    ifi, direction, marginals = fg._fuse(channels)
    assert 0.0 <= ifi <= 1.0
    assert -1.0 <= direction <= 1.0
    assert set(marginals.keys()) == set(channels.keys())
    for m in marginals.values():
        assert 0.0 <= m <= 1.0


def test_fuse_direction_bullish_vs_bearish():
    fg = FactorGraphFusion(bp_iters=10, damping=0.5)
    # Bullish: all positive-polarity channels positive
    bullish = {"ofi_hurst": 0.9, "hawkes_branching_ratio": 0.9, "onchain_lead_strength": 0.9,
               "cross_asset_spillover": 0.9, "multi_horizon_coherence": 0.9}
    bearish = {k: -v for k, v in bullish.items()}
    _, d_up, _ = fg._fuse(bullish)
    _, d_dn, _ = fg._fuse(bearish)
    assert d_up > 0
    assert d_dn < 0


@pytest.mark.asyncio
async def test_publish_throttle_and_signal_bus_roundtrip():
    bus = OracleSignalBus()
    fg = FactorGraphFusion(signal_bus=bus, bp_iters=5, publish_hz=1000.0)
    await fg.initialize()
    # Seed channels via signal bus
    for ch in ["ofi_hurst", "hawkes_branching_ratio", "mirror_execution_strength"]:
        bus.publish("BTCUSDT", ch, 0.8, source="test")
    out = fg.maybe_publish("BTCUSDT", ts=1000.0)
    assert out is not None
    assert 0.0 <= out["ifi"] <= 1.0
    # Throttle: next call within interval returns None if publish_hz low
    fg2 = FactorGraphFusion(signal_bus=bus, bp_iters=5, publish_hz=0.1)
    await fg2.initialize()
    r1 = fg2.maybe_publish("BTCUSDT", ts=2000.0)
    r2 = fg2.maybe_publish("BTCUSDT", ts=2000.5)  # throttled
    assert r1 is not None
    assert r2 is None
    # IFI also in signal_bus under ORACLE_CHANNEL_NAME
    snaps = bus.all_snapshots()
    assert "BTCUSDT" in snaps
    assert FactorGraphFusion.ORACLE_CHANNEL_NAME in snaps["BTCUSDT"]


def test_update_weights():
    fg = FactorGraphFusion()
    fg.update_weights({"ofi_hurst": 2.5, "bogus": "xx"})
    assert fg.weights["ofi_hurst"] == 2.5
    # bogus ignored
    assert "bogus" not in fg.weights or not isinstance(fg.weights.get("bogus"), str)


def test_metrics_shape():
    fg = FactorGraphFusion()
    m = fg.metrics()
    for k in ("fg_fusions_total", "fg_publishes_total", "fg_bp_iters_total", "fg_symbols_active"):
        assert k in m
