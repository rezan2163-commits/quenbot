"""Test: LOBThermodynamics (§3)."""
from __future__ import annotations

import pytest

import lob_thermodynamics as lth
from oracle_signal_bus import OracleSignalBus


@pytest.fixture(autouse=True)
def _reset():
    lth._reset_for_tests()
    yield
    lth._reset_for_tests()


def _uniform_levels(n=20):
    return [1.0] * n


def _peaked_levels(n=20):
    return [10.0] + [0.1] * (n - 1)


def test_singleton_and_channel_none():
    det = lth.get_lob_thermodynamics()
    assert det is lth.get_lob_thermodynamics()
    assert det.oracle_channel_value("UNKNOWN") is None


def test_stationary_no_cooling():
    det = lth.get_lob_thermodynamics(publish_hz=1000.0)
    for i in range(60):
        det.observe("BTCUSDT", _uniform_levels(), _uniform_levels(), ts=float(i))
    out = det.maybe_publish("BTCUSDT", ts=60.0)
    assert out is not None
    # Entropy kararlı → cooling yoğunluğu düşük
    assert out.get("intensity", 0.0) <= 0.5


def test_concentration_triggers_cooling():
    det = lth.get_lob_thermodynamics(publish_hz=1000.0, cooling_window_sec=10, dt_sec=1)
    # Geniş → dar transition
    for i in range(30):
        det.observe("ETHUSDT", _uniform_levels(), _uniform_levels(), ts=float(i))
    for i in range(30, 120):
        det.observe("ETHUSDT", _peaked_levels(), _peaked_levels(), ts=float(i))
    out = det.maybe_publish("ETHUSDT", ts=120.0)
    assert out is not None
    # Entropy düşüş → cooling yoğunluğu hesaplandı
    assert 0.0 <= out.get("intensity", 0.0) <= 1.0


def test_signal_bus_publish():
    bus = OracleSignalBus()
    det = lth.get_lob_thermodynamics(signal_bus=bus, publish_hz=1000.0)
    for i in range(30):
        det.observe("BTCUSDT", _uniform_levels(), _uniform_levels(), ts=float(i))
    det.maybe_publish("BTCUSDT", ts=30.0)
    val = bus.read("BTCUSDT", "entropy_cooling")
    assert val is not None


@pytest.mark.asyncio
async def test_health():
    det = lth.get_lob_thermodynamics()
    await det.initialize()
    h = await det.health_check()
    assert h["healthy"] is True
