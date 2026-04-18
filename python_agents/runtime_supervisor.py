"""
runtime_supervisor.py — §12 Runtime Supervisor & Watchdog
===========================================================
Agent/detektör health'ini periyodik kontrol eder, heartbeat dosyası
yazar ve fail-soft restart isteği kaydeder. Default OFF.

Hiçbir agent'i zorla durdurmaz/öldürmez — yalnızca:
  • health_check()'leri toplar, status dosyasına yazar
  • heartbeat dosyasını güncel tutar (watchdog scripti okur)
  • orchestrator'a opsiyonel restart-request callback verebilir
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class _ComponentRecord:
    name: str
    getter: Callable[[], Any]
    consecutive_failures: int = 0
    last_ok_ts: float = 0.0
    last_error: Optional[str] = None


class RuntimeSupervisor:
    def __init__(
        self,
        status_path: str,
        heartbeat_path: Optional[str] = None,
        interval_sec: float = 30.0,
        max_restart_attempts: int = 3,
        restart_callback: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> None:
        self.status_path = status_path
        self.heartbeat_path = heartbeat_path
        self.interval = float(interval_sec)
        self.max_restart_attempts = int(max_restart_attempts)
        self._restart_callback = restart_callback
        self._components: List[_ComponentRecord] = []
        self._restart_counts: Dict[str, int] = {}
        self._started = False
        self._task: Optional[asyncio.Task] = None
        self._last_cycle_ts: float = 0.0
        self._stats = {"cycles": 0, "failures": 0, "restarts_requested": 0}

    def register(self, name: str, getter: Callable[[], Any]) -> None:
        """Bir component'i supervisor'a kayıt et."""
        if not name or getter is None:
            return
        self._components.append(_ComponentRecord(name=name, getter=getter))

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._task = asyncio.create_task(self._loop())
        logger.info("RuntimeSupervisor started (interval=%.0fs, components=%d)",
                    self.interval, len(self._components))

    async def stop(self) -> None:
        self._started = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _loop(self) -> None:
        while self._started:
            try:
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("Supervisor tick err: %s", e)
            try:
                await asyncio.sleep(self.interval)
            except asyncio.CancelledError:
                break

    async def _tick(self) -> None:
        self._last_cycle_ts = time.time()
        self._stats["cycles"] += 1
        report = await self._collect_health()
        self._write_status(report)
        self._write_heartbeat()

    async def _collect_health(self) -> Dict[str, Any]:
        per: Dict[str, Any] = {}
        unhealthy: List[str] = []
        for rec in self._components:
            try:
                obj = rec.getter()
            except Exception as e:
                per[rec.name] = {"enabled": False, "error": f"getter: {e}"}
                continue
            if obj is None:
                per[rec.name] = {"enabled": False}
                continue
            try:
                if hasattr(obj, "health_check"):
                    h = obj.health_check()
                    if asyncio.iscoroutine(h):
                        h = await h
                elif hasattr(obj, "status"):
                    h = obj.status()
                else:
                    h = {"healthy": True}
                healthy = bool(h.get("healthy", True)) if isinstance(h, dict) else True
                per[rec.name] = {"enabled": True, "healthy": healthy, "details": h if isinstance(h, dict) else {}}
                if healthy:
                    rec.consecutive_failures = 0
                    rec.last_ok_ts = time.time()
                    rec.last_error = None
                else:
                    rec.consecutive_failures += 1
                    self._stats["failures"] += 1
                    unhealthy.append(rec.name)
            except Exception as e:
                rec.consecutive_failures += 1
                rec.last_error = str(e)
                self._stats["failures"] += 1
                per[rec.name] = {"enabled": True, "healthy": False, "error": str(e)}
                unhealthy.append(rec.name)
        # Request restart for persistently unhealthy components
        for rec in self._components:
            if rec.consecutive_failures >= 3:
                attempts = self._restart_counts.get(rec.name, 0)
                if attempts < self.max_restart_attempts and self._restart_callback is not None:
                    self._restart_counts[rec.name] = attempts + 1
                    self._stats["restarts_requested"] += 1
                    logger.warning("Supervisor restart-request: %s (attempt %d/%d)",
                                   rec.name, attempts + 1, self.max_restart_attempts)
                    try:
                        await self._restart_callback(rec.name)
                        rec.consecutive_failures = 0
                    except Exception as e:
                        logger.debug("restart_callback fail for %s: %s", rec.name, e)
        return {
            "ts": self._last_cycle_ts,
            "components": per,
            "unhealthy": unhealthy,
            "restart_counts": dict(self._restart_counts),
            "stats": dict(self._stats),
        }

    def _write_status(self, report: Dict[str, Any]) -> None:
        try:
            os.makedirs(os.path.dirname(self.status_path) or ".", exist_ok=True)
            tmp = f"{self.status_path}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(report, f, default=str, separators=(",", ":"))
            os.replace(tmp, self.status_path)
        except Exception as e:
            logger.debug("status write fail: %s", e)

    def _write_heartbeat(self) -> None:
        if not self.heartbeat_path:
            return
        try:
            with open(self.heartbeat_path, "w", encoding="utf-8") as f:
                f.write(str(int(time.time())))
        except Exception as e:
            logger.debug("heartbeat write fail: %s", e)

    def status(self) -> Dict[str, Any]:
        return {
            "running": self._started,
            "last_cycle_ts": self._last_cycle_ts,
            "components": [c.name for c in self._components],
            "stats": dict(self._stats),
            "restart_counts": dict(self._restart_counts),
        }

    def metrics(self) -> Dict[str, Any]:
        return {
            "supervisor_cycles_total": self._stats["cycles"],
            "supervisor_failures_total": self._stats["failures"],
            "supervisor_restarts_requested_total": self._stats["restarts_requested"],
            "supervisor_components_registered": len(self._components),
        }


_instance: Optional[RuntimeSupervisor] = None


def get_runtime_supervisor(**kwargs: Any) -> RuntimeSupervisor:
    global _instance
    if _instance is None:
        _instance = RuntimeSupervisor(**kwargs)
    return _instance


def _reset_for_tests() -> None:
    global _instance
    _instance = None
