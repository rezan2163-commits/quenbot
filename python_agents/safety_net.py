"""
Safety Net — Phase 5 Finalization
=====================================
Intel upgrade modullerini canli sistemi bozmadan izleyen devre kesici.
DECISION_ROUTER_ENABLED=True on kosullari:

  1. FastBrain 24 saatlik Brier skoru baseline * BRIER_TOL altinda,
  2. FastBrain 24 saatlik yon dogruluk orani baseline * HITRATE_TOL ustunde,
  3. Confluence skoru gunluk ortalamasi baseline'dan <= DRIFT_SIGMA sapiyor,
  4. FeatureStore yazma hata orani <= FS_FAILURE_TOL.

Uyum saglanmazsa otomatik `trip()` tetiklenir: FAST_BRAIN devre disi,
`SAFETY_NET_TRIPPED` event yayinlanir, sentinel `.safety_net_trip.json`
yazilir (restart sonrasi korunur). Online learning agirlik rotasyonu
`weights_frozen` flag'i uzerinden durur.

Hot-path guvenligi: tum olcum hesaplari arka plan task'inda yapilir.
Event handler'lar sadece hafif kuyruklara ekleme yapar.

Flag: `SAFETY_NET_ENABLED` (default OFF). Import guvenligi igin
`config` / `event_bus` yoksa modul hala yuklenir; sadece aktif islevler
`get_safety_net(...)` cagrildiginda baglanir.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import statistics
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ───────────────── helpers ─────────────────

def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def _now() -> float:
    return time.time()


@dataclass
class _BrierSample:
    ts: float
    p: float            # predicted probability of up
    realized_up: bool   # realized outcome
    hit: bool           # directional correctness


@dataclass
class _ConfluenceSample:
    ts: float
    symbol: str
    score: float


@dataclass
class SafetyNetStatus:
    enabled: bool
    tripped: bool
    trip_reason: Optional[str]
    trip_ts: Optional[float]
    reset_ts: Optional[float]
    baseline: Dict[str, Any]
    rolling: Dict[str, Any]
    last_check_ts: float


class SafetyNet:
    """Intelligence guardian — accuracy / drift / fs health watchdog."""

    WINDOW_HOURS = 24
    MIN_SAMPLES_BEFORE_CHECK = 30
    BASELINE_BOOTSTRAP_DAYS = 3

    def __init__(
        self,
        event_bus: Any = None,
        config: Any = None,
        database: Any = None,
        feature_store: Any = None,
        baseline_path: str = "python_agents/.safety_net_baseline.json",
        trip_sentinel_path: str = "python_agents/.safety_net_trip.json",
        brier_tol: float = 1.25,
        hitrate_tol: float = 0.80,
        degradation_window_min: int = 120,
        drift_sigma: float = 3.0,
        fs_failure_tol: float = 0.05,
        bg_interval_sec: int = 30,
    ) -> None:
        self._event_bus = event_bus
        self._config = config
        self._database = database
        self._feature_store = feature_store

        self.baseline_path = Path(baseline_path)
        self.trip_sentinel_path = Path(trip_sentinel_path)
        self.brier_tol = float(brier_tol)
        self.hitrate_tol = float(hitrate_tol)
        self.degradation_window_sec = int(degradation_window_min) * 60
        self.drift_sigma = float(drift_sigma)
        self.fs_failure_tol = float(fs_failure_tol)
        self.bg_interval_sec = int(bg_interval_sec)

        # FastBrain predictions buffer (24h window)
        self._brier_samples: Deque[_BrierSample] = deque(maxlen=50000)
        # pending predictions awaiting realized outcome
        # keyed by (symbol, ts) → prediction row
        self._pending_predictions: Deque[Dict[str, Any]] = deque(maxlen=20000)

        # Confluence rolling window per-symbol
        self._confluence_window: Dict[str, Deque[_ConfluenceSample]] = defaultdict(
            lambda: deque(maxlen=5000)
        )
        self._drift_start_ts: Optional[float] = None

        # Feature store failure tracking
        self._fs_failure_ratio: float = 0.0
        self._fs_queue_ratio: float = 0.0
        self._fs_degraded_since: Optional[float] = None

        self.baseline: Dict[str, Any] = self._load_baseline()
        self._tripped: bool = False
        self._trip_reason: Optional[str] = None
        self._trip_ts: Optional[float] = None
        self._reset_ts: Optional[float] = None
        self._last_check_ts: float = 0.0

        self._bg_task: Optional[asyncio.Task] = None
        self._degraded_since: Optional[float] = None

        # sentinel rehydration: previous trip survives restarts
        self._load_sentinel()

    # ───────────── public API ─────────────
    def start(self) -> Optional[asyncio.Task]:
        """Background watchdog task'ini baslatir. Idempotent."""
        if self._bg_task is not None and not self._bg_task.done():
            return self._bg_task
        self._subscribe()
        self._bg_task = asyncio.create_task(self._bg_loop())
        logger.info("🛡️ SafetyNet background task online")
        return self._bg_task

    def trip(self, reason: str, metrics: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Manuel veya otomatik trip. Idempotent; ikinci cagri sentinel'i guncel tutar."""
        payload = {
            "reason": str(reason)[:200],
            "metrics": metrics or {},
            "trip_ts": _now(),
            "baseline": self.baseline,
        }
        self._tripped = True
        self._trip_reason = payload["reason"]
        self._trip_ts = payload["trip_ts"]
        self._reset_ts = None
        # runtime fast_brain disable
        if self._config is not None:
            try:
                setattr(self._config, "FAST_BRAIN_ENABLED", False)
            except Exception:
                pass
        self._write_sentinel(payload)
        self._publish_sync("SAFETY_NET_TRIPPED", payload)
        logger.critical("🛡️ SafetyNet TRIPPED: %s", payload["reason"])
        return payload

    def reset(self, operator: str, note: str = "") -> Dict[str, Any]:
        """Operator override — sentinel silinir, state temizlenir."""
        payload = {
            "operator": str(operator)[:64],
            "note": str(note)[:200],
            "reset_ts": _now(),
            "previous_reason": self._trip_reason,
        }
        self._tripped = False
        self._trip_reason = None
        self._trip_ts = None
        self._reset_ts = payload["reset_ts"]
        self._drift_start_ts = None
        self._degraded_since = None
        try:
            if self.trip_sentinel_path.exists():
                self.trip_sentinel_path.unlink()
        except Exception as exc:
            logger.warning("SafetyNet reset: sentinel delete failed: %s", exc)
        self._publish_sync("SAFETY_NET_RESET", payload)
        logger.info("🛡️ SafetyNet RESET by %s", payload["operator"])
        return payload

    def status(self) -> Dict[str, Any]:
        rolling = {
            "brier_samples": len(self._brier_samples),
            "brier_24h": self._rolling_brier(),
            "hitrate_24h": self._rolling_hitrate(),
            "confluence_symbols_tracked": len(self._confluence_window),
            "drift_start_ts": self._drift_start_ts,
            "fs_failure_ratio": self._fs_failure_ratio,
            "fs_queue_ratio": self._fs_queue_ratio,
            "fs_degraded_since": self._fs_degraded_since,
            "degraded_since": self._degraded_since,
        }
        return {
            "enabled": True,
            "healthy": not self._tripped,
            "tripped": self._tripped,
            "trip_reason": self._trip_reason,
            "trip_ts": self._trip_ts,
            "reset_ts": self._reset_ts,
            "baseline": self.baseline,
            "rolling": rolling,
            "last_check_ts": self._last_check_ts,
            "sentinel_exists": self.trip_sentinel_path.exists(),
        }

    def metrics(self) -> Dict[str, Any]:
        """Prometheus-friendly scalar metrikler."""
        return {
            "safety_net_tripped": int(self._tripped),
            "safety_net_brier_24h": _safe_float(self._rolling_brier()),
            "safety_net_hitrate_24h": _safe_float(self._rolling_hitrate()),
            "safety_net_confluence_symbols": len(self._confluence_window),
            "safety_net_fs_failure_ratio": self._fs_failure_ratio,
            "safety_net_fs_queue_ratio": self._fs_queue_ratio,
            "safety_net_brier_samples": len(self._brier_samples),
        }

    # ───────────── event subscription ─────────────
    def _subscribe(self) -> None:
        bus = self._event_bus
        if bus is None:
            return
        try:
            from event_bus import EventType
            subscribe = getattr(bus, "subscribe", None)
            if subscribe is None:
                return
            subscribe(EventType.FAST_BRAIN_PREDICTION, self._on_fast_brain_prediction)
            subscribe(EventType.CONFLUENCE_SCORE, self._on_confluence_score)
        except Exception as exc:
            logger.debug("safety_net subscribe skipped: %s", exc)

    async def _on_fast_brain_prediction(self, event: Any) -> None:
        """Hot path — sadece kuyruga ekle, hesaplama arka planda."""
        try:
            data = _event_data(event)
            if not data:
                return
            symbol = str(data.get("symbol") or "").upper()
            p = data.get("probability")
            ts = float(data.get("ts") or _now())
            if symbol and p is not None:
                self._pending_predictions.append({
                    "symbol": symbol,
                    "p": _safe_float(p),
                    "ts": ts,
                })
        except Exception:
            pass

    async def _on_confluence_score(self, event: Any) -> None:
        try:
            data = _event_data(event)
            if not data:
                return
            symbol = str(data.get("symbol") or "").upper()
            score = data.get("score")
            if not symbol or score is None:
                return
            self._confluence_window[symbol].append(
                _ConfluenceSample(ts=_now(), symbol=symbol, score=_safe_float(score))
            )
        except Exception:
            pass

    # ───────────── background watchdog ─────────────
    async def _bg_loop(self) -> None:
        logger.info("🛡️ safety_net watchdog loop baslatildi (%ds)", self.bg_interval_sec)
        while True:
            try:
                await self._tick()
            except Exception as exc:
                logger.debug("safety_net tick error: %s", exc)
            await asyncio.sleep(self.bg_interval_sec)

    async def _tick(self) -> None:
        self._last_check_ts = _now()
        await self._resolve_pending_predictions()
        self._trim_windows()
        await self._check_feature_store()
        if self._tripped:
            return  # skip further drift/accuracy checks once tripped
        await self._check_accuracy()
        await self._check_drift()
        self._maybe_bootstrap_baseline()

    async def _resolve_pending_predictions(self) -> None:
        """Pending tahminleri price_movements ile eslestirir."""
        if not self._pending_predictions:
            return
        now = _now()
        mature_age = 60 * 60  # 1 saat
        batch: List[Dict[str, Any]] = []
        keep: Deque[Dict[str, Any]] = deque(maxlen=self._pending_predictions.maxlen)
        for row in self._pending_predictions:
            if now - float(row.get("ts", now)) >= mature_age:
                batch.append(row)
            else:
                keep.append(row)
        self._pending_predictions = keep
        if not batch:
            return
        db = self._database
        for row in batch:
            hit, realized_up = await self._resolve_outcome(row, db)
            if hit is None:
                continue
            self._brier_samples.append(
                _BrierSample(
                    ts=float(row["ts"]),
                    p=float(row["p"]),
                    realized_up=bool(realized_up),
                    hit=bool(hit),
                )
            )

    async def _resolve_outcome(
        self, row: Dict[str, Any], db: Any
    ) -> Tuple[Optional[bool], bool]:
        if db is None:
            return None, False
        try:
            symbol = row["symbol"]
            ts = datetime.utcfromtimestamp(float(row["ts"]))
            since = ts - timedelta(minutes=5)
            until = ts + timedelta(minutes=65)
            rows = await db.fetch(
                "SELECT change_pct FROM price_movements"
                " WHERE symbol=$1 AND start_time BETWEEN $2 AND $3"
                " ORDER BY ABS(change_pct) DESC LIMIT 1",
                symbol, since, until,
            )
            if not rows:
                return None, False
            pct = _safe_float(rows[0].get("change_pct"))
            realized_up = pct > 0
            predicted_up = float(row["p"]) >= 0.5
            hit = (realized_up == predicted_up)
            return hit, realized_up
        except Exception as exc:
            logger.debug("safety_net resolve_outcome error: %s", exc)
            return None, False

    def _trim_windows(self) -> None:
        cutoff = _now() - self.WINDOW_HOURS * 3600
        # Brier samples are time-ordered but deque — prune from left
        while self._brier_samples and self._brier_samples[0].ts < cutoff:
            self._brier_samples.popleft()
        # confluence windows
        for sym, dq in list(self._confluence_window.items()):
            while dq and dq[0].ts < cutoff:
                dq.popleft()
            if not dq:
                self._confluence_window.pop(sym, None)

    async def _check_feature_store(self) -> None:
        fs = self._feature_store
        if fs is None:
            return
        try:
            getter = getattr(fs, "health_check", None)
            if getter is None:
                return
            health = getter()
            if asyncio.iscoroutine(health):
                health = await health
            if not isinstance(health, dict):
                return
            total = int(health.get("total_written") or 0) + int(health.get("write_errors") or 0)
            errors = int(health.get("write_errors") or 0)
            self._fs_failure_ratio = (errors / total) if total > 0 else 0.0
            queue = int(health.get("queue_size") or 0)
            qmax = int(health.get("queue_max") or 0)
            self._fs_queue_ratio = (queue / qmax) if qmax > 0 else 0.0
            degraded = (
                self._fs_failure_ratio > self.fs_failure_tol
                or self._fs_queue_ratio > 0.80
            )
            if degraded:
                if self._fs_degraded_since is None:
                    self._fs_degraded_since = _now()
                elif _now() - self._fs_degraded_since >= 600:
                    # 10 dakikadir surekli degraded
                    await self._publish(
                        "SAFETY_NET_FS_DEGRADED",
                        {
                            "failure_ratio": self._fs_failure_ratio,
                            "queue_ratio": self._fs_queue_ratio,
                            "sustained_sec": _now() - self._fs_degraded_since,
                        },
                    )
                    logger.warning(
                        "safety_net: feature_store sustained degradation "
                        "(fail=%.3f, queue=%.2f)",
                        self._fs_failure_ratio,
                        self._fs_queue_ratio,
                    )
            else:
                self._fs_degraded_since = None
        except Exception as exc:
            logger.debug("safety_net fs check error: %s", exc)

    async def _check_accuracy(self) -> None:
        baseline_brier = self.baseline.get("brier")
        baseline_hitrate = self.baseline.get("hitrate")
        if baseline_brier is None or baseline_hitrate is None:
            return  # still bootstrapping baseline
        if len(self._brier_samples) < self.MIN_SAMPLES_BEFORE_CHECK:
            return
        brier = self._rolling_brier()
        hitrate = self._rolling_hitrate()
        degraded = (
            (brier is not None and brier > baseline_brier * self.brier_tol)
            or (hitrate is not None and hitrate < baseline_hitrate * self.hitrate_tol)
        )
        if degraded:
            if self._degraded_since is None:
                self._degraded_since = _now()
            elif _now() - self._degraded_since >= self.degradation_window_sec:
                self.trip(
                    "accuracy_degraded",
                    metrics={
                        "brier": brier,
                        "baseline_brier": baseline_brier,
                        "brier_tol": self.brier_tol,
                        "hitrate": hitrate,
                        "baseline_hitrate": baseline_hitrate,
                        "hitrate_tol": self.hitrate_tol,
                        "sustained_sec": _now() - self._degraded_since,
                    },
                )
        else:
            self._degraded_since = None

    async def _check_drift(self) -> None:
        baseline_conf = self.baseline.get("confluence", {})
        if not baseline_conf:
            return
        means = baseline_conf.get("per_symbol_mean", {})
        stds = baseline_conf.get("per_symbol_std", {})
        if not means:
            return
        drifted_symbols: List[str] = []
        total_symbols = 0
        for sym, dq in self._confluence_window.items():
            if len(dq) < 20:
                continue
            total_symbols += 1
            today_mean = statistics.fmean(s.score for s in dq)
            base_mean = _safe_float(means.get(sym), 0.0)
            base_std = max(_safe_float(stds.get(sym), 1e-6), 1e-6)
            z = abs(today_mean - base_mean) / base_std
            if z >= self.drift_sigma:
                drifted_symbols.append(sym)
        if total_symbols == 0:
            return
        drift_ratio = len(drifted_symbols) / total_symbols
        if drift_ratio >= 0.50:
            if self._drift_start_ts is None:
                self._drift_start_ts = _now()
            elif _now() - self._drift_start_ts >= 30 * 60:
                await self._publish(
                    "SAFETY_NET_DRIFT_ALERT",
                    {
                        "drifted_symbols": drifted_symbols[:20],
                        "total_symbols": total_symbols,
                        "ratio": drift_ratio,
                        "sustained_sec": _now() - self._drift_start_ts,
                    },
                )
                # Note: drift alone does not trip fast_brain; it just freezes
                # weight rotation via online_learning SAFETY_NET_TRIPPED subscriber.
                # Emit TRIPPED with drift reason (partial trip — online_learning freezes).
                self.trip(
                    "confluence_drift",
                    metrics={
                        "drifted_symbols": drifted_symbols[:20],
                        "ratio": drift_ratio,
                    },
                )
        else:
            self._drift_start_ts = None

    # ───────────── rolling stats ─────────────
    def _rolling_brier(self) -> Optional[float]:
        if not self._brier_samples:
            return None
        vals = [((s.p - (1.0 if s.realized_up else 0.0)) ** 2) for s in self._brier_samples]
        return statistics.fmean(vals)

    def _rolling_hitrate(self) -> Optional[float]:
        if not self._brier_samples:
            return None
        return sum(1 for s in self._brier_samples if s.hit) / len(self._brier_samples)

    # ───────────── baseline ─────────────
    def _load_baseline(self) -> Dict[str, Any]:
        try:
            if self.baseline_path.exists():
                return json.loads(self.baseline_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.debug("safety_net baseline load skipped: %s", exc)
        return {}

    def _save_baseline(self) -> None:
        try:
            self.baseline_path.parent.mkdir(parents=True, exist_ok=True)
            self.baseline_path.write_text(json.dumps(self.baseline, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.debug("safety_net baseline save skipped: %s", exc)

    def _maybe_bootstrap_baseline(self) -> None:
        """Ilk 3 gun icinde rolling metriklerden baseline cikar."""
        if self.baseline.get("brier") is not None and self.baseline.get("hitrate") is not None:
            return
        if len(self._brier_samples) < 500:
            return
        brier = self._rolling_brier()
        hitrate = self._rolling_hitrate()
        if brier is None or hitrate is None:
            return
        # 30th percentile equivalent: use mean * 1.10 for brier (worse threshold)
        self.baseline["brier"] = brier * 1.10
        self.baseline["hitrate"] = hitrate * 0.90
        conf: Dict[str, Any] = {"per_symbol_mean": {}, "per_symbol_std": {}}
        for sym, dq in self._confluence_window.items():
            if len(dq) < 50:
                continue
            vals = [s.score for s in dq]
            conf["per_symbol_mean"][sym] = statistics.fmean(vals)
            if len(vals) > 1:
                conf["per_symbol_std"][sym] = max(statistics.pstdev(vals), 1e-6)
        self.baseline["confluence"] = conf
        self.baseline["bootstrapped_at"] = _now()
        self._save_baseline()
        logger.info("🛡️ safety_net baseline bootstrapped: brier=%.4f hitrate=%.3f", brier, hitrate)

    # ───────────── sentinel ─────────────
    def _write_sentinel(self, payload: Dict[str, Any]) -> None:
        try:
            self.trip_sentinel_path.parent.mkdir(parents=True, exist_ok=True)
            self.trip_sentinel_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.error("safety_net sentinel write failed: %s", exc)

    def _load_sentinel(self) -> None:
        try:
            if not self.trip_sentinel_path.exists():
                return
            data = json.loads(self.trip_sentinel_path.read_text(encoding="utf-8"))
            self._tripped = True
            self._trip_reason = str(data.get("reason") or "unknown")
            self._trip_ts = _safe_float(data.get("trip_ts"), _now())
            # enforce runtime disable on boot
            if self._config is not None:
                try:
                    setattr(self._config, "FAST_BRAIN_ENABLED", False)
                except Exception:
                    pass
            logger.warning(
                "🛡️ safety_net sentinel detected on boot: %s (FAST_BRAIN disabled)",
                self._trip_reason,
            )
        except Exception as exc:
            logger.debug("safety_net sentinel load skipped: %s", exc)

    # ───────────── event publish helpers ─────────────
    def _publish_sync(self, name: str, payload: Dict[str, Any]) -> None:
        bus = self._event_bus
        if bus is None:
            return
        try:
            from event_bus import EventType
            publish = getattr(bus, "publish", None)
            if publish is None:
                return
            et = getattr(EventType, name, None)
            if et is None:
                return
            result = publish(et, payload)
            if asyncio.iscoroutine(result):
                # fire-and-forget
                try:
                    asyncio.get_event_loop().create_task(result)
                except Exception:
                    pass
        except Exception as exc:
            logger.debug("safety_net publish_sync skipped: %s", exc)

    async def _publish(self, name: str, payload: Dict[str, Any]) -> None:
        bus = self._event_bus
        if bus is None:
            return
        try:
            from event_bus import EventType
            publish = getattr(bus, "publish", None)
            if publish is None:
                return
            et = getattr(EventType, name, None)
            if et is None:
                return
            result = publish(et, payload)
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:
            logger.debug("safety_net publish skipped: %s", exc)


def _event_data(event: Any) -> Dict[str, Any]:
    """Esnek event payload cikartici."""
    if event is None:
        return {}
    if isinstance(event, dict):
        return event
    data = getattr(event, "data", None)
    if isinstance(data, dict):
        return data
    payload = getattr(event, "payload", None)
    if isinstance(payload, dict):
        return payload
    return {}


# ───────────── singleton ─────────────
_instance: Optional[SafetyNet] = None


def get_safety_net(
    event_bus: Any = None,
    config: Any = None,
    database: Any = None,
    feature_store: Any = None,
    **overrides: Any,
) -> SafetyNet:
    global _instance
    if _instance is None:
        _instance = SafetyNet(
            event_bus=event_bus,
            config=config,
            database=database,
            feature_store=feature_store,
            **overrides,
        )
    return _instance


def _reset_safety_net_for_tests() -> None:
    global _instance
    _instance = None
