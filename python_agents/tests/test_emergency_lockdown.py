"""Aşama 3 — emergency_lockdown tests."""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

import emergency_lockdown as el_mod
from emergency_lockdown import EmergencyLockdown, get_emergency_lockdown, is_engaged


@pytest.fixture(autouse=True)
def _reset(tmp_path):
    el_mod._reset_for_tests()
    yield
    el_mod._reset_for_tests()


class _FakeSafetyNet:
    def __init__(self):
        self.tripped = False
        self.last_reason = None
    def trip(self, reason: str, metrics=None):
        self.tripped = True
        self.last_reason = reason
        return {"tripped": True, "reason": reason}


def test_engage_sets_state_and_trips_safety_net(tmp_path: Path):
    sn = _FakeSafetyNet()
    lock = EmergencyLockdown(safety_net=sn, state_dir=tmp_path)
    out = lock.engage(reason="test halt", source="cli")
    assert out["engaged"] is True
    assert out["reason"] == "test halt"
    assert sn.tripped is True
    assert "test halt" in sn.last_reason
    # Snapshot file written.
    files = list(tmp_path.glob("emergency_lockdown_*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["state"]["engaged"] is True


def test_engage_idempotent(tmp_path: Path):
    lock = EmergencyLockdown(state_dir=tmp_path)
    a = lock.engage(reason="r1", source="cli")
    b = lock.engage(reason="r2", source="api")
    assert a["reason"] == "r1"
    assert b["reason"] == "r1"  # second call is no-op


def test_disengage_releases_and_removes_sentinel(tmp_path: Path):
    sentinel = tmp_path / "sentinel"
    sentinel.write_text("x")
    lock = EmergencyLockdown(state_dir=tmp_path / "state", sentinel_path=sentinel)
    lock.engage(reason="r", source="cli")
    out = lock.disengage(operator="alice", note="manual")
    assert out["released"] is True
    assert lock.is_engaged() is False
    assert not sentinel.exists()


def test_singleton_is_engaged_helper(tmp_path: Path):
    assert is_engaged() is False
    lock = get_emergency_lockdown(state_dir=tmp_path)
    lock.engage(reason="r", source="api")
    assert is_engaged() is True
    lock.disengage(operator="op")
    assert is_engaged() is False


def test_sentinel_loop_engages_within_3_seconds(tmp_path: Path):
    sentinel = tmp_path / "sentinel"
    lock = EmergencyLockdown(state_dir=tmp_path / "state", sentinel_path=sentinel, sentinel_poll_sec=0.05)

    async def _runner():
        await lock.start_sentinel_watch()
        # Touch the sentinel.
        await asyncio.sleep(0.05)
        sentinel.write_text("now")
        # Wait up to 3s for engagement.
        deadline = time.time() + 3.0
        while time.time() < deadline and not lock.is_engaged():
            await asyncio.sleep(0.05)
        await lock.stop_sentinel_watch()

    asyncio.get_event_loop().run_until_complete(_runner()) if False else asyncio.run(_runner())
    assert lock.is_engaged() is True
    assert lock.state.source == "sentinel"
