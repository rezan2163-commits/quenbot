"""OnlineLearningEvaluator tests."""
from __future__ import annotations

import json
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def _mk_log(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def test_ingest_and_score_with_maturity(tmp_path):
    from online_learning import OnlineLearningEvaluator, _reset_online_learning_for_tests
    _reset_online_learning_for_tests()

    log = tmp_path / "shadow.jsonl"
    now = time.time()
    rows = [
        {"symbol": "BTCUSDT", "ts": now - 3700,
         "fast_direction": "up", "fast_probability": 0.8,
         "gemma_action": "BUY", "agreed": True, "shadow": True,
         "chosen_by": "gemma", "action": "BUY"},
        {"symbol": "BTCUSDT", "ts": now - 3600,
         "fast_direction": "down", "fast_probability": 0.2,
         "gemma_action": "HOLD", "agreed": False, "shadow": True,
         "chosen_by": "gemma", "action": "HOLD"},
    ]
    _mk_log(str(log), rows)

    prices = {"BTCUSDT": [100.0, 102.0]}  # decision-time, horizon-time
    counter = {"i": 0}
    def lookup(sym):
        i = counter["i"]
        counter["i"] += 1
        arr = prices.get(sym, [])
        return arr[min(i, len(arr) - 1)] if arr else None

    ev = OnlineLearningEvaluator(
        log_path=str(log), horizon_min=60, interval_min=1,
        min_samples=1, state_path=str(tmp_path / "state.json"),
        price_lookup=lookup,
    )
    # step 1: ingest — price_at_decision captured
    added = ev._ingest_new_rows()
    assert added == 2
    # step 2: score matured — price_at_horizon captured
    scored = ev._score_matured_rows()
    assert scored == 2
    m = ev.rolling_metrics("BTCUSDT")
    assert m["samples"] == 2
    # BTCUSDT went up 100→102 → fast_brain "up" correct, "down" wrong
    assert m["fast_brain"]["n"] == 2
    # calibration bins should have entries at bin 8 (0.80) and bin 2 (0.20)
    bins = m["calibration_bins"]
    assert bins[8]["count"] == 1 and bins[8]["p_realized_up"] == 1.0
    # row 2: px0=102, px1=102 → ret=0 → realized_up=False → p_real=0
    assert bins[2]["count"] == 1 and bins[2]["p_realized_up"] == 0.0
    assert m["ece"] is not None


def test_pending_not_scored_before_horizon(tmp_path):
    from online_learning import OnlineLearningEvaluator
    log = tmp_path / "shadow.jsonl"
    now = time.time()
    _mk_log(str(log), [{
        "symbol": "ETHUSDT", "ts": now - 10,  # very fresh
        "fast_direction": "up", "fast_probability": 0.7,
        "gemma_action": "BUY", "agreed": True,
    }])
    ev = OnlineLearningEvaluator(
        log_path=str(log), horizon_min=60,
        state_path=str(tmp_path / "s.json"),
        price_lookup=lambda s: 100.0,
    )
    ev._ingest_new_rows()
    assert ev._score_matured_rows() == 0
    assert len(ev._pending) == 1


def test_log_rotation_resets_offset(tmp_path):
    from online_learning import OnlineLearningEvaluator
    log = tmp_path / "shadow.jsonl"
    _mk_log(str(log), [{"symbol": "X", "ts": 1, "fast_direction": "up",
                        "fast_probability": 0.6, "gemma_action": "BUY",
                        "agreed": True}])
    ev = OnlineLearningEvaluator(log_path=str(log),
                                 state_path=str(tmp_path / "s.json"),
                                 price_lookup=lambda s: 100.0)
    ev._ingest_new_rows()
    pre_offset = ev._last_offset
    assert pre_offset > 0
    # shrink log (simulate rotation)
    _mk_log(str(log), [])
    ev._ingest_new_rows()
    assert ev._last_offset == 0


def test_metrics_shapes(tmp_path):
    from online_learning import OnlineLearningEvaluator
    ev = OnlineLearningEvaluator(log_path=str(tmp_path / "x.jsonl"),
                                 state_path=str(tmp_path / "s.json"),
                                 price_lookup=lambda s: 100.0)
    m = ev.metrics()
    assert "online_learning_scored_total" in m
    import asyncio
    h = asyncio.new_event_loop().run_until_complete(ev.health_check())
    assert h["healthy"] is True
