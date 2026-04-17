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
        self._load_state()

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
        }

    def metrics(self) -> Dict[str, Any]:
        return {
            "online_learning_scored_total": self._total_scored,
            "online_learning_pending": len(self._pending),
            "online_learning_tracked_symbols": len(self._scored),
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
        self._last_run_ts = time.time()
        self._save_state()
        return {"ingested": added, "scored": matured}

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
