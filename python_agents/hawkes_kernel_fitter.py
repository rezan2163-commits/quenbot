"""
hawkes_kernel_fitter.py — Kendini tetikleyen emir akışı (§2)
==============================================================
Bacry-Mastromatteo-Muzy (2015) finansal Hawkes uyarlaması. Rolling pencere
üzerinde (default 30 dk) exponential-kernel multivariate Hawkes process
fit eder:

    λ_i(t) = μ_i + Σ_j ∫_{-∞}^{t} α_{ij} e^{-β_{ij}(t-s)} dN_j(s)

Basit, nümerik stabil EM (Lewis thinning yerine kontrol değişken yaklaşımı).
Marks: {buy, sell, cancel, iceberg_refill, large_trade}. 'tick' lib opsiyonel;
default pure-python implementasyon (< 300 satır).

Branching ratio n = α/β → kritiklik göstergesi. Asimetrik α: yön dominansı.
Oracle kanalı: 'hawkes_branching_ratio' ∈ [-1,+1] (buy-sell dominansı imzalı).

Graceful degradation:
    min_events altında "insufficient" döner. tick yoksa yerel EM.
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


MARK_TYPES = ("buy", "sell", "cancel", "iceberg_refill", "large_trade")
_M = len(MARK_TYPES)
_MARK_IDX = {m: i for i, m in enumerate(MARK_TYPES)}


def _fit_exp_hawkes(
    events: List[Tuple[float, int]],
    window_sec: float,
    beta_init: float = 1.0,
    iterations: int = 50,
) -> Tuple[List[float], List[List[float]], float]:
    """Basit EM: sabit β, μ ve α tahmini. Logl-likelihood döner.

    Returns:
        mu: list[M] baseline rates
        alpha: M×M excitation
        loglik: log-likelihood
    """
    if not events:
        return [0.0] * _M, [[0.0] * _M for _ in range(_M)], 0.0
    T = max(1e-6, window_sec)
    counts = [0] * _M
    for _, m in events:
        counts[m] += 1
    # init
    mu = [max(1e-6, c / T) for c in counts]
    alpha = [[0.1 if i != j else 0.2 for j in range(_M)] for i in range(_M)]
    beta = [[beta_init for _ in range(_M)] for _ in range(_M)]
    # sorted events
    events = sorted(events, key=lambda e: e[0])
    # EM loop
    loglik = 0.0
    for _ in range(iterations):
        # per-event branching probability p_{k <- j}
        # denom_k = mu_{m_k} + Σ_j Σ_{t_i < t_k, m_i=j} α_{j,m_k} e^{-β_{j,m_k}(t_k-t_i)}
        # Use single-kernel exponential recursion R_{m_i→m_k}
        R = [[0.0] * _M for _ in range(_M)]  # R[j][i] = contribution of mark-j events to intensity for mark-i
        last_t = events[0][0]
        contrib_sum = [[0.0] * _M for _ in range(_M)]  # for alpha M-step
        baseline_sum = [0.0] * _M
        total_ll = 0.0
        for (t_k, m_k) in events:
            dt = t_k - last_t
            if dt > 0:
                for j in range(_M):
                    for i in range(_M):
                        R[j][i] *= math.exp(-beta[j][i] * dt)
            last_t = t_k
            # intensity for m_k
            inten = mu[m_k]
            for j in range(_M):
                inten += alpha[j][m_k] * R[j][m_k]
            inten = max(inten, 1e-12)
            # soft branching: baseline / other contributions
            baseline_sum[m_k] += mu[m_k] / inten
            for j in range(_M):
                contrib_sum[j][m_k] += (alpha[j][m_k] * R[j][m_k]) / inten
            total_ll += math.log(inten)
            # add self contribution AFTER likelihood
            R[m_k][m_k] += 1.0  # unit mark contribution at t_k
            for i in range(_M):
                if i != m_k:
                    R[m_k][i] += 1.0
        # integral term ≈ μ T + Σ α (since ∫₀^T e^{-βτ}dτ ≈ 1/β per event assuming T » 1/β)
        # We use 1-exp(-βT)/β ≈ 1/β for simplicity on ea
        integral = sum(mu[i] * T for i in range(_M))
        for j in range(_M):
            for i in range(_M):
                integral += alpha[j][i] * counts[j] / max(beta[j][i], 1e-9)
        loglik = total_ll - integral
        # M-step
        new_mu = [max(1e-9, baseline_sum[i] / T) for i in range(_M)]
        new_alpha = [[max(1e-9, contrib_sum[j][i] / max(1, counts[j])) for i in range(_M)] for j in range(_M)]
        # convergence
        diff = sum(abs(new_mu[i] - mu[i]) for i in range(_M))
        diff += sum(abs(new_alpha[j][i] - alpha[j][i]) for j in range(_M) for i in range(_M))
        mu, alpha = new_mu, new_alpha
        if diff < 1e-6:
            break
    return mu, alpha, loglik


@dataclass
class _SymbolState:
    events: Deque[Tuple[float, int]] = field(default_factory=lambda: deque(maxlen=20000))
    last_publish_ts: float = 0.0
    last_mu: List[float] = field(default_factory=lambda: [0.0] * _M)
    last_alpha: List[List[float]] = field(default_factory=lambda: [[0.0] * _M for _ in range(_M)])
    last_branching: float = 0.0
    last_channel_value: float = 0.0


class HawkesKernelFitter:
    PUBLISH_HZ_DEFAULT = 0.5
    ORACLE_CHANNEL_NAME = "hawkes_branching_ratio"

    def __init__(
        self,
        event_bus: Any = None,
        feature_store: Any = None,
        signal_bus: Any = None,
        window_min: int = 30,
        em_iter: int = 50,
        min_events: int = 500,
        publish_hz: float = 0.5,
        beta_init: float = 1.0,
    ) -> None:
        self.event_bus = event_bus
        self.feature_store = feature_store
        self.signal_bus = signal_bus
        self.window_sec = int(window_min) * 60
        self.em_iter = int(em_iter)
        self.min_events = int(min_events)
        self.publish_interval = 1.0 / max(0.01, publish_hz)
        self.beta_init = float(beta_init)
        self._states: Dict[str, _SymbolState] = {}
        self._stats = {"events": 0, "fits": 0, "publishes": 0}
        self._initialized = False

    async def initialize(self) -> None:
        self._initialized = True
        logger.info(
            "HawkesKernelFitter ready (window=%dm, em_iter=%d, min_events=%d)",
            self.window_sec // 60, self.em_iter, self.min_events,
        )

    def observe(self, symbol: str, mark: str, ts: Optional[float] = None) -> None:
        if not symbol or mark not in _MARK_IDX:
            return
        ts = float(ts) if ts is not None else time.time()
        st = self._states.setdefault(symbol, _SymbolState())
        st.events.append((ts, _MARK_IDX[mark]))
        cutoff = ts - self.window_sec
        while st.events and st.events[0][0] < cutoff:
            st.events.popleft()
        self._stats["events"] += 1

    def _fit(self, symbol: str, ts: float) -> Optional[Tuple[List[float], List[List[float]], float]]:
        st = self._states.get(symbol)
        if st is None:
            return None
        events = [e for e in st.events if e[0] >= ts - self.window_sec]
        if len(events) < self.min_events:
            return None
        # normalize timestamps to [0, T]
        t0 = events[0][0]
        rel = [(e[0] - t0, e[1]) for e in events]
        T = rel[-1][0] or 1.0
        mu, alpha, ll = _fit_exp_hawkes(rel, window_sec=T, beta_init=self.beta_init, iterations=self.em_iter)
        self._stats["fits"] += 1
        return mu, alpha, ll

    def maybe_publish(self, symbol: str, ts: Optional[float] = None) -> Optional[Dict[str, Any]]:
        st = self._states.get(symbol)
        if st is None:
            return None
        ts = float(ts) if ts is not None else time.time()
        if (ts - st.last_publish_ts) < self.publish_interval:
            return None
        fit = self._fit(symbol, ts)
        if fit is None:
            return None
        mu, alpha, ll = fit
        st.last_mu = mu
        st.last_alpha = alpha
        # branching ratio: max eigenvalue approx = spectral radius ≈ max row sum / beta
        n = max(sum(alpha[j]) for j in range(_M)) / max(self.beta_init, 1e-9)
        st.last_branching = n
        # signed dominance: (α[buy→*] sum − α[sell→*] sum) normalized
        buy = _MARK_IDX["buy"]
        sell = _MARK_IDX["sell"]
        buy_excite = sum(alpha[buy])
        sell_excite = sum(alpha[sell])
        denom = buy_excite + sell_excite + 1e-9
        dominance = (buy_excite - sell_excite) / denom
        ch_val = max(-1.0, min(1.0, dominance * min(1.0, n)))
        st.last_channel_value = ch_val
        st.last_publish_ts = ts
        if self.signal_bus is not None:
            try:
                self.signal_bus.publish(
                    symbol=symbol, channel=self.ORACLE_CHANNEL_NAME, value=ch_val,
                    source="hawkes_kernel", extra={"branching_ratio": n, "dominance": dominance, "loglik": ll},
                )
            except Exception as e:
                logger.debug("Hawkes signal_bus skip: %s", e)
        self._stats["publishes"] += 1
        out = {"symbol": symbol, "ts": ts, "branching_ratio": n, "dominance": dominance, "loglik": ll}
        if self.event_bus is not None:
            try:
                from event_bus import EventType, Event
                asyncio.create_task(
                    self.event_bus.publish(Event(type=EventType.HAWKES_KERNEL_UPDATE, source="hawkes_kernel", data=out))
                )
            except Exception as e:
                logger.debug("Hawkes event skip: %s", e)
        if self.feature_store is not None:
            try:
                from datetime import datetime, timezone
                self.feature_store.write(
                    symbol=symbol, ts=datetime.fromtimestamp(ts, tz=timezone.utc),
                    features={"hawkes_branching": n, "hawkes_dominance": dominance},
                )
            except Exception as e:
                logger.debug("Hawkes fs skip: %s", e)
        return out

    def snapshot(self, symbol: str) -> Optional[Dict[str, Any]]:
        st = self._states.get(symbol)
        if st is None:
            return None
        return {
            "symbol": symbol,
            "branching_ratio": st.last_branching,
            "channel_value": st.last_channel_value,
            "events_in_window": len(st.events),
        }

    def all_snapshots(self) -> Dict[str, Dict[str, Any]]:
        return {sym: self.snapshot(sym) for sym in self._states}

    def oracle_channel_value(self, symbol: str) -> Optional[float]:
        st = self._states.get(symbol)
        return None if st is None else st.last_channel_value

    async def health_check(self) -> Dict[str, Any]:
        return {"healthy": self._initialized, "symbols": len(self._states), **self._stats}

    def metrics(self) -> Dict[str, Any]:
        return {
            "hawkes_events_total": self._stats["events"],
            "hawkes_fits_total": self._stats["fits"],
            "hawkes_publishes_total": self._stats["publishes"],
            "hawkes_symbols_active": len(self._states),
        }


_instance: Optional[HawkesKernelFitter] = None


def get_hawkes_fitter(
    event_bus: Any = None, feature_store: Any = None, signal_bus: Any = None, **kwargs: Any,
) -> HawkesKernelFitter:
    global _instance
    if _instance is None:
        _instance = HawkesKernelFitter(
            event_bus=event_bus, feature_store=feature_store, signal_bus=signal_bus, **kwargs,
        )
    else:
        if event_bus is not None and _instance.event_bus is None:
            _instance.event_bus = event_bus
        if feature_store is not None and _instance.feature_store is None:
            _instance.feature_store = feature_store
        if signal_bus is not None and _instance.signal_bus is None:
            _instance.signal_bus = signal_bus
    return _instance


def _reset_for_tests() -> None:
    global _instance
    _instance = None
