"""test_directive_gatekeeper.py — Aşama 1 gatekeeper filters."""
from __future__ import annotations

import os
import time

import pytest

from directive_gatekeeper import (
    DirectiveGatekeeper, HARD_BLOCKLIST, _reset_for_tests,
)


@pytest.fixture(autouse=True)
def _reset():
    _reset_for_tests()
    yield
    _reset_for_tests()


def _d(action="ADJUST_CONFIDENCE_THRESHOLD", confidence=0.9, symbol="BTCUSDT"):
    return {"action": action, "confidence": confidence, "symbol": symbol,
            "severity": "medium", "rationale": "test", "shadow": True}


def test_disabled_accepts_everything_except_hard_blocklist(tmp_path):
    gk = DirectiveGatekeeper(
        enabled=False, confidence_min=0.8, max_per_hour=1,
        allowlist=["ADJUST_CONFIDENCE_THRESHOLD"],
        rejected_log_path=str(tmp_path / "rej.jsonl"),
    )
    d = gk.evaluate(_d(confidence=0.1, action="PAUSE_SYMBOL"))
    assert d.accepted is True
    assert d.filter_name == "disabled"
    # hard blocklist always rejected
    d2 = gk.evaluate(_d(action="FORCE_TRADE"))
    assert d2.accepted is False
    assert d2.filter_name == "blocklist"


def test_confidence_filter(tmp_path):
    gk = DirectiveGatekeeper(
        enabled=True, confidence_min=0.8, max_per_hour=5,
        allowlist=["ADJUST_CONFIDENCE_THRESHOLD"],
        rejected_log_path=str(tmp_path / "rej.jsonl"),
    )
    low = gk.evaluate(_d(confidence=0.5))
    assert low.accepted is False and low.filter_name == "confidence"
    assert "0.50" in low.reason
    high = gk.evaluate(_d(confidence=0.85))
    assert high.accepted is True


def test_allowlist_filter(tmp_path):
    gk = DirectiveGatekeeper(
        enabled=True, confidence_min=0.5, max_per_hour=5,
        allowlist=["PAUSE_SYMBOL"],
        rejected_log_path=str(tmp_path / "rej.jsonl"),
    )
    d = gk.evaluate(_d(action="ADJUST_CONFIDENCE_THRESHOLD", confidence=0.9))
    assert d.accepted is False and d.filter_name == "allowlist"


def test_hard_blocklist_always_blocked(tmp_path):
    gk = DirectiveGatekeeper(
        enabled=True, confidence_min=0.1, max_per_hour=100,
        allowlist=list(HARD_BLOCKLIST),  # even if allowlist contains them...
        rejected_log_path=str(tmp_path / "rej.jsonl"),
    )
    for action in HARD_BLOCKLIST:
        d = gk.evaluate(_d(action=action, confidence=0.99))
        assert d.accepted is False
        assert d.filter_name == "blocklist"


def test_rate_limit_token_bucket(tmp_path):
    gk = DirectiveGatekeeper(
        enabled=True, confidence_min=0.5, max_per_hour=2,
        allowlist=["ADJUST_CONFIDENCE_THRESHOLD"],
        rejected_log_path=str(tmp_path / "rej.jsonl"),
    )
    now = time.time()
    assert gk.evaluate(_d(), now=now).accepted
    assert gk.evaluate(_d(), now=now + 1).accepted
    over = gk.evaluate(_d(), now=now + 2)
    assert over.accepted is False
    assert over.filter_name == "rate_limit"
    # Sliding — after an hour the window resets.
    later = gk.evaluate(_d(), now=now + 3700)
    assert later.accepted is True


def test_rejection_log_written(tmp_path):
    log = tmp_path / "rej.jsonl"
    gk = DirectiveGatekeeper(
        enabled=True, confidence_min=0.9, max_per_hour=5,
        allowlist=["ADJUST_CONFIDENCE_THRESHOLD"],
        rejected_log_path=str(log),
    )
    gk.evaluate(_d(confidence=0.1))
    assert log.exists()
    content = log.read_text(encoding="utf-8")
    assert '"accepted": false' in content
    assert '"confidence"' in content


def test_stats_histogram(tmp_path):
    gk = DirectiveGatekeeper(
        enabled=True, confidence_min=0.9, max_per_hour=1,
        allowlist=["ADJUST_CONFIDENCE_THRESHOLD"],
        rejected_log_path=str(tmp_path / "rej.jsonl"),
    )
    # 1 accepted, 2 rejected (confidence), 1 rejected (rate_limit)
    gk.evaluate(_d(confidence=0.95))
    gk.evaluate(_d(confidence=0.1))
    gk.evaluate(_d(confidence=0.1))
    gk.evaluate(_d(confidence=0.95))  # rate-limited
    st = gk.stats()
    assert st["accepted_total"] == 1
    assert st["rejected_total"] == 3
    assert st["rejected_by_confidence"] == 2
    assert st["rejected_by_rate_limit"] == 1
    hist = st["rejection_histogram_1h"]
    assert hist.get("confidence") == 2
    assert hist.get("rate_limit") == 1


def test_rejection_rate_computation(tmp_path):
    gk = DirectiveGatekeeper(
        enabled=True, confidence_min=0.9, max_per_hour=50,
        allowlist=["ADJUST_CONFIDENCE_THRESHOLD"],
        rejected_log_path=str(tmp_path / "rej.jsonl"),
    )
    gk.evaluate(_d(confidence=0.95))  # accepted
    gk.evaluate(_d(confidence=0.95))  # accepted
    gk.evaluate(_d(confidence=0.1))   # rejected
    gk.evaluate(_d(confidence=0.1))   # rejected
    rate = gk.rejection_rate(window_sec=60)
    assert 0.4 <= rate <= 0.6


def test_malformed_directive_safe(tmp_path):
    gk = DirectiveGatekeeper(
        enabled=True, confidence_min=0.5, max_per_hour=5,
        allowlist=["ADJUST_CONFIDENCE_THRESHOLD"],
        rejected_log_path=str(tmp_path / "rej.jsonl"),
    )
    # Missing 'action' — should be rejected (empty action not in allowlist)
    d = gk.evaluate({"confidence": 0.9})
    assert d.accepted is False
