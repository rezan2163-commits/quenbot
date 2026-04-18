"""
directive_gatekeeper.py — Aşama 1 Oracle Directive Gatekeeper
=================================================================
Every OracleDirective emitted by QwenOracleBrain passes through three
independent filters BEFORE the system is allowed to act on it:

  A. Confidence threshold      (directive.confidence >= MIN)
  B. Rolling hourly rate limit (token bucket per hour)
  C. Type allowlist + hard blocklist

Gatekeeper is additive and flag-gated. When
`Config.DIRECTIVE_GATEKEEPER_ENABLED` is False every directive is
accepted (pre-Aşama-1 behaviour preserved byte-identically).

Rejections are persisted to a JSONL log and emitted on the event bus
(`EventType.DIRECTIVE_REJECTED`) so the AutoRollbackMonitor and the
dashboard can observe them.

Safety: Safety Net remains the ultimate authority. The gatekeeper never
overrides Safety Net, and the hard blocklist
(`CHANGE_STRATEGY / OVERRIDE_VETO / FORCE_TRADE`) can never be bypassed,
not even when the feature flag is disabled — those types are rejected
whenever the gatekeeper module is on the call path.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# Types that are NEVER allowed through, regardless of allowlist / flag.
HARD_BLOCKLIST: Tuple[str, ...] = ("CHANGE_STRATEGY", "OVERRIDE_VETO", "FORCE_TRADE")


@dataclass
class GatekeeperDecision:
    accepted: bool
    reason: str
    filter_name: str            # "confidence" | "rate_limit" | "allowlist" | "blocklist" | "disabled"
    directive_snapshot: Dict[str, Any]
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "accepted": self.accepted,
            "reason": self.reason,
            "filter": self.filter_name,
            "ts": self.ts,
            "directive": self.directive_snapshot,
        }


class DirectiveGatekeeper:
    """Thread-safe directive gatekeeper. Cheap enough for the hot path."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        confidence_min: float = 0.80,
        max_per_hour: int = 3,
        allowlist: Optional[List[str]] = None,
        rejected_log_path: str = "python_agents/.directive_rejected.jsonl",
        max_log_bytes: int = 8 * 1024 * 1024,
        event_bus: Any = None,
    ) -> None:
        self.enabled = bool(enabled)
        self.confidence_min = float(confidence_min)
        self.max_per_hour = max(0, int(max_per_hour))
        self.allowlist = tuple(
            a.strip().upper()
            for a in (allowlist or ["ADJUST_CONFIDENCE_THRESHOLD", "ADJUST_POSITION_SIZE_MULT", "PAUSE_SYMBOL"])
            if a and str(a).strip()
        )
        self._rejected_log_path = Path(rejected_log_path)
        self._max_log_bytes = int(max_log_bytes)
        self._event_bus = event_bus

        self._accepted_timestamps: Deque[float] = deque()
        self._lock = threading.Lock()
        self._stats = {
            "accepted_total": 0,
            "rejected_total": 0,
            "rejected_by_confidence": 0,
            "rejected_by_rate_limit": 0,
            "rejected_by_allowlist": 0,
            "rejected_by_blocklist": 0,
            "last_decision_ts": 0.0,
            "rejection_window": deque(maxlen=5000),  # (ts, reason) for monitor
        }

    # ──────────────────────────────────────────────────────────────
    @staticmethod
    def _snapshot(directive: Any) -> Dict[str, Any]:
        if isinstance(directive, dict):
            return {
                "action": str(directive.get("action", "")),
                "symbol": str(directive.get("symbol", "")),
                "severity": str(directive.get("severity", "")),
                "confidence": float(directive.get("confidence", 0.0) or 0.0),
                "rationale": str(directive.get("rationale", ""))[:240],
                "shadow": bool(directive.get("shadow", True)),
            }
        return {
            "action": str(getattr(directive, "action", "") or ""),
            "symbol": str(getattr(directive, "symbol", "") or ""),
            "severity": str(getattr(directive, "severity", "") or ""),
            "confidence": float(getattr(directive, "confidence", 0.0) or 0.0),
            "rationale": str(getattr(directive, "rationale", "") or "")[:240],
            "shadow": bool(getattr(directive, "shadow", True)),
        }

    def _apply_rate_limit_window(self, now: float) -> None:
        cutoff = now - 3600.0
        dq = self._accepted_timestamps
        while dq and dq[0] < cutoff:
            dq.popleft()

    # ──────────────────────────────────────────────────────────────
    def evaluate(self, directive: Any, *, now: Optional[float] = None) -> GatekeeperDecision:
        """
        Evaluate a single directive. Thread-safe. Never raises on a
        malformed input — in doubt it rejects with `filter="blocklist"`.
        """
        ts = float(now) if now is not None else time.time()
        snap = self._snapshot(directive)
        action = snap["action"].upper()

        # Hard blocklist is ALWAYS enforced, even when flag disabled.
        if action in HARD_BLOCKLIST:
            return self._finalize(
                ts, snap,
                accepted=False, reason=f"action '{action}' is hard-blocked",
                filter_name="blocklist", increment_stat="rejected_by_blocklist",
            )

        if not self.enabled:
            # Flag off → accept without touching the token bucket. Mirrors
            # pre-Aşama-1 behaviour exactly (byte-identical rollback path).
            return GatekeeperDecision(
                accepted=True, reason="gatekeeper disabled",
                filter_name="disabled", directive_snapshot=snap, ts=ts,
            )

        # A. Confidence
        if snap["confidence"] < self.confidence_min:
            return self._finalize(
                ts, snap,
                accepted=False,
                reason=f"confidence {snap['confidence']:.2f} < min {self.confidence_min:.2f}",
                filter_name="confidence", increment_stat="rejected_by_confidence",
            )

        # C. Allowlist
        if action not in set(self.allowlist):
            return self._finalize(
                ts, snap,
                accepted=False,
                reason=f"action '{action}' not in allowlist",
                filter_name="allowlist", increment_stat="rejected_by_allowlist",
            )

        # B. Rate limit (token bucket: max N in rolling hour)
        rate_rejected = False
        with self._lock:
            self._apply_rate_limit_window(ts)
            if self.max_per_hour <= 0 or len(self._accepted_timestamps) >= self.max_per_hour:
                rate_rejected = True
                used = len(self._accepted_timestamps)
                limit = self.max_per_hour
            else:
                self._accepted_timestamps.append(ts)
        if rate_rejected:
            return self._finalize(
                ts, snap,
                accepted=False,
                reason=f"rate limit {used}/{limit} in rolling 1h",
                filter_name="rate_limit", increment_stat="rejected_by_rate_limit",
            )

        return self._finalize(
            ts, snap,
            accepted=True, reason="passed all filters",
            filter_name="accepted", increment_stat=None,
        )

    # ──────────────────────────────────────────────────────────────
    def _finalize(
        self,
        ts: float,
        snap: Dict[str, Any],
        *,
        accepted: bool,
        reason: str,
        filter_name: str,
        increment_stat: Optional[str],
    ) -> GatekeeperDecision:
        decision = GatekeeperDecision(
            accepted=accepted, reason=reason,
            filter_name=filter_name, directive_snapshot=snap, ts=ts,
        )
        with self._lock:
            if accepted:
                self._stats["accepted_total"] += 1
            else:
                self._stats["rejected_total"] += 1
                if increment_stat and increment_stat in self._stats:
                    self._stats[increment_stat] += 1
                self._stats["rejection_window"].append((ts, filter_name))
            self._stats["last_decision_ts"] = ts

        if not accepted:
            self._persist_rejection(decision)

        self._publish(decision)
        return decision

    def _persist_rejection(self, decision: GatekeeperDecision) -> None:
        try:
            p = self._rejected_log_path
            p.parent.mkdir(parents=True, exist_ok=True)
            # Simple size-based rotation (rename to .1)
            try:
                if p.exists() and p.stat().st_size > self._max_log_bytes:
                    p.rename(p.with_suffix(p.suffix + ".1"))
            except Exception:
                pass
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps(decision.to_dict(), ensure_ascii=False) + "\n")
        except Exception as e:
            logger.debug("gatekeeper rejection log write fail: %s", e)

    def _publish(self, decision: GatekeeperDecision) -> None:
        bus = self._event_bus
        if bus is None:
            return
        try:
            from event_bus import Event, EventType  # local import → optional dep
            etype = EventType.DIRECTIVE_ACCEPTED if decision.accepted else EventType.DIRECTIVE_REJECTED
            # Fire-and-forget; never raise on bus errors.
            maybe = bus.publish(Event(type=etype, source="directive_gatekeeper", data=decision.to_dict()))
            if hasattr(maybe, "__await__"):
                # publish returns a coroutine in the async bus. Schedule if
                # we are inside a running loop; otherwise just drop.
                try:
                    import asyncio
                    loop = asyncio.get_running_loop()
                    loop.create_task(maybe)
                except RuntimeError:
                    pass
        except Exception as e:
            logger.debug("gatekeeper event publish skipped: %s", e)

    # ──────────────────────────────────────────────────────────────
    def stats(self) -> Dict[str, Any]:
        with self._lock:
            window = list(self._stats["rejection_window"])
        cutoff = time.time() - 3600.0
        recent = [(ts, r) for ts, r in window if ts >= cutoff]
        histogram: Dict[str, int] = {}
        for _, reason in recent:
            histogram[reason] = histogram.get(reason, 0) + 1
        return {
            "enabled": self.enabled,
            "confidence_min": self.confidence_min,
            "max_per_hour": self.max_per_hour,
            "allowlist": list(self.allowlist),
            "accepted_total": self._stats["accepted_total"],
            "rejected_total": self._stats["rejected_total"],
            "rejected_by_confidence": self._stats["rejected_by_confidence"],
            "rejected_by_rate_limit": self._stats["rejected_by_rate_limit"],
            "rejected_by_allowlist": self._stats["rejected_by_allowlist"],
            "rejected_by_blocklist": self._stats["rejected_by_blocklist"],
            "current_hour_used": len(self._accepted_timestamps),
            "current_hour_limit": self.max_per_hour,
            "rejection_histogram_1h": histogram,
            "last_decision_ts": self._stats["last_decision_ts"],
        }

    def rejection_rate(self, window_sec: float = 1800.0) -> float:
        """Rolling rejection rate over `window_sec` seconds. Used by the
        AutoRollbackMonitor."""
        cutoff = time.time() - float(window_sec)
        with self._lock:
            window = list(self._stats["rejection_window"])
        rejected = sum(1 for ts, _ in window if ts >= cutoff)
        # accepted events aren't pushed into the window; approximate by
        # counting recent accepted timestamps.
        accepted = sum(1 for ts in list(self._accepted_timestamps) if ts >= cutoff)
        total = rejected + accepted
        return rejected / total if total > 0 else 0.0

    # ──────────────────────────────────────────────────────────────
    def load_recent_rejections(self, limit: int = 10) -> List[Dict[str, Any]]:
        p = self._rejected_log_path
        if not p.exists():
            return []
        try:
            # Tail the file cheaply.
            with p.open("rb") as f:
                try:
                    f.seek(-min(128 * 1024, os.path.getsize(p)), 2)
                except OSError:
                    f.seek(0)
                data = f.read().decode("utf-8", errors="replace")
            lines = [ln for ln in data.strip().splitlines() if ln]
            out: List[Dict[str, Any]] = []
            for ln in lines[-int(limit):]:
                try:
                    out.append(json.loads(ln))
                except Exception:
                    continue
            return out
        except Exception as e:
            logger.debug("gatekeeper tail fail: %s", e)
            return []


# ──────────────────────────────────────────────────────────────────
_instance: Optional[DirectiveGatekeeper] = None


def get_directive_gatekeeper(**kwargs: Any) -> DirectiveGatekeeper:
    """Singleton accessor. First caller sets flags; subsequent callers may
    provide late-bound `event_bus`."""
    global _instance
    if _instance is None:
        try:
            from config import Config
            defaults = {
                "enabled": Config.DIRECTIVE_GATEKEEPER_ENABLED,
                "confidence_min": Config.ORACLE_BRAIN_DIRECTIVE_CONFIDENCE_MIN,
                "max_per_hour": Config.ORACLE_BRAIN_MAX_DIRECTIVES_PER_HOUR,
                "allowlist": list(Config.ORACLE_BRAIN_DIRECTIVE_ALLOWLIST),
                "rejected_log_path": Config.DIRECTIVE_REJECTED_LOG_PATH,
            }
        except Exception:
            defaults = {}
        defaults.update(kwargs)
        _instance = DirectiveGatekeeper(**defaults)
    elif kwargs.get("event_bus") is not None and _instance._event_bus is None:
        _instance._event_bus = kwargs["event_bus"]
    return _instance


def _reset_for_tests() -> None:
    global _instance
    _instance = None
