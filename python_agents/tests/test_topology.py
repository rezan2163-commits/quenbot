"""Test: TopologicalLOBAnalyzer (§7)."""
from __future__ import annotations

import random

import pytest

import topological_lob_analyzer as tla


@pytest.fixture(autouse=True)
def _reset():
    tla._reset_for_tests()
    yield
    tla._reset_for_tests()


def test_singleton_and_channel_none():
    det = tla.get_topology()
    assert det is tla.get_topology()
    assert det.oracle_channel_value("UNKNOWN") is None


def test_empty_levels_ignored():
    det = tla.get_topology()
    det.observe("BTCUSDT", [], ts=1.0)
    snap = det.snapshot("BTCUSDT")
    assert snap is None


def test_compact_cloud_low_birth():
    random.seed(3)
    det = tla.get_topology(publish_hz=1000.0, max_edge=5.0)
    for i in range(30):
        pts = [(random.gauss(0, 0.1), random.gauss(0, 0.1)) for _ in range(20)]
        det.observe("BTCUSDT", pts, ts=float(i))
    out = det.maybe_publish("BTCUSDT", ts=30.0)
    assert out is not None
    # Sıkı bulut → anomali düşük
    assert 0.0 <= out["birth"] <= 1.0


def test_throttle():
    det = tla.get_topology(publish_hz=0.1)
    pts = [(float(i), 1.0) for i in range(10)]
    for i in range(20):
        det.observe("BTCUSDT", pts, ts=float(i))
    det.maybe_publish("BTCUSDT", ts=20.0)
    second = det.maybe_publish("BTCUSDT", ts=20.5)
    assert second is None


@pytest.mark.asyncio
async def test_health():
    det = tla.get_topology()
    await det.initialize()
    h = await det.health_check()
    assert h["healthy"] is True
    assert "backend" in h
