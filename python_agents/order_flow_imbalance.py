"""
order_flow_imbalance.py — Order Flow Imbalance (Cont–Kukanov–Stoikov) + Hurst
=============================================================================
Intel Upgrade Phase 1. Level-1 orderbook güncellemelerinden OFI zamansal serisi
üretir, çoklu pencereler üzerinde yuvarlanan toplam + Hurst üstel (R/S) ile
kalıcılık ölçer.

OFI formülü (Cont, Kukanov, Stoikov 2014):
    OFI_t = Δbid_qty · 1[bid_price_t ≥ bid_price_{t-1}]
          − Δask_qty · 1[ask_price_t ≤ ask_price_{t-1}]

Yayınlanan özellikler (feature_store + event):
    ofi_1s, ofi_10s, ofi_1m, ofi_5m, ofi_30m   — rolling sum
    ofi_hurst_2h                                — R/S-based Hurst estimator
    ofi_zscore_24h                              — rolling z-score (fallback 1s)

Kalıcılık (persistence):
    H > 0.5 → trendli (accumulation/distribution footprint)
    H ≈ 0.5 → random walk
    H < 0.5 → mean-reverting (noise trader)
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ──────────── matematiksel yardımcılar ────────────
def hurst_rs(series: List[float], min_n: int = 32) -> Optional[float]:
    """R/S (Rescaled Range) tabanlı Hurst üstel tahmini.

    Mandelbrot (1968) + Hurst (1951). Seride log(R/S) ~ H·log(n) beklenir.
    numpy kullanmadan pure-python: ajanların hot-path'inde güvenli.

    Args:
        series: zaman sıralı (örn. 1m OFI toplamları) float listesi.
        min_n: minimum pencere; bundan kısaysa None döner.

    Returns:
        H ∈ [0, 1] tahmini veya None (yetersiz/dejenere veri).
    """
    n = len(series)
    if n < min_n:
        return None
    # Pencere boyutları: log-uniform scale (min_n, n/2)
    max_win = max(min_n * 2, n // 2)
    if max_win <= min_n:
        return None
    # geometrik pencereler
    windows: List[int] = []
    w = float(min_n)
    while w <= max_win:
        windows.append(int(round(w)))
        w *= 1.5
    windows = sorted(set(windows))
    if len(windows) < 3:
        return None
    # Her pencere için ortalama R/S
    rs_pairs: List[Tuple[float, float]] = []
    for win in windows:
        if win >= n:
            continue
        # non-overlapping chunk'lar
        rs_vals: List[float] = []
        for start in range(0, n - win + 1, win):
            chunk = series[start:start + win]
            if len(chunk) < win:
                continue
            mean = sum(chunk) / win
            dev = [x - mean for x in chunk]
            # cumulative deviation
            cum = 0.0
            cmin = math.inf
            cmax = -math.inf
            for d in dev:
                cum += d
                if cum < cmin: cmin = cum
                if cum > cmax: cmax = cum
            rng = cmax - cmin
            # standard deviation
            var = sum(x * x for x in dev) / win
            std = math.sqrt(max(var, 1e-24))
            if std <= 1e-12 or rng <= 0:
                continue
            rs_vals.append(rng / std)
        if rs_vals:
            rs_mean = sum(rs_vals) / len(rs_vals)
            if rs_mean > 0:
                rs_pairs.append((math.log(win), math.log(rs_mean)))
    if len(rs_pairs) < 3:
        return None
    # log-log lineer regresyon — eğim = H
    xs = [p[0] for p in rs_pairs]
    ys = [p[1] for p in rs_pairs]
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs)
    if den <= 1e-12:
        return None
    h = num / den
    # klip [0, 1]
    return max(0.0, min(1.0, float(h)))


@dataclass
class OFIState:
    """Per-sembol iç durum."""
    last_bid_px: float = 0.0
    last_bid_qty: float = 0.0
    last_ask_px: float = 0.0
    last_ask_qty: float = 0.0
    # (ts, ofi_value) — 1s, 10s, 1m, 5m, 30m toplamlar için raw stream
    raw: Deque[Tuple[float, float]] = field(default_factory=lambda: deque(maxlen=50000))
    # 1m bucket toplamları — Hurst için 2h içinde ~120 örnek yeterli
    minute_sums: Deque[Tuple[int, float]] = field(default_factory=lambda: deque(maxlen=180))
    cur_minute: int = 0
    cur_minute_acc: float = 0.0
    # 24h z-score için dakika OFI'lerini ayrıca saklayalım
    zscore_samples: Deque[float] = field(default_factory=lambda: deque(maxlen=24 * 60))
    last_publish_ts: float = 0.0


class OrderFlowImbalanceEngine:
    """Sembol başına OFI + rolling windows + Hurst."""

    WINDOWS_SEC = (1.0, 10.0, 60.0, 300.0, 1800.0)  # ofi_1s,10s,1m,5m,30m
    PUBLISH_HZ = 2.0

    def __init__(self, event_bus=None, feature_store=None, publish_hz: float = 2.0) -> None:
        self.event_bus = event_bus
        self.feature_store = feature_store
        self.publish_hz = max(0.1, float(publish_hz))
        self._min_publish_interval = 1.0 / self.publish_hz
        self._state: Dict[str, OFIState] = {}
        self._snapshots: Dict[str, Dict[str, Any]] = {}
        self._total_updates = 0

    # ──────────── event handler ────────────
    async def on_order_book(self, event) -> None:
        d = getattr(event, "data", None) or {}
        symbol = d.get("symbol")
        if not symbol:
            return
        bids = d.get("bids") or []
        asks = d.get("asks") or []
        if not bids or not asks:
            return
        try:
            top_bid_px = float(bids[0][0]); top_bid_qty = float(bids[0][1])
            top_ask_px = float(asks[0][0]); top_ask_qty = float(asks[0][1])
        except (ValueError, IndexError, TypeError):
            return
        if top_bid_px <= 0 or top_ask_px <= 0 or top_ask_px <= top_bid_px:
            return

        st = self._state.setdefault(symbol, OFIState())
        ts = time.time()

        # İlk gözlem: OFI=0 olarak kalır
        if st.last_bid_px > 0:
            ofi = self._compute_ofi_increment(
                top_bid_px, top_bid_qty, top_ask_px, top_ask_qty,
                st.last_bid_px, st.last_bid_qty, st.last_ask_px, st.last_ask_qty,
            )
            st.raw.append((ts, ofi))
            # minute bucket
            cur_minute = int(ts // 60)
            if st.cur_minute == 0:
                st.cur_minute = cur_minute
            if cur_minute != st.cur_minute:
                st.minute_sums.append((st.cur_minute, st.cur_minute_acc))
                st.zscore_samples.append(st.cur_minute_acc)
                st.cur_minute = cur_minute
                st.cur_minute_acc = 0.0
            st.cur_minute_acc += ofi

        st.last_bid_px = top_bid_px
        st.last_bid_qty = top_bid_qty
        st.last_ask_px = top_ask_px
        st.last_ask_qty = top_ask_qty
        self._total_updates += 1

        if ts - st.last_publish_ts >= self._min_publish_interval:
            st.last_publish_ts = ts
            await self._publish(symbol, st, ts)

    async def on_trade(self, event) -> None:
        """Fallback OFI update from trade flow when L1 stream is unavailable.

        Uses signed trade quantity as a proxy imbalance signal. This keeps OFI
        alive during orderbook outages without disabling true ORDER_BOOK-based OFI.
        """
        d = getattr(event, "data", None) or {}
        symbol = d.get("symbol")
        if not symbol:
            return
        try:
            qty = float(d.get("quantity", 0) or 0)
            side = str(d.get("side", "buy")).lower()
        except (TypeError, ValueError):
            return
        if qty <= 0:
            return

        ts = time.time()
        st = self._state.setdefault(symbol, OFIState())
        signed_ofi = qty if side.startswith("b") else -qty
        st.raw.append((ts, signed_ofi))

        cur_minute = int(ts // 60)
        if st.cur_minute == 0:
            st.cur_minute = cur_minute
        if cur_minute != st.cur_minute:
            st.minute_sums.append((st.cur_minute, st.cur_minute_acc))
            st.zscore_samples.append(st.cur_minute_acc)
            st.cur_minute = cur_minute
            st.cur_minute_acc = 0.0
        st.cur_minute_acc += signed_ofi

        self._total_updates += 1
        if ts - st.last_publish_ts >= self._min_publish_interval:
            st.last_publish_ts = ts
            await self._publish(symbol, st, ts)

    @staticmethod
    def _compute_ofi_increment(
        b_px: float, b_qty: float, a_px: float, a_qty: float,
        lb_px: float, lb_qty: float, la_px: float, la_qty: float,
    ) -> float:
        """Tek bir L1 güncellemesi için OFI artışı."""
        # bid side
        if b_px > lb_px:
            bid_term = b_qty  # yeni daha iyi bid → likidite birikimi
        elif b_px < lb_px:
            bid_term = -lb_qty  # bid level iptal edildi
        else:
            bid_term = b_qty - lb_qty  # aynı level, qty değişimi
        # ask side
        if a_px < la_px:
            ask_term = a_qty  # yeni daha iyi ask → agresif satış likiditesi
        elif a_px > la_px:
            ask_term = -la_qty
        else:
            ask_term = a_qty - la_qty
        return float(bid_term - ask_term)

    # ──────────── rolling özetler ────────────
    def _rolling_sum(self, raw: Deque[Tuple[float, float]], window_sec: float, now: float) -> float:
        cutoff = now - window_sec
        s = 0.0
        # tail'den geriye tarama: deque, thread-safe değil ama single-threaded event loop'ta OK
        for ts, v in reversed(raw):
            if ts < cutoff:
                break
            s += v
        return float(s)

    async def _publish(self, symbol: str, st: OFIState, now: float) -> None:
        raw = st.raw
        sums = {
            "ofi_1s": self._rolling_sum(raw, 1.0, now),
            "ofi_10s": self._rolling_sum(raw, 10.0, now),
            "ofi_1m": self._rolling_sum(raw, 60.0, now),
            "ofi_5m": self._rolling_sum(raw, 300.0, now),
            "ofi_30m": self._rolling_sum(raw, 1800.0, now),
        }
        # Hurst: son 2h'ın dakika toplamları
        minute_vals = [v for _, v in st.minute_sums]
        if st.cur_minute_acc != 0.0:
            minute_vals = minute_vals + [st.cur_minute_acc]
        h = hurst_rs(minute_vals) if len(minute_vals) >= 32 else None
        # 24h z-score (dakikalık)
        z = self._zscore(st.zscore_samples, sums["ofi_1m"])

        snap = {
            **sums,
            "ofi_hurst_2h": h,
            "ofi_zscore_24h": z,
            "ts": now,
        }
        self._snapshots[symbol] = snap

        # feature_store async write (non-blocking)
        if self.feature_store is not None:
            try:
                from datetime import datetime, timezone
                asyncio.create_task(self.feature_store.write(
                    symbol=symbol,
                    ts=datetime.fromtimestamp(now, tz=timezone.utc),
                    features={f"ofi.{k}": v for k, v in snap.items() if k != "ts"},
                ))
            except Exception as e:
                logger.debug("ofi→feature_store skip: %s", e)

        # Event yayını
        if self.event_bus is not None:
            try:
                from event_bus import Event, EventType
                if hasattr(EventType, "ORDER_FLOW_IMBALANCE"):
                    await self.event_bus.publish(Event(
                        type=EventType.ORDER_FLOW_IMBALANCE,
                        source="ofi",
                        data={"symbol": symbol, **snap},
                    ))
            except Exception as e:
                logger.debug("ofi publish skip: %s", e)

    @staticmethod
    def _zscore(samples: Deque[float], x: float) -> Optional[float]:
        n = len(samples)
        if n < 30:
            return None
        m = sum(samples) / n
        var = sum((s - m) ** 2 for s in samples) / n
        std = math.sqrt(max(var, 1e-24))
        if std <= 1e-9:
            return 0.0
        return float((x - m) / std)

    # ──────────── public API ────────────
    def snapshot(self, symbol: str) -> Optional[Dict[str, Any]]:
        return self._snapshots.get(symbol)

    def all_snapshots(self) -> Dict[str, Dict[str, Any]]:
        return dict(self._snapshots)

    async def health_check(self) -> Dict[str, Any]:
        return {
            "healthy": True,
            "tracked_symbols": len(self._state),
            "total_updates": self._total_updates,
            "message": f"{len(self._state)} sembolde OFI izleniyor",
        }

    def metrics(self) -> Dict[str, Any]:
        return {
            "ofi_updates_total": self._total_updates,
            "ofi_tracked_symbols": len(self._state),
        }


# ─────────── singleton ───────────
_ofi: Optional[OrderFlowImbalanceEngine] = None


def get_ofi_engine(
    event_bus=None,
    feature_store=None,
    publish_hz: float = 2.0,
) -> OrderFlowImbalanceEngine:
    global _ofi
    if _ofi is None:
        _ofi = OrderFlowImbalanceEngine(
            event_bus=event_bus,
            feature_store=feature_store,
            publish_hz=publish_hz,
        )
    else:
        if event_bus is not None and _ofi.event_bus is None:
            _ofi.event_bus = event_bus
        if feature_store is not None and _ofi.feature_store is None:
            _ofi.feature_store = feature_store
    return _ofi
