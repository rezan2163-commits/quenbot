"""test_warmup_from_history.py — Aşama 1 historical warmup bootstrap."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(HERE, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from warmup_from_history import HistoricalWarmup, _percentile, _brier


class _StubDB:
    def __init__(self, rows):
        self._rows = rows
        self.updates = []
        self.pool = None  # disabled — impact updates will short-circuit

    async def fetch(self, query, *args):
        # emulate the real fetch interface: return list of dicts
        return list(self._rows)


class _StubRag:
    def __init__(self):
        self.added = []
        self._coll = None
        self._inmem = []

    # We do NOT define `_coll.add`; warmup falls back to _inmem.


def _mk_row(i, label="TP", symbol="BTCUSDT", mag=1.0, direction="up", p=0.8, conf=0.85, pnl=0.5):
    return {
        "id": i,
        "symbol": symbol,
        "event_ts": datetime(2026, 4, 1, 12, i % 50, tzinfo=timezone.utc),
        "move_magnitude_pct": mag,
        "move_direction": direction,
        "label": label,
        "horizon_minutes": 60,
        "confluence_score_t_minus_1h": conf,
        "fast_brain_p_t_minus_1h": p,
        "features_t_minus_1h": {
            "bocpd_consensus": 0.6, "hawkes_kernel_update": 0.4,
            "entropy_cooling": 0.3, "wasserstein_drift_zscore": -0.2,
            "ifi": 0.7, "confluence_score": conf,
        },
        "realized_pnl_pct": pnl,
        "decided": True, "decision_source": "shadow", "decision_path": "shadow",
    }


@pytest.mark.asyncio
async def test_warmup_dry_run_produces_report(tmp_path):
    rows = []
    for i in range(60):
        rows.append(_mk_row(i, label="TP"))
        rows.append(_mk_row(100 + i, label="FP", direction="down", p=0.7, pnl=-0.3))
        rows.append(_mk_row(200 + i, label="TN", direction="flat", p=0.3, pnl=0.0))
    db = _StubDB(rows)
    rag = _StubRag()
    w = HistoricalWarmup(
        days=30, symbols=["BTCUSDT"], rag_limit=10, dry_run=True,
        trust_path=str(tmp_path / "trust.json"),
        safety_baseline_path=str(tmp_path / "baseline.json"),
        checkpoint_path=str(tmp_path / "ckpt.json"),
        report_dir=str(tmp_path / "reports"),
        db=db, rag=rag,
    )
    summary = await w.run()
    assert summary["rows_processed"] == 180
    assert summary["dry_run"] is True
    # Dry run should NOT write files but report strings are returned
    assert "dry-run" in (summary.get("trust_file") or "")
    assert "dry-run" in (summary.get("baseline_file") or "")


@pytest.mark.asyncio
async def test_trust_scores_populated_per_channel(tmp_path):
    # TP rows have strong IFI; FP rows have weak IFI — posteriors diverge.
    rows = []
    for i in range(30):
        r = _mk_row(i, label="TP")
        r["features_t_minus_1h"] = {"ifi": 0.9, "confluence_score": 0.85, "entropy_cooling": 0.2}
        rows.append(r)
    for i in range(30):
        r = _mk_row(100 + i, label="FP", direction="down")
        r["features_t_minus_1h"] = {"ifi": 0.1, "confluence_score": 0.4, "entropy_cooling": 0.9}
        rows.append(r)
    db = _StubDB(rows)
    rag = _StubRag()
    w = HistoricalWarmup(
        days=7, symbols=["BTCUSDT"], rag_limit=0, dry_run=False,
        trust_path=str(tmp_path / "trust.json"),
        safety_baseline_path=str(tmp_path / "baseline.json"),
        checkpoint_path=str(tmp_path / "ckpt.json"),
        report_dir=str(tmp_path / "reports"),
        db=db, rag=rag,
    )
    await w.run()
    trust_file = Path(tmp_path / "trust.json")
    assert trust_file.exists()
    payload = json.loads(trust_file.read_text(encoding="utf-8"))
    channels = payload["channels"]
    # At least one channel must be non-uniform (alpha_TP != alpha_FP).
    non_uniform = any(
        abs(ch.get("alpha_TP", 1.0) - ch.get("alpha_FP", 1.0)) > 0.1
        for ch in channels.values()
    )
    assert non_uniform, "trust posteriors should be non-uniform after seeded labels"


@pytest.mark.asyncio
async def test_safety_baseline_populated(tmp_path):
    rows = [
        _mk_row(i, p=0.7 + (i % 3) * 0.05, direction="up" if i % 2 == 0 else "down")
        for i in range(200)
    ]
    db = _StubDB(rows)
    rag = _StubRag()
    w = HistoricalWarmup(
        days=7, symbols=["BTCUSDT"], rag_limit=0, dry_run=False,
        trust_path=str(tmp_path / "trust.json"),
        safety_baseline_path=str(tmp_path / "baseline.json"),
        checkpoint_path=str(tmp_path / "ckpt.json"),
        report_dir=str(tmp_path / "reports"),
        db=db, rag=rag,
    )
    await w.run()
    bl = Path(tmp_path / "baseline.json")
    assert bl.exists()
    payload = json.loads(bl.read_text(encoding="utf-8"))
    assert "brier" in payload and payload["brier"] is not None
    assert "hitrate" in payload
    assert payload["bootstrapped_from"] == "historical_warmup"


@pytest.mark.asyncio
async def test_rag_warm_cache_respects_limit(tmp_path):
    rows = [_mk_row(i, label="TP") for i in range(600)]
    rows += [_mk_row(1000 + i, label="FP", direction="down") for i in range(600)]
    db = _StubDB(rows)
    rag = _StubRag()
    w = HistoricalWarmup(
        days=7, symbols=["BTCUSDT"], rag_limit=50, dry_run=False,
        trust_path=str(tmp_path / "trust.json"),
        safety_baseline_path=str(tmp_path / "baseline.json"),
        checkpoint_path=str(tmp_path / "ckpt.json"),
        report_dir=str(tmp_path / "reports"),
        db=db, rag=rag,
    )
    out = await w.run()
    assert out["rag_written"] <= 50
    # Fallback in-memory buffer holds the entries.
    assert len(rag._inmem) == out["rag_written"]


def test_percentile_helper():
    assert _percentile([], 50.0) is None
    assert _percentile([1.0, 2.0, 3.0, 4.0], 50.0) == pytest.approx(2.5)
    assert _percentile([0.1] * 10, 30.0) == pytest.approx(0.1)


def test_brier_helper():
    assert _brier(1.0, True) == 0.0
    assert _brier(0.0, False) == 0.0
    assert _brier(0.8, False) == pytest.approx(0.64)


@pytest.mark.asyncio
async def test_checkpoint_persists(tmp_path):
    rows = [_mk_row(i) for i in range(5)]
    db = _StubDB(rows)
    rag = _StubRag()
    w = HistoricalWarmup(
        days=1, symbols=["BTCUSDT"], rag_limit=0, dry_run=False,
        trust_path=str(tmp_path / "trust.json"),
        safety_baseline_path=str(tmp_path / "baseline.json"),
        checkpoint_path=str(tmp_path / "ckpt.json"),
        report_dir=str(tmp_path / "reports"),
        db=db, rag=rag,
    )
    await w.run()
    ckpt = json.loads((tmp_path / "ckpt.json").read_text(encoding="utf-8"))
    assert ckpt["processed_rows"] == 5
    assert ckpt["finished_at"] is not None
