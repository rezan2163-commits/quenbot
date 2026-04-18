"""Tests for the signal reasoning bundle builder."""
from utils.reasoning import build_reasoning_bundle


def test_empty_metadata_returns_empty_triggers():
    bundle = build_reasoning_bundle(None)
    assert bundle["triggers"] == []
    assert bundle["confluence_score"] is None


def test_full_bundle_sorted_by_strength_desc():
    meta = {
        "confluence_score": 0.81,
        "brain_reasoning": "RSI oversold + MACD bullish cross",
        "similarity": 0.72,
        "hawkes_branching_ratio": 0.64,
        "bocpd_changepoint_prob": 0.58,
        "ifi_score": 0.73,
        "data_density": 0.85,
        "regime": "TRENDING_UP",
    }
    bundle = build_reasoning_bundle(meta)
    triggers = bundle["triggers"]
    assert len(triggers) == 5
    # sorted descending
    for a, b in zip(triggers, triggers[1:]):
        assert a["strength"] >= b["strength"]
    cats = {t["category"] for t in triggers}
    assert {"confluence", "indicator", "microstructure", "changepoint", "factor_graph"}.issubset(cats)
    assert bundle["regime"] == "TRENDING_UP"
    assert bundle["data_density"] == 0.85


def test_mamis_aligned_adds_microstructure_trigger():
    bundle = build_reasoning_bundle({
        "mamis_ensemble": {
            "aligned": True,
            "mamis_confidence": 0.7,
            "pattern_type": "iceberg_buy",
        },
    })
    microstruct = [t for t in bundle["triggers"] if t["category"] == "microstructure"]
    assert len(microstruct) == 1
    assert "iceberg_buy" in microstruct[0]["label"]


def test_similar_patterns_attached_when_count_positive():
    bundle = build_reasoning_bundle(
        {"confluence_score": 0.6},
        similar_patterns={"count": 14, "avg_realized_pct": 0.028, "win_rate": 0.71},
    )
    sp = bundle["similar_patterns"]
    assert sp is not None
    assert sp["count"] == 14
    assert sp["win_rate"] == 0.71


def test_missing_fields_still_builds_bundle():
    bundle = build_reasoning_bundle({"brain_reasoning": "bullish engulfing"})
    triggers = bundle["triggers"]
    assert any("bullish engulfing" in t["label"] for t in triggers)


def test_non_numeric_fields_ignored_silently():
    bundle = build_reasoning_bundle({
        "confluence_score": "nope",
        "hawkes_branching_ratio": None,
        "ifi_score": float("nan"),
    })
    assert bundle["triggers"] == []
