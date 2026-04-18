"""Aşama 3 — weekly_ack_watchdog tests."""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

import weekly_ack_watchdog as wd_mod
from weekly_ack_watchdog import WeeklyAckWatchdog, ASAMA_2_PROFILE, _iso_week_label
from config import Config


@pytest.fixture(autouse=True)
def _reset():
    wd_mod._reset_for_tests()
    # Snapshot Aşama 3 defaults for restoration after every test.
    snap = {k: getattr(Config, k) for k in ASAMA_2_PROFILE.keys()}
    yield
    for k, v in snap.items():
        setattr(Config, k, v)
    wd_mod._reset_for_tests()


def _ts(dt: datetime) -> float:
    return dt.replace(tzinfo=timezone.utc).timestamp()


def _make(tmp_path: Path, *, now_dt: datetime, grace_hours: int = 168):
    return WeeklyAckWatchdog(
        ack_dir=tmp_path,
        grace_hours=grace_hours,
        clock=lambda: _ts(now_dt),
    )


def test_ack_present_keeps_a3_profile(tmp_path: Path):
    # Set fake "now" — Wednesday of week 2026-16.
    now = datetime(2026, 4, 22, 12, 0, 0)
    week = _iso_week_label(now.replace(tzinfo=timezone.utc))
    (tmp_path / f".weekly_ack_{week}.json").write_text(json.dumps({"week": week, "ts": _ts(now)}))
    wd = _make(tmp_path, now_dt=now)
    out = wd.check_once()
    assert out["ack_present"] is True
    assert out["degraded"] is False
    # A3 max stays at 30.
    assert Config.ORACLE_BRAIN_MAX_DIRECTIVES_PER_HOUR == 30


def test_missing_ack_within_grace_does_not_degrade(tmp_path: Path):
    # Tuesday (only ~36h since Mon 00:00).
    now = datetime(2026, 4, 21, 12, 0, 0)
    wd = _make(tmp_path, now_dt=now, grace_hours=168)
    out = wd.check_once()
    assert out["ack_present"] is False
    assert out["degraded"] is False
    assert out["elapsed_hours"] < 168
    assert Config.ORACLE_BRAIN_MAX_DIRECTIVES_PER_HOUR == 30


def test_missing_ack_past_grace_degrades(tmp_path: Path):
    # Set Aşama 3 explicit values up front.
    Config.ORACLE_BRAIN_MAX_DIRECTIVES_PER_HOUR = 30
    Config.ORACLE_BRAIN_DIRECTIVE_ALLOWLIST = list(ASAMA_2_PROFILE["ORACLE_BRAIN_DIRECTIVE_ALLOWLIST"]) + ["CHANGE_STRATEGY"]
    Config.ORACLE_BRAIN_DIRECTIVE_BLOCKLIST_HARD = ["OVERRIDE_VETO", "FORCE_TRADE", "DISABLE_SAFETY_NET"]
    # Push "now" past Monday + 7d (i.e. ack covers prior week which has no file).
    # Use a tiny grace so the test is deterministic.
    now = datetime(2026, 4, 24, 12, 0, 0)
    wd = _make(tmp_path, now_dt=now, grace_hours=1)
    out = wd.check_once()
    assert out["degraded"] is True
    # Profile should now equal Aşama 2.
    assert Config.ORACLE_BRAIN_MAX_DIRECTIVES_PER_HOUR == 10
    assert "CHANGE_STRATEGY" not in Config.ORACLE_BRAIN_DIRECTIVE_ALLOWLIST
    assert "CHANGE_STRATEGY" in Config.ORACLE_BRAIN_DIRECTIVE_BLOCKLIST_HARD


def test_ack_creation_restores_a3(tmp_path: Path):
    Config.ORACLE_BRAIN_MAX_DIRECTIVES_PER_HOUR = 30
    Config.ORACLE_BRAIN_DIRECTIVE_ALLOWLIST = list(ASAMA_2_PROFILE["ORACLE_BRAIN_DIRECTIVE_ALLOWLIST"]) + ["CHANGE_STRATEGY"]
    Config.ORACLE_BRAIN_DIRECTIVE_BLOCKLIST_HARD = ["OVERRIDE_VETO", "FORCE_TRADE", "DISABLE_SAFETY_NET"]
    now = datetime(2026, 4, 24, 12, 0, 0)
    week = _iso_week_label(now.replace(tzinfo=timezone.utc))
    wd = _make(tmp_path, now_dt=now, grace_hours=1)
    wd.check_once()
    assert Config.ORACLE_BRAIN_MAX_DIRECTIVES_PER_HOUR == 10
    # Operator acks now.
    (tmp_path / f".weekly_ack_{week}.json").write_text(json.dumps({"week": week}))
    out = wd.check_once()
    assert out["ack_present"] is True
    assert out["degraded"] is False
    assert Config.ORACLE_BRAIN_MAX_DIRECTIVES_PER_HOUR == 30
    assert "CHANGE_STRATEGY" in Config.ORACLE_BRAIN_DIRECTIVE_ALLOWLIST


def test_status_dict_shape(tmp_path: Path):
    now = datetime(2026, 4, 22, 12, 0, 0)
    wd = _make(tmp_path, now_dt=now)
    s = wd.status()
    assert {"enabled", "running", "degraded", "current_week", "ack_present", "grace_hours"} <= set(s.keys())
    assert s["current_week"] == "2026-17"
