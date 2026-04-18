"""Integration tests for mission-control HTTP handlers.

We build a tiny aiohttp application wiring only the handlers that matter and
exercise them end-to-end via aiohttp's TestServer/TestClient (no plugin
dependency beyond pytest-asyncio, already installed).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import mission_control_aggregator as mc
from event_bus import get_event_bus
from module_registry import MODULE_REGISTRY


pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Handler factory — mirrors production wiring in main.py
# ---------------------------------------------------------------------------

def _install_routes(app: web.Application, *, llm_bridge=None, restart_callback=None) -> None:
    async def snapshot_handler(_request):
        return web.json_response(mc.snapshot(force=True))

    async def autopsy_handler(request):
        mid = request.match_info.get("module_id", "")
        if mid not in MODULE_REGISTRY:
            return web.json_response({"error": f"unknown module: {mid}"}, status=404)
        bundle = mc.autopsy_bundle(mid, log_tail=["sample log line"])
        if llm_bridge is not None:
            try:
                res = await llm_bridge.call_llm(
                    task="mission_control_autopsy",
                    system="system", prompt="prompt",
                    json_mode=False, temperature=0.2,
                )
                if isinstance(res, dict) and res.get("success"):
                    bundle["qwen_diagnosis"] = {
                        "summary_tr": res["text"],
                        "suggested_actions_tr": [],
                        "confidence": 0.7,
                        "generated_at": time.time(),
                    }
            except Exception:
                pass
        return web.json_response(bundle)

    async def restart_handler(request):
        mid = request.match_info.get("module_id", "")
        if mid not in MODULE_REGISTRY:
            return web.json_response({"error": "unknown"}, status=404)
        required = os.environ.get("QUENBOT_ADMIN_TOKEN", "")
        if required:
            supplied = (
                request.headers.get("X-Admin-Token") or request.query.get("token") or ""
            )
            if supplied != required:
                return web.json_response({"error": "unauthorized"}, status=401)
        if restart_callback is None:
            return web.json_response(
                {"ok": False, "error": "restart not available"}, status=503
            )
        await restart_callback(mid)
        return web.json_response(
            {"ok": True, "module_id": mid, "restarted_at": time.time()}
        )

    async def stream_handler(request):
        response = web.StreamResponse(
            status=200,
            headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache"},
        )
        await response.prepare(request)
        for _ in range(2):
            snap = mc.snapshot(force=True)
            payload = json.dumps(
                {"ts": snap["generated_at"], "modules": len(snap["modules"])}
            )
            await response.write(f"data: {payload}\n\n".encode("utf-8"))
            await asyncio.sleep(0.05)
        return response

    app.router.add_get("/api/mission-control/snapshot", snapshot_handler)
    app.router.add_get("/api/mission-control/stream", stream_handler)
    app.router.add_get("/api/mission-control/autopsy/{module_id}", autopsy_handler)
    app.router.add_post("/api/mission-control/restart/{module_id}", restart_handler)


@contextlib.asynccontextmanager
async def _make_client(**kwargs):
    app = web.Application()
    _install_routes(app, **kwargs)
    server = TestServer(app)
    client = TestClient(server)
    await client.start_server()
    try:
        yield client
    finally:
        await client.close()


@pytest.fixture(autouse=True)
def _reset_state():
    mc._reset_all_caches_for_tests()
    bus = get_event_bus()
    bus._history.clear()
    bus._significant_history.clear()
    bus._latest_heartbeats.clear()
    os.environ.pop("QUENBOT_ADMIN_TOKEN", None)
    yield


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_snapshot_endpoint_200_and_shape():
    async with _make_client() as client:
        resp = await client.get("/api/mission-control/snapshot")
        assert resp.status == 200
        data = await resp.json()
        for key in ("modules", "edges", "vital_signs", "qwen_pulse", "overall_health_score"):
            assert key in data
        assert len(data["modules"]) == len(MODULE_REGISTRY)


async def test_autopsy_endpoint_returns_bundle():
    async with _make_client() as client:
        resp = await client.get("/api/mission-control/autopsy/hawkes_kernel_fitter")
        assert resp.status == 200
        data = await resp.json()
        assert data["module_id"] == "hawkes_kernel_fitter"
        assert "timeline_5min" in data
        assert "dependencies_status" in data
        assert "restart" in data["operator_actions_available"]


async def test_autopsy_unknown_module_404():
    async with _make_client() as client:
        resp = await client.get("/api/mission-control/autopsy/__not_real__")
        assert resp.status == 404


async def test_autopsy_with_mock_llm_attaches_diagnosis():
    class FakeBridge:
        async def call_llm(self, **kwargs):
            return {"success": True, "text": "Hücre sağlıklı görünüyor.", "error": None}

    async with _make_client(llm_bridge=FakeBridge()) as client:
        resp = await client.get("/api/mission-control/autopsy/brain")
        assert resp.status == 200
        data = await resp.json()
        assert data["qwen_diagnosis"] is not None
        assert "summary_tr" in data["qwen_diagnosis"]


async def test_autopsy_when_llm_unavailable_returns_null_diagnosis():
    class BrokenBridge:
        async def call_llm(self, **kwargs):
            raise RuntimeError("LLM offline")

    async with _make_client(llm_bridge=BrokenBridge()) as client:
        resp = await client.get("/api/mission-control/autopsy/brain")
        assert resp.status == 200
        data = await resp.json()
        assert data["qwen_diagnosis"] is None


async def test_restart_without_callback_returns_503():
    async with _make_client() as client:
        resp = await client.post("/api/mission-control/restart/scout_agent")
        assert resp.status == 503


async def test_restart_calls_callback_and_returns_ok():
    calls = []

    async def cb(name):
        calls.append(name)

    async with _make_client(restart_callback=cb) as client:
        resp = await client.post("/api/mission-control/restart/scout_agent")
        assert resp.status == 200
        data = await resp.json()
        assert data["ok"] is True
        assert calls == ["scout_agent"]


async def test_restart_unauthorized_when_admin_token_set():
    os.environ["QUENBOT_ADMIN_TOKEN"] = "secret"

    async def cb(name):
        return None

    async with _make_client(restart_callback=cb) as client:
        resp = await client.post("/api/mission-control/restart/scout_agent")
        assert resp.status == 401
        resp2 = await client.post(
            "/api/mission-control/restart/scout_agent?token=secret"
        )
        assert resp2.status == 200


async def test_sse_stream_emits_frames():
    async with _make_client() as client:
        resp = await client.get("/api/mission-control/stream")
        assert resp.status == 200
        assert resp.headers.get("Content-Type", "").startswith("text/event-stream")
        body = await asyncio.wait_for(resp.read(), timeout=3.0)
        text = body.decode("utf-8", errors="ignore")
        assert text.count("data: ") >= 2


async def test_snapshot_returns_edges_and_qwen_pulse():
    async with _make_client() as client:
        resp = await client.get("/api/mission-control/snapshot")
        data = await resp.json()
        assert isinstance(data["edges"], list)
        assert isinstance(data["qwen_pulse"], dict)
        assert "asama" in data["qwen_pulse"]
"""Integration tests for mission-control HTTP handlers.

We build a tiny aiohttp application wiring only the handlers that matter and
exercise them end-to-end. This keeps tests hermetic while still exercising
real JSON serialization, status codes, and aggregator integration.
"""
