"""Aşama 3 — qwen_self_audit tests."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from scripts.qwen_self_audit import (
    QwenSelfAuditor, DirectiveHistorySource, AuditVerdict,
)


def _make_dir(row_id: int, **overrides):
    base = {
        "directive_id": f"d{row_id}",
        "symbol": "BTCUSDT",
        "action": "ADJUST_CONFIDENCE_THRESHOLD",
        "severity": "medium",
        "rationale": f"sample {row_id}",
        "ts": 1700000000 + row_id,
    }
    base.update(overrides)
    return base


class _FakeBridge:
    """Returns a deterministic verdict pattern: Hayır for ids divisible by 3,
    Kısmi for divisible by 5, else Evet."""
    def __init__(self):
        self.calls = 0
    async def chat_respond(self, *, user_message: str, system_context: dict) -> str:
        self.calls += 1
        # Read the directive id out of the prompt.
        idx = self.calls
        if idx % 3 == 0:
            return "HAYIR — eski karar artık zayıf görünüyor"
        if idx % 5 == 0:
            return "KISMİ — kısmen geçerli"
        return "EVET — hâlâ savunulabilir"


@pytest.mark.asyncio
async def test_self_audit_with_sparse_history_produces_valid_markdown(tmp_path: Path):
    aud = QwenSelfAuditor(
        history=DirectiveHistorySource(iterable=[]),
        llm_bridge=None,
        report_dir=tmp_path,
        latest_path=tmp_path / "latest.json",
        sample_size=10,
    )
    rep = await aud.run(month_label="2026-04")
    assert rep.sample_size == 0
    assert rep.disagreement_rate == 0.0
    assert rep.alert_emitted is False
    md = Path(rep.output_path).read_text(encoding="utf-8")
    assert "Sample boş" in md or "Aylık Öz-Denetim" in md
    sidecar = json.loads(Path(rep.json_path).read_text(encoding="utf-8"))
    assert sidecar["sample_size"] == 0


@pytest.mark.asyncio
async def test_self_audit_aggregates_disagreement_above_threshold(tmp_path: Path):
    rows = [_make_dir(i) for i in range(1, 11)]
    bridge = _FakeBridge()
    aud = QwenSelfAuditor(
        history=DirectiveHistorySource(iterable=rows),
        llm_bridge=bridge,
        report_dir=tmp_path,
        latest_path=tmp_path / "latest.json",
        sample_size=10,
        threshold=0.10,  # very low to force the alert
    )
    rep = await aud.run(month_label="2026-04")
    assert rep.sample_size == 10
    # Pattern: idx 3,6,9 -> no (3); idx 5,10 -> partial (2); rest -> yes (5).
    # Disagreement = (3+2)/10 = 0.5 > 0.10.
    assert rep.disagreement_rate >= 0.4
    assert rep.alert_emitted is True


@pytest.mark.asyncio
async def test_self_audit_below_threshold_does_not_alert(tmp_path: Path):
    rows = [_make_dir(i) for i in range(1, 11)]

    class _AlwaysYes:
        async def chat_respond(self, *, user_message, system_context):
            return "EVET — kesinlikle"

    aud = QwenSelfAuditor(
        history=DirectiveHistorySource(iterable=rows),
        llm_bridge=_AlwaysYes(),
        report_dir=tmp_path,
        latest_path=tmp_path / "latest.json",
        sample_size=10,
        threshold=0.40,
    )
    rep = await aud.run(month_label="2026-04")
    assert rep.disagreement_rate == 0.0
    assert rep.alert_emitted is False


def test_parse_verdict_handles_turkish_variants():
    a = QwenSelfAuditor._parse_verdict("d1", "EVET — hâlâ doğru")
    assert a.verdict == "yes"
    b = QwenSelfAuditor._parse_verdict("d2", "HAYIR — yanlış")
    assert b.verdict == "no"
    c = QwenSelfAuditor._parse_verdict("d3", "KISMİ — kısmen")
    assert c.verdict == "partial"
    d = QwenSelfAuditor._parse_verdict("d4", "Belki olabilir")
    assert d.verdict == "unknown"


@pytest.mark.asyncio
async def test_history_source_falls_back_to_brain_log(tmp_path: Path):
    class _D:
        def __init__(self, did, ts):
            self.directive_id = did
            self.symbol = "BTCUSDT"
            self.action = "PAUSE_SYMBOL"
            self.severity = "medium"
            self.ts = ts
            self.rationale = "x"
    class _Brain:
        _directive_log = [_D(f"d{i}", 1700000000 + i) for i in range(5)]

    src = DirectiveHistorySource(brain=_Brain())
    rows = src.sample(n=3, lookback_days=36500)  # huge lookback so nothing filtered
    assert len(rows) == 3
    assert all("directive_id" in r for r in rows)
