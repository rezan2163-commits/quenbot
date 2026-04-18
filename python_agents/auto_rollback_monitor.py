"""
auto_rollback_monitor.py — Aşama 1 Oracle Regression Watchdog
=================================================================
Background async task that watches the health of the Qwen Oracle Brain
and forces it back into Shadow Mode on any of six triggers:

  1. Gatekeeper rejection rate > `rejection_rate_threshold` sustained
     for `rejection_window_min`.
  2. Counterfactual shadow accuracy < `accuracy_threshold` over the last
     `accuracy_window` directives that have realized outcomes.
  3. Safety Net trip.
  4. Qwen `meta_confidence` < `meta_conf_min` for `meta_conf_streak`
     consecutive directives.
  5. Runtime supervisor reports the oracle component UNHEALTHY for more
     than `unhealthy_grace_sec`.
  6. Operator sentinel file `force_sentinel_path` exists on disk.

On rollback the monitor:
  - Emits `EventType.ORACLE_AUTO_ROLLBACK`.
  - Writes `.auto_rollback_<ts>.json` (forensic bundle).
  - Sets `Config.ORACLE_BRAIN_SHADOW = True` in-memory AND writes
    `.oracle_shadow_forced.json` so the state survives a restart.
  - Logs WARNING prominently.

Reset is manual: operator deletes `.oracle_shadow_forced.json` (and the
force sentinel if used), sets `QUENBOT_ORACLE_BRAIN_SHADOW=0`, restarts.

Additive; flag-gated via `Config.AUTO_ROLLBACK_ENABLED`. Safe to
instantiate even when every dependency is `None` — the loop degrades to
a cheap no-op in that case.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

logger = logging.getLogger(__name__)


TRIGGER_REJECTION = "rejection_rate"
TRIGGER_ACCURACY = "shadow_accuracy"
TRIGGER_SAFETY_NET = "safety_net_trip"
TRIGGER_META_CONF = "meta_confidence_streak"
TRIGGER_UNHEALTHY = "runtime_unhealthy"
TRIGGER_MANUAL = "operator_sentinel"

ALL_TRIGGERS = (
    TRIGGER_REJECTION, TRIGGER_ACCURACY, TRIGGER_SAFETY_NET,
    TRIGGER_META_CONF, TRIGGER_UNHEALTHY, TRIGGER_MANUAL,
)


@dataclass
class RollbackState:
    rolled_back: bool = False
    trigger: Optional[str] = None
    reason: Optional[str] = None
    ts: Optional[float] = None
    forensic_path: Optional[str] = None


class AutoRollbackMonitor:
    def __init__(
        self,
        *,
        enabled: bool = True,
        gatekeeper: Any = None,
        safety_net: Any = None,
        runtime_supervisor: Any = None,
        oracle_brain: Any = None,
        event_bus: Any = None,
        config_obj: Any = None,
        rejection_rate_threshold: float = 0.60,
        rejection_window_min: int = 30,
        accuracy_threshold: float = 0.45,
        accuracy_window: int = 50,
        meta_conf_min: float = 0.40,
        meta_conf_streak: int = 10,
        unhealthy_grace_sec: int = 300,
        force_sentinel_path: str = "/tmp/quenbot_force_shadow",
        shadow_forced_path: str = "python_agents/.oracle_shadow_forced.json",
        forensic_dir: str = "python_agents/.auto_rollback",
        check_interval_sec: int = 15,
    ) -> None:
        self.enabled = bool(enabled)
        self._gatekeeper = gatekeeper
        self._safety_net = safety_net
        self._runtime_supervisor = runtime_supervisor
        self._oracle_brain = oracle_brain
        self._event_bus = event_bus
        self._config = config_obj

        self.rejection_rate_threshold = float(rejection_rate_threshold)
        self.rejection_window_sec = int(rejection_window_min) * 60
        self.accuracy_threshold = float(accuracy_threshold)
        self.accuracy_window = int(accuracy_window)
        self.meta_conf_min = float(meta_conf_min)
        self.meta_conf_streak = int(meta_conf_streak)
        self.unhealthy_grace_sec = int(unhealthy_grace_sec)
        self.force_sentinel_path = Path(force_sentinel_path)
        self.shadow_forced_path = Path(shadow_forced_path)
        self.forensic_dir = Path(forensic_dir)
        self.check_interval_sec = max(1, int(check_interval_sec))

        # Rolling telemetry.
        self._meta_conf_streak_count = 0
        self._unhealthy_since: Optional[float] = None
        self._shadow_outcomes: Deque[bool] = deque(maxlen=max(self.accuracy_window * 4, 200))
        self._state = RollbackState()
        self._last_check_ts = 0.0
        self._bg_task: Optional[asyncio.Task] = None
        self._running = False

        # Re-hydrate previously forced rollback so a restart keeps shadow
        # pinned until the operator explicitly clears it.
        self._load_existing_forced()

    # ──────────────────────────────────────────────────────────────
    def _load_existing_forced(self) -> None:
        try:
            if self.shadow_forced_path.exists():
                payload = json.loads(self.shadow_forced_path.read_text(encoding="utf-8") or "{}")
                self._state = RollbackState(
                    rolled_back=True,
                    trigger=str(payload.get("trigger") or "persisted"),
                    reason=str(payload.get("reason") or "persisted force shadow"),
                    ts=float(payload.get("ts") or time.time()),
                    forensic_path=payload.get("forensic_path"),
                )
                if self._config is not None:
                    try:
                        setattr(self._config, "ORACLE_BRAIN_SHADOW", True)
                    except Exception:
                        pass
                logger.warning(
                    "⚠️ AutoRollback: previous forced shadow detected (%s). Oracle pinned to shadow until operator clears %s",
                    self._state.trigger, self.shadow_forced_path,
                )
        except Exception as exc:
            logger.debug("auto_rollback hydrate failed: %s", exc)

    # ──────────────────────────────────────────────────────────────
    def record_shadow_outcome(self, hit: bool) -> None:
        """Record whether a shadow directive turned out correct. Used by
        the online learning / counterfactual pipeline."""
        try:
            self._shadow_outcomes.append(bool(hit))
        except Exception:
            pass

    def record_meta_confidence(self, meta_conf: float) -> None:
        try:
            mc = float(meta_conf)
        except Exception:
            return
        if mc < self.meta_conf_min:
            self._meta_conf_streak_count += 1
        else:
            self._meta_conf_streak_count = 0

    # ──────────────────────────────────────────────────────────────
    def start(self) -> Optional[asyncio.Task]:
        if self._bg_task is not None and not self._bg_task.done():
            return self._bg_task
        if not self.enabled:
            return None
        self._running = True
        self._bg_task = asyncio.create_task(self._loop())
        logger.info("🛡️ AutoRollbackMonitor online (check=%ds)", self.check_interval_sec)
        return self._bg_task

    async def stop(self) -> None:
        self._running = False
        if self._bg_task is not None:
            self._bg_task.cancel()
            try:
                await self._bg_task
            except (asyncio.CancelledError, Exception):
                pass
            self._bg_task = None

    async def _loop(self) -> None:
        while self._running:
            try:
                await self.evaluate_once()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("auto_rollback loop err: %s", e)
            await asyncio.sleep(self.check_interval_sec)

    # ──────────────────────────────────────────────────────────────
    async def evaluate_once(self) -> Optional[RollbackState]:
        """Run one evaluation pass. Returns a RollbackState only when a
        fresh rollback was triggered during this call."""
        self._last_check_ts = time.time()
        if self._state.rolled_back:
            # Already tripped — nothing more to do (reset is manual).
            return None

        trigger, reason, metrics = self._first_trigger()
        if trigger is None:
            return None
        return await self._fire_rollback(trigger, reason, metrics)

    def _first_trigger(self) -> "tuple[Optional[str], Optional[str], Dict[str, Any]]":
        metrics: Dict[str, Any] = {}

        # 6. Manual sentinel (cheapest check first so operator wins.)
        try:
            if self.force_sentinel_path.exists():
                return (TRIGGER_MANUAL, f"sentinel {self.force_sentinel_path} present", metrics)
        except Exception:
            pass

        # 3. Safety Net trip
        sn = self._safety_net
        if sn is not None:
            try:
                tripped = bool(getattr(sn, "tripped", False))
                metrics["safety_net_tripped"] = tripped
                if tripped:
                    reason = str(getattr(sn, "_trip_reason", None) or "safety_net tripped")
                    return (TRIGGER_SAFETY_NET, f"safety_net trip: {reason}", metrics)
            except Exception:
                pass

        # 1. Rejection rate
        gk = self._gatekeeper
        if gk is not None:
            try:
                rate = float(gk.rejection_rate(self.rejection_window_sec))
                metrics["rejection_rate"] = rate
                if rate > self.rejection_rate_threshold:
                    return (
                        TRIGGER_REJECTION,
                        f"rejection rate {rate:.2f} > {self.rejection_rate_threshold:.2f} in {self.rejection_window_sec // 60}m",
                        metrics,
                    )
            except Exception:
                pass

        # 2. Shadow accuracy over last N with realized outcome
        if len(self._shadow_outcomes) >= self.accuracy_window:
            recent = list(self._shadow_outcomes)[-self.accuracy_window:]
            acc = sum(1 for x in recent if x) / len(recent)
            metrics["shadow_accuracy"] = acc
            if acc < self.accuracy_threshold:
                return (
                    TRIGGER_ACCURACY,
                    f"shadow accuracy {acc:.2f} < {self.accuracy_threshold:.2f} over last {self.accuracy_window}",
                    metrics,
                )

        # 4. Qwen meta_confidence streak
        if self._meta_conf_streak_count >= self.meta_conf_streak:
            metrics["meta_conf_streak"] = self._meta_conf_streak_count
            return (
                TRIGGER_META_CONF,
                f"meta_confidence < {self.meta_conf_min:.2f} for {self._meta_conf_streak_count} consecutive directives",
                metrics,
            )

        # 5. Runtime supervisor UNHEALTHY grace
        sup = self._runtime_supervisor
        if sup is not None:
            unhealthy = self._oracle_unhealthy(sup)
            now = time.time()
            if unhealthy:
                if self._unhealthy_since is None:
                    self._unhealthy_since = now
                elapsed = now - self._unhealthy_since
                metrics["runtime_unhealthy_sec"] = elapsed
                if elapsed > self.unhealthy_grace_sec:
                    return (
                        TRIGGER_UNHEALTHY,
                        f"runtime_supervisor oracle UNHEALTHY for {int(elapsed)}s > {self.unhealthy_grace_sec}s",
                        metrics,
                    )
            else:
                self._unhealthy_since = None

        return (None, None, metrics)

    @staticmethod
    def _oracle_unhealthy(sup: Any) -> bool:
        try:
            st = sup.status() if hasattr(sup, "status") else None
            if not isinstance(st, dict):
                return False
            components = st.get("components") or {}
            oracle = components.get("oracle") or components.get("qwen_oracle_brain")
            if not oracle:
                return False
            healthy = oracle.get("healthy") if isinstance(oracle, dict) else None
            return healthy is False
        except Exception:
            return False

    # ──────────────────────────────────────────────────────────────
    async def _fire_rollback(
        self, trigger: str, reason: str, metrics: Dict[str, Any],
    ) -> RollbackState:
        ts = time.time()
        forensic_path = self._write_forensic(ts, trigger, reason, metrics)

        self._state = RollbackState(
            rolled_back=True, trigger=trigger, reason=reason,
            ts=ts, forensic_path=forensic_path,
        )

        # Flip runtime flag and oracle_brain instance (if attached).
        if self._config is not None:
            try:
                setattr(self._config, "ORACLE_BRAIN_SHADOW", True)
            except Exception:
                pass
        if self._oracle_brain is not None and hasattr(self._oracle_brain, "shadow"):
            try:
                self._oracle_brain.shadow = True
            except Exception:
                pass

        # Persist forced-shadow sentinel so restart keeps the lock.
        try:
            self.shadow_forced_path.parent.mkdir(parents=True, exist_ok=True)
            self.shadow_forced_path.write_text(
                json.dumps({
                    "trigger": trigger, "reason": reason, "ts": ts,
                    "metrics": metrics, "forensic_path": forensic_path,
                }, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.error("auto_rollback shadow-forced write failed: %s", e)

        # Emit event (best-effort).
        await self._emit_rollback_event({
            "trigger": trigger, "reason": reason, "ts": ts,
            "metrics": metrics, "forensic_path": forensic_path,
        })

        logger.warning(
            "🚨 ORACLE AUTO-ROLLBACK triggered — %s :: %s (forensic=%s)",
            trigger, reason, forensic_path,
        )
        return self._state

    async def _emit_rollback_event(self, payload: Dict[str, Any]) -> None:
        bus = self._event_bus
        if bus is None:
            return
        try:
            from event_bus import Event, EventType
            maybe = bus.publish(Event(type=EventType.ORACLE_AUTO_ROLLBACK, source="auto_rollback_monitor", data=payload))
            if hasattr(maybe, "__await__"):
                await maybe
        except Exception as e:
            logger.debug("auto_rollback event publish skipped: %s", e)

    def _write_forensic(
        self, ts: float, trigger: str, reason: str, metrics: Dict[str, Any],
    ) -> Optional[str]:
        try:
            self.forensic_dir.mkdir(parents=True, exist_ok=True)
            # forensic bundle: metrics + last 100 gatekeeper rejections + brain recent directives
            bundle: Dict[str, Any] = {
                "ts": ts, "trigger": trigger, "reason": reason, "metrics": metrics,
            }
            gk = self._gatekeeper
            if gk is not None:
                try:
                    bundle["gatekeeper_stats"] = gk.stats()
                    bundle["recent_rejections"] = gk.load_recent_rejections(limit=100)
                except Exception:
                    pass
            brain = self._oracle_brain
            if brain is not None:
                try:
                    bundle["brain_last_directives"] = list(getattr(brain, "_directive_log", []))[-100:]
                    bundle["brain_stats"] = brain.health_check.__wrapped__(brain) if hasattr(brain.health_check, "__wrapped__") else None  # type: ignore
                except Exception:
                    pass
            fname = f".auto_rollback_{int(ts)}.json"
            path = self.forensic_dir / fname
            # Use default=str so dataclasses/deques serialise.
            path.write_text(json.dumps(bundle, indent=2, default=str), encoding="utf-8")
            return str(path)
        except Exception as e:
            logger.debug("auto_rollback forensic write fail: %s", e)
            return None

    # ──────────────────────────────────────────────────────────────
    def force_rollback(self, reason: str = "operator") -> RollbackState:
        """Synchronous operator-triggered rollback. Safe to call from API
        handlers (uses a throwaway event loop if no loop is running)."""
        if self._state.rolled_back:
            return self._state
        import asyncio as _asyncio
        try:
            loop = _asyncio.get_running_loop()
            # Schedule; return state immediately even though event emit is async.
            ts = time.time()
            self._state = RollbackState(
                rolled_back=True, trigger=TRIGGER_MANUAL,
                reason=reason, ts=ts,
            )
            loop.create_task(self._fire_rollback(TRIGGER_MANUAL, reason, {"operator": True}))
            return self._state
        except RuntimeError:
            return _asyncio.run(self._fire_rollback(TRIGGER_MANUAL, reason, {"operator": True}))

    def reset(self, *, operator: str) -> Dict[str, Any]:
        """Operator-only. Clears forced-shadow lock. Does NOT re-enable
        active mode — that is controlled by the env var on restart."""
        payload = {
            "operator": str(operator)[:64],
            "cleared_at": time.time(),
            "previous_state": {
                "trigger": self._state.trigger,
                "reason": self._state.reason,
                "ts": self._state.ts,
            },
        }
        self._state = RollbackState()
        self._meta_conf_streak_count = 0
        self._unhealthy_since = None
        try:
            if self.shadow_forced_path.exists():
                self.shadow_forced_path.unlink()
        except Exception as exc:
            logger.warning("auto_rollback reset: sentinel delete failed: %s", exc)
        try:
            if self.force_sentinel_path.exists():
                self.force_sentinel_path.unlink()
        except Exception:
            pass
        logger.info("🛡️ AutoRollback reset by %s", payload["operator"])
        return payload

    # ──────────────────────────────────────────────────────────────
    def status(self) -> Dict[str, Any]:
        gk = self._gatekeeper
        metrics: Dict[str, Any] = {}
        if gk is not None:
            try:
                metrics["rejection_rate"] = gk.rejection_rate(self.rejection_window_sec)
            except Exception:
                metrics["rejection_rate"] = None
        if len(self._shadow_outcomes) >= self.accuracy_window:
            recent = list(self._shadow_outcomes)[-self.accuracy_window:]
            metrics["shadow_accuracy"] = sum(1 for x in recent if x) / len(recent)
        metrics["meta_conf_streak"] = self._meta_conf_streak_count

        if self._state.rolled_back:
            light = "triggered"
        elif (
            (metrics.get("rejection_rate") or 0.0) > self.rejection_rate_threshold * 0.7
            or (metrics.get("shadow_accuracy") or 1.0) < self.accuracy_threshold * 1.1
            or self._meta_conf_streak_count > self.meta_conf_streak * 0.7
            or self.force_sentinel_path.exists()
        ):
            light = "warning"
        else:
            light = "armed"

        return {
            "enabled": self.enabled,
            "state": {
                "rolled_back": self._state.rolled_back,
                "trigger": self._state.trigger,
                "reason": self._state.reason,
                "ts": self._state.ts,
                "forensic_path": self._state.forensic_path,
            },
            "light": light,
            "metrics": metrics,
            "thresholds": {
                "rejection_rate_threshold": self.rejection_rate_threshold,
                "rejection_window_min": self.rejection_window_sec // 60,
                "accuracy_threshold": self.accuracy_threshold,
                "accuracy_window": self.accuracy_window,
                "meta_conf_min": self.meta_conf_min,
                "meta_conf_streak": self.meta_conf_streak,
                "unhealthy_grace_sec": self.unhealthy_grace_sec,
            },
            "sentinel_exists": self.force_sentinel_path.exists(),
            "shadow_forced_exists": self.shadow_forced_path.exists(),
            "last_check_ts": self._last_check_ts,
        }


# ──────────────────────────────────────────────────────────────────
_instance: Optional[AutoRollbackMonitor] = None


def get_auto_rollback_monitor(**kwargs: Any) -> AutoRollbackMonitor:
    global _instance
    if _instance is None:
        try:
            from config import Config
            defaults = {
                "enabled": Config.AUTO_ROLLBACK_ENABLED,
                "rejection_rate_threshold": Config.AUTO_ROLLBACK_REJECTION_RATE_THRESHOLD,
                "rejection_window_min": Config.AUTO_ROLLBACK_REJECTION_WINDOW_MIN,
                "accuracy_threshold": Config.AUTO_ROLLBACK_ACCURACY_THRESHOLD,
                "accuracy_window": Config.AUTO_ROLLBACK_ACCURACY_WINDOW,
                "meta_conf_min": Config.AUTO_ROLLBACK_META_CONF_MIN,
                "meta_conf_streak": Config.AUTO_ROLLBACK_META_CONF_STREAK,
                "unhealthy_grace_sec": Config.AUTO_ROLLBACK_UNHEALTHY_GRACE_SEC,
                "force_sentinel_path": Config.AUTO_ROLLBACK_FORCE_SENTINEL,
                "shadow_forced_path": Config.AUTO_ROLLBACK_SHADOW_FORCED_PATH,
                "forensic_dir": Config.AUTO_ROLLBACK_FORENSIC_DIR,
                "check_interval_sec": Config.AUTO_ROLLBACK_CHECK_INTERVAL_SEC,
                "config_obj": Config,
            }
        except Exception:
            defaults = {}
        defaults.update(kwargs)
        _instance = AutoRollbackMonitor(**defaults)
    else:
        # Late-bound dependency injection.
        for key in ("gatekeeper", "safety_net", "runtime_supervisor", "oracle_brain", "event_bus"):
            v = kwargs.get(key)
            if v is not None and getattr(_instance, f"_{key}", None) is None:
                setattr(_instance, f"_{key}", v)
    return _instance


def _reset_for_tests() -> None:
    global _instance
    _instance = None
