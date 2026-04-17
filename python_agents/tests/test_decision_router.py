"""Decision Router tests — shadow never overrides, active mode override rules."""
from __future__ import annotations

import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _mk_router(tmp_path, shadow=True, max_rows=100000):
    from decision_router import DecisionRouter, _reset_decision_router_for_tests
    _reset_decision_router_for_tests()
    return DecisionRouter(
        shadow=shadow,
        log_path=str(tmp_path / "router.jsonl"),
        max_log_rows=max_rows,
        t_high=0.65,
        t_low=0.45,
    )


def test_shadow_never_overrides_gemma(tmp_path):
    r = _mk_router(tmp_path, shadow=True)
    gemma = {"action": "HOLD", "confidence": 0.6}
    fast = {"direction": "up", "probability": 0.9, "confidence": 0.8}
    d = r.route("BTCUSDT", gemma, fast)
    assert d.chosen_by == "gemma"
    assert d.action == "HOLD"
    assert d.shadow is True
    assert d.agreed is False


def test_fast_brain_unavailable_keeps_gemma(tmp_path):
    r = _mk_router(tmp_path, shadow=False)
    gemma = {"action": "BUY", "confidence": 0.7}
    d = r.route("BTCUSDT", gemma, None)
    assert d.chosen_by == "gemma"
    assert d.action == "BUY"


def test_active_mode_agreement_high_prob_overrides(tmp_path):
    r = _mk_router(tmp_path, shadow=False)
    gemma = {"action": "BUY", "confidence": 0.55}
    fast = {"direction": "up", "probability": 0.82, "confidence": 0.64}
    d = r.route("ETHUSDT", gemma, fast)
    assert d.agreed is True
    assert d.chosen_by == "fast_brain"
    assert d.confidence >= 0.64


def test_active_mode_disagreement_keeps_gemma(tmp_path):
    r = _mk_router(tmp_path, shadow=False)
    gemma = {"action": "BUY", "confidence": 0.6}
    fast = {"direction": "down", "probability": 0.2, "confidence": 0.6}
    d = r.route("ETHUSDT", gemma, fast)
    assert d.agreed is False
    assert d.chosen_by == "gemma"


def test_active_mode_neutral_fast_keeps_gemma(tmp_path):
    r = _mk_router(tmp_path, shadow=False)
    gemma = {"action": "BUY", "confidence": 0.6}
    fast = {"direction": "neutral", "probability": 0.55, "confidence": 0.1}
    d = r.route("ETHUSDT", gemma, fast)
    assert d.chosen_by == "gemma"
    assert d.agreed is False


def test_log_appended_jsonl(tmp_path):
    r = _mk_router(tmp_path, shadow=True)
    r.route("BTCUSDT", {"action": "HOLD", "confidence": 0.5},
            {"direction": "up", "probability": 0.7, "confidence": 0.4})
    log_path = tmp_path / "router.jsonl"
    assert log_path.exists()
    line = log_path.read_text(encoding="utf-8").strip().splitlines()[0]
    row = json.loads(line)
    assert row["symbol"] == "BTCUSDT"
    assert row["shadow"] is True
    assert row["chosen_by"] == "gemma"


def test_log_rotates_when_full(tmp_path):
    r = _mk_router(tmp_path, shadow=True, max_rows=3)
    for i in range(4):
        r.route(f"S{i}", {"action": "HOLD", "confidence": 0.5},
                {"direction": "up", "probability": 0.7, "confidence": 0.4})
    assert (tmp_path / "router.jsonl.1").exists()
    assert (tmp_path / "router.jsonl").exists()


def test_metrics_and_health(tmp_path):
    r = _mk_router(tmp_path, shadow=True)
    r.route("BTCUSDT", {"action": "BUY", "confidence": 0.6},
            {"direction": "up", "probability": 0.7, "confidence": 0.4})
    r.route("ETHUSDT", {"action": "BUY", "confidence": 0.6},
            {"direction": "down", "probability": 0.3, "confidence": 0.4})
    m = r.metrics()
    assert m["decision_router_routed_total"] == 2
    assert m["decision_router_agree_total"] == 1
    assert m["decision_router_disagree_total"] == 1

    import asyncio
    h = asyncio.new_event_loop().run_until_complete(r.health_check())
    assert h["healthy"] is True
    assert h["shadow"] is True
