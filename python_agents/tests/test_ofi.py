"""OFI formül ve Hurst R/S için unit testler."""
from __future__ import annotations

import math
import random
from types import SimpleNamespace

import pytest

from order_flow_imbalance import (
    OrderFlowImbalanceEngine,
    hurst_rs,
)


def test_ofi_increment_qty_change_same_level():
    # Aynı level, sadece qty değişimi: OFI = (Δbid_qty) − (Δask_qty)
    ofi = OrderFlowImbalanceEngine._compute_ofi_increment(
        b_px=100.0, b_qty=12.0, a_px=101.0, a_qty=8.0,
        lb_px=100.0, lb_qty=10.0, la_px=101.0, la_qty=10.0,
    )
    # bid_term = 12 - 10 = +2; ask_term = 8 - 10 = -2; OFI = 2 - (-2) = 4
    assert math.isclose(ofi, 4.0)


def test_ofi_increment_bid_price_up():
    # Yeni daha iyi bid → bid_term = +b_qty
    # ask değişmediği için ask_term = a_qty - la_qty
    ofi = OrderFlowImbalanceEngine._compute_ofi_increment(
        b_px=100.5, b_qty=5.0, a_px=101.0, a_qty=7.0,
        lb_px=100.0, lb_qty=10.0, la_px=101.0, la_qty=7.0,
    )
    # bid_term = +5; ask_term = 0; OFI = 5
    assert math.isclose(ofi, 5.0)


def test_ofi_increment_ask_price_up():
    # Ask yukarı → ask_term = -la_qty (iptal), bid değişmedi
    ofi = OrderFlowImbalanceEngine._compute_ofi_increment(
        b_px=100.0, b_qty=10.0, a_px=101.5, a_qty=3.0,
        lb_px=100.0, lb_qty=10.0, la_px=101.0, la_qty=8.0,
    )
    # bid_term = 0; ask_term = -8; OFI = 0 - (-8) = 8
    assert math.isclose(ofi, 8.0)


def test_hurst_persistent_trend():
    # y_t = y_{t-1} + 0.1 + small noise → persistent (H > 0.5)
    random.seed(42)
    series = [0.0]
    for _ in range(300):
        series.append(series[-1] + 0.1 + random.gauss(0, 0.05))
    # Kümülatif trend içinden ilk farkları alalım (OFI benzeri: seri)
    # Trendli artan değerler kendi başına persistent
    h = hurst_rs(series, min_n=16)
    assert h is not None
    assert h > 0.5


def test_hurst_random_walk_near_half():
    random.seed(7)
    # white noise artışlar → H ≈ 0.5
    series = [random.gauss(0, 1) for _ in range(400)]
    h = hurst_rs(series, min_n=16)
    assert h is not None
    assert 0.30 <= h <= 0.70  # loose band due to small-sample bias


def test_hurst_too_short_returns_none():
    assert hurst_rs([1.0, 2.0, 3.0], min_n=32) is None


@pytest.mark.asyncio
async def test_ofi_engine_tracks_after_first_update():
    eng = OrderFlowImbalanceEngine(event_bus=None, feature_store=None, publish_hz=1000.0)

    def mk_event(bid_px, bid_qty, ask_px, ask_qty):
        return SimpleNamespace(data={
            "symbol": "BTCUSDT",
            "bids": [[bid_px, bid_qty]],
            "asks": [[ask_px, ask_qty]],
        })

    # ilk güncelleme: OFI=0 (önce state yok)
    await eng.on_order_book(mk_event(100, 10, 101, 10))
    # qty artışı → pozitif OFI birikimi
    for _ in range(5):
        await eng.on_order_book(mk_event(100, 20, 101, 5))
    snap = eng.snapshot("BTCUSDT")
    assert snap is not None
    # Son state'e göre bid up qty / ask down qty → ofi_1s pozitif
    assert snap["ofi_1s"] is not None
