"""test_backfill_directive_impact.py — Aşama 2 historical backfill."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List

import pytest

from scripts.backfill_directive_impact import (
    BackfillDB, DirectiveImpactBackfill, SOURCE_TAG, SYNTH_PREFIX,
    BACKFILL_TYPES, build_synthetic_row, compute_impact_score,
    pick_directive_type,
)


class _FakeDB(BackfillDB):
    def __init__(self, rows: List[Dict[str, Any]]):
        super().__init__(pool=None)
        self._rows = rows
        self.inserted: List[Dict[str, Any]] = []
        self._existing: set = set()

    async def fetch_counterfactuals(self, *, since_ts, symbols, limit):
        return [r for r in self._rows if r["symbol"] in symbols][:limit]

    async def directive_exists(self, directive_id):
        return directive_id in self._existing

    async def insert_synthetic(self, rows):
        for r in rows:
            if r["directive_id"] in self._existing:
                continue
            self._existing.add(r["directive_id"])
            self.inserted.append(r)
        return len(rows)


def _mk_row(i, label, pnl, sym="BTCUSDT"):
    return {
        "id": i,
        "symbol": sym,
        "event_ts": datetime(2026, 4, 1, 12, i % 60, tzinfo=timezone.utc),
        "label": label,
        "move_magnitude_pct": abs(pnl),
        "realized_pnl_pct": pnl,
        "historical_impact_simulation": None,
    }


def test_pick_directive_type_covers_all_labels():
    assert pick_directive_type(_mk_row(1, "TP", 0.05)) == "ADJUST_POSITION_SIZE_MULT"
    assert pick_directive_type(_mk_row(2, "TP", 0.01)) == "ADJUST_CONFIDENCE_THRESHOLD"
    assert pick_directive_type(_mk_row(3, "FP", 0.05)) == "PAUSE_SYMBOL"
    assert pick_directive_type(_mk_row(4, "FP", 0.01)) == "CHANGE_STRATEGY_WEIGHT"
    assert pick_directive_type(_mk_row(5, "FN", 0.01)) == "RESUME_SYMBOL"
    assert pick_directive_type(_mk_row(6, "TN", 0.01)) == "ADJUST_TP_SL_RATIO"


def test_compute_impact_score_bounds_and_signs():
    # TP with positive pnl -> positive impact
    v = compute_impact_score(_mk_row(1, "TP", 0.03))
    assert 0 < v <= 1
    # FP with positive pnl -> negative (label polarity)
    v = compute_impact_score(_mk_row(1, "FP", 0.03))
    assert -1 <= v < 0
    # Clipping at 5%
    v = compute_impact_score(_mk_row(1, "TP", 0.50))
    assert v == 1.0 or v > 0.99


def test_build_synthetic_row_tags_correctly():
    r = build_synthetic_row(_mk_row(42, "TP", 0.04), now_ts=1_700_000_000)
    assert r["directive_id"].startswith(SYNTH_PREFIX)
    assert r["synthetic"] is True
    assert r["source_tag"] == SOURCE_TAG
    assert r["action"] in BACKFILL_TYPES
    assert -1.0 <= r["impact_score"] <= 1.0


@pytest.mark.asyncio
async def test_backfill_inserts_synthetic_rows():
    rows = [_mk_row(i, "TP" if i % 2 else "FP", 0.02 if i % 2 else -0.02) for i in range(20)]
    fake = _FakeDB(rows)
    runner = DirectiveImpactBackfill(
        db=fake, days=90, symbols=["BTCUSDT"], limit=100, dry_run=False,
    )
    report = await runner.run()
    assert report.rows_fetched == 20
    assert report.rows_inserted == 20
    assert all(r["synthetic"] for r in fake.inserted)
    assert all(r["source_tag"] == SOURCE_TAG for r in fake.inserted)
    assert report.impact_std >= 0.0
    # All six types may or may not appear depending on labels, but at
    # least two distinct types should surface.
    assert len(set(r["action"] for r in fake.inserted)) >= 2


@pytest.mark.asyncio
async def test_backfill_dry_run_does_not_insert():
    rows = [_mk_row(i, "TP", 0.02) for i in range(5)]
    fake = _FakeDB(rows)
    runner = DirectiveImpactBackfill(
        db=fake, days=90, symbols=["BTCUSDT"], limit=100, dry_run=True,
    )
    report = await runner.run()
    assert report.dry_run is True
    assert fake.inserted == []
    # rows_inserted in dry-run reports what *would* be inserted.
    assert report.rows_inserted == 5


@pytest.mark.asyncio
async def test_backfill_is_idempotent_via_directive_exists():
    rows = [_mk_row(i, "TP", 0.02) for i in range(3)]
    fake = _FakeDB(rows)
    # Pre-mark one directive_id as already present.
    synthetic_id = build_synthetic_row(rows[1], now_ts=1_700_000_000)["directive_id"]
    fake._existing.add(synthetic_id)
    runner = DirectiveImpactBackfill(
        db=fake, days=90, symbols=["BTCUSDT"], limit=100, dry_run=False,
        clock=lambda: 1_700_000_000.0,
    )
    report = await runner.run()
    assert report.skipped_existing == 1
    assert report.rows_inserted == 2
