"""
Aşama 3 — Weekly Acknowledgement Watchdog
=========================================

Background task that polls the operator's weekly ack file. If the
acknowledgement for the current ISO week is missing past the grace period
(default 7 days), the system auto-degrades to the Aşama 2 throttle profile
and emits ``WEEKLY_ACK_MISSING`` + ``SYSTEM_AUTO_DEGRADED``. As soon as an
ack appears, the Aşama 3 profile is restored.

The degrade/restore is implemented as a runtime mutation of ``Config``
attributes (gatekeeper reads them dynamically). Original values are stashed
in :attr:`_saved_a3_profile` so restoration is exact.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger("quenbot.weekly_ack_watchdog")


# Aşama 2 fallback profile — the watchdog reverts to these when an ack is
# missing past grace. Keep numbers in sync with Aşama 2 defaults.
ASAMA_2_PROFILE: Dict[str, Any] = {
    "ORACLE_BRAIN_MAX_DIRECTIVES_PER_HOUR": 10,
    "ORACLE_BRAIN_DIRECTIVE_ALLOWLIST": [
        "ADJUST_CONFIDENCE_THRESHOLD", "ADJUST_POSITION_SIZE_MULT", "PAUSE_SYMBOL",
        "RESUME_SYMBOL", "CHANGE_STRATEGY_WEIGHT", "ADJUST_TP_SL_RATIO",
    ],
    "ORACLE_BRAIN_DIRECTIVE_BLOCKLIST_HARD": [
        "CHANGE_STRATEGY", "OVERRIDE_VETO", "FORCE_TRADE", "DISABLE_SAFETY_NET",
    ],
}


def _iso_week_label(dt: Optional[datetime] = None) -> str:
    dt = dt or datetime.now(timezone.utc)
    iso = dt.isocalendar()
    return f"{iso[0]}-{iso[1]:02d}"


def _ack_path_for(week_label: str, ack_dir: Path) -> Path:
    return ack_dir / f".weekly_ack_{week_label}.json"


@dataclass
class WatchdogStatus:
    enabled: bool = False
    running: bool = False
    degraded: bool = False
    last_check_ts: Optional[float] = None
    last_check_iso: Optional[str] = None
    current_week: Optional[str] = None
    ack_present: bool = False
    ack_path: Optional[str] = None
    grace_hours: int = 168
    week_started_at_ts: Optional[float] = None
    saved_profile_keys: list = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


class WeeklyAckWatchdog:
    """Polls the weekly ack file and toggles A3↔A2 profile."""

    def __init__(
        self,
        *,
        event_bus: Any = None,
        ack_dir: Optional[Path] = None,
        grace_hours: Optional[int] = None,
        interval_sec: Optional[float] = None,
        clock: Any = None,
    ) -> None:
        from config import Config
        self.event_bus = event_bus
        self.ack_dir = Path(ack_dir or getattr(Config, "WEEKLY_ACK_DIR", "python_agents/.weekly_ack"))
        self.grace_hours = int(
            grace_hours
            if grace_hours is not None
            else getattr(Config, "WEEKLY_ACK_GRACE_HOURS", 168)
        )
        self.interval_sec = float(
            interval_sec
            if interval_sec is not None
            else getattr(Config, "WEEKLY_ACK_WATCHDOG_INTERVAL_SEC", 3600)
        )
        self.clock = clock or (lambda: time.time())
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._degraded = False
        self._saved_a3_profile: Dict[str, Any] = {}

    # ── public API ──────────────────────────────────────────────────
    def status(self) -> Dict[str, Any]:
        from config import Config
        now_ts = self._now_ts()
        wk = _iso_week_label(datetime.fromtimestamp(now_ts, tz=timezone.utc))
        ack_path = _ack_path_for(wk, self.ack_dir)
        return WatchdogStatus(
            enabled=bool(getattr(Config, "WEEKLY_ACK_WATCHDOG_ENABLED", False)),
            running=self._running,
            degraded=self._degraded,
            last_check_ts=now_ts,
            last_check_iso=datetime.fromtimestamp(now_ts, tz=timezone.utc).isoformat(),
            current_week=wk,
            ack_present=ack_path.exists(),
            ack_path=str(ack_path),
            grace_hours=self.grace_hours,
            week_started_at_ts=self._week_started_ts(now_ts),
            saved_profile_keys=list(self._saved_a3_profile.keys()),
        ).as_dict()

    def check_once(self) -> Dict[str, Any]:
        """Single synchronous check — exposed for tests + manual triggers."""
        now_ts = self._now_ts()
        wk = _iso_week_label(datetime.fromtimestamp(now_ts, tz=timezone.utc))
        ack_path = _ack_path_for(wk, self.ack_dir)
        present = ack_path.exists()
        # If ack present → restore (if previously degraded).
        if present:
            if self._degraded:
                self._restore()
                self._publish("SYSTEM_AUTO_RESTORED", {"week": wk, "ack_path": str(ack_path)})
                self._publish("WEEKLY_ACK_RECEIVED", {"week": wk, "ack_path": str(ack_path)})
            return {"week": wk, "ack_present": True, "degraded": False}
        # No ack — check grace.
        week_started = self._week_started_ts(now_ts)
        elapsed = (now_ts - week_started) / 3600.0
        if elapsed >= self.grace_hours:
            if not self._degraded:
                self._degrade()
                payload = {
                    "week": wk,
                    "elapsed_hours": elapsed,
                    "grace_hours": self.grace_hours,
                    "ack_path": str(ack_path),
                }
                self._publish("WEEKLY_ACK_MISSING", payload)
                self._publish("SYSTEM_AUTO_DEGRADED", payload)
            return {"week": wk, "ack_present": False, "degraded": True, "elapsed_hours": elapsed}
        return {"week": wk, "ack_present": False, "degraded": False, "elapsed_hours": elapsed}

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        t = self._task
        if t is not None:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    # ── internals ───────────────────────────────────────────────────
    def _now_ts(self) -> float:
        try:
            return float(self.clock())
        except Exception:
            return time.time()

    def _week_started_ts(self, now_ts: float) -> float:
        """ISO week start (Monday 00:00 UTC) for the week containing now_ts."""
        now_dt = datetime.fromtimestamp(now_ts, tz=timezone.utc)
        # ISO weekday: Mon=1..Sun=7
        weekday = now_dt.isoweekday()
        start = (now_dt - timedelta(days=weekday - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
        return start.timestamp()

    def _degrade(self) -> None:
        from config import Config
        if self._degraded:
            return
        self._saved_a3_profile = {}
        for k, v in ASAMA_2_PROFILE.items():
            try:
                self._saved_a3_profile[k] = getattr(Config, k)
            except Exception:
                self._saved_a3_profile[k] = None
            try:
                setattr(Config, k, v if not isinstance(v, list) else list(v))
            except Exception as exc:
                logger.error("watchdog degrade set %s failed: %s", k, exc)
        self._degraded = True
        logger.error(
            "🟠 WEEKLY_ACK_MISSING — system auto-degraded to Aşama 2 profile (max_directives=%d)",
            ASAMA_2_PROFILE["ORACLE_BRAIN_MAX_DIRECTIVES_PER_HOUR"],
        )

    def _restore(self) -> None:
        from config import Config
        if not self._degraded:
            return
        for k, v in self._saved_a3_profile.items():
            try:
                setattr(Config, k, v if not isinstance(v, list) else list(v))
            except Exception as exc:
                logger.error("watchdog restore set %s failed: %s", k, exc)
        self._saved_a3_profile = {}
        self._degraded = False
        logger.info("🟢 WEEKLY_ACK received — Aşama 3 profile restored")

    def _publish(self, event_name: str, payload: Dict[str, Any]) -> None:
        bus = self.event_bus
        if bus is None:
            return
        try:
            from event_bus import EventType, Event
            ev = getattr(EventType, event_name, None)
            if ev is None:
                return
            try:
                asyncio.get_event_loop().create_task(bus.publish(Event(type=ev, source="weekly_ack_watchdog", data=payload)))
            except RuntimeError:
                pub = getattr(bus, "publish_sync", None)
                if pub is not None:
                    pub(Event(type=ev, source="weekly_ack_watchdog", data=payload))
        except Exception as exc:
            logger.debug("weekly_ack_watchdog publish skip: %s", exc)

    async def _loop(self) -> None:
        while self._running:
            try:
                self.check_once()
            except Exception as exc:
                logger.debug("watchdog loop err: %s", exc)
            await asyncio.sleep(self.interval_sec)


# ─── singleton ──────────────────────────────────────────────────────
_INSTANCE: Optional[WeeklyAckWatchdog] = None


def get_weekly_ack_watchdog(**kwargs: Any) -> WeeklyAckWatchdog:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = WeeklyAckWatchdog(**kwargs)
    return _INSTANCE


def _reset_for_tests() -> None:
    global _INSTANCE
    _INSTANCE = None
