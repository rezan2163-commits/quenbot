"""
Aşama 3 — Emergency Lockdown
============================

Instant system halt reachable via three independent paths:

1. ``POST /api/oracle/emergency-lockdown`` (requires ``EMERGENCY_TOKEN`` env secret)
2. CLI: ``python python_agents/scripts/emergency_lockdown.py --reason "..."``
3. File sentinel: ``touch /tmp/quenbot_emergency``

Side effects when engaged:

* Sets a process-local flag that the Brain consults on every tick.
* Trips :class:`SafetyNet` (if reachable) so all dependent gates close too.
* Emits :data:`EventType.EMERGENCY_LOCKDOWN` for any other subscribers
  (e.g. the Strategist halts new paper-trade openings).
* Writes a forensic snapshot ``.emergency_lockdown_<ts>.json``.

Existing paper positions roll to natural exit — lockdown does NOT close them.
Reset requires explicit operator action (:func:`disengage`).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("quenbot.emergency_lockdown")


@dataclass
class LockdownState:
    engaged: bool = False
    engaged_at: Optional[float] = None
    reason: Optional[str] = None
    source: Optional[str] = None  # "api" | "cli" | "sentinel" | "auto"
    snapshot_path: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


class EmergencyLockdown:
    """Process-singleton lockdown manager."""

    def __init__(
        self,
        *,
        event_bus: Any = None,
        safety_net: Any = None,
        brain: Any = None,
        state_dir: Optional[str] = None,
        sentinel_path: Optional[str] = None,
        sentinel_poll_sec: float = 5.0,
    ) -> None:
        from config import Config  # local import — avoid hard cycle on import
        self.event_bus = event_bus
        self.safety_net = safety_net
        self.brain = brain
        self.state_dir = Path(state_dir or getattr(Config, "EMERGENCY_STATE_DIR", "python_agents/.emergency"))
        self.sentinel_path = Path(sentinel_path or getattr(Config, "EMERGENCY_SENTINEL_PATH", "/tmp/quenbot_emergency"))
        self.sentinel_poll_sec = float(sentinel_poll_sec)
        self.state = LockdownState()
        self._sentinel_task: Optional[asyncio.Task] = None
        self._sentinel_running = False

    # ── public API ──────────────────────────────────────────────────
    def is_engaged(self) -> bool:
        return bool(self.state.engaged)

    def status(self) -> Dict[str, Any]:
        return self.state.as_dict()

    def engage(self, *, reason: str, source: str = "api", extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if self.state.engaged:
            logger.warning("emergency_lockdown.engage: already engaged (no-op)")
            return self.state.as_dict()
        now = time.time()
        self.state = LockdownState(
            engaged=True,
            engaged_at=now,
            reason=str(reason)[:512],
            source=str(source)[:32],
            extra=dict(extra or {}),
        )
        # 1) trip safety net so signal/risk paths close.
        try:
            sn = self.safety_net
            if sn is not None and hasattr(sn, "trip"):
                sn.trip(reason=f"emergency_lockdown: {reason}", metrics={"source": source})
        except Exception as exc:
            logger.debug("emergency_lockdown safety_net trip skipped: %s", exc)
        # 2) flag brain (if hookable). Brain checks emergency_lockdown.is_engaged()
        #    in its own _tick_symbol; nothing else to do here besides logging.
        # 3) snapshot
        snap_path = self._write_snapshot()
        self.state.snapshot_path = str(snap_path) if snap_path else None
        # 4) emit
        self._publish_sync("EMERGENCY_LOCKDOWN", self.state.as_dict())
        logger.error("🚨 EMERGENCY LOCKDOWN ENGAGED — source=%s reason=%s", source, reason)
        return self.state.as_dict()

    def disengage(self, *, operator: str, note: str = "") -> Dict[str, Any]:
        if not self.state.engaged:
            return {"engaged": False, "released": False, "note": "was not engaged"}
        prev = self.state.as_dict()
        self.state = LockdownState()
        # Best-effort sentinel cleanup so we don't immediately re-engage on next poll.
        try:
            if self.sentinel_path.exists():
                self.sentinel_path.unlink()
        except Exception:
            pass
        payload = {"released_by": operator, "note": note, "previous": prev, "released_at": time.time()}
        self._publish_sync("EMERGENCY_LOCKDOWN_RELEASED", payload)
        logger.warning("🟢 emergency lockdown released by %s — note=%s", operator, note)
        return {"engaged": False, "released": True, **payload}

    # ── sentinel watcher ─────────────────────────────────────────────
    async def start_sentinel_watch(self) -> None:
        if self._sentinel_running:
            return
        self._sentinel_running = True
        self._sentinel_task = asyncio.create_task(self._sentinel_loop())

    async def stop_sentinel_watch(self) -> None:
        self._sentinel_running = False
        t = self._sentinel_task
        if t is not None:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
            self._sentinel_task = None

    async def _sentinel_loop(self) -> None:
        while self._sentinel_running:
            try:
                if self.sentinel_path.exists() and not self.state.engaged:
                    self.engage(reason="sentinel file present", source="sentinel")
            except Exception as exc:
                logger.debug("sentinel poll err: %s", exc)
            await asyncio.sleep(self.sentinel_poll_sec)

    # ── helpers ──────────────────────────────────────────────────────
    def _write_snapshot(self) -> Optional[Path]:
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            ts = int(self.state.engaged_at or time.time())
            path = self.state_dir / f"emergency_lockdown_{ts}.json"
            payload = {"state": self.state.as_dict(), "ts": ts}
            path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            return path
        except Exception as exc:
            logger.warning("emergency snapshot write failed: %s", exc)
            return None

    def _publish_sync(self, event_name: str, payload: Dict[str, Any]) -> None:
        bus = self.event_bus
        if bus is None:
            return
        try:
            from event_bus import EventType, Event
            ev = getattr(EventType, event_name, None)
            if ev is None:
                return
            evt = Event(type=ev, source="emergency_lockdown", data=payload)
            try:
                # publish() is async — schedule fire-and-forget.
                asyncio.get_event_loop().create_task(bus.publish(evt))
            except RuntimeError:
                # no loop — best-effort sync fallback
                pub = getattr(bus, "publish_sync", None)
                if pub is not None:
                    pub(evt)
        except Exception as exc:
            logger.debug("emergency_lockdown publish skip: %s", exc)


# ─── singleton ──────────────────────────────────────────────────────
_INSTANCE: Optional[EmergencyLockdown] = None


def get_emergency_lockdown(**kwargs: Any) -> EmergencyLockdown:
    """Lazy singleton. Late-binds event_bus / safety_net / brain when first
    available; ignores subsequent re-registrations."""
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = EmergencyLockdown(**kwargs)
    else:
        for k in ("event_bus", "safety_net", "brain"):
            v = kwargs.get(k)
            if v is not None and getattr(_INSTANCE, k, None) is None:
                setattr(_INSTANCE, k, v)
    return _INSTANCE


def is_engaged() -> bool:
    """Cheap top-level helper — Brain may import this without keeping a ref."""
    return _INSTANCE is not None and _INSTANCE.is_engaged()


def _reset_for_tests() -> None:
    global _INSTANCE
    _INSTANCE = None
