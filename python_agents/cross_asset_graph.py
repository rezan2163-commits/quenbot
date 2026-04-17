"""
Cross-Asset Graph — Phase 2 Intel Upgrade
==========================================
Sembol evreni üzerinde **lead/lag** ve **spillover** yapısını tahmin eder.

Yaklaşım:
  1. Her sembol için `scout.price_update` akışından 15 sn bin'lere düşürülmüş
     log-return serisi tutulur (son 2 saat).
  2. Periyodik olarak (her CROSS_ASSET_REBUILD_INTERVAL_MIN dk):
        - Tüm çiftler için cross-correlation fonksiyonu hesaplanır, argmax lag
          alınır → yön + gecikme kenarı.
        - |ρ| < MIN_EDGE_STRENGTH ise kenar atılır.
        - Lead/lag graph yönlendirilir: argmax_lag > 0 ise A → B (A liderdir).
  3. Bir leader sembolde "büyük hamle" algılanırsa (|return| ≥ LEADER_MIN_BPS),
     komşu düğümlere `LEAD_LAG_ALERT` event'i yayılır.
  4. Graph snapshot JSON olarak diske yazılır; `/api/cross-asset/graph`
     endpoint'inden sorgulanabilir.
  5. Confluence engine bir `cross_asset_spillover` sinyali sorgulayabilir:
     ilgili sembolün "açık" leader alert'i varsa z-skoru pozitif/negatif.

Bağımlılıklar: sadece stdlib + zaten mevcut numpy. networkx yok.

Gerçek-zamanlı güvenlik:
  * bounded deque'ler, O(N²) rebuild periyodik (ana akış bloklanmaz).
  * Her türlü hesap hatası sessizce yakalanır, önceki snapshot geçerli kalır.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

try:
    import numpy as np  # type: ignore
    _HAS_NUMPY = True
except Exception:
    _HAS_NUMPY = False

logger = logging.getLogger(__name__)


# ──────────── data classes ────────────
@dataclass
class _Series:
    """Bir sembolün binlenmiş log-return serisi."""
    # bin_index -> mean log return (bin_index = floor(ts / step))
    bins: Dict[int, Tuple[float, int]] = field(default_factory=dict)
    last_price: Optional[float] = None
    last_ts: float = 0.0
    total_ticks: int = 0

    def add_tick(self, ts: float, price: float, step_sec: int) -> None:
        self.total_ticks += 1
        if price <= 0:
            return
        if self.last_price is None or self.last_price <= 0:
            self.last_price = price
            self.last_ts = ts
            return
        # log return
        try:
            r = math.log(price / self.last_price)
        except ValueError:
            return
        bi = int(ts // step_sec)
        cur = self.bins.get(bi)
        if cur is None:
            self.bins[bi] = (r, 1)
        else:
            s, n = cur
            self.bins[bi] = (s + r, n + 1)
        self.last_price = price
        self.last_ts = ts

    def prune(self, min_bin: int) -> None:
        if not self.bins:
            return
        for k in [k for k in self.bins if k < min_bin]:
            del self.bins[k]

    def vector(self, start_bin: int, end_bin: int) -> List[float]:
        """[start, end) aralığında bin başına mean return; eksik binler 0.0."""
        out = [0.0] * (end_bin - start_bin)
        for bi, (s, n) in self.bins.items():
            if start_bin <= bi < end_bin and n > 0:
                out[bi - start_bin] = s / n
        return out


@dataclass
class Edge:
    src: str
    dst: str
    lag_bins: int      # src, dst'den `lag_bins` bin önce hareket eder
    rho: float         # [-1, 1] — argmax cross-correlation
    samples: int


@dataclass
class LeaderAlert:
    leader: str
    followers: List[Tuple[str, int, float]]   # (dst, lag_sec, rho)
    move_bps: float
    direction: str
    ts: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "leader": self.leader,
            "followers": [
                {"symbol": s, "expected_lag_sec": l, "rho": round(r, 3)}
                for s, l, r in self.followers
            ],
            "leader_move_bps": round(self.move_bps, 2),
            "direction": self.direction,
            "ts": self.ts,
        }


# ──────────── cross-correlation ────────────
def _crosscorr(x: List[float], y: List[float], max_lag: int) -> Tuple[int, float]:
    """
    argmax_{l ∈ [-max_lag, max_lag]} ρ(x_{t}, y_{t+l}).
    Pozitif lag: y, x'ten sonra gelir → x lider.
    Negatif lag: y, x'ten önce gelir → y lider.

    Döner (best_lag, best_rho). Yetersiz veri → (0, 0.0).
    """
    n = len(x)
    if n < 10 or len(y) != n:
        return 0, 0.0

    if _HAS_NUMPY:
        xa = np.asarray(x, dtype=np.float64)
        ya = np.asarray(y, dtype=np.float64)
        xm = xa - xa.mean()
        ym = ya - ya.mean()
        xs = xm.std()
        ys = ym.std()
        if xs < 1e-12 or ys < 1e-12:
            return 0, 0.0
        best_lag = 0
        best_rho = 0.0
        for lag in range(-max_lag, max_lag + 1):
            if lag >= 0:
                a = xm[: n - lag]
                b = ym[lag:]
            else:
                a = xm[-lag:]
                b = ym[: n + lag]
            if len(a) < 10:
                continue
            denom = xs * ys * len(a)
            if denom < 1e-12:
                continue
            r = float(np.dot(a, b) / denom)
            if abs(r) > abs(best_rho):
                best_rho = r
                best_lag = lag
        return best_lag, best_rho

    # Pure-python fallback (numpy yoksa)
    def mean(v: List[float]) -> float:
        return sum(v) / len(v) if v else 0.0

    def std(v: List[float], m: float) -> float:
        if not v:
            return 0.0
        var = sum((a - m) ** 2 for a in v) / len(v)
        return math.sqrt(var)

    xm = mean(x)
    ym = mean(y)
    xs = std(x, xm)
    ys = std(y, ym)
    if xs < 1e-12 or ys < 1e-12:
        return 0, 0.0
    best_lag, best_rho = 0, 0.0
    for lag in range(-max_lag, max_lag + 1):
        if lag >= 0:
            a = x[: n - lag]
            b = y[lag:]
        else:
            a = x[-lag:]
            b = y[: n + lag]
        if len(a) < 10:
            continue
        s = 0.0
        for i in range(len(a)):
            s += (a[i] - xm) * (b[i] - ym)
        r = s / (xs * ys * len(a))
        if abs(r) > abs(best_rho):
            best_rho = r
            best_lag = lag
    return best_lag, best_rho


# ──────────── engine ────────────
class CrossAssetGraphEngine:
    """Cross-asset lead/lag grafiği + spillover alert yayını."""

    def __init__(
        self,
        event_bus=None,
        feature_store=None,
        symbols: Optional[List[str]] = None,
        step_sec: int = 15,
        history_sec: int = 7200,
        max_lag_sec: int = 300,
        min_samples: int = 60,
        min_edge: float = 0.08,
        rebuild_interval_sec: int = 900,
        alert_cooldown_sec: int = 60,
        leader_min_bps: float = 15.0,
        graph_path: str = "python_agents/.cross_asset/latest_graph.json",
    ) -> None:
        self.event_bus = event_bus
        self.feature_store = feature_store
        self.symbols: List[str] = list(symbols or [])
        self.step_sec = max(5, int(step_sec))
        self.history_sec = max(self.step_sec * 20, int(history_sec))
        self.max_lag_sec = max(self.step_sec, int(max_lag_sec))
        self.max_lag_bins = max(1, self.max_lag_sec // self.step_sec)
        self.min_samples = max(20, int(min_samples))
        self.min_edge = float(min_edge)
        self.rebuild_interval_sec = max(30, int(rebuild_interval_sec))
        self.alert_cooldown_sec = max(1, int(alert_cooldown_sec))
        self.leader_min_bps = float(leader_min_bps)
        self.graph_path = graph_path

        self._series: Dict[str, _Series] = {s: _Series() for s in self.symbols}
        self._edges: List[Edge] = []
        self._last_rebuild: float = 0.0
        self._last_leader_alert_ts: Dict[str, float] = {}
        self._active_spillovers: Dict[str, Tuple[float, float]] = {}
        # dst -> (expires_at, signed_z)
        self._rebuilds = 0
        self._alerts = 0
        self._rebuild_task: Optional[asyncio.Task] = None

    # ──────────── event handlers ────────────
    async def on_price_update(self, event) -> None:
        d = getattr(event, "data", None) or {}
        symbol = d.get("symbol")
        if not symbol:
            return
        try:
            price = float(d.get("price", 0) or 0)
        except (TypeError, ValueError):
            return
        if price <= 0:
            return
        ts_raw = d.get("timestamp")
        if isinstance(ts_raw, (int, float)):
            ts = float(ts_raw)
        elif isinstance(ts_raw, str):
            try:
                ts = datetime.fromisoformat(ts_raw).timestamp()
            except Exception:
                ts = time.time()
        elif isinstance(ts_raw, datetime):
            ts = ts_raw.timestamp()
        else:
            ts = time.time()

        s = self._series.get(symbol)
        if s is None:
            if symbol not in self.symbols:
                self.symbols.append(symbol)
            s = _Series()
            self._series[symbol] = s
        prev_price = s.last_price
        s.add_tick(ts, price, self.step_sec)

        # Leader hareketi tespiti: kısa pencerede büyük return
        if prev_price and prev_price > 0:
            try:
                move_bps = (price / prev_price - 1.0) * 10000.0
            except Exception:
                move_bps = 0.0
            if abs(move_bps) >= self.leader_min_bps:
                await self._maybe_emit_leader_alert(symbol, move_bps, ts)

        # history pruning (lazy)
        if s.total_ticks % 200 == 0:
            min_bin = int((ts - self.history_sec) // self.step_sec)
            s.prune(min_bin)

    # ──────────── periyodik rebuild ────────────
    async def rebuild_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self.rebuild_interval_sec)
                await self.rebuild()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception("cross_asset rebuild loop hata: %s", e)

    async def rebuild(self) -> None:
        """Tüm (A,B) çiftleri için lead/lag grafiğini yeniden hesapla."""
        now = time.time()
        end_bin = int(now // self.step_sec)
        start_bin = end_bin - (self.history_sec // self.step_sec)
        syms = [s for s, ser in self._series.items()
                if sum(n for _, n in ser.bins.values()) >= self.min_samples]
        if len(syms) < 2:
            logger.debug("cross_asset rebuild skip: %d valid symbols", len(syms))
            self._last_rebuild = now
            return

        vectors: Dict[str, List[float]] = {
            s: self._series[s].vector(start_bin, end_bin) for s in syms
        }
        new_edges: List[Edge] = []
        for i, a in enumerate(syms):
            xa = vectors[a]
            for b in syms[i + 1:]:
                xb = vectors[b]
                try:
                    lag, rho = _crosscorr(xa, xb, self.max_lag_bins)
                except Exception as e:
                    logger.debug("crosscorr %s~%s hata: %s", a, b, e)
                    continue
                if abs(rho) < self.min_edge:
                    continue
                if lag > 0:
                    new_edges.append(Edge(src=a, dst=b, lag_bins=lag, rho=rho, samples=len(xa)))
                elif lag < 0:
                    new_edges.append(Edge(src=b, dst=a, lag_bins=-lag, rho=rho, samples=len(xa)))
                # lag==0 → simetrik, lead/lag yönü yok: atla

        self._edges = new_edges
        self._last_rebuild = now
        self._rebuilds += 1
        logger.info("cross_asset graph rebuilt: %d nodes, %d edges", len(syms), len(new_edges))
        await self._persist_graph(syms)
        await self._publish_graph_event(syms)

    async def _persist_graph(self, nodes: List[str]) -> None:
        try:
            payload = {
                "ts": self._last_rebuild,
                "step_sec": self.step_sec,
                "history_sec": self.history_sec,
                "nodes": nodes,
                "edges": [
                    {
                        "src": e.src, "dst": e.dst,
                        "lag_sec": e.lag_bins * self.step_sec,
                        "rho": round(e.rho, 4),
                        "samples": e.samples,
                    }
                    for e in self._edges
                ],
            }
            p = Path(self.graph_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as e:
            logger.debug("cross_asset persist skip: %s", e)

    async def _publish_graph_event(self, nodes: List[str]) -> None:
        if self.event_bus is None:
            return
        try:
            from event_bus import Event, EventType
            if hasattr(EventType, "CROSS_ASSET_GRAPH_UPDATED"):
                await self.event_bus.publish(Event(
                    type=EventType.CROSS_ASSET_GRAPH_UPDATED,
                    source="cross_asset_graph",
                    data={
                        "ts": self._last_rebuild,
                        "node_count": len(nodes),
                        "edge_count": len(self._edges),
                    },
                ))
        except Exception as e:
            logger.debug("cross_asset graph event skip: %s", e)

    # ──────────── leader alerts ────────────
    async def _maybe_emit_leader_alert(self, leader: str, move_bps: float, ts: float) -> None:
        last = self._last_leader_alert_ts.get(leader, 0.0)
        if ts - last < self.alert_cooldown_sec:
            return
        followers = self.followers_of(leader)
        if not followers:
            return
        self._last_leader_alert_ts[leader] = ts
        direction = "up" if move_bps > 0 else "down"
        alert = LeaderAlert(
            leader=leader,
            followers=followers,
            move_bps=move_bps,
            direction=direction,
            ts=ts,
        )

        # Follower'ların confluence'ına spillover sinyali besle
        signed = math.copysign(min(3.0, abs(move_bps) / max(1.0, self.leader_min_bps)), move_bps)
        for dst, lag_sec, _rho in followers:
            self._active_spillovers[dst] = (ts + 2 * max(lag_sec, self.step_sec), signed)

        self._alerts += 1
        if self.event_bus is not None:
            try:
                from event_bus import Event, EventType
                if hasattr(EventType, "LEAD_LAG_ALERT"):
                    await self.event_bus.publish(Event(
                        type=EventType.LEAD_LAG_ALERT,
                        source="cross_asset_graph",
                        data=alert.to_dict(),
                    ))
            except Exception as e:
                logger.debug("lead_lag publish skip: %s", e)

    # ──────────── public API ────────────
    def followers_of(self, leader: str) -> List[Tuple[str, int, float]]:
        """leader -> [(follower, expected_lag_sec, rho), ...] |rho| desc."""
        out: List[Tuple[str, int, float]] = []
        for e in self._edges:
            if e.src == leader:
                out.append((e.dst, e.lag_bins * self.step_sec, e.rho))
        out.sort(key=lambda x: abs(x[2]), reverse=True)
        return out[:8]

    def leaders_of(self, follower: str) -> List[Tuple[str, int, float]]:
        out: List[Tuple[str, int, float]] = []
        for e in self._edges:
            if e.dst == follower:
                out.append((e.src, e.lag_bins * self.step_sec, e.rho))
        out.sort(key=lambda x: abs(x[2]), reverse=True)
        return out[:8]

    def spillover_signal(self, symbol: str) -> float:
        """
        Confluence engine için signed magnitude:
          aktif leader alert yoksa 0.0;
          varsa ±z-benzeri (±3σ clip edilmiş).
        """
        exp = self._active_spillovers.get(symbol)
        if not exp:
            return 0.0
        expires_at, signed = exp
        if time.time() > expires_at:
            del self._active_spillovers[symbol]
            return 0.0
        return signed

    def graph_snapshot(self) -> Dict[str, Any]:
        return {
            "ts": self._last_rebuild,
            "nodes": list(self._series.keys()),
            "edges": [
                {
                    "src": e.src, "dst": e.dst,
                    "lag_sec": e.lag_bins * self.step_sec,
                    "rho": round(e.rho, 4),
                }
                for e in self._edges
            ],
        }

    async def health_check(self) -> Dict[str, Any]:
        return {
            "healthy": True,
            "tracked_symbols": len(self._series),
            "edges": len(self._edges),
            "last_rebuild": self._last_rebuild,
            "rebuilds_total": self._rebuilds,
            "alerts_total": self._alerts,
            "active_spillovers": len(self._active_spillovers),
            "message": f"{len(self._edges)} kenar, {len(self._series)} sembol",
        }

    def metrics(self) -> Dict[str, Any]:
        return {
            "cross_asset_edges": len(self._edges),
            "cross_asset_symbols": len(self._series),
            "cross_asset_rebuilds_total": self._rebuilds,
            "cross_asset_alerts_total": self._alerts,
            "cross_asset_active_spillovers": len(self._active_spillovers),
        }


_engine: Optional[CrossAssetGraphEngine] = None


def get_cross_asset_engine(*args, **kwargs) -> CrossAssetGraphEngine:
    global _engine
    if _engine is None:
        _engine = CrossAssetGraphEngine(*args, **kwargs)
    return _engine
