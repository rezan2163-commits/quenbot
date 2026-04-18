"""test_warmup_isolation.py — warmup entries must not leak into live stats.

The guarantees we validate here:
  1. Every row written by the warmup script carries a warmup marker (tag
     on RAG entries, `historical_impact_simulation` jsonb on DB rows, and
     — when the script writes new rows — `warmup_generated=TRUE`).
  2. RAG metadata always carries `source_tag=historical_warmup` so that
     live consumers can filter out warmup entries if they choose.
  3. The script's fetch query only processes rows NOT already flagged as
     warmup, so a re-run is a no-op once the bootstrap has completed.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(HERE, "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

from warmup_from_history import HistoricalWarmup


class _StubDB:
    def __init__(self, rows):
        self._rows = rows
        self.queries = []
        self.pool = None

    async def fetch(self, query, *args):
        self.queries.append((query, args))
        return list(self._rows)


class _StubRag:
    def __init__(self):
        self._inmem = []
        self._coll = None


def _row(i, label="TP"):
    return {
        "id": i, "symbol": "BTCUSDT",
        "event_ts": datetime(2026, 4, 1, 12, i % 60, tzinfo=timezone.utc),
        "move_magnitude_pct": 1.0, "move_direction": "up",
        "label": label, "horizon_minutes": 60,
        "confluence_score_t_minus_1h": 0.8, "fast_brain_p_t_minus_1h": 0.7,
        "features_t_minus_1h": {"ifi": 0.5, "confluence_score": 0.8},
        "realized_pnl_pct": 0.3,
        "decided": True, "decision_source": "shadow", "decision_path": "shadow",
    }


@pytest.mark.asyncio
async def test_rag_entries_carry_warmup_source_tag(tmp_path):
    rag = _StubRag()
    rows = [_row(i, "TP") for i in range(3)] + [_row(100 + i, "FP") for i in range(3)]
    w = HistoricalWarmup(
        days=7, symbols=["BTCUSDT"], rag_limit=100, dry_run=False,
        trust_path=str(tmp_path / "trust.json"),
        safety_baseline_path=str(tmp_path / "baseline.json"),
        checkpoint_path=str(tmp_path / "ckpt.json"),
        report_dir=str(tmp_path / "reports"),
        db=_StubDB(rows), rag=rag,
    )
    await w.run()
    assert rag._inmem, "warmup should have populated rag in-memory buffer"
    for _id, _doc, meta in rag._inmem:
        assert meta.get("source_tag") == "historical_warmup"
        assert meta.get("warmup") is True
        assert str(_id).startswith("warmup-")


@pytest.mark.asyncio
async def test_fetch_query_filters_already_warmed_rows(tmp_path):
    db = _StubDB([])
    w = HistoricalWarmup(
        days=7, symbols=["BTCUSDT"], rag_limit=0, dry_run=True,
        trust_path=str(tmp_path / "trust.json"),
        safety_baseline_path=str(tmp_path / "baseline.json"),
        checkpoint_path=str(tmp_path / "ckpt.json"),
        report_dir=str(tmp_path / "reports"),
        db=db, rag=_StubRag(),
    )
    await w.run()
    assert db.queries, "warmup must issue a fetch query"
    query_text, _args = db.queries[0]
    # Must exclude rows already produced by warmup.
    assert "warmup_generated = FALSE" in query_text or "warmup_generated = False" in query_text
    # Must only touch counterfactual_observations — never oracle_directives.
    assert "oracle_directives" not in query_text
    assert "oracle_reasoning_traces" not in query_text


@pytest.mark.asyncio
async def test_impact_payload_tags_source(tmp_path):
    w = HistoricalWarmup(
        days=7, symbols=["BTCUSDT"], rag_limit=0, dry_run=True,
        trust_path=str(tmp_path / "trust.json"),
        safety_baseline_path=str(tmp_path / "baseline.json"),
        checkpoint_path=str(tmp_path / "ckpt.json"),
        report_dir=str(tmp_path / "reports"),
    )
    row = _row(1, "TP")
    payload = w._impact_payload(row)
    assert payload["source"] == "warmup_from_history"
    assert "generated_at" in payload
    assert payload["label"] == "TP"
