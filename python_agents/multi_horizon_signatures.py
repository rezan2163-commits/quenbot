"""
multi_horizon_signatures.py — Çoklu-zaman-ufuk bot imza coherence
==================================================================
Intel Upgrade Phase 1. SystematicTradeDetector'ı 4 farklı zaman ufkunda
(5m, 30m, 2h, 6h) paralel çalıştırır. Aynı dominant_bot_type aynı anda
birden fazla ufukta görülüyorsa bu, institutional bir footprint'in
zaman ölçeğinde tutarlı (coherent) olduğunu gösterir — stealth
accumulation/distribution'ın en güçlü tekil göstergelerinden.

coherence = (dominant_type & conf > 0.5 olan ufuk sayısı) / 4

Bu modül `systematic_trade_detector.py`'a DOKUNMAZ; kendi 4 bağımsız
instance'ını tutar ve aynı trade akışını hepsine besler.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from systematic_trade_detector import SystematicTradeDetector, SystematicActivityReport

logger = logging.getLogger(__name__)


HORIZONS_DEFAULT_SEC: Tuple[int, ...] = (300, 1800, 7200, 21600)  # 5m, 30m, 2h, 6h


@dataclass
class HorizonReport:
    window_sec: int
    symbol: str
    dominant_bot_type: Optional[str] = None
    direction_confidence: float = 0.0
    predicted_direction: str = "neutral"
    accumulation_score: float = 0.0
    systematic_ratio: float = 0.0


@dataclass
class MultiHorizonSnapshot:
    symbol: str
    ts: float
    per_horizon: Dict[int, HorizonReport] = field(default_factory=dict)
    dominant_type: Optional[str] = None
    coherence: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "ts": self.ts,
            "dominant_type": self.dominant_type,
            "coherence": round(self.coherence, 4),
            "per_horizon": {
                str(k): {
                    "dominant_bot_type": v.dominant_bot_type,
                    "direction_confidence": round(v.direction_confidence, 4),
                    "predicted_direction": v.predicted_direction,
                    "accumulation_score": round(v.accumulation_score, 4),
                    "systematic_ratio": round(v.systematic_ratio, 4),
                } for k, v in self.per_horizon.items()
            }
        }


class MultiHorizonSignatureEngine:
    """4 bağımsız SystematicTradeDetector'ı farklı pencerelerle çalıştırır."""

    COHERENCE_MIN_CONF = 0.5

    def __init__(
        self,
        event_bus=None,
        feature_store=None,
        horizons_sec: Tuple[int, ...] = HORIZONS_DEFAULT_SEC,
        publish_hz: float = 0.5,
    ) -> None:
        self.event_bus = event_bus
        self.feature_store = feature_store
        self.horizons = tuple(sorted(int(h) for h in horizons_sec))
        self.publish_hz = max(0.05, float(publish_hz))
        self._min_publish_interval = 1.0 / self.publish_hz
        # Her ufuk için bağımsız detektör, farklı ANALYSIS_WINDOW_SECONDS ile
        self._detectors: Dict[int, SystematicTradeDetector] = {}
        for h in self.horizons:
            d = SystematicTradeDetector()
            d.ANALYSIS_WINDOW_SECONDS = int(h)
            # Büyük ufuk için trade buffer'ı genişlet (yaklaşık trades/s × window)
            d.MAX_TRADES_PER_SYMBOL = max(1000, int(h * 3))
            self._detectors[h] = d
        self._last_publish: Dict[str, float] = {}
        self._snapshots: Dict[str, MultiHorizonSnapshot] = {}
        self._total_trades = 0

    # ──────────── event handler ────────────
    async def on_trade(self, event) -> None:
        d = getattr(event, "data", None) or {}
        symbol = d.get("symbol")
        if not symbol:
            return
        try:
            price = float(d.get("price", 0) or 0)
            qty = float(d.get("quantity", 0) or 0)
        except (ValueError, TypeError):
            return
        if price <= 0 or qty <= 0:
            return
        side = str(d.get("side", "buy")).lower()
        ts_raw = d.get("timestamp")
        if isinstance(ts_raw, str):
            try:
                ts = datetime.fromisoformat(ts_raw)
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            except Exception:
                ts = datetime.now(timezone.utc)
        elif isinstance(ts_raw, datetime):
            ts = ts_raw if ts_raw.tzinfo else ts_raw.replace(tzinfo=timezone.utc)
        else:
            ts = datetime.now(timezone.utc)

        trade = {
            "symbol": symbol,
            "price": price,
            "quantity": qty,
            "side": side,
            "timestamp": ts,
            "trade_id": d.get("trade_id"),
        }
        # Aynı trade'i her detektöre besle
        for det in self._detectors.values():
            det.ingest_trade(trade)
        self._total_trades += 1

        now = time.time()
        if now - self._last_publish.get(symbol, 0.0) >= self._min_publish_interval:
            self._last_publish[symbol] = now
            await self._analyze_and_publish(symbol, now)

    # ──────────── analiz ────────────
    async def _analyze_and_publish(self, symbol: str, now: float) -> None:
        per_horizon: Dict[int, HorizonReport] = {}
        types_with_conf: List[str] = []
        for h, det in self._detectors.items():
            try:
                rep = det.analyze_symbol(symbol)
            except Exception as e:
                logger.debug("mh analyze hata (%s, %s): %s", symbol, h, e)
                rep = None
            hr = HorizonReport(window_sec=h, symbol=symbol)
            if rep:
                hr.dominant_bot_type = rep.dominant_bot_type
                hr.direction_confidence = float(rep.direction_confidence)
                hr.predicted_direction = rep.predicted_price_direction
                hr.accumulation_score = float(rep.accumulation_score)
                hr.systematic_ratio = float(rep.systematic_trade_ratio)
                if rep.dominant_bot_type and rep.direction_confidence >= self.COHERENCE_MIN_CONF:
                    types_with_conf.append(rep.dominant_bot_type)
            per_horizon[h] = hr

        dominant_type: Optional[str] = None
        coherence = 0.0
        if types_with_conf:
            counter = Counter(types_with_conf)
            dominant_type, count = counter.most_common(1)[0]
            coherence = count / float(len(self.horizons))

        snap = MultiHorizonSnapshot(
            symbol=symbol,
            ts=now,
            per_horizon=per_horizon,
            dominant_type=dominant_type,
            coherence=coherence,
        )
        self._snapshots[symbol] = snap

        # feature_store
        if self.feature_store is not None:
            try:
                asyncio.create_task(self.feature_store.write(
                    symbol=symbol,
                    ts=datetime.fromtimestamp(now, tz=timezone.utc),
                    features={
                        "mh.coherence": coherence,
                        "mh.dominant_type": dominant_type or "",
                        **{
                            f"mh.conf_{h}": per_horizon[h].direction_confidence
                            for h in self.horizons
                        },
                        **{
                            f"mh.acc_{h}": per_horizon[h].accumulation_score
                            for h in self.horizons
                        },
                    },
                ))
            except Exception as e:
                logger.debug("mh→feature_store skip: %s", e)

        # Event yayını
        if self.event_bus is not None:
            try:
                from event_bus import Event, EventType
                if hasattr(EventType, "MULTI_HORIZON_SIGNATURE"):
                    await self.event_bus.publish(Event(
                        type=EventType.MULTI_HORIZON_SIGNATURE,
                        source="multi_horizon_signatures",
                        data=snap.to_dict(),
                    ))
            except Exception as e:
                logger.debug("mh publish skip: %s", e)

    # ──────────── public API ────────────
    def snapshot(self, symbol: str) -> Optional[Dict[str, Any]]:
        s = self._snapshots.get(symbol)
        return s.to_dict() if s else None

    def all_snapshots(self) -> Dict[str, Dict[str, Any]]:
        return {s: v.to_dict() for s, v in self._snapshots.items()}

    async def health_check(self) -> Dict[str, Any]:
        return {
            "healthy": True,
            "horizons_sec": list(self.horizons),
            "tracked_symbols": len(self._snapshots),
            "total_trades": self._total_trades,
            "message": f"{len(self.horizons)} ufukta {len(self._snapshots)} sembol",
        }

    def metrics(self) -> Dict[str, Any]:
        return {
            "mh_trades_ingested_total": self._total_trades,
            "mh_tracked_symbols": len(self._snapshots),
        }


# ─────────── singleton ───────────
_engine: Optional[MultiHorizonSignatureEngine] = None


def get_multi_horizon_engine(
    event_bus=None,
    feature_store=None,
    horizons_sec: Tuple[int, ...] = HORIZONS_DEFAULT_SEC,
    publish_hz: float = 0.5,
) -> MultiHorizonSignatureEngine:
    global _engine
    if _engine is None:
        _engine = MultiHorizonSignatureEngine(
            event_bus=event_bus,
            feature_store=feature_store,
            horizons_sec=horizons_sec,
            publish_hz=publish_hz,
        )
    else:
        if event_bus is not None and _engine.event_bus is None:
            _engine.event_bus = event_bus
        if feature_store is not None and _engine.feature_store is None:
            _engine.feature_store = feature_store
    return _engine
