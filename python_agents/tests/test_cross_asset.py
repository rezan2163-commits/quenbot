"""Cross-Asset Graph engine tests."""
from __future__ import annotations

import asyncio
import math

import pytest

from cross_asset_graph import CrossAssetGraphEngine, _crosscorr


def _make_series(n: int, seed: float = 0.0) -> list:
    """Deterministic pseudo-random returns."""
    out = []
    v = seed
    for i in range(n):
        v = math.sin(i * 0.7 + seed) * 0.01
        out.append(v)
    return out


def test_crosscorr_detects_zero_lag_perfect():
    x = [0.01, -0.02, 0.03, -0.01, 0.02, 0.00, 0.01, -0.03, 0.02, 0.01, -0.02, 0.03]
    y = list(x)
    lag, rho = _crosscorr(x, y, max_lag=3)
    assert lag == 0
    assert rho > 0.99


def test_crosscorr_detects_positive_lag():
    # y, x'in 2 bin gecikmişi — y_t = x_{t-2}
    x = [0.01, -0.02, 0.03, -0.01, 0.02, 0.00, 0.01, -0.03, 0.02, 0.01, -0.02, 0.03, 0.01, -0.01]
    y = [0.0, 0.0] + x[:-2]
    lag, rho = _crosscorr(x, y, max_lag=4)
    # y, x'ten sonra geldiği için argmax pozitif lag olmalı
    assert lag == 2
    assert rho > 0.9


def test_crosscorr_insufficient_data():
    lag, rho = _crosscorr([0.01, 0.02], [0.01, 0.02], max_lag=3)
    assert lag == 0
    assert rho == 0.0


def test_crosscorr_constant_series():
    x = [0.01] * 30
    y = [0.01] * 30
    lag, rho = _crosscorr(x, y, max_lag=3)
    # std sıfır → 0 döner (sıfırla bölme koruması)
    assert rho == 0.0


@pytest.mark.asyncio
async def test_engine_ingest_and_rebuild_builds_edges():
    # Engine floor'u step_sec=5; test ona uyumlu tasarlı
    eng = CrossAssetGraphEngine(
        event_bus=None,
        feature_store=None,
        symbols=["AAA", "BBB"],
        step_sec=5,
        history_sec=1800,
        max_lag_sec=60,
        min_samples=20,
        min_edge=0.2,
        rebuild_interval_sec=9999,
        leader_min_bps=99999,
    )

    class FakeEvt:
        def __init__(self, data):
            self.data = data

    import time as _time
    # base'i geçmişe taşı ki bin'ler rebuild'in "şimdi" anından önce kalsın
    base = float(int(_time.time())) - 1200
    shift_sec = 15  # BBB, AAA'nın 15sn gecikmişi → 3 bin
    n_ticks = 200   # adequate samples

    prices = [100.0]
    for i in range(1, n_ticks + shift_sec + 5):
        prices.append(prices[-1] * (1.0 + math.sin(i * 0.25) * 0.004))

    for i in range(shift_sec, n_ticks + shift_sec):
        await eng.on_price_update(FakeEvt({"symbol": "AAA", "price": prices[i], "timestamp": base + i}))
        await eng.on_price_update(FakeEvt({"symbol": "BBB", "price": prices[i - shift_sec], "timestamp": base + i}))

    await eng.rebuild()
    assert len(eng._edges) >= 1, f"en az bir kenar bulunmalı (edges={eng._edges})"
    aaa_leads = [e for e in eng._edges if e.src == "AAA" and e.dst == "BBB"]
    assert len(aaa_leads) == 1
    # shift_sec=15, step_sec=5 → lag_bins ≈ 3
    assert aaa_leads[0].lag_bins >= 1


@pytest.mark.asyncio
async def test_leader_alert_respects_cooldown():
    eng = CrossAssetGraphEngine(
        symbols=["X", "Y"], step_sec=1, history_sec=60, max_lag_sec=5,
        min_samples=20, min_edge=0.01, rebuild_interval_sec=9999,
        alert_cooldown_sec=30, leader_min_bps=10,
    )
    # Edge manuel olarak kur
    from cross_asset_graph import Edge
    eng._edges = [Edge(src="X", dst="Y", lag_bins=2, rho=0.9, samples=30)]

    class FakeEvt:
        def __init__(self, data):
            self.data = data

    import time as _time
    t0 = _time.time()
    # 2% jump → alert tetiklenmeli
    await eng.on_price_update(FakeEvt({"symbol": "X", "price": 100.0, "timestamp": t0}))
    await eng.on_price_update(FakeEvt({"symbol": "X", "price": 102.0, "timestamp": t0 + 1}))
    assert eng._alerts == 1
    spill = eng.spillover_signal("Y")
    assert spill > 0

    # Hemen bir jump daha → cooldown nedeniyle alert artmamalı
    await eng.on_price_update(FakeEvt({"symbol": "X", "price": 104.0, "timestamp": t0 + 2}))
    assert eng._alerts == 1


@pytest.mark.asyncio
async def test_spillover_signal_expires():
    eng = CrossAssetGraphEngine(
        symbols=["X", "Y"], step_sec=1, max_lag_sec=5, alert_cooldown_sec=1,
        leader_min_bps=10, rebuild_interval_sec=9999, min_edge=0.01, min_samples=20,
    )
    from cross_asset_graph import Edge
    eng._edges = [Edge(src="X", dst="Y", lag_bins=1, rho=0.9, samples=30)]

    # Expiration past-dated
    eng._active_spillovers["Y"] = (0.0, 2.0)
    assert eng.spillover_signal("Y") == 0.0
    assert "Y" not in eng._active_spillovers


def test_health_and_metrics():
    eng = CrossAssetGraphEngine(symbols=["A", "B"])
    m = eng.metrics()
    assert "cross_asset_edges" in m
    assert m["cross_asset_edges"] == 0
