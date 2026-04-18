"""Safety Net unit tests — Phase 5 Finalization.

Tum testler senkron/fast. Async event loop gereksinimi `pytest.mark.asyncio`
kullanmadan manuel `asyncio.run` ile karsilanir (conftest sadelik icin).
"""
from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path

import pytest


def _fresh(tmp_path: Path, **overrides):
    from safety_net import SafetyNet, _reset_safety_net_for_tests
    _reset_safety_net_for_tests()
    sn = SafetyNet(
        event_bus=None,
        config=None,
        database=None,
        feature_store=None,
        baseline_path=str(tmp_path / "baseline.json"),
        trip_sentinel_path=str(tmp_path / "trip.json"),
        brier_tol=1.25,
        hitrate_tol=0.80,
        degradation_window_min=1,  # 1 dk — testlerde hizli trip
        drift_sigma=3.0,
        fs_failure_tol=0.05,
        bg_interval_sec=1,
        **overrides,
    )
    return sn


def test_trip_and_reset_sentinel(tmp_path: Path):
    sn = _fresh(tmp_path)
    assert sn.status()["tripped"] is False
    sn.trip("synthetic test")
    assert sn.status()["tripped"] is True
    sentinel = Path(sn.trip_sentinel_path)
    assert sentinel.exists()
    payload = json.loads(sentinel.read_text())
    assert payload["reason"] == "synthetic test"
    sn.reset("ops_user", "smoke")
    assert sn.status()["tripped"] is False
    assert not sentinel.exists()


def test_sentinel_rehydration_disables_fast_brain(tmp_path: Path):
    """Sentinel dosyasi varken bootta Config.FAST_BRAIN_ENABLED=False olmali."""
    from safety_net import SafetyNet, _reset_safety_net_for_tests

    class _FakeCfg:
        FAST_BRAIN_ENABLED = True

    sentinel = tmp_path / "trip.json"
    sentinel.write_text(json.dumps({
        "reason": "prior_trip",
        "trip_ts": 1.0,
    }))
    _reset_safety_net_for_tests()
    fake_cfg = _FakeCfg()
    sn = SafetyNet(
        event_bus=None,
        config=fake_cfg,
        trip_sentinel_path=str(sentinel),
        baseline_path=str(tmp_path / "baseline.json"),
    )
    assert sn.status()["tripped"] is True
    assert fake_cfg.FAST_BRAIN_ENABLED is False


def test_accuracy_degradation_triggers_trip(tmp_path: Path):
    """Baseline + degraded rolling samples + sustained window → trip."""
    from safety_net import _BrierSample
    import time as _t

    sn = _fresh(tmp_path)
    # seed baseline
    sn.baseline = {"brier": 0.25, "hitrate": 0.60}
    # fabricate 400 degraded samples: predicted 0.9 up but always down
    now = _t.time()
    for i in range(400):
        sn._brier_samples.append(_BrierSample(
            ts=now - i * 10,
            p=0.9,
            realized_up=False,
            hit=False,
        ))
    # manually backdate _degraded_since so window elapses
    sn._degraded_since = now - sn.degradation_window_sec - 10
    asyncio.run(sn._check_accuracy())
    assert sn.status()["tripped"] is True
    assert sn.status()["trip_reason"] == "accuracy_degraded"


def test_drift_detection_flag(tmp_path: Path):
    from safety_net import _ConfluenceSample
    import time as _t

    sn = _fresh(tmp_path)
    sn.baseline = {
        "brier": 0.25,
        "hitrate": 0.60,
        "confluence": {
            "per_symbol_mean": {f"SYM{i}": 0.3 for i in range(10)},
            "per_symbol_std": {f"SYM{i}": 0.05 for i in range(10)},
        },
    }
    now = _t.time()
    for i in range(10):
        # feed 50 samples at mean+10σ → clearly drifted (>3σ)
        for _ in range(50):
            sn._confluence_window[f"SYM{i}"].append(
                _ConfluenceSample(ts=now, symbol=f"SYM{i}", score=0.8)
            )
    # backdate drift_start so sustained window elapses
    sn._drift_start_ts = now - 31 * 60
    asyncio.run(sn._check_drift())
    status = sn.status()
    assert status["tripped"] is True
    assert status["trip_reason"] in {"confluence_drift"}


def test_rolling_brier_and_hitrate(tmp_path: Path):
    from safety_net import _BrierSample
    sn = _fresh(tmp_path)
    import time as _t
    now = _t.time()
    # 4 predictions; p=0.9 correct, p=0.1 correct
    sn._brier_samples.append(_BrierSample(ts=now, p=0.9, realized_up=True, hit=True))
    sn._brier_samples.append(_BrierSample(ts=now, p=0.1, realized_up=False, hit=True))
    sn._brier_samples.append(_BrierSample(ts=now, p=0.7, realized_up=False, hit=False))
    sn._brier_samples.append(_BrierSample(ts=now, p=0.3, realized_up=True, hit=False))
    brier = sn._rolling_brier()
    hit = sn._rolling_hitrate()
    assert brier is not None and 0.0 <= brier <= 1.0
    assert hit == 0.5


def test_get_safety_net_singleton(tmp_path: Path):
    from safety_net import get_safety_net, _reset_safety_net_for_tests
    _reset_safety_net_for_tests()
    a = get_safety_net(
        baseline_path=str(tmp_path / "b.json"),
        trip_sentinel_path=str(tmp_path / "t.json"),
    )
    b = get_safety_net()
    assert a is b
    _reset_safety_net_for_tests()
