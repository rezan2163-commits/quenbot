"""
directive_impact_tracker.py — Aşama 2 Directive Impact Feedback
=================================================================
Tracks each live Oracle directive's downstream effect over a rolling
window and writes ``impact_score`` back to ``oracle_directives``.

Measurement protocol
--------------------
When a directive ``d`` is issued at time ``t``:

  baseline window = [t - BASELINE_WINDOW_SEC, t]
  measure  window = [t,                        t + MEASURE_WINDOW_SEC]

Per directive type, we aggregate a single scalar metric over each
window (signal count, win rate, realized pnl, TP/SL ratio, …) and
compute ``impact_score = clip((after - before) / scale, -1, +1)``.

Types measured (Aşama 2 allowlist):
  - ``ADJUST_CONFIDENCE_THRESHOLD``   — Δ(signal count · win rate)
  - ``ADJUST_POSITION_SIZE_MULT``     — Δ(average realized pnl)
  - ``PAUSE_SYMBOL``                  — verification: count of NEW
    signals for the symbol during measure window (lower is better →
    negated so impact_score ≥ 0 when pause held)
  - ``RESUME_SYMBOL``                 — verification: signals resumed
  - ``CHANGE_STRATEGY_WEIGHT``        — Δ(confluence score mean)
  - ``ADJUST_TP_SL_RATIO``            — Δ(TP hit rate − SL hit rate)

Data sources are pluggable: the tracker accepts a ``data_provider`` with
``fetch_signals(symbol, start_ts, end_ts)`` returning a list of dicts
(``win`` bool, ``pnl`` float, ``confluence`` float, ``tp_hit`` bool,
``sl_hit`` bool).  When no provider is attached, the tracker falls back
to an in-memory buffer populated by ``record_signal()``.

All code paths are flag-gated via
``Config.DIRECTIVE_IMPACT_TRACKER_ENABLED``.  A disabled tracker silently
records pending directives but never measures — safe to leave attached.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


DIRECTIVE_TYPE_METRICS = {
    "ADJUST_CONFIDENCE_THRESHOLD": "signal_quality",
    "ADJUST_POSITION_SIZE_MULT":   "avg_pnl",
    "PAUSE_SYMBOL":                "pause_verification",
    "RESUME_SYMBOL":               "resume_verification",
    "CHANGE_STRATEGY_WEIGHT":      "confluence_mean",
    "ADJUST_TP_SL_RATIO":          "tp_sl_delta",
}


@dataclass
class PendingDirective:
    directive_id: str
    directive_type: str
    symbol: str
    issued_ts: float
    baseline: Dict[str, float] = field(default_factory=dict)
    synthetic: bool = False
    source_tag: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ImpactMeasurement:
    directive_id: str
    directive_type: str
    symbol: str
    issued_ts: float
    measured_at_ts: float
    impact_score: float
    metric_name: str
    before: float
    after: float
    synthetic: bool = False
    source_tag: Optional[str] = None


def _clip(v: float, lo: float = -1.0, hi: float = 1.0) -> float:
    if math.isnan(v) or math.isinf(v):
        return 0.0
    return max(lo, min(hi, v))


class DirectiveImpactTracker:
    """Tracks impact for live (and optionally synthetic) directives."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        baseline_window_sec: int = 3600,
        measure_window_sec: int = 14400,
        check_interval_sec: int = 60,
        data_provider: Any = None,
        db_writer: Any = None,
        event_bus: Any = None,
        cache_path: Optional[str] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self.enabled = bool(enabled)
        self.baseline_window_sec = int(baseline_window_sec)
        self.measure_window_sec = int(measure_window_sec)
        self.check_interval_sec = max(1, int(check_interval_sec))
        self._provider = data_provider
        self._db = db_writer
        self._event_bus = event_bus
        self._now = clock or time.time
        self.cache_path = Path(cache_path) if cache_path else None

        self._pending: Dict[str, PendingDirective] = {}
        self._signals_by_symbol: Dict[str, Deque[Dict[str, Any]]] = {}
        self._measurements: Deque[ImpactMeasurement] = deque(maxlen=2000)
        self._bg_task: Optional[asyncio.Task] = None
        self._running = False

    # ──────────────────────────────────────────────────────────────
    def record_signal(self, symbol: str, *, ts: Optional[float] = None, **fields: Any) -> None:
        """Fallback in-memory signal recorder. `fields` may include
        `win: bool`, `pnl: float`, `confluence: float`, `tp_hit: bool`,
        `sl_hit: bool`. Ignored when a data_provider is attached."""
        t = float(ts if ts is not None else self._now())
        buf = self._signals_by_symbol.setdefault(symbol, deque(maxlen=5000))
        buf.append({"ts": t, **fields})

    # ──────────────────────────────────────────────────────────────
    async def register_directive(
        self,
        directive: Any,
        *,
        synthetic: bool = False,
        source_tag: Optional[str] = None,
    ) -> Optional[PendingDirective]:
        """Record baseline metrics at directive issuance and enqueue for
        measurement after ``measure_window_sec``. Returns ``None`` if the
        directive type is not tracked."""
        if not self.enabled:
            return None
        dtype = getattr(directive, "action", None) or getattr(directive, "directive_type", None)
        if dtype not in DIRECTIVE_TYPE_METRICS:
            return None
        did = str(getattr(directive, "directive_id", None) or getattr(directive, "id", None) or f"did_{int(time.time()*1000)}")
        symbol = str(getattr(directive, "symbol", None) or "UNKNOWN")
        ts = float(getattr(directive, "ts", None) or getattr(directive, "issued_ts", None) or self._now())
        params = dict(getattr(directive, "params", None) or {})
        baseline = await self._aggregate(symbol, ts - self.baseline_window_sec, ts, dtype)
        pending = PendingDirective(
            directive_id=did, directive_type=dtype, symbol=symbol, issued_ts=ts,
            baseline=baseline, synthetic=bool(synthetic), source_tag=source_tag, params=params,
        )
        self._pending[did] = pending
        return pending

    # ──────────────────────────────────────────────────────────────
    async def measure_ready(self, *, now: Optional[float] = None) -> List[ImpactMeasurement]:
        """Measure any pending directive whose measure window has elapsed."""
        if not self.enabled or not self._pending:
            return []
        t = float(now if now is not None else self._now())
        results: List[ImpactMeasurement] = []
        ready_ids = [
            did for did, p in self._pending.items()
            if t - p.issued_ts >= self.measure_window_sec
        ]
        for did in ready_ids:
            p = self._pending.pop(did)
            try:
                after = await self._aggregate(
                    p.symbol, p.issued_ts, p.issued_ts + self.measure_window_sec, p.directive_type,
                )
                impact = self._score(p.directive_type, p.baseline, after, p.params)
                meas = ImpactMeasurement(
                    directive_id=p.directive_id, directive_type=p.directive_type,
                    symbol=p.symbol, issued_ts=p.issued_ts, measured_at_ts=t,
                    impact_score=impact, metric_name=DIRECTIVE_TYPE_METRICS[p.directive_type],
                    before=float(list(p.baseline.values())[0] if p.baseline else 0.0),
                    after=float(list(after.values())[0] if after else 0.0),
                    synthetic=p.synthetic, source_tag=p.source_tag,
                )
                self._measurements.append(meas)
                await self._persist(meas)
                await self._emit(meas)
                results.append(meas)
            except Exception as exc:
                logger.debug("impact measure fail %s: %s", did, exc)
        if self.cache_path is not None:
            try:
                self._write_cache()
            except Exception:
                pass
        return results

    # ──────────────────────────────────────────────────────────────
    async def measure_synthetic(
        self,
        directive: Any,
        *,
        baseline: Dict[str, float],
        after: Dict[str, float],
        source_tag: str = "aşama2_backfill",
    ) -> ImpactMeasurement:
        """Direct measurement path used by the historical backfill
        script. Does not require the measure-window wait."""
        dtype = getattr(directive, "action", None) or getattr(directive, "directive_type", None) or "UNKNOWN"
        did = str(getattr(directive, "directive_id", None) or f"syn_{int(time.time()*1000)}")
        symbol = str(getattr(directive, "symbol", None) or "UNKNOWN")
        ts = float(getattr(directive, "ts", None) or self._now())
        impact = self._score(dtype, baseline, after, dict(getattr(directive, "params", None) or {}))
        meas = ImpactMeasurement(
            directive_id=did, directive_type=dtype, symbol=symbol,
            issued_ts=ts, measured_at_ts=self._now(), impact_score=impact,
            metric_name=DIRECTIVE_TYPE_METRICS.get(dtype, "synthetic"),
            before=float(list(baseline.values())[0] if baseline else 0.0),
            after=float(list(after.values())[0] if after else 0.0),
            synthetic=True, source_tag=source_tag,
        )
        self._measurements.append(meas)
        return meas

    # ──────────────────────────────────────────────────────────────
    def _score(
        self,
        dtype: str,
        before: Dict[str, float],
        after: Dict[str, float],
        params: Dict[str, Any],
    ) -> float:
        metric = DIRECTIVE_TYPE_METRICS.get(dtype, "generic")
        b = float(before.get(metric, 0.0))
        a = float(after.get(metric, 0.0))
        if metric == "signal_quality":
            scale = max(abs(b), 1e-3)
            return _clip((a - b) / scale)
        if metric == "avg_pnl":
            scale = max(abs(b), 0.005)
            return _clip((a - b) / scale)
        if metric == "pause_verification":
            # Lower new-signal count during pause = positive impact.
            new_signals = float(after.get("signal_count", a))
            return _clip(1.0 - min(new_signals / 5.0, 2.0))
        if metric == "resume_verification":
            new_signals = float(after.get("signal_count", a))
            return _clip(min(new_signals / 3.0, 1.0))
        if metric == "confluence_mean":
            return _clip((a - b) / max(abs(b), 0.05))
        if metric == "tp_sl_delta":
            return _clip(a - b)  # already a delta in [-1, 1]
        return _clip((a - b) / max(abs(b), 1e-3))

    # ──────────────────────────────────────────────────────────────
    async def _aggregate(
        self, symbol: str, start_ts: float, end_ts: float, dtype: str,
    ) -> Dict[str, float]:
        metric = DIRECTIVE_TYPE_METRICS.get(dtype, "generic")
        rows = await self._fetch_signals(symbol, start_ts, end_ts)
        if not rows:
            return {metric: 0.0, "signal_count": 0.0}
        n = len(rows)
        wins = sum(1 for r in rows if bool(r.get("win")))
        pnls = [float(r.get("pnl") or 0.0) for r in rows]
        confs = [float(r.get("confluence") or 0.0) for r in rows]
        tp_hits = sum(1 for r in rows if bool(r.get("tp_hit")))
        sl_hits = sum(1 for r in rows if bool(r.get("sl_hit")))
        win_rate = wins / n if n else 0.0
        tp_rate = tp_hits / n if n else 0.0
        sl_rate = sl_hits / n if n else 0.0
        out = {
            "signal_count": float(n),
            "signal_quality": float(n * win_rate),
            "avg_pnl": sum(pnls) / n if n else 0.0,
            "pause_verification": float(n),
            "resume_verification": float(n),
            "confluence_mean": sum(confs) / n if n else 0.0,
            "tp_sl_delta": tp_rate - sl_rate,
        }
        # Always include the primary metric key for the scorer.
        out.setdefault(metric, out.get(metric, 0.0))
        return out

    async def _fetch_signals(self, symbol: str, start_ts: float, end_ts: float) -> List[Dict[str, Any]]:
        provider = self._provider
        if provider is not None:
            try:
                res = provider.fetch_signals(symbol, start_ts, end_ts)
                if asyncio.iscoroutine(res):
                    res = await res
                return list(res or [])
            except Exception as exc:
                logger.debug("impact provider err: %s", exc)
        buf = self._signals_by_symbol.get(symbol, ())
        return [r for r in buf if start_ts <= float(r.get("ts", 0.0)) < end_ts]

    # ──────────────────────────────────────────────────────────────
    async def _persist(self, meas: ImpactMeasurement) -> None:
        db = self._db
        if db is None:
            return
        try:
            fn = getattr(db, "update_directive_impact", None)
            if fn is None:
                return
            res = fn(
                directive_id=meas.directive_id,
                impact_score=meas.impact_score,
                impact_measured_at=meas.measured_at_ts,
            )
            if asyncio.iscoroutine(res):
                await res
        except Exception as exc:
            logger.debug("impact persist skip: %s", exc)

    async def _emit(self, meas: ImpactMeasurement) -> None:
        bus = self._event_bus
        if bus is None:
            return
        try:
            from event_bus import Event, EventType
            maybe = bus.publish(Event(
                type=EventType.DIRECTIVE_IMPACT_MEASURED,
                source="directive_impact_tracker",
                data={
                    "directive_id": meas.directive_id,
                    "directive_type": meas.directive_type,
                    "symbol": meas.symbol,
                    "impact_score": meas.impact_score,
                    "synthetic": meas.synthetic,
                    "source_tag": meas.source_tag,
                    "metric_name": meas.metric_name,
                },
            ))
            if hasattr(maybe, "__await__"):
                await maybe
        except Exception as exc:
            logger.debug("impact event skip: %s", exc)

    def _write_cache(self) -> None:
        if self.cache_path is None:
            return
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "measurements": [
                    {
                        "directive_id": m.directive_id,
                        "directive_type": m.directive_type,
                        "symbol": m.symbol,
                        "issued_ts": m.issued_ts,
                        "measured_at_ts": m.measured_at_ts,
                        "impact_score": m.impact_score,
                        "metric_name": m.metric_name,
                        "before": m.before,
                        "after": m.after,
                        "synthetic": m.synthetic,
                        "source_tag": m.source_tag,
                    }
                    for m in list(self._measurements)[-500:]
                ],
                "pending": len(self._pending),
                "updated_at": self._now(),
            }
            self.cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.debug("impact cache write: %s", exc)

    # ──────────────────────────────────────────────────────────────
    # Read helpers consumed by Qwen prompt / auto-rollback / API.
    def recent(self, n: int = 50, *, synthetic: Optional[bool] = None) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for m in reversed(list(self._measurements)):
            if synthetic is not None and m.synthetic is not synthetic:
                continue
            out.append({
                "directive_id": m.directive_id,
                "directive_type": m.directive_type,
                "symbol": m.symbol,
                "issued_ts": m.issued_ts,
                "measured_at_ts": m.measured_at_ts,
                "impact_score": m.impact_score,
                "metric_name": m.metric_name,
                "synthetic": m.synthetic,
                "source_tag": m.source_tag,
            })
            if len(out) >= n:
                break
        return out

    def aggregate_by_type(self) -> Dict[str, Dict[str, float]]:
        buckets: Dict[str, Dict[str, List[float]]] = {}
        for m in self._measurements:
            b = buckets.setdefault(m.directive_type, {"live": [], "synthetic": []})
            (b["synthetic"] if m.synthetic else b["live"]).append(m.impact_score)
        out: Dict[str, Dict[str, float]] = {}
        for dtype, b in buckets.items():
            out[dtype] = {
                "live_count": float(len(b["live"])),
                "live_mean": sum(b["live"]) / len(b["live"]) if b["live"] else 0.0,
                "synthetic_count": float(len(b["synthetic"])),
                "synthetic_mean": sum(b["synthetic"]) / len(b["synthetic"]) if b["synthetic"] else 0.0,
            }
        return out

    def rolling_mean_impact(self, hours: int = 24, *, synthetic: bool = False) -> Optional[float]:
        cutoff = self._now() - hours * 3600
        vals = [
            m.impact_score for m in self._measurements
            if m.measured_at_ts >= cutoff and m.synthetic is synthetic
        ]
        if not vals:
            return None
        return sum(vals) / len(vals)

    def synthetic_baseline(self) -> Dict[str, float]:
        """Mean + std of synthetic measurements. Used by safety-net
        regression guard."""
        vals = [m.impact_score for m in self._measurements if m.synthetic]
        if not vals:
            return {"count": 0.0, "mean": 0.0, "std": 1.0}
        n = len(vals)
        mean = sum(vals) / n
        var = sum((v - mean) ** 2 for v in vals) / n
        std = math.sqrt(var) if var > 0 else 1e-6
        return {"count": float(n), "mean": mean, "std": std}

    # ──────────────────────────────────────────────────────────────
    def start(self) -> Optional[asyncio.Task]:
        if self._bg_task is not None and not self._bg_task.done():
            return self._bg_task
        if not self.enabled:
            return None
        self._running = True
        self._bg_task = asyncio.create_task(self._loop())
        logger.info("📈 DirectiveImpactTracker online (baseline=%ds measure=%ds)",
                    self.baseline_window_sec, self.measure_window_sec)
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
                await self.measure_ready()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("impact loop err: %s", exc)
            await asyncio.sleep(self.check_interval_sec)

    # ──────────────────────────────────────────────────────────────
    def stats(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "pending_count": len(self._pending),
            "measurements_count": len(self._measurements),
            "by_type": self.aggregate_by_type(),
            "rolling_24h_live": self.rolling_mean_impact(24, synthetic=False),
            "rolling_24h_synthetic": self.rolling_mean_impact(24, synthetic=True),
        }


