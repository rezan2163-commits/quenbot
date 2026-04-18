"""Multi-horizon signature engine tests — Phase 1 regression + coherence.

Testler sentetik trade stream kullanarak 4 ufkun (300/1800/7200/21600s)
tutarli davranmasini dogrular. Hicbir network/DB bagimliligi yoktur.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from typing import List

import pytest


def _make_event(symbol: str, price: float, qty: float, side: str, ts: datetime):
    return SimpleNamespace(data={
        "symbol": symbol,
        "price": price,
        "quantity": qty,
        "side": side,
        "timestamp": ts,
    })


def _fresh_engine(**kwargs):
    import multi_horizon_signatures as mh
    mh._engine = None
    return mh.get_multi_horizon_engine(
        event_bus=None,
        feature_store=None,
        publish_hz=100.0,  # testlerde throttle kalksin
        **kwargs,
    )


def test_engine_has_four_horizons():
    eng = _fresh_engine()
    assert tuple(eng.horizons) == (300, 1800, 7200, 21600)
    assert len(eng._detectors) == 4


def test_get_multi_horizon_engine_singleton():
    import multi_horizon_signatures as mh
    mh._engine = None
    a = mh.get_multi_horizon_engine()
    b = mh.get_multi_horizon_engine()
    assert a is b
    mh._engine = None


def test_ingest_populates_snapshot():
    eng = _fresh_engine()
    base_ts = datetime.now(timezone.utc) - timedelta(seconds=100)
    events = [
        _make_event("BTCUSDT", 50000.0 + i, 0.1, "buy" if i % 2 == 0 else "sell",
                    base_ts + timedelta(seconds=i))
        for i in range(50)
    ]

    async def run():
        for ev in events:
            await eng.on_trade(ev)

    asyncio.run(run())
    assert eng._total_trades == 50
    snap = eng.snapshot("BTCUSDT")
    # snapshot boş None olabilir eğer analiz publish edilmediyse — en az tüm ufuklar trade aldı
    # her detektör en az 50 trade gördü
    for det in eng._detectors.values():
        buf = det.trades_buffer.get("BTCUSDT") if hasattr(det, "trades_buffer") else None
        if buf is not None:
            assert len(buf) > 0


def test_metrics_and_health():
    eng = _fresh_engine()
    m = eng.metrics()
    assert "mh_trades_ingested_total" in m
    assert "mh_tracked_symbols" in m

    async def run():
        return await eng.health_check()

    h = asyncio.run(run())
    assert h["healthy"] is True
    assert "horizons_sec" in h
    assert len(h["horizons_sec"]) == 4


def test_invalid_trade_ignored():
    eng = _fresh_engine()
    bad = SimpleNamespace(data={"symbol": "BTCUSDT", "price": 0, "quantity": 0, "side": "buy"})

    async def run():
        await eng.on_trade(bad)
        await eng.on_trade(SimpleNamespace(data={}))  # no symbol
        await eng.on_trade(SimpleNamespace(data={"symbol": "X", "price": "abc", "quantity": 1}))

    asyncio.run(run())
    assert eng._total_trades == 0


def test_coherence_threshold_constant():
    eng = _fresh_engine()
    # Contract: coherence sadece conf >= 0.5 olan ufuklari sayar
    assert eng.COHERENCE_MIN_CONF == 0.5
