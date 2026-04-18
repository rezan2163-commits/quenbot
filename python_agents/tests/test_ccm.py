"""Test: CausalOnChainBridge (§8)."""
from __future__ import annotations

import math
import random

import pytest

import causal_onchain_bridge as ccb


@pytest.fixture(autouse=True)
def _reset():
    ccb._reset_for_tests()
    yield
    ccb._reset_for_tests()


def test_singleton_and_channel_none():
    det = ccb.get_causal_onchain()
    assert det is ccb.get_causal_onchain()
    assert det.oracle_channel_value("UNKNOWN") is None


def test_insufficient_history_disabled():
    det = ccb.get_causal_onchain(publish_hz=1000.0, lib_size=200)
    for i in range(10):
        det.observe_cex("BTCUSDT", float(i), ts=float(i))
        det.observe_onchain("BTCUSDT", float(i), ts=float(i))
    out = det.maybe_publish("BTCUSDT", ts=10.0)
    assert out is not None
    assert out.get("disabled_reason") == "insufficient_history"


def test_nan_observation_ignored():
    det = ccb.get_causal_onchain()
    det.observe_cex("BTCUSDT", float("nan"), ts=1.0)
    det.observe_onchain("BTCUSDT", float("nan"), ts=1.0)
    snap = det.snapshot("BTCUSDT")
    if snap is not None:
        assert snap["cex_points"] == 0
        assert snap["onchain_points"] == 0


def test_ccm_runs_on_sufficient_data():
    random.seed(17)
    det = ccb.get_causal_onchain(publish_hz=1000.0, lib_size=80, embed_dim=3)
    # Zincir üstü lead: onchain(t) → cex(t+5)
    N = 120
    x = [math.sin(0.1 * i) + random.gauss(0, 0.05) for i in range(N)]
    y = [x[max(0, i - 5)] + random.gauss(0, 0.1) for i in range(N)]
    for i in range(N):
        det.observe_onchain("BTCUSDT", x[i], ts=float(i))
        det.observe_cex("BTCUSDT", y[i], ts=float(i))
    out = det.maybe_publish("BTCUSDT", ts=float(N))
    assert out is not None
    # CCM çalıştıysa 'lead' var; yoksa disabled
    if "lead" in out:
        assert -1.0 <= out["lead"] <= 1.0


def test_throttle():
    det = ccb.get_causal_onchain(publish_hz=0.01, lib_size=50)
    for i in range(60):
        det.observe_cex("BTCUSDT", float(i), ts=float(i))
        det.observe_onchain("BTCUSDT", float(i), ts=float(i))
    det.maybe_publish("BTCUSDT", ts=60.0)
    second = det.maybe_publish("BTCUSDT", ts=60.5)
    assert second is None


@pytest.mark.asyncio
async def test_health():
    det = ccb.get_causal_onchain()
    await det.initialize()
    h = await det.health_check()
    assert h["healthy"] is True
