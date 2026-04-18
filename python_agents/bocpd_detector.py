"""
bocpd_detector.py — Bayesian Online Changepoint Detection (7 streams)
======================================================================
Adams & MacKay (2007) yaklaşımı. Her sembol için 7 paralel akışta run-length
posteriorı tutar. ≥ N akış (default 4) eş zamanlı changepoint bildirirse
konsensüs sinyali yayınlanır.

Matematiksel kalp:
    P(r_t | x_{1:t}) ∝ Σ_{r_{t-1}} π(x_t | r_{t-1}) · H(r_t | r_{t-1}) · P(r_{t-1} | x_{1:t-1})
    Hazard: H(τ) = 1/λ (sabit, default λ=1800 sn).

Akışlar:
    1. aggressor_imbalance  (= aggressor_buy_ratio - 0.5)
    2. trade_size_cv        (rolling std/mean)
    3. ofi_1m               (mevcut ofi engine'inden)
    4. kyle_lambda          (mikrostruktürden)
    5. trade_intensity      (arrival rate, Hz)
    6. spread_bps           (best bid-ask spread bps)
    7. volume_per_tick      (sliding mean)

Operasyonel rol:
    EventType.BOCPD_CONSENSUS_CHANGEPOINT yayınlar.
    OracleSignalBus 'bocpd_consensus' kanalını günceller (∈ [0,1] yoğunluk).
    feature_store'a 1 Hz altında snapshot yazar (publish_hz limitli).

Graceful degradation:
    numpy yoksa pure-python fallback (yavaş ama çalışır).
    Modül asla hot loop'u bloklamaz; her event O(R) (R = run_length truncation).
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

try:
    import numpy as np  # type: ignore
    _NP_OK = True
except Exception:  # pragma: no cover
    np = None  # type: ignore
    _NP_OK = False


# ─── Conjugate Normal-Gamma student-t predictive helper ──────────
class _StreamModel:
    """Tek bir akış için online run-length posteriorı (constant hazard).

    Conjugate prior: Normal-Inverse-Gamma over (μ, σ²). Predictive density:
    Student-t. Sufficient statistics rolling güncellenir; truncation R sonrası
    en eski kütle birikim kayması yapılır (memory-bounded).
    """

    def __init__(
        self,
        hazard_lambda_sec: float,
        truncation: int = 300,
        prior_mu: float = 0.0,
        prior_kappa: float = 1.0,
        prior_alpha: float = 1.0,
        prior_beta: float = 1.0,
    ) -> None:
        self.hazard_p = 1.0 / max(1.0, float(hazard_lambda_sec))  # her gözlemde
        self.R = max(8, int(truncation))
        # Run-length posterior log probabilities (size up to R+1)
        self._logR: List[float] = [0.0]  # log P(r_0=0) = 0 -> normalize sonra
        # Sufficient stats per run-length
        self._mu: List[float] = [prior_mu]
        self._kappa: List[float] = [prior_kappa]
        self._alpha: List[float] = [prior_alpha]
        self._beta: List[float] = [prior_beta]
        self._prior = (prior_mu, prior_kappa, prior_alpha, prior_beta)
        # Saat geçen normalize için son timestamp
        self._last_ts: Optional[float] = None
        # En son hesaplanan changepoint olasılığı (run_length=0 kütlesi)
        self.last_cp_prob: float = 0.0
        self.observations: int = 0

    @staticmethod
    def _logsumexp(values: List[float]) -> float:
        if not values:
            return float("-inf")
        m = max(values)
        if m == float("-inf"):
            return float("-inf")
        s = 0.0
        for v in values:
            s += math.exp(v - m)
        return m + math.log(s) if s > 0 else float("-inf")

    def _student_t_logpdf(self, x: float, mu: float, kappa: float, alpha: float, beta: float) -> float:
        # Predictive: t with df=2α, loc=μ, scale²=β(κ+1)/(α κ)
        df = 2.0 * alpha
        scale_sq = beta * (kappa + 1.0) / (alpha * kappa)
        if scale_sq <= 0 or df <= 0:
            return -1e9
        z = (x - mu) ** 2 / (df * scale_sq)
        # log Γ((df+1)/2) - log Γ(df/2) - 0.5 log(df π scale²) - (df+1)/2 log(1+z)
        return (
            math.lgamma((df + 1.0) / 2.0)
            - math.lgamma(df / 2.0)
            - 0.5 * math.log(df * math.pi * scale_sq)
            - 0.5 * (df + 1.0) * math.log1p(z)
        )

    def update(self, x: float, ts: float) -> float:
        """Yeni gözlemi posteriorı günceller, run_length=0 olasılığını döndürür."""
        if x is None or x != x:  # NaN
            return self.last_cp_prob
        self.observations += 1
        # Predictive log prob each run-length
        n = len(self._logR)
        pred_logp = [
            self._student_t_logpdf(x, self._mu[i], self._kappa[i], self._alpha[i], self._beta[i])
            for i in range(n)
        ]
        h = max(1e-9, min(0.999, self.hazard_p))
        log_h = math.log(h)
        log_1mh = math.log(1.0 - h)
        # Growth probabilities r_t = r_{t-1}+1 (her i için)
        growth = [self._logR[i] + pred_logp[i] + log_1mh for i in range(n)]
        # Changepoint mass (sum over all r_{t-1} of P(r_{t-1}) * π * h)
        cp_terms = [self._logR[i] + pred_logp[i] + log_h for i in range(n)]
        cp_mass = self._logsumexp(cp_terms)
        # New posterior: R'[0] = cp_mass, R'[i+1] = growth[i]
        new_logR = [cp_mass] + growth
        # Truncate
        if len(new_logR) > self.R:
            tail = new_logR[self.R:]
            tail_mass = self._logsumexp(tail)
            new_logR = new_logR[: self.R - 1] + [self._logsumexp([new_logR[self.R - 1], tail_mass])]
        # Normalize
        Z = self._logsumexp(new_logR)
        if Z == float("-inf"):
            new_logR = [0.0]
            self._mu = [self._prior[0]]
            self._kappa = [self._prior[1]]
            self._alpha = [self._prior[2]]
            self._beta = [self._prior[3]]
            self._last_ts = ts
            self.last_cp_prob = 1.0
            return 1.0
        new_logR = [v - Z for v in new_logR]
        # Update sufficient stats: r=0 -> prior, r>0 -> incremental update of (μ,κ,α,β)
        new_mu = [self._prior[0]]
        new_kappa = [self._prior[1]]
        new_alpha = [self._prior[2]]
        new_beta = [self._prior[3]]
        for i in range(n):
            mu, kap, a, b = self._mu[i], self._kappa[i], self._alpha[i], self._beta[i]
            new_kap = kap + 1.0
            new_mu_i = (kap * mu + x) / new_kap
            new_a = a + 0.5
            new_b = b + (kap * (x - mu) ** 2) / (2.0 * new_kap)
            new_mu.append(new_mu_i)
            new_kappa.append(new_kap)
            new_alpha.append(new_a)
            new_beta.append(new_b)
        if len(new_mu) > self.R:
            new_mu = new_mu[: self.R]
            new_kappa = new_kappa[: self.R]
            new_alpha = new_alpha[: self.R]
            new_beta = new_beta[: self.R]
        self._logR = new_logR[: len(new_mu)]
        self._mu, self._kappa, self._alpha, self._beta = new_mu, new_kappa, new_alpha, new_beta
        self._last_ts = ts
        self.last_cp_prob = math.exp(new_logR[0]) if new_logR else 0.0
        return self.last_cp_prob


# ─── Per-symbol multi-stream state ───────────────────────────────
@dataclass
class _SymbolState:
    streams: Dict[str, _StreamModel] = field(default_factory=dict)
    # recent CP firings: stream_name -> deque[(ts, prob)]
    recent_cp: Dict[str, Deque[Tuple[float, float]]] = field(default_factory=dict)
    last_publish_ts: float = 0.0
    last_consensus_score: float = 0.0
    last_consensus_streams: int = 0


STREAM_NAMES = (
    "aggressor_imbalance",
    "trade_size_cv",
    "ofi_1m",
    "kyle_lambda",
    "trade_intensity",
    "spread_bps",
    "volume_per_tick",
)


class BOCPDDetector:
    """Multi-stream BOCPD with consensus voting."""

    PUBLISH_HZ_DEFAULT = 1.0
    ORACLE_CHANNEL_NAME = "bocpd_consensus"

    def __init__(
        self,
        event_bus: Any = None,
        feature_store: Any = None,
        signal_bus: Any = None,
        hazard_lambda_sec: float = 1800.0,
        min_streams: int = 4,
        consensus_window_sec: int = 60,
        cp_threshold: float = 0.9,
        run_length_truncation: int = 300,
        publish_hz: float = 1.0,
    ) -> None:
        self.event_bus = event_bus
        self.feature_store = feature_store
        self.signal_bus = signal_bus
        self.hazard_lambda_sec = float(hazard_lambda_sec)
        self.min_streams = int(min_streams)
        self.consensus_window_sec = int(consensus_window_sec)
        self.cp_threshold = float(cp_threshold)
        self.run_length_truncation = int(run_length_truncation)
        self.publish_interval = 1.0 / max(0.01, float(publish_hz))
        self._states: Dict[str, _SymbolState] = {}
        self._stats = {"updates": 0, "consensus_emits": 0, "errors": 0}
        self._initialized = False

    # ─── Lifecycle ──────────────────────────────────────────────
    async def initialize(self) -> None:
        self._initialized = True
        logger.info(
            "BOCPDDetector ready (λ=%.0fs, min_streams=%d, threshold=%.2f, R=%d)",
            self.hazard_lambda_sec, self.min_streams, self.cp_threshold, self.run_length_truncation,
        )

    def _ensure_state(self, symbol: str) -> _SymbolState:
        st = self._states.get(symbol)
        if st is None:
            st = _SymbolState()
            for name in STREAM_NAMES:
                st.streams[name] = _StreamModel(
                    hazard_lambda_sec=self.hazard_lambda_sec,
                    truncation=self.run_length_truncation,
                )
                st.recent_cp[name] = deque(maxlen=64)
            self._states[symbol] = st
        return st

    # ─── Update API (sync, hot-path safe) ───────────────────────
    def update_streams(
        self,
        symbol: str,
        ts: float,
        values: Dict[str, float],
    ) -> Dict[str, float]:
        """Birden fazla akışı tek seferde günceller, CP olasılıklarını döndürür."""
        if not symbol or not values:
            return {}
        st = self._ensure_state(symbol)
        cp_probs: Dict[str, float] = {}
        for name, v in values.items():
            model = st.streams.get(name)
            if model is None:
                continue
            try:
                p = model.update(float(v), float(ts))
                cp_probs[name] = p
                if p >= self.cp_threshold:
                    st.recent_cp[name].append((float(ts), p))
                self._stats["updates"] += 1
            except Exception as e:
                self._stats["errors"] += 1
                logger.debug("BOCPD update error %s.%s: %s", symbol, name, e)
        return cp_probs

    def consensus_score(self, symbol: str, ts: Optional[float] = None) -> Tuple[int, float]:
        """Konsensüs penceresi içindeki tetikleyen akış sayısı + log-likelihood toplam."""
        st = self._states.get(symbol)
        if st is None:
            return (0, 0.0)
        ts = float(ts) if ts is not None else time.time()
        lo = ts - self.consensus_window_sec
        triggered = 0
        joint_loglik = 0.0
        for name, dq in st.recent_cp.items():
            # En yakın firing'i ara
            best_p = 0.0
            for t, p in reversed(dq):
                if t < lo:
                    break
                if p > best_p:
                    best_p = p
            if best_p >= self.cp_threshold:
                triggered += 1
                # log P contribution (clip to avoid -inf)
                joint_loglik += math.log(max(best_p, 1e-9))
        return triggered, joint_loglik

    # ─── Publish loop helper ────────────────────────────────────
    def maybe_publish(self, symbol: str, ts: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """Throttled publish; konsensüs varsa event yayını + signal_bus güncellemesi."""
        st = self._states.get(symbol)
        if st is None:
            return None
        ts = float(ts) if ts is not None else time.time()
        if (ts - st.last_publish_ts) < self.publish_interval:
            return None
        triggered, joint_ll = self.consensus_score(symbol, ts)
        # bus value: yoğunluk = triggered / 7 (her zaman doldur, consensus eşiği aşmasa da)
        intensity = triggered / float(len(STREAM_NAMES))
        st.last_publish_ts = ts
        st.last_consensus_score = intensity
        st.last_consensus_streams = triggered
        if self.signal_bus is not None:
            try:
                self.signal_bus.publish(
                    symbol=symbol,
                    channel=self.ORACLE_CHANNEL_NAME,
                    value=intensity,
                    source="bocpd_detector",
                    quality=1.0,
                    extra={"triggered": triggered, "joint_loglik": joint_ll},
                )
            except Exception as e:
                logger.debug("BOCPD signal_bus publish error: %s", e)
        out: Optional[Dict[str, Any]] = None
        if triggered >= self.min_streams:
            self._stats["consensus_emits"] += 1
            out = {
                "symbol": symbol,
                "ts": ts,
                "triggered_streams": triggered,
                "joint_loglik": joint_ll,
                "intensity": intensity,
            }
            if self.event_bus is not None:
                try:
                    from event_bus import EventType, Event  # local import to avoid cycle at module load
                    asyncio.create_task(
                        self.event_bus.publish(
                            Event(
                                type=EventType.BOCPD_CONSENSUS_CHANGEPOINT,
                                source="bocpd_detector",
                                data=out,
                            )
                        )
                    )
                except Exception as e:
                    logger.debug("BOCPD event publish skip: %s", e)
        # feature_store snapshot (best effort)
        if self.feature_store is not None:
            try:
                from datetime import datetime, timezone
                self.feature_store.write(
                    symbol=symbol,
                    ts=datetime.fromtimestamp(ts, tz=timezone.utc),
                    features={
                        "bocpd_intensity": intensity,
                        "bocpd_triggered": float(triggered),
                        "bocpd_joint_loglik": float(joint_ll),
                    },
                )
            except Exception as e:
                logger.debug("BOCPD feature_store skip: %s", e)
        return out

    # ─── Public read API ────────────────────────────────────────
    def snapshot(self, symbol: str) -> Optional[Dict[str, Any]]:
        st = self._states.get(symbol)
        if st is None:
            return None
        return {
            "symbol": symbol,
            "consensus_intensity": st.last_consensus_score,
            "triggered_streams": st.last_consensus_streams,
            "stream_cp_probs": {
                name: model.last_cp_prob for name, model in st.streams.items()
            },
            "observations_per_stream": {
                name: model.observations for name, model in st.streams.items()
            },
        }

    def all_snapshots(self) -> Dict[str, Dict[str, Any]]:
        return {sym: self.snapshot(sym) for sym in self._states}

    def oracle_channel_value(self, symbol: str) -> Optional[float]:
        st = self._states.get(symbol)
        return None if st is None else st.last_consensus_score

    async def health_check(self) -> Dict[str, Any]:
        return {
            "healthy": self._initialized,
            "symbols": len(self._states),
            **self._stats,
        }

    def metrics(self) -> Dict[str, Any]:
        return {
            "bocpd_updates_total": self._stats["updates"],
            "bocpd_consensus_emits_total": self._stats["consensus_emits"],
            "bocpd_errors_total": self._stats["errors"],
            "bocpd_symbols_active": len(self._states),
        }


# ─── Singleton ───────────────────────────────────────────────
_instance: Optional[BOCPDDetector] = None


def get_bocpd_detector(
    event_bus: Any = None,
    feature_store: Any = None,
    signal_bus: Any = None,
    **kwargs: Any,
) -> BOCPDDetector:
    """DI uyumlu singleton."""
    global _instance
    if _instance is None:
        _instance = BOCPDDetector(
            event_bus=event_bus,
            feature_store=feature_store,
            signal_bus=signal_bus,
            **kwargs,
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
