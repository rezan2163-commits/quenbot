"""
Backfill features from trades (Phase 4 Finalization helper)
============================================================
Gorevi: verilen `(symbol, start, end)` araligi icin ham `trades`
tablosundan 1-dakikalik feature satirlari uretmek. Live singleton
motorlari KIRLETMEZ — her cagri temiz in-memory motor kurar.

`backfill_counterfactuals.py` feature_store kapsami olmayan zaman
pencereleri icin bu fallback'i kullanir.

Not: Bu modul mock-friendly. `--mock` modu icin `Database`'in
`fetch_trades(symbol, start, end)` signature'i soz konusu oldugunda
bir MockDatabase verilebilir.
"""
from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class FeatureRow:
    ts: datetime
    symbol: str
    mid_price: float
    vwap_1m: float
    ret_1m: float
    ret_5m: float
    vol_1m: float
    trade_count_1m: int
    buy_ratio_1m: float
    # lightweight microstructure placeholders
    ofi_proxy: float = 0.0
    returns_zscore_5m: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ts": self.ts.isoformat(),
            "symbol": self.symbol,
            "mid_price": self.mid_price,
            "vwap_1m": self.vwap_1m,
            "ret_1m": self.ret_1m,
            "ret_5m": self.ret_5m,
            "vol_1m": self.vol_1m,
            "trade_count_1m": self.trade_count_1m,
            "buy_ratio_1m": self.buy_ratio_1m,
            "ofi_proxy": self.ofi_proxy,
            "returns_zscore_5m": self.returns_zscore_5m,
        }


async def _fetch_trades(db: Any, symbol: str, start: datetime, end: datetime) -> List[Dict[str, Any]]:
    """Pool-safe trades fetch (timestamp + price + quantity + side)."""
    if db is None:
        return []
    fetcher = getattr(db, "fetch", None)
    if fetcher is None:
        return []
    try:
        rows = await fetcher(
            "SELECT timestamp, price, quantity, side FROM trades"
            " WHERE symbol=$1 AND timestamp BETWEEN $2 AND $3"
            " ORDER BY timestamp ASC",
            symbol, start, end,
        )
        return list(rows or [])
    except Exception as exc:
        logger.debug("fetch_trades %s skipped: %s", symbol, exc)
        return []


def _minute_bucket(ts: datetime) -> datetime:
    return ts.replace(second=0, microsecond=0)


async def build_feature_rows(
    db: Any,
    symbol: str,
    start: datetime,
    end: datetime,
) -> List[FeatureRow]:
    """Verilen zaman araligi icin 1-dakikalik feature satirlari."""
    trades = await _fetch_trades(db, symbol, start, end)
    if not trades:
        return []
    # group by minute
    buckets: Dict[datetime, List[Dict[str, Any]]] = {}
    for t in trades:
        ts = t.get("timestamp")
        if isinstance(ts, str):
            try:
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:
                continue
        if not isinstance(ts, datetime):
            continue
        buckets.setdefault(_minute_bucket(ts), []).append(t)

    rows: List[FeatureRow] = []
    ordered_ts = sorted(buckets.keys())
    price_history: List[Tuple[datetime, float]] = []
    for ts in ordered_ts:
        bucket = buckets[ts]
        if not bucket:
            continue
        prices: List[float] = []
        volumes: List[float] = []
        buys = 0
        for tr in bucket:
            try:
                p = float(tr.get("price") or 0)
                q = float(tr.get("quantity") or 0)
                side = str(tr.get("side") or "").lower()
            except Exception:
                continue
            if p <= 0 or q <= 0:
                continue
            prices.append(p)
            volumes.append(q)
            if side in {"buy", "bid", "b"}:
                buys += 1
        if not prices:
            continue
        total_vol = sum(volumes)
        vwap = (
            sum(p * v for p, v in zip(prices, volumes)) / total_vol
            if total_vol > 0 else (sum(prices) / len(prices))
        )
        mid = (min(prices) + max(prices)) / 2.0
        # ret_1m vs previous minute close
        ret_1m = 0.0
        if price_history:
            prev_px = price_history[-1][1]
            if prev_px > 0:
                ret_1m = (mid - prev_px) / prev_px
        # ret_5m
        ret_5m = 0.0
        cutoff_5m = ts - timedelta(minutes=5)
        base_5m = next((px for (pts, px) in price_history if pts >= cutoff_5m), None)
        if base_5m and base_5m > 0:
            ret_5m = (mid - base_5m) / base_5m
        # returns zscore over last 5m (if enough history)
        recent_returns: List[float] = []
        for i in range(1, len(price_history)):
            if price_history[i][0] < cutoff_5m:
                continue
            prev = price_history[i - 1][1]
            curr = price_history[i][1]
            if prev > 0:
                recent_returns.append((curr - prev) / prev)
        zscore = 0.0
        if len(recent_returns) > 3:
            mean_r = sum(recent_returns) / len(recent_returns)
            var = sum((r - mean_r) ** 2 for r in recent_returns) / max(1, len(recent_returns) - 1)
            std = math.sqrt(max(var, 1e-12))
            zscore = (ret_1m - mean_r) / std if std > 0 else 0.0
        # OFI proxy: buy_count_ratio deviation from 0.5
        buy_ratio = buys / len(bucket) if bucket else 0.5
        ofi_proxy = (buy_ratio - 0.5) * 2.0
        rows.append(FeatureRow(
            ts=ts,
            symbol=symbol,
            mid_price=mid,
            vwap_1m=vwap,
            ret_1m=ret_1m,
            ret_5m=ret_5m,
            vol_1m=total_vol,
            trade_count_1m=len(bucket),
            buy_ratio_1m=buy_ratio,
            ofi_proxy=ofi_proxy,
            returns_zscore_5m=zscore,
        ))
        price_history.append((ts, mid))
        # bound history
        if len(price_history) > 60:
            price_history = price_history[-60:]
    return rows


def pick_feature_at(
    rows: List[FeatureRow], target_ts: datetime, tolerance_min: int = 5
) -> Optional[FeatureRow]:
    """`target_ts`'e en yakin (gecmisteki) feature satirini dondurur."""
    if not rows:
        return None
    tol = timedelta(minutes=int(tolerance_min))
    best: Optional[FeatureRow] = None
    for r in rows:
        if r.ts > target_ts:
            break
        if target_ts - r.ts <= tol:
            best = r
    return best


__all__ = ["FeatureRow", "build_feature_rows", "pick_feature_at"]
