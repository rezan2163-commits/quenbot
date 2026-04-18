"""test_cascade_rollback.py — Aşama 2 authority cascade trigger."""
from __future__ import annotations

import asyncio

import pytest

from auto_rollback_monitor import (
    AutoRollbackMonitor, TRIGGER_CASCADE, _reset_for_tests,
)


@pytest.fixture(autouse=True)
def _reset():
    _reset_for_tests()
    yield
    _reset_for_tests()


class _Brain:
    def __init__(self, pct: float):
        self._pct = pct
        self.shadow = False

    def authority_override_pct_1h(self) -> float:
        return self._pct


def _mk_monitor(tmp_path, **kw):
    defaults = dict(
        enabled=True,
        rejection_rate_threshold=0.99, rejection_window_min=30,
        accuracy_threshold=0.0, accuracy_window=10_000,
        meta_conf_min=0.0, meta_conf_streak=10_000,
        unhealthy_grace_sec=10_000,
        force_sentinel_path=str(tmp_path / "sent"),
        shadow_forced_path=str(tmp_path / "forced.json"),
        forensic_dir=str(tmp_path / "fx"),
        check_interval_sec=1,
        cascade_detection=True, max_agent_override_pct=0.30,
    )
    defaults.update(kw)
    return AutoRollbackMonitor(**defaults)


def test_cascade_fires_when_authority_exceeds_threshold(tmp_path):
    m = _mk_monitor(tmp_path, oracle_brain=_Brain(0.45))
    st = asyncio.run(m.evaluate_once())
    assert st is not None
    assert st.trigger == TRIGGER_CASCADE
    assert st.rolled_back is True


def test_cascade_does_not_fire_under_threshold(tmp_path):
    m = _mk_monitor(tmp_path, oracle_brain=_Brain(0.20))
    st = asyncio.run(m.evaluate_once())
    assert st is None


def test_cascade_disabled_by_flag(tmp_path):
    m = _mk_monitor(tmp_path, oracle_brain=_Brain(0.99), cascade_detection=False)
    st = asyncio.run(m.evaluate_once())
    assert st is None
