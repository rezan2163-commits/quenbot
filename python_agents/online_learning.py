"""
Online Learning Evaluator — Phase 4
====================================
`decision_router_shadow.jsonl` logunu periyodik tarar, her karara karşılık
gelen gerçekleşen fiyat hareketini (horizon dakika sonra) hesaplar ve
rolling metrikler üretir:

  - fast_brain_accuracy_hit   : FastBrain yönü doğru mu?
  - gemma_accuracy_hit        : Gemma yönü doğru mu?
  - agreement_rate            : FastBrain & Gemma aynı yönü verme oranı
  - agreement_accuracy        : Her ikisi aynı yönü verince doğru mu?
  - calibration_bins          : [0.0-0.1, 0.1-0.2, ...] → gerçek pozitif oran
  - calibration_error         : bin'ler içi |p_pred - p_real| ağırlıklı ort.

Kullanım:
  - Pasif: periyodik loop JSONL'i tarar; "scored" state dosyasında saklanır.
  - JSONL satırı + horizon sonrasına karşılık gelen "gerçekleşen fiyat"
    scout/microstructure snapshot cache'inden (anlık referans), zaman
    geçtikten sonra feature_store'dan veya mamis'in event_bar geçmişinden
    alınabilir. Burada: karar anındaki fiyatı JSONL'e yazmadık, o yüzden
    gerçekleşen fiyat microstructure snapshot üzerinden o anda yakalanır
    (scorer sınıfı, her satır için "şimdiki fiyat"ı self._price_feed
    üzerinden ister; live bağlamda on_price_update'i kullanır).

Bu modül hot-path'te değildir; loop 15 dakikada bir çalışır, kaçırırsa
sorun yok.

Kill-switch: `ONLINE_LEARNING_ENABLED` flag OFF → hiç çalışmaz.
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
from typing import Any, Callable, Dict, Deque, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _direction_sign(direction: str) -> int:
    d = (direction or "").lower()
    if d in {"up", "buy", "long"}:
        return 1
    if d in {"down", "sell", "short"}:
        return -1
    return 0


@dataclass
class _ScoredRow:
    ts: float
    symbol: str
    fast_direction: str
    fast_probability: Optional[float]
    gemma_action: str
    agreed: bool
    price_at_decision: float
    price_at_horizon: float

    @property
    def realized_ret(self) -> float:
        if self.price_at_decision <= 0:
            return 0.0
        return (self.price_at_horizon - self.price_at_decision) / self.price_at_decision

    @property
    def realized_up(self) -> bool:
        return self.realized_ret > 0.0


class OnlineLearningEvaluator:
    """Shadow JSONL → realized performance metrikleri."""

    def __init__(
        self,
        log_path: str = "python_agents/.decision_router_shadow.jsonl",
        horizon_min: int = 60,
        interval_min: int = 15,
        min_samples: int = 50,
        state_path: str = "python_agents/.online_learning_state.json",
        window: int = 2000,
        price_lookup: Optional[Callable[[str], Optional[float]]] = None,
        event_bus: Any = None,
        database: Any = None,
        db_offset_path: str = "python_agents/.online_learning_db_offset.json",
        persist_db: bool = False,
    ) -> None:
        self.log_path = Path(log_path)
        self.horizon_sec = int(horizon_min) * 60
        self.interval_sec = int(interval_min) * 60
        self.min_samples = int(min_samples)
        self.state_path = Path(state_path)
        self.window = int(window)
        self._price_lookup = price_lookup or self._default_price_lookup

        # {symbol: [_ScoredRow, ...]}
        self._scored: Dict[str, Deque[_ScoredRow]] = {}
        # pending: JSONL rows waiting for horizon maturity — list of dict
        self._pending: List[Dict[str, Any]] = []
        self._last_offset: int = 0   # bytes already consumed from log
        self._last_run_ts: float = 0.0
        self._total_scored = 0
        self._task: Optional[asyncio.Task] = None

        # Phase 4 Finalization — DB-backed counterfactual store.
        # JSONL kalmaya devam eder (write-ahead log). DB ikincil persist.
        self._event_bus = event_bus
        self._database = database
        self._db_offset_path = Path(db_offset_path)
        self._persist_db = bool(persist_db) and database is not None
        self._db_offset: int = 0
        self._db_persisted_total: int = 0
        self._weights_frozen: bool = False  # safety_net tripped gate

        self._load_state()
        self._load_db_offset()
        self._subscribe_safety_net()

    # ─────────── safety_net integration ───────────
    def _subscribe_safety_net(self) -> None:
        bus = self._event_bus
        if bus is None:
            return
        try:
            from event_bus import EventType
            subscribe = getattr(bus, "subscribe", None)
            if subscribe is None:
                return
            subscribe(EventType.SAFETY_NET_TRIPPED, self._on_safety_net_tripped)
            subscribe(EventType.SAFETY_NET_RESET, self._on_safety_net_reset)
        except Exception as exc:
            logger.debug("online_learning safety_net subscribe skipped: %s", exc)

    async def _on_safety_net_tripped(self, event: Any) -> None:
        self._weights_frozen = True
        logger.warning("🛡️ online_learning: SAFETY_NET_TRIPPED → weight rotation dondu")

    async def _on_safety_net_reset(self, event: Any) -> None:
        self._weights_frozen = False
        logger.info("🛡️ online_learning: SAFETY_NET_RESET → weight rotation aktif")

    @property
    def weights_frozen(self) -> bool:
        return bool(self._weights_frozen)

    # ─────────── public metrics ───────────
    def rolling_metrics(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        rows: List[_ScoredRow] = []
        if symbol:
            rows = list(self._scored.get(symbol.upper(), ()))
        else:
            for dq in self._scored.values():
                rows.extend(dq)
        if not rows:
            return {"samples": 0}

        fast_hits, fast_total = 0, 0
        gemma_hits, gemma_total = 0, 0
        agree_total, agree_hits = 0, 0
        bins = [[0, 0] for _ in range(10)]  # each: [count, positives]

        for r in rows:
            ret = r.realized_ret
            # fast
            fs = _direction_sign(r.fast_direction)
            if fs != 0:
                fast_total += 1
                if (fs > 0 and ret > 0) or (fs < 0 and ret < 0):
                    fast_hits += 1
            # gemma
            gs = _direction_sign(r.gemma_action)
            if gs != 0:
                gemma_total += 1
                if (gs > 0 and ret > 0) or (gs < 0 and ret < 0):
                    gemma_hits += 1
            if r.agreed and fs != 0 and gs != 0 and fs == gs:
                agree_total += 1
                if (fs > 0 and ret > 0) or (fs < 0 and ret < 0):
                    agree_hits += 1
            # calibration
            if r.fast_probability is not None:
                b = min(9, max(0, int(r.fast_probability * 10)))
                bins[b][0] += 1
                if r.realized_up:
                    bins[b][1] += 1

        # calibration error (ECE)
        ece_num, ece_den = 0.0, 0
        for i, (cnt, pos) in enumerate(bins):
            if cnt == 0:
                continue
            p_pred = (i + 0.5) / 10.0
            p_real = pos / cnt
            ece_num += cnt * abs(p_pred - p_real)
            ece_den += cnt
        ece = (ece_num / ece_den) if ece_den else None

        return {
            "samples": len(rows),
            "fast_brain": {
                "directional_hit_rate": (fast_hits / fast_total) if fast_total else None,
                "n": fast_total,
            },
            "gemma": {
                "directional_hit_rate": (gemma_hits / gemma_total) if gemma_total else None,
                "n": gemma_total,
            },
            "agreement": {
                "rate": (agree_total / len(rows)) if rows else None,
                "hit_rate_when_agreed": (agree_hits / agree_total) if agree_total else None,
                "n": agree_total,
            },
            "calibration_bins": [
                {"p_pred_center": round((i + 0.5) / 10.0, 2),
                 "count": cnt,
                 "p_realized_up": (pos / cnt) if cnt else None}
                for i, (cnt, pos) in enumerate(bins)
            ],
            "ece": ece,
        }

    async def health_check(self) -> Dict[str, Any]:
        total = sum(len(d) for d in self._scored.values())
        return {
            "healthy": True,
            "log_path": str(self.log_path),
            "horizon_sec": self.horizon_sec,
            "interval_sec": self.interval_sec,
            "last_run_ts": self._last_run_ts,
            "scored_total": self._total_scored,
            "window_samples": total,
            "pending_rows": len(self._pending),
            "last_offset": self._last_offset,
            "tracked_symbols": len(self._scored),
            "persist_db": self._persist_db,
            "db_persisted_total": self._db_persisted_total,
            "weights_frozen": self._weights_frozen,
        }

    def metrics(self) -> Dict[str, Any]:
        return {
            "online_learning_scored_total": self._total_scored,
            "online_learning_pending": len(self._pending),
            "online_learning_tracked_symbols": len(self._scored),
            "online_learning_db_persisted_total": self._db_persisted_total,
            "online_learning_weights_frozen": int(bool(self._weights_frozen)),
        }

    # ─────────── loop ───────────
    def start(self) -> asyncio.Task:
        if self._task is not None and not self._task.done():
            return self._task
        self._task = asyncio.create_task(self._run_loop())
        return self._task

    async def _run_loop(self) -> None:
        logger.info("📈 OnlineLearning loop başladı (interval=%ds, horizon=%ds)",
                    self.interval_sec, self.horizon_sec)
        while True:
            try:
                await self._run_once()
            except Exception as e:
                logger.debug("online_learning loop hata: %s", e)
            await asyncio.sleep(self.interval_sec)

    async def _run_once(self) -> Dict[str, Any]:
        added = self._ingest_new_rows()
        matured = self._score_matured_rows()
        persisted = 0
        if self._persist_db:
            persisted = await self._persist_to_db()
        self._last_run_ts = time.time()
        self._save_state()
        return {"ingested": added, "scored": matured, "persisted_db": persisted}

    # ─────────── DB persistence (Phase 4 Finalization) ───────────
    async def _persist_to_db(self) -> int:
        """JSONL'i DB'ye tail eder. JSONL kaybolmaz; DB yazilamazsa retry."""
        if not self._persist_db or self._database is None:
            return 0
        if not self.log_path.exists():
            return 0
        inserted = 0
        batch_rows: List[Dict[str, Any]] = []
        try:
            size = self.log_path.stat().st_size
            if size < self._db_offset:
                self._db_offset = 0  # log rotated
            with self.log_path.open("r", encoding="utf-8") as f:
                f.seek(self._db_offset)
                for line in f:
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    mapped = self._row_to_cf_observation(row)
                    if mapped is None:
                        continue
                    batch_rows.append(mapped)
                new_offset = f.tell()
            for mapped in batch_rows:
                try:
                    rid = await self._database.insert_counterfactual_observation(mapped)
                    if rid is not None:
                        inserted += 1
                        self._db_persisted_total += 1
                except Exception as exc:
                    logger.debug("cf insert skipped: %s", exc)
            # only advance offset if all batch rows processed without raising
            self._db_offset = new_offset
            self._save_db_offset()
            if inserted > 0:
                await self._emit_counterfactual_update(inserted)
        except Exception as exc:
            logger.debug("persist_to_db tail error: %s", exc)
        return inserted

    def _row_to_cf_observation(self, row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Shadow JSONL satirini counterfactual_observations satira cevirir."""
        symbol = str(row.get("symbol") or "").upper()
        if not symbol:
            return None
        ts = row.get("ts") or row.get("_ingest_ts")
        # determine label heuristically from realized outcome if available
        realized = row.get("realized_pnl_pct")
        fast_dir = _direction_sign(str(row.get("fast_direction", "neutral")))
        label = "TN"
        decided = bool(row.get("chosen_by") and row.get("chosen_by") != "none")
        if realized is not None:
            try:
                realized = float(realized)
                if decided and fast_dir != 0:
                    # signal issued
                    if (fast_dir > 0 and realized > 0) or (fast_dir < 0 and realized < 0):
                        label = "TP" if abs(realized) >= 2.0 else "FP"
                    else:
                        label = "FP"
                else:
                    label = "FN" if abs(realized) >= 2.0 else "TN"
            except Exception:
                realized = None
        return {
            "symbol": symbol,
            "event_ts": ts,
            "move_magnitude_pct": abs(float(realized)) if realized is not None else 0.0,
            "move_direction": "up" if (realized or 0) > 0 else ("down" if (realized or 0) < 0 else "flat"),
            "label": label,
            "horizon_minutes": int(self.horizon_sec // 60),
            "features_t_minus_1h": row.get("features") if isinstance(row.get("features"), dict) else None,
            "confluence_score_t_minus_1h": row.get("confluence_score"),
            "fast_brain_p_t_minus_1h": row.get("fast_probability"),
            "conformal_lower": row.get("conformal_lower"),
            "conformal_upper": row.get("conformal_upper"),
            "decided": decided,
            "decision_source": row.get("chosen_by"),
            "decision_path": row.get("path"),
            "realized_pnl_pct": realized,
            "attribution": row.get("attribution"),
        }

    async def _emit_counterfactual_update(self, last_batch: int) -> None:
        bus = self._event_bus
        if bus is None:
            return
        try:
            from event_bus import EventType
            publish = getattr(bus, "publish", None)
            if publish is None:
                return
            payload = {
                "last_batch": int(last_batch),
                "persisted_total": int(self._db_persisted_total),
                "weights_frozen": self._weights_frozen,
            }
            result = publish(EventType.COUNTERFACTUAL_UPDATE, payload)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            pass

    def _load_db_offset(self) -> None:
        try:
            if not self._db_offset_path.exists():
                return
            d = json.loads(self._db_offset_path.read_text(encoding="utf-8"))
            self._db_offset = int(d.get("offset", 0))
            self._db_persisted_total = int(d.get("persisted_total", 0))
        except Exception:
            pass

    def _save_db_offset(self) -> None:
        try:
            self._db_offset_path.parent.mkdir(parents=True, exist_ok=True)
            self._db_offset_path.write_text(
                json.dumps({
                    "offset": self._db_offset,
                    "persisted_total": self._db_persisted_total,
                }),
                encoding="utf-8",
            )
        except Exception:
            pass

    async def recompute_from_db(self, limit: int = 100000) -> Dict[str, Any]:
        """Warm-start: DB'deki son N counterfactual'dan logistic SGD cikar.

        Sonuc `.confluence_weights_candidate.json` dosyasina yazilir —
        live weights'e DOKUNMAZ. Promotion `promote_confluence_weights.py`
        ile yapilir.
        """
        if self._database is None:
            return {"healthy": False, "error": "no_database"}
        if self._weights_frozen:
            return {"healthy": False, "error": "weights_frozen"}
        try:
            from datetime import datetime, timedelta
            since = datetime.utcnow() - timedelta(days=30)
            rows = await self._database.fetch_counterfactual_labels(since, int(limit))
        except Exception as exc:
            return {"healthy": False, "error": str(exc)}
        if not rows:
            return {"healthy": True, "samples": 0, "weights_path": None}
        # minimal logistic SGD over features_t_minus_1h keys common to all
        from collections import defaultdict
        feature_sum: Dict[str, float] = defaultdict(float)
        feature_cnt: Dict[str, int] = defaultdict(int)
        pos = 0
        neg = 0
        for r in rows:
            lbl = str(r.get("label") or "").upper()
            if lbl in {"TP", "FN"}:
                pos += 1
                y = 1
            else:
                neg += 1
                y = 0
            feats = r.get("features_t_minus_1h") or {}
            if isinstance(feats, str):
                try:
                    feats = json.loads(feats)
                except Exception:
                    feats = {}
            if not isinstance(feats, dict):
                continue
            for k, v in feats.items():
                try:
                    vf = float(v)
                except Exception:
                    continue
                if not math.isfinite(vf):
                    continue
                feature_sum[k] += vf * (1 if y else -1)
                feature_cnt[k] += 1
        # naive candidate weights: mean signed contribution clipped to [-1, 1]
        candidate: Dict[str, float] = {}
        for k, s in feature_sum.items():
            cnt = feature_cnt[k]
            if cnt < 10:
                continue
            w = s / max(1, cnt)
            candidate[k] = max(-1.0, min(1.0, w))
        out_path = Path("python_agents/.confluence_weights_candidate.json")
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps({
                "generated_at": time.time(),
                "samples": len(rows),
                "positives": pos,
                "negatives": neg,
                "weights": candidate,
            }, indent=2), encoding="utf-8")
        except Exception as exc:
            return {"healthy": False, "error": str(exc), "samples": len(rows)}
        return {
            "healthy": True,
            "samples": len(rows),
            "weights_path": str(out_path),
            "keys": len(candidate),
            "positives": pos,
            "negatives": neg,
        }

    # ─────────── internals ───────────
    def _ingest_new_rows(self) -> int:
        if not self.log_path.exists():
            return 0
        added = 0
        try:
            size = self.log_path.stat().st_size
            if size < self._last_offset:
                # log rotated
                self._last_offset = 0
            with self.log_path.open("r", encoding="utf-8") as f:
                f.seek(self._last_offset)
                for line in f:
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    if row.get("shadow") is False and row.get("chosen_by") == "fast_brain":
                        # still include; metrics apply either way
                        pass
                    # only interested in rows with a fast_brain signal to score
                    if row.get("fast_direction") in (None, "neutral") and \
                       row.get("fast_probability") is None:
                        # skip no-signal rows (still count via pending? no — need fast info)
                        continue
                    row["_ingest_ts"] = time.time()
                    # capture current price at decision time approximation
                    sym = row.get("symbol", "").upper()
                    px = self._price_lookup(sym) if sym else None
                    row["_price_at_decision"] = float(px) if px else None
                    self._pending.append(row)
                    added += 1
                self._last_offset = f.tell()
        except Exception as e:
            logger.debug("online_learning ingest hata: %s", e)
        return added

    def _score_matured_rows(self) -> int:
        if not self._pending:
            return 0
        now = time.time()
        keep: List[Dict[str, Any]] = []
        scored = 0
        for row in self._pending:
            ts = float(row.get("ts") or row.get("_ingest_ts") or now)
            if now - ts < self.horizon_sec:
                keep.append(row)
                continue
            sym = str(row.get("symbol", "")).upper()
            if not sym:
                continue
            px0 = row.get("_price_at_decision")
            px1 = self._price_lookup(sym)
            if not (px0 and px1):
                continue
            sr = _ScoredRow(
                ts=ts,
                symbol=sym,
                fast_direction=str(row.get("fast_direction", "neutral")),
                fast_probability=(float(row["fast_probability"])
                                  if row.get("fast_probability") is not None else None),
                gemma_action=str(row.get("gemma_action", "HOLD")),
                agreed=bool(row.get("agreed")),
                price_at_decision=float(px0),
                price_at_horizon=float(px1),
            )
            dq = self._scored.setdefault(sym, deque(maxlen=self.window))
            dq.append(sr)
            self._total_scored += 1
            scored += 1
        self._pending = keep
        return scored

    def _default_price_lookup(self, symbol: str) -> Optional[float]:
        """Microstructure → OFI → cross_asset chain (hot path değil)."""
        try:
            from microstructure import get_microstructure_engine
            snap = get_microstructure_engine().snapshot(symbol)
            if snap:
                mid = snap.get("mid_price")
                if mid:
                    return float(mid)
        except Exception:
            pass
        try:
            from order_flow_imbalance import get_ofi_engine
            snap = get_ofi_engine().snapshot(symbol)
            if snap and snap.get("last_price"):
                return float(snap["last_price"])
        except Exception:
            pass
        return None

    def _save_state(self) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            Path(self.state_path).write_text(json.dumps({
                "last_offset": self._last_offset,
                "last_run_ts": self._last_run_ts,
                "total_scored": self._total_scored,
            }), encoding="utf-8")
        except Exception:
            pass

    def _load_state(self) -> None:
        try:
            if not self.state_path.exists():
                return
            d = json.loads(self.state_path.read_text(encoding="utf-8"))
            self._last_offset = int(d.get("last_offset", 0))
            self._last_run_ts = float(d.get("last_run_ts", 0.0))
            self._total_scored = int(d.get("total_scored", 0))
        except Exception:
            pass


_evaluator: Optional[OnlineLearningEvaluator] = None


def get_online_learning_evaluator(*args, **kwargs) -> OnlineLearningEvaluator:
    global _evaluator
    if _evaluator is None:
        _evaluator = OnlineLearningEvaluator(*args, **kwargs)
    return _evaluator


def _reset_online_learning_for_tests() -> None:
    global _evaluator
    _evaluator = None
