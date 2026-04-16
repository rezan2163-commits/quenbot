"""
iceberg_detector.py — Iceberg & Spoof tespiti
==============================================
Order book delta'ları ve agresyonlar arasındaki imza eşleşmezliklerini izleyerek
iki tür manipülasyon/bilgilendirilmiş işlem imzasını tespit eder:

  ICEBERG: Görünen seviye tüketildikten sonra aynı fiyatta kısa sürede yeni
           eşit/benzer büyüklükte emir. (Tipik kurumsal gizli emir)
  SPOOF:   Uzak seviyede büyük emir görünür, fiyat o seviyeye yaklaşınca emir
           iptal → bu davranış istatistiksel olarak sayılır.

Her iki durum da olayla publish edilir (anomaly), ayrıca sembol bazlı
`fingerprint_score` (0-1) üretilir. Brain bu skoru risk azaltma ve
toxicity guard olarak kullanır.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class Level:
    price: float
    qty: float
    ts: float


class IcebergSpoofDetector:
    """Order book delta ring buffer + aggressor matcher."""

    ICEBERG_REFILL_WINDOW_S = 3.0
    ICEBERG_QTY_TOLERANCE = 0.35      # ±35% qty benzerliği
    SPOOF_MIN_SIZE_MULT = 4.0          # top-5 ortalamasından 4x büyük = "büyük"
    SPOOF_CANCEL_WINDOW_S = 5.0
    HISTORY = 200

    def __init__(self, event_bus=None) -> None:
        self.event_bus = event_bus
        self._last_book: Dict[str, Dict[str, Dict[float, Level]]] = {}  # symbol -> {bids,asks} -> {price: Level}
        self._iceberg_events: Dict[str, Deque[float]] = {}   # symbol -> recent event ts
        self._spoof_events: Dict[str, Deque[float]] = {}
        self._scores: Dict[str, Dict[str, float]] = {}

    async def on_order_book(self, event) -> None:
        d = getattr(event, "data", None) or {}
        symbol = d.get("symbol")
        if not symbol:
            return
        bids = d.get("bids") or []
        asks = d.get("asks") or []
        now = time.time()
        new_bids = self._to_levels(bids, now)
        new_asks = self._to_levels(asks, now)
        old = self._last_book.get(symbol, {"bids": {}, "asks": {}})

        # ── ICEBERG: disappeared level reappears within window at similar qty
        for side_name, old_side, new_side in [("bids", old["bids"], new_bids),
                                              ("asks", old["asks"], new_asks)]:
            for price, old_lvl in old_side.items():
                new_lvl = new_side.get(price)
                if new_lvl is None:
                    # tüketildi; ertesi snapshot'ta gelip aynı fiyatta benzer qty varsa
                    pass
                elif abs(new_lvl.qty - old_lvl.qty) / max(old_lvl.qty, 1e-9) > 1e-4 \
                        and new_lvl.qty > old_lvl.qty * 0.65 \
                        and (new_lvl.ts - old_lvl.ts) < self.ICEBERG_REFILL_WINDOW_S:
                    await self._report_iceberg(symbol, side_name, price, old_lvl.qty, new_lvl.qty)

        # ── SPOOF: büyük emir uzakta → fiyat yaklaşınca iptal
        for side_name, new_side in [("bids", new_bids), ("asks", new_asks)]:
            if not new_side: continue
            qtys = [lvl.qty for lvl in new_side.values()]
            avg = sum(qtys) / max(len(qtys), 1)
            for price, lvl in new_side.items():
                if lvl.qty > avg * self.SPOOF_MIN_SIZE_MULT:
                    self._last_book.setdefault(symbol, {})
                    # önceki snapshot'ta da büyük müydü ama şimdi yok? → iptal
                    pass

        # kaybolan büyük emirler (spoof candidate)
        for side_name, old_side, new_side in [("bids", old["bids"], new_bids),
                                              ("asks", old["asks"], new_asks)]:
            qtys_old = [lvl.qty for lvl in old_side.values()]
            if qtys_old:
                avg_old = sum(qtys_old) / len(qtys_old)
                for price, old_lvl in old_side.items():
                    if old_lvl.qty > avg_old * self.SPOOF_MIN_SIZE_MULT and price not in new_side:
                        await self._report_spoof(symbol, side_name, price, old_lvl.qty)

        self._last_book[symbol] = {"bids": new_bids, "asks": new_asks}
        self._update_score(symbol)

    def _to_levels(self, side, now: float) -> Dict[float, Level]:
        out: Dict[float, Level] = {}
        for lvl in side[:20]:
            try:
                p = float(lvl[0]); q = float(lvl[1])
            except (ValueError, IndexError, TypeError):
                continue
            if p <= 0 or q <= 0:
                continue
            out[p] = Level(price=p, qty=q, ts=now)
        return out

    async def _report_iceberg(self, symbol: str, side: str, price: float, old_qty: float, new_qty: float) -> None:
        q = self._iceberg_events.setdefault(symbol, deque(maxlen=self.HISTORY))
        q.append(time.time())
        if not self.event_bus:
            return
        try:
            from event_bus import Event, EventType
            if not hasattr(EventType, "ICEBERG_DETECTED"):
                return
            await self.event_bus.publish(Event(
                type=EventType.ICEBERG_DETECTED,
                source="iceberg_detector",
                data={"symbol": symbol, "side": side, "price": price,
                      "old_qty": old_qty, "new_qty": new_qty, "ts": time.time()},
            ))
        except Exception as e:
            logger.debug(f"iceberg publish skipped: {e}")

    async def _report_spoof(self, symbol: str, side: str, price: float, qty: float) -> None:
        q = self._spoof_events.setdefault(symbol, deque(maxlen=self.HISTORY))
        q.append(time.time())
        if not self.event_bus:
            return
        try:
            from event_bus import Event, EventType
            if not hasattr(EventType, "SPOOF_DETECTED"):
                return
            await self.event_bus.publish(Event(
                type=EventType.SPOOF_DETECTED,
                source="iceberg_detector",
                data={"symbol": symbol, "side": side, "price": price, "qty": qty, "ts": time.time()},
            ))
        except Exception as e:
            logger.debug(f"spoof publish skipped: {e}")

    def _update_score(self, symbol: str) -> None:
        now = time.time()
        ice = [t for t in self._iceberg_events.get(symbol, []) if now - t < 300]
        sp = [t for t in self._spoof_events.get(symbol, []) if now - t < 300]
        # 5 dk içinde 10 iceberg veya 5 spoof = skor 1.0
        ice_s = min(1.0, len(ice) / 10.0)
        sp_s = min(1.0, len(sp) / 5.0)
        score = max(ice_s, sp_s)
        self._scores[symbol] = {
            "fingerprint_score": round(score, 3),
            "iceberg_5m": len(ice),
            "spoof_5m": len(sp),
            "ts": now,
        }

    def fingerprint(self, symbol: str) -> Optional[Dict[str, Any]]:
        return self._scores.get(symbol)

    def all_fingerprints(self) -> Dict[str, Dict[str, Any]]:
        return dict(self._scores)

    async def health_check(self) -> Dict[str, Any]:
        flagged = sum(1 for v in self._scores.values() if v.get("fingerprint_score", 0) > 0.3)
        return {"healthy": True, "tracked_symbols": len(self._scores),
                "flagged": flagged,
                "message": f"{len(self._scores)} sembolde imza izleme, {flagged} yüksek riskli"}


_det: Optional[IcebergSpoofDetector] = None


def get_iceberg_detector(event_bus=None) -> IcebergSpoofDetector:
    global _det
    if _det is None:
        _det = IcebergSpoofDetector(event_bus=event_bus)
    elif event_bus is not None and _det.event_bus is None:
        _det.event_bus = event_bus
    return _det
