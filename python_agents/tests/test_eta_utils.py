"""Tests for ETA estimation helpers."""
import math

from utils.eta import build_eta_bundle, estimate_eta_seconds, refine_eta_with_oracle


def test_atr_rate_produces_finite_p50_p80():
    p50, p80 = estimate_eta_seconds(
        entry_price=100.0, target_price=102.0, atr_per_minute=0.1, directional_bias=0.5,
    )
    # Distance = 2, velocity = 0.05/min → 40 min → 2400 sec
    assert math.isclose(p50, 2400.0, abs_tol=1e-6)
    # p80 = p50 * 1.6
    assert math.isclose(p80, p50 * 1.6, abs_tol=1e-6)


def test_short_direction_distance_is_absolute():
    p50_up, _ = estimate_eta_seconds(100.0, 102.0, 0.1, 0.5)
    p50_down, _ = estimate_eta_seconds(100.0, 98.0, 0.1, 0.5)
    assert math.isclose(p50_up, p50_down, abs_tol=1e-6)


def test_invalid_inputs_return_inf():
    p50, p80 = estimate_eta_seconds(0, 100, 0.1, 0.5)
    assert math.isinf(p50) and math.isinf(p80)
    p50, p80 = estimate_eta_seconds(100, 100, 0, 0.5)
    assert math.isinf(p50)


def test_zero_distance_is_zero_seconds():
    p50, p80 = estimate_eta_seconds(100, 100, 0.1, 0.5)
    assert p50 == 0.0 and p80 == 0.0


def test_hawkes_compresses_eta():
    p50_base, p80_base = (3600.0, 7200.0)
    p50_new, p80_new = refine_eta_with_oracle(
        p50_base, p80_base, hawkes_branching_ratio=0.8,
    )
    # factor = 1 - 0.3 * 0.8 = 0.76
    assert math.isclose(p50_new, p50_base * 0.76, abs_tol=1e-6)
    assert math.isclose(p80_new, p80_base * 0.76, abs_tol=1e-6)


def test_wasserstein_widens_p80_when_z_exceeds_2():
    p50, p80 = refine_eta_with_oracle(
        3600.0, 7200.0, wasserstein_drift_zscore=-2.5,
    )
    assert math.isclose(p50, 3600.0, abs_tol=1e-6)  # unchanged
    assert math.isclose(p80, 7200.0 * 1.5, abs_tol=1e-6)


def test_wasserstein_below_threshold_noop():
    p50, p80 = refine_eta_with_oracle(3600.0, 7200.0, wasserstein_drift_zscore=1.2)
    assert p50 == 3600.0 and p80 == 7200.0


def test_build_eta_bundle_prefers_historical_similar_patterns():
    bundle = build_eta_bundle(
        entry_price=100.0,
        target_price=102.0,
        atr_per_minute=0.1,
        similar_patterns={"count": 15, "avg_time_to_target_seconds": 1200},
    )
    assert bundle is not None
    assert bundle["basis"] == "historical_similar_patterns"
    assert bundle["p50_seconds"] == 1200.0


def test_build_eta_bundle_falls_back_to_atr_rate():
    bundle = build_eta_bundle(
        entry_price=100.0,
        target_price=102.0,
        atr_per_minute=0.1,
        similar_patterns={"count": 1},  # too few samples
    )
    assert bundle is not None
    assert bundle["basis"] == "atr_rate"
    assert bundle["p50_seconds"] > 0


def test_build_eta_bundle_returns_none_without_inputs():
    bundle = build_eta_bundle(
        entry_price=100.0, target_price=102.0, atr_per_minute=None,
    )
    assert bundle is None


def test_build_eta_bundle_applies_oracle_refinement():
    bundle = build_eta_bundle(
        entry_price=100.0,
        target_price=102.0,
        atr_per_minute=0.1,
        oracle_state={"hawkes_branching_ratio": 0.5, "wasserstein_drift_zscore": 3.0},
    )
    assert bundle is not None
    # Base p50 = 2400; factor = 1 - 0.3*0.5 = 0.85 → 2040
    assert math.isclose(bundle["p50_seconds"], 2040.0, abs_tol=1e-4)
