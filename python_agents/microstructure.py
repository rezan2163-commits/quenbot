"""
microstructure.py — Piyasa mikroyapı metrikleri
================================================
Order Book Imbalance (OBI), Micro-Price (Stoikov 2018), VPIN (Easley/Lopez de Prado),
Kyle's Lambda (price impact). Tüm metrikler ring-buffer üzerinde O(1) güncellenir.

Bu modül event_bus'a `ORDER_BOOK_UPDATE` ve `SCOUT_PRICE_UPDATE` subscribe olur,
hesapladığı özellikleri `MICROSTRUCTURE_FEATURES` event'i olarak yayınlar ve
sembol-bazında in-memory snapshot tutar (brain'in okuması için).
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


@dataclass
class MicroSnapshot:
    symbol: str
    ts: float = 0.0
    mid_price: float = 0.0
    micro_price: float = 0.0  # Stoikov weighted micro-price
    obi: float = 0.0           # order book imbalance [-1, +1]
    bid_depth: float = 0.0
    ask_depth: float = 0.0
    spread_bps: float = 0.0
    vpin: float = 0.0          # volume-synchronized PIN [0,1]
    kyle_lambda: float = 0.0   # price impact per unit volume
    aggressor_buy_ratio: float = 0.0
    trade_intensity: float = 0.0  # trades per second, rolling


class MicrostructureEngine:
    """Per-symbol rolling microstructure features.

    Memory bounded: ring buffers (≤ MAX_TRADES * N_SYMBOLS). Thread-safe değil
    ama tüm event loop tek thread'de çalıştığı için sorun yok.
    """

    MAX_TRADES = 512           # per symbol
    VPIN_BUCKETS = 50          # rolling buckets for VPIN
    VPIN_BUCKET_VOL = 50.0     # volume per bucket (auto-scales per symbol below)
    KYLE_WINDOW = 100          # trades in regression window

    def __init__(self, event_bus=None):
        self.event_bus = event_bus
        self._snapshots: Dict[str, MicroSnapshot] = {}
        self._trades: Dict[str, Deque[Tuple[float, float, float, str]]] = {}
        # per-symbol VPIN running buckets: (buy_vol, sell_vol, filled_vol)
        self._vpin_state: Dict[str, Dict[str, Any]] = {}
        self._last_publish: Dict[str, float] = {}

    # ─────────── Event subscribers ───────────
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
            top_bid_p = float(bids[0][0]); top_bid_q = float(bids[0][1])
            top_ask_p = float(asks[0][0]); top_ask_q = float(asks[0][1])
        except (ValueError, IndexError, TypeError):
            return
        if top_bid_p <= 0 or top_ask_p <= 0 or top_ask_p <= top_bid_p:
            return

        # Depth sum first 5 levels
        bid_depth = sum(float(lvl[1]) for lvl in bids[:5])
        ask_depth = sum(float(lvl[1]) for lvl in asks[:5])
        total = bid_depth + ask_depth
        obi = (bid_depth - ask_depth) / total if total > 0 else 0.0

        # Stoikov micro-price:  micro = (q_a * p_b + q_b * p_a) / (q_a + q_b)
        denom = top_bid_q + top_ask_q
        if denom > 0:
            micro = (top_ask_q * top_bid_p + top_bid_q * top_ask_p) / denom
        else:
            micro = (top_bid_p + top_ask_p) / 2.0

        mid = (top_bid_p + top_ask_p) / 2.0
        spread_bps = (top_ask_p - top_bid_p) / mid * 10_000 if mid > 0 else 0.0

        snap = self._snapshots.setdefault(symbol, MicroSnapshot(symbol=symbol))
        snap.ts = time.time()
        snap.mid_price = mid
        snap.micro_price = micro
        snap.obi = obi
        snap.bid_depth = bid_depth
        snap.ask_depth = ask_depth
        snap.spread_bps = spread_bps

        await self._maybe_publish(symbol, snap)

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
        ts = time.time()

        trades = self._trades.setdefault(symbol, deque(maxlen=self.MAX_TRADES))
        trades.append((ts, price, qty, side))

        self._update_vpin(symbol, price, qty, side)
        self._update_kyle(symbol)
        self._update_aggressor(symbol)
        self._update_intensity(symbol)

        snap = self._snapshots.setdefault(symbol, MicroSnapshot(symbol=symbol))
        snap.ts = ts
        if snap.mid_price == 0:
            snap.mid_price = price
            snap.micro_price = price

        await self._maybe_publish(symbol, snap)

    # ─────────── Feature calculators ───────────
    def _update_vpin(self, symbol: str, price: float, qty: float, side: str) -> None:
        """VPIN via tick-rule bucketing (no tick classifier needed — side provided)."""
        st = self._vpin_state.setdefault(symbol, {
            "buckets": deque(maxlen=self.VPIN_BUCKETS),
            "cur_buy": 0.0, "cur_sell": 0.0, "cur_vol": 0.0,
            "bucket_vol": self.VPIN_BUCKET_VOL,
        })
        if side.startswith("b"):
            st["cur_buy"] += qty
        else:
            st["cur_sell"] += qty
        st["cur_vol"] += qty

        target = st["bucket_vol"]
        while st["cur_vol"] >= target:
            # finalize bucket
            ratio = st["cur_buy"] / max(st["cur_buy"] + st["cur_sell"], 1e-12)
            st["buckets"].append(abs(2.0 * ratio - 1.0))
            st["cur_buy"] = 0.0; st["cur_sell"] = 0.0; st["cur_vol"] = 0.0

        vpin = sum(st["buckets"]) / max(len(st["buckets"]), 1) if st["buckets"] else 0.0
        self._snapshots[symbol].vpin = float(vpin)

    def _update_kyle(self, symbol: str) -> None:
        """λ ≈ Δp / signed_vol via OLS on a rolling window.

        Basit, sağlam kapalı form: slope = cov(x,y)/var(x), x=signed_vol, y=Δp.
        """
        trades = self._trades.get(symbol)
        if not trades or len(trades) < 20:
            return
        window = list(trades)[-self.KYLE_WINDOW :]
        if len(window) < 20:
            return
        prices = [t[1] for t in window]
        xs: List[float] = []
        ys: List[float] = []
        for i in range(1, len(window)):
            dp = prices[i] - prices[i - 1]
            qty = window[i][2]
            sgn = 1.0 if window[i][3].startswith("b") else -1.0
            xs.append(sgn * qty)
            ys.append(dp)
        if not xs:
            return
        n = len(xs)
        mx = sum(xs) / n
        my = sum(ys) / n
        num = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
        den = sum((xs[i] - mx) ** 2 for i in range(n))
        lam = num / den if den > 1e-12 else 0.0
        # normalize by price for cross-symbol comparability
        mid = self._snapshots[symbol].mid_price or prices[-1] or 1.0
        self._snapshots[symbol].kyle_lambda = float(lam / mid * 1e6)  # per million units of notional

    def _update_aggressor(self, symbol: str) -> None:
        trades = self._trades.get(symbol)
        if not trades:
            return
        recent = list(trades)[-100:]
        buy = sum(t[2] for t in recent if t[3].startswith("b"))
        sell = sum(t[2] for t in recent if not t[3].startswith("b"))
        tot = buy + sell
        self._snapshots[symbol].aggressor_buy_ratio = float(buy / tot) if tot > 0 else 0.5

    def _update_intensity(self, symbol: str) -> None:
        trades = self._trades.get(symbol)
        if not trades or len(trades) < 5:
            return
        recent = list(trades)[-60:]
        dt = recent[-1][0] - recent[0][0]
        self._snapshots[symbol].trade_intensity = len(recent) / dt if dt > 0 else 0.0

    # ─────────── Publish ───────────
    async def _maybe_publish(self, symbol: str, snap: MicroSnapshot) -> None:
        now = time.time()
        if now - self._last_publish.get(symbol, 0.0) < 1.0:  # 1 Hz max
            return
        self._last_publish[symbol] = now
        if not self.event_bus:
            return
        try:
            from event_bus import Event, EventType  # local import to avoid cycles
            if not hasattr(EventType, "MICROSTRUCTURE_FEATURES"):
                return
            await self.event_bus.publish(Event(
                type=EventType.MICROSTRUCTURE_FEATURES,
                source="microstructure",
                data={
                    "symbol": symbol,
                    "mid_price": snap.mid_price,
                    "micro_price": snap.micro_price,
                    "obi": snap.obi,
                    "vpin": snap.vpin,
                    "kyle_lambda": snap.kyle_lambda,
                    "spread_bps": snap.spread_bps,
                    "aggressor_buy_ratio": snap.aggressor_buy_ratio,
                    "trade_intensity": snap.trade_intensity,
                    "ts": snap.ts,
                },
            ))
        except Exception as e:
            logger.debug(f"microstructure publish skipped: {e}")

    # ─────────── Public snapshot API (for brain/strategist) ───────────
    def snapshot(self, symbol: str) -> Optional[Dict[str, Any]]:
        snap = self._snapshots.get(symbol)
        if not snap:
            return None
        return {
            "symbol": snap.symbol,
            "mid_price": snap.mid_price,
            "micro_price": snap.micro_price,
            "obi": snap.obi,
            "vpin": snap.vpin,
            "kyle_lambda": snap.kyle_lambda,
            "spread_bps": snap.spread_bps,
            "aggressor_buy_ratio": snap.aggressor_buy_ratio,
            "trade_intensity": snap.trade_intensity,
            "age_s": max(0.0, time.time() - snap.ts) if snap.ts else None,
        }

    def all_snapshots(self) -> Dict[str, Dict[str, Any]]:
        return {s: self.snapshot(s) for s in self._snapshots.keys() if self._snapshots[s].mid_price > 0}

    async def health_check(self) -> Dict[str, Any]:
        alive = [s for s, v in self._snapshots.items() if v.mid_price > 0]
        return {"healthy": True, "tracked_symbols": len(alive), "message": f"{len(alive)} sembol izleniyor"}


# module-level singleton for easy access
_engine: Optional[MicrostructureEngine] = None


def get_microstructure_engine(event_bus=None) -> MicrostructureEngine:
    global _engine
    if _engine is None:
        _engine = MicrostructureEngine(event_bus=event_bus)
    elif event_bus is not None and _engine.event_bus is None:
        _engine.event_bus = event_bus
    return _engine