# ──────────────────────────────────────────────────────────────────
_instance: Optional[DirectiveImpactTracker] = None


def get_directive_impact_tracker(**kwargs: Any) -> DirectiveImpactTracker:
    global _instance
    if _instance is None:
        try:
            from config import Config
            defaults = {
                "enabled": getattr(Config, "DIRECTIVE_IMPACT_TRACKER_ENABLED", True),
                "baseline_window_sec": getattr(Config, "DIRECTIVE_IMPACT_BASELINE_WINDOW_SEC", 3600),
                "measure_window_sec": getattr(Config, "DIRECTIVE_IMPACT_MEASURE_WINDOW_SEC", 14400),
                "check_interval_sec": getattr(Config, "DIRECTIVE_IMPACT_CHECK_INTERVAL_SEC", 60),
                "cache_path": getattr(Config, "DIRECTIVE_IMPACT_CACHE_PATH", None),
            }
        except Exception:
            defaults = {}
        defaults.update(kwargs)
        _instance = DirectiveImpactTracker(**defaults)
    else:
        attr_map = {"data_provider": "_provider", "db_writer": "_db", "event_bus": "_event_bus"}
        for key, attr in attr_map.items():
            v = kwargs.get(key)
            if v is not None and getattr(_instance, attr, None) is None:
                setattr(_instance, attr, v)
    return _instance


def _reset_for_tests() -> None:
    global _instance
    _instance = None
