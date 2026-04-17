"""FastBrain engine tests — model/lightgbm yoksa graceful degradation."""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def test_engine_dormant_without_model(tmp_path):
    from fast_brain import FastBrainEngine, _reset_fast_brain_engine_for_tests
    _reset_fast_brain_engine_for_tests()
    eng = FastBrainEngine(
        model_path=str(tmp_path / "nope.lgb"),
        calibration_path=str(tmp_path / "nope.calib.json"),
        min_features=1,
    )
    assert eng.enabled is False
    assert eng.predict("BTCUSDT") is None


def test_predict_without_model_returns_none(tmp_path):
    from fast_brain import FastBrainEngine
    eng = FastBrainEngine(model_path=str(tmp_path / "x.lgb"), min_features=1)
    assert eng.predict("ETHUSDT", features={"ofi_hurst_2h": 0.3}) is None


def test_sigmoid_and_calibration_platt():
    from fast_brain import _Calibration, _sigmoid
    cal = _Calibration(method="platt", a=2.0, b=0.0)
    # σ(2*0.5) = σ(1)
    assert abs(cal.apply(0.5) - _sigmoid(1.0)) < 1e-9


def test_calibration_isotonic_interp():
    from fast_brain import _Calibration
    cal = _Calibration(method="isotonic",
                       isotonic_x=[0.0, 0.5, 1.0],
                       isotonic_y=[0.1, 0.5, 0.9])
    assert abs(cal.apply(0.25) - 0.3) < 1e-6
    assert cal.apply(-1.0) == 0.1
    assert cal.apply(2.0) == 0.9


def test_calibration_none_clamps_probability():
    from fast_brain import _Calibration
    cal = _Calibration(method="none")
    # input zaten olasılık gibi → aynen döner
    assert cal.apply(0.7) == 0.7
    # raw score (>1) → sigmoid
    v = cal.apply(5.0)
    assert 0.9 < v < 1.0


def test_feature_vector_missing_report(tmp_path):
    from fast_brain import FastBrainEngine
    eng = FastBrainEngine(model_path=str(tmp_path / "x.lgb"), min_features=1)
    feats, missing = eng.collect_features("BTCUSDT")
    # Singleton'lar bootstrap edilmediği için hepsi eksik olmalı
    assert isinstance(feats, dict)
    assert set(missing).issubset(set(eng.feature_order))
    assert len(missing) <= len(eng.feature_order)


def test_direction_from_thresholds_with_manual_booster(tmp_path, monkeypatch):
    """Stub booster ile predict path'ini doğrula."""
    from fast_brain import FastBrainEngine, FastBrainPrediction

    class _StubBooster:
        best_iteration = 1
        def num_feature(self): return 12
        def predict(self, vec, num_iteration=None): return [0.9]

    eng = FastBrainEngine(model_path=str(tmp_path / "x.lgb"), min_features=1)
    eng._booster = _StubBooster()
    eng._enabled = True
    pred = eng.predict("BTCUSDT", features={"ofi_hurst_2h": 0.1, "confluence_score": 0.5,
                                             "mh_coherence": 0.4, "vpin_zscore": 0.2})
    assert isinstance(pred, FastBrainPrediction)
    assert pred.direction == "up"
    assert pred.probability > 0.65
    assert pred.features_used == 4
    assert pred.confidence > 0.3
