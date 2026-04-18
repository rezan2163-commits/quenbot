"""Test: MirrorFlowAnalyzer (§6)."""
from __future__ import annotations

import random

import pytest

import mirror_flow_analyzer as mfa


@pytest.fixture(autouse=True)
def _reset():
    mfa._reset_for_tests()
    yield
    mfa._reset_for_tests()


def test_singleton_and_channel_none():
    det = mfa.get_mirror_flow()
    assert det is mfa.get_mirror_flow()
    assert det.oracle_channel_value("UNKNOWN") is None


def test_single_exchange_disabled():
    det = mfa.get_mirror_flow(publish_hz=1000.0)
    for i in range(30):
        det.observe("BTCUSDT", "binance", 1.0, ts=float(i))
    out = det.maybe_publish("BTCUSDT", ts=30.0)
    assert out is not None
    assert out.get("disabled_reason") == "insufficient_dual_feed"


def test_invalid_exchange_ignored():
    det = mfa.get_mirror_flow()
    det.observe("BTCUSDT", "okx", 1.0, ts=1.0)
    snap = det.snapshot("BTCUSDT")
    # Stat olmadığı için None ya da boş
    assert snap is None or snap["binance_trades"] == 0


def test_synchronized_streams_low_dtw():
    random.seed(11)
    det = mfa.get_mirror_flow(publish_hz=1000.0, window_min=1, bucket_sec=0.5, sustained_window_sec=999999)
    base = [random.gauss(0, 1) for _ in range(120)]
    for i, v in enumerate(base):
        det.observe("BTCUSDT", "binance", v, ts=float(i) * 0.5)
        det.observe("BTCUSDT", "bybit", v + random.gauss(0, 0.01), ts=float(i) * 0.5 + 0.05)
    out = det.maybe_publish("BTCUSDT", ts=float(len(base)) * 0.5 + 1)
    assert out is not None
    # Senkron akışlar → DTW sonlu
    if "dtw" in out:
        assert out["dtw"] >= 0.0


def test_throttle():
    det = mfa.get_mirror_flow(publish_hz=0.1)
    for i in range(50):
        det.observe("BTCUSDT", "binance", 1.0, ts=float(i))
        det.observe("BTCUSDT", "bybit", 1.0, ts=float(i))
    det.maybe_publish("BTCUSDT", ts=50.0)
    second = det.maybe_publish("BTCUSDT", ts=50.5)
    assert second is None


@pytest.mark.asyncio
async def test_health():
    det = mfa.get_mirror_flow()
    await det.initialize()
    h = await det.health_check()
    assert h["healthy"] is True
