"""Aşama 3 — Tests for weekly_strategic_review."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.weekly_strategic_review import (
    WeeklyStrategicReview,
    WeeklyDataCollector,
    WeeklyData,
    render_markdown,
    _iso_week_label,
)


def test_dry_run_produces_no_file_but_returns_path(tmp_path: Path):
    runner = WeeklyStrategicReview(report_dir=tmp_path / "reports")
    rep = runner.generate(week_label="2026-16", dry_run=True)
    assert rep.dry_run is True
    assert rep.bytes_written == 0
    assert rep.week_label == "2026-16"
    assert rep.output_path.endswith("weekly_strategic_2026-16.md")
    assert not Path(rep.output_path).exists()


def test_live_run_writes_valid_markdown_with_empty_inputs(tmp_path: Path):
    runner = WeeklyStrategicReview(report_dir=tmp_path)
    rep = runner.generate(week_label="2026-16", dry_run=False)
    out = Path(rep.output_path)
    assert out.exists()
    md = out.read_text(encoding="utf-8")
    # Required sections.
    for header in [
        "# QuenBot Haftalık Stratejik İnceleme",
        "## 🎯 Bu Haftanın Performansı",
        "## 📈 Strateji Evrimi",
        "## ⚠️ Anormallikler ve İkazlar",
        "## 🧠 Qwen'in Öğrenme Trajektorisi",
        "## 📋 Operator Aksiyon Gerektiren",
        "## ✍️ Operator Onayı",
    ]:
        assert header in md, f"missing header: {header}"
    assert "weekly_ack_2026-16.json" in md


def test_collector_uses_brain_authority(tmp_path: Path):
    class FakeBrain:
        _directive_log = [object()] * 7
        def authority_override_pct_1h(self) -> float:
            return 0.42

    coll = WeeklyDataCollector(brain=FakeBrain())
    started = datetime(2026, 4, 13, tzinfo=timezone.utc)
    finished = datetime(2026, 4, 20, tzinfo=timezone.utc)
    data = coll.collect(week_label="2026-16", started=started, finished=finished)
    assert data.directives_total == 7
    assert data.qwen_authority_pct == 0.42


def test_collector_ranks_top_and_least_trusted_from_impact_tracker(tmp_path: Path):
    class FakeTracker:
        def rolling_mean_impact(self, hours, synthetic=False):
            return 0.123
        def aggregate_by_type(self):
            return {
                "ADJUST_CONFIDENCE_THRESHOLD": {"live_count": 12, "live_mean": 0.30, "synthetic_count": 0, "synthetic_mean": 0.0},
                "PAUSE_SYMBOL": {"live_count": 5, "live_mean": -0.40, "synthetic_count": 0, "synthetic_mean": 0.0},
                "RESUME_SYMBOL": {"live_count": 8, "live_mean": 0.05, "synthetic_count": 0, "synthetic_mean": 0.0},
                "CHANGE_STRATEGY_WEIGHT": {"live_count": 0, "live_mean": None, "synthetic_count": 0, "synthetic_mean": 0.0},
            }

    coll = WeeklyDataCollector(impact_tracker=FakeTracker())
    started = datetime(2026, 4, 13, tzinfo=timezone.utc)
    finished = datetime(2026, 4, 20, tzinfo=timezone.utc)
    data = coll.collect(week_label="2026-16", started=started, finished=finished)
    assert data.impact_mean == 0.123
    types_top = [r["directive_type"] for r in data.top_trusted_channels]
    types_bot = [r["directive_type"] for r in data.least_trusted_channels]
    # CHANGE_STRATEGY_WEIGHT excluded (live_count=0).
    assert "ADJUST_CONFIDENCE_THRESHOLD" in types_top
    assert "PAUSE_SYMBOL" in types_bot


def test_render_markdown_handles_partial_data():
    data = WeeklyData(
        week_label="2026-16",
        started_at="2026-04-13T00:00:00+00:00",
        finished_at="2026-04-20T00:00:00+00:00",
        directives_total=42,
        impact_mean=0.087,
        qwen_authority_pct=0.21,
    )
    md = render_markdown(data)
    assert "**42**" in md
    assert "0.087" in md
    assert "%21.0" in md


def test_iso_week_label_format():
    label = _iso_week_label(datetime(2026, 4, 20, tzinfo=timezone.utc))
    assert label == "2026-17"
