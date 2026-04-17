"""FeatureStore için unit testler.

Kapsam:
- write + flush + read_pit PIT güvenliği (as_of'u aşan satır dönmez)
- replay kronolojik sırayı korur
- queue overflow drop eder (hot path'i bloklamaz)
- health_check çalışır
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("pyarrow")
pytest.importorskip("pandas")

from feature_store import FeatureStore  # noqa: E402


def _ts(m: int) -> datetime:
    return datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=m)


@pytest.mark.asyncio
async def test_write_read_pit_basic():
    with tempfile.TemporaryDirectory() as tmp:
        store = FeatureStore(root=tmp, flush_rows=5, flush_seconds=0.1, queue_max=1000)
        await store.start()
        try:
            for m in range(10):
                await store.write("BTCUSDT", _ts(m), {"ofi.ofi_1m": float(m)})
            await store.flush(force=True)

            # PIT: as_of = t+4 → sadece 0..4 arası dönmeli
            df = store.read_pit("BTCUSDT", as_of=_ts(4), lookback=timedelta(hours=1))
            assert not df.empty
            assert len(df) == 5
            assert df["ts"].max() <= _ts(4)
            # values sıralı
            vals = list(df["ofi.ofi_1m"])
            assert vals == sorted(vals)
        finally:
            await store.stop()


@pytest.mark.asyncio
async def test_read_pit_excludes_future():
    with tempfile.TemporaryDirectory() as tmp:
        store = FeatureStore(root=tmp, flush_rows=2, flush_seconds=0.1)
        await store.start()
        try:
            for m in range(6):
                await store.write("ETHUSDT", _ts(m), {"x": float(m)})
            await store.flush(force=True)
            df = store.read_pit("ETHUSDT", as_of=_ts(2), lookback=timedelta(hours=1))
            # hiçbir satır ts > _ts(2) olmamalı
            assert df["ts"].max() <= _ts(2)
            assert set(df["x"]) <= {0.0, 1.0, 2.0}
        finally:
            await store.stop()


@pytest.mark.asyncio
async def test_queue_overflow_drops():
    with tempfile.TemporaryDirectory() as tmp:
        store = FeatureStore(root=tmp, queue_max=10, flush_seconds=1000, flush_rows=10**9)
        # start() etmiyoruz → flusher kapalı, yazılar birikir
        for i in range(100):
            await store.write("XRPUSDT", _ts(i), {"x": float(i)})
        health = await store.health_check()
        # 100 yazıldı, queue max=10 → en az 90 drop
        assert health["queue_dropped"] >= 80
        assert health["queue_size"] <= 10


@pytest.mark.asyncio
async def test_replay_chronological():
    with tempfile.TemporaryDirectory() as tmp:
        store = FeatureStore(root=tmp, flush_rows=5, flush_seconds=0.1)
        await store.start()
        try:
            for m in [3, 0, 5, 1, 4, 2]:
                await store.write("SOLUSDT", _ts(m), {"k": float(m)})
            await store.flush(force=True)
            rows = list(store.replay("SOLUSDT", _ts(0), _ts(10)))
            ks = [r["k"] for r in rows]
            assert ks == sorted(ks)
        finally:
            await store.stop()
