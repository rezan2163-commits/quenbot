"""test_runtime_supervisor.py — §12 tests."""
from __future__ import annotations

import asyncio
import json
import os
import time

import pytest

from runtime_supervisor import (
    RuntimeSupervisor, get_runtime_supervisor, _reset_for_tests as _reset_rs,
)


@pytest.fixture(autouse=True)
def _reset():
    _reset_rs()
    yield
    _reset_rs()


class _FakeComponent:
    def __init__(self, healthy: bool = True):
        self._healthy = healthy

    def set_healthy(self, v: bool) -> None:
        self._healthy = v

    async def health_check(self):
        return {"healthy": self._healthy}


def test_singleton():
    a = get_runtime_supervisor(status_path="/tmp/qb_status_test.json")
    b = get_runtime_supervisor(status_path="/tmp/ignored.json")
    assert a is b


@pytest.mark.asyncio
async def test_tick_writes_status_and_heartbeat(tmp_path):
    status = str(tmp_path / "status.json")
    hb = str(tmp_path / "heartbeat")
    sup = RuntimeSupervisor(status_path=status, heartbeat_path=hb, interval_sec=60.0)
    c1 = _FakeComponent(True)
    sup.register("c1", lambda: c1)
    await sup._tick()
    assert os.path.exists(status)
    assert os.path.exists(hb)
    data = json.loads(open(status).read())
    assert "components" in data
    assert data["components"]["c1"]["healthy"] is True


@pytest.mark.asyncio
async def test_restart_callback_triggered_after_3_failures(tmp_path):
    status = str(tmp_path / "status.json")
    calls = []

    async def cb(name: str) -> None:
        calls.append(name)

    sup = RuntimeSupervisor(status_path=status, interval_sec=60.0,
                            max_restart_attempts=2, restart_callback=cb)
    bad = _FakeComponent(False)
    sup.register("bad", lambda: bad)
    # 3 consecutive failures → restart requested
    for _ in range(3):
        await sup._tick()
    assert len(calls) >= 1
    assert calls[0] == "bad"


@pytest.mark.asyncio
async def test_restart_attempts_capped(tmp_path):
    status = str(tmp_path / "status.json")
    calls = []

    async def cb(name: str) -> None:
        calls.append(name)

    sup = RuntimeSupervisor(status_path=status, interval_sec=60.0,
                            max_restart_attempts=2, restart_callback=cb)
    bad = _FakeComponent(False)
    sup.register("bad", lambda: bad)
    for _ in range(20):
        await sup._tick()
    assert len(calls) <= 2  # capped at max_restart_attempts


@pytest.mark.asyncio
async def test_healthy_clears_failure_counter(tmp_path):
    status = str(tmp_path / "status.json")
    sup = RuntimeSupervisor(status_path=status, interval_sec=60.0)
    c = _FakeComponent(False)
    sup.register("c", lambda: c)
    await sup._tick()
    await sup._tick()
    c.set_healthy(True)
    await sup._tick()
    # find the record for "c"
    rec = next(r for r in sup._components if r.name == "c")
    assert rec.consecutive_failures == 0


@pytest.mark.asyncio
async def test_start_stop_cancellable(tmp_path):
    sup = RuntimeSupervisor(status_path=str(tmp_path / "s.json"), interval_sec=0.05)
    sup.register("c", lambda: _FakeComponent(True))
    await sup.start()
    await asyncio.sleep(0.12)
    await sup.stop()
    assert sup._started is False


def test_metrics_shape(tmp_path):
    sup = RuntimeSupervisor(status_path=str(tmp_path / "s.json"))
    m = sup.metrics()
    for k in ("supervisor_cycles_total", "supervisor_failures_total",
              "supervisor_restarts_requested_total", "supervisor_components_registered"):
        assert k in m


def test_status_and_none_getter(tmp_path):
    sup = RuntimeSupervisor(status_path=str(tmp_path / "s.json"))
    sup.register("c_none", lambda: None)
    st = sup.status()
    assert "c_none" in st["components"]
