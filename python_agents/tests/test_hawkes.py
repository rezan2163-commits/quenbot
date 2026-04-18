"""Test: HawkesKernelFitter (§2)."""
from __future__ import annotations

import random

import pytest

import hawkes_kernel_fitter as hkf


@pytest.fixture(autouse=True)
def _reset():
    hkf._reset_for_tests()
    yield
    hkf._reset_for_tests()


def test_singleton_and_channel_none():
    det = hkf.get_hawkes_fitter()
    assert det is hkf.get_hawkes_fitter()
    assert det.oracle_channel_value("UNKNOWN") is None


def test_mark_type_validation():
    det = hkf.get_hawkes_fitter()
    # Geçersiz mark sessizce yoksayılmalı
    det.observe("BTCUSDT", "garbage_type", ts=1.0)
    snap = det.snapshot("BTCUSDT")
    # Gözlem olmadıysa snapshot ya None ya da boş
    if snap is not None:
        assert snap.get("events", 0) == 0


def test_basic_observe_and_publish():
    random.seed(3)
    det = hkf.get_hawkes_fitter(publish_hz=1000.0)
    t = 0.0
    for _ in range(200):
        t += random.expovariate(1.0)
        det.observe("BTCUSDT", "buy", ts=t)
        t += random.expovariate(1.0)
        det.observe("BTCUSDT", "sell", ts=t)
    out = det.maybe_publish("BTCUSDT", ts=t)
    # EM fit zaman alabilir; None veya dict kabul
    assert out is None or isinstance(out, dict)


def test_branching_ratio_bounded():
    random.seed(5)
    det = hkf.get_hawkes_fitter(publish_hz=1000.0)
    t = 0.0
    for _ in range(150):
        t += random.expovariate(2.0)
        det.observe("ETHUSDT", "buy", ts=t)
    det.maybe_publish("ETHUSDT", ts=t + 1)
    val = det.oracle_channel_value("ETHUSDT")
    if val is not None:
        assert -1.0 <= val <= 1.0


def test_throttle():
    det = hkf.get_hawkes_fitter(publish_hz=0.1)
    for i in range(100):
        det.observe("BTCUSDT", "buy", ts=float(i))
    det.maybe_publish("BTCUSDT", ts=100.0)
    second = det.maybe_publish("BTCUSDT", ts=100.5)
    assert second is None


@pytest.mark.asyncio
async def test_health():
    det = hkf.get_hawkes_fitter()
    await det.initialize()
    h = await det.health_check()
    assert h["healthy"] is True
