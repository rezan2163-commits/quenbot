"""Test: PathSignatureEngine (§5)."""
from __future__ import annotations

import random

import pytest

import path_signature_engine as pse


@pytest.fixture(autouse=True)
def _reset():
    pse._reset_for_tests()
    yield
    pse._reset_for_tests()


def test_singleton_and_channel_none():
    det = pse.get_path_signature()
    assert det is pse.get_path_signature()
    assert det.oracle_channel_value("UNKNOWN") is None


def test_observe_and_compute_signature():
    random.seed(2)
    det = pse.get_path_signature(publish_hz=1000.0)
    t = 0.0
    for _ in range(60):
        t += 1.0
        det.observe("BTCUSDT", dlog_p=random.gauss(0, 0.001),
                    d_obi=random.gauss(0, 0.05), d_ofi=random.gauss(0, 0.1), ts=t)
    out = det.maybe_publish("BTCUSDT", ts=t)
    assert out is not None
    assert "signature" in out or "similarity" in out


def test_invalid_nan_observation_ignored():
    det = pse.get_path_signature()
    det.observe("BTCUSDT", dlog_p=float("nan"), d_obi=0.0, d_ofi=0.0, ts=1.0)
    snap = det.snapshot("BTCUSDT")
    # NaN reddedildi → ya yok ya da path boş
    if snap is not None:
        assert snap.get("path_len", 0) == 0


def test_throttle():
    det = pse.get_path_signature(publish_hz=0.1)
    for i in range(60):
        det.observe("BTCUSDT", 0.001 * i, 0.0, 0.0, ts=float(i))
    det.maybe_publish("BTCUSDT", ts=60.0)
    second = det.maybe_publish("BTCUSDT", ts=60.5)
    assert second is None


@pytest.mark.asyncio
async def test_health():
    det = pse.get_path_signature()
    await det.initialize()
    h = await det.health_check()
    assert h["healthy"] is True
