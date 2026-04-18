"""
path_signature_engine.py — Lyons rough-path imzaları (§5)
============================================================
3-kanallı path (Δlog p, ΔOBI, ΔOFI) üzerinde truncated signature. depth-3:
iisignature varsa dim≈14; yoksa pure-numpy depth-2 (9 skaler) fallback.

Matematiksel kalp (Lyons 1998):
    S(X)_{i_1...i_k} = ∫_{0<u_1<...<u_k<T} dX^{i_1}_{u_1} ... dX^{i_k}_{u_k}

Sözleşme:
    - 30 sn pencerede sliding signature.
    - ChromaDB'de 'whale_execution_signatures' koleksiyonuna karşı cosine
      similarity arama; top-1 > threshold ise 'path_signature_similarity'
      kanalına yoğunluk publish + PATH_SIGNATURE_MATCH event.
    - Chroma yoksa degraded mode (match disabled, signature yine hesaplanır).

Graceful degradation:
    iisignature/chromadb eksikse fallback; modül hot-path bloklamaz.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

try:
    import iisignature  # type: ignore
    _IISIG_OK = True
except Exception:
    iisignature = None  # type: ignore
    _IISIG_OK = False

try:
    import numpy as np  # type: ignore
    _NP_OK = True
except Exception:
    np = None  # type: ignore
    _NP_OK = False

try:
    import chromadb  # type: ignore
    _CHROMA_OK = True
except Exception:
    chromadb = None  # type: ignore
    _CHROMA_OK = False


def _depth2_signature(path: List[List[float]]) -> List[float]:
    """Depth-2 truncated signature for d-channel path. Output: d + d² = 12 (d=3)."""
    if not path or len(path) < 2:
        return []
    d = len(path[0])
    # first level = X_T - X_0
    first = [path[-1][i] - path[0][i] for i in range(d)]
    # second level: S^{ij} = ∫ X^i dX^j via trapezoidal ≈ sum over segments
    second = [0.0] * (d * d)
    for k in range(1, len(path)):
        dx = [path[k][i] - path[k - 1][i] for i in range(d)]
        midpoint = [(path[k][i] + path[k - 1][i]) / 2.0 - path[0][i] for i in range(d)]
        for i in range(d):
            for j in range(d):
                second[i * d + j] += midpoint[i] * dx[j]
    return first + second


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


@dataclass
class _SymbolState:
    path: Deque[Tuple[float, List[float]]] = field(default_factory=lambda: deque(maxlen=4096))
    last_publish_ts: float = 0.0
    last_signature: List[float] = field(default_factory=list)
    last_similarity: float = 0.0
    last_match_label: Optional[str] = None


class PathSignatureEngine:
    PUBLISH_HZ_DEFAULT = 0.5
    ORACLE_CHANNEL_NAME = "path_signature_similarity"

    def __init__(
        self,
        event_bus: Any = None,
        feature_store: Any = None,
        signal_bus: Any = None,
        window_sec: int = 30,
        depth: int = 3,
        min_similarity: float = 0.85,
        chroma_collection: str = "whale_execution_signatures",
        publish_hz: float = 0.5,
    ) -> None:
        self.event_bus = event_bus
        self.feature_store = feature_store
        self.signal_bus = signal_bus
        self.window_sec = int(window_sec)
        self.depth = int(depth)
        self.min_similarity = float(min_similarity)
        self.collection_name = chroma_collection
        self.publish_interval = 1.0 / max(0.01, publish_hz)
        self._states: Dict[str, _SymbolState] = {}
        self._stats = {"samples": 0, "computes": 0, "matches": 0, "publishes": 0}
        self._initialized = False
        self._iisig_prepare = None
        self._chroma_col = None

    async def initialize(self) -> None:
        if _IISIG_OK and self.depth >= 3:
            try:
                self._iisig_prepare = iisignature.prepare(3, self.depth)
            except Exception as e:
                logger.debug("iisignature prepare skip: %s", e)
                self._iisig_prepare = None
        self._initialized = True
        logger.info(
            "PathSignatureEngine ready (depth=%d, window=%ds, iisignature=%s, chroma=%s)",
            self.depth, self.window_sec, _IISIG_OK, _CHROMA_OK,
        )

    def _ensure_chroma(self) -> None:
        if self._chroma_col is not None or not _CHROMA_OK:
            return
        try:
            client = chromadb.PersistentClient(path="python_agents/.chroma")
            self._chroma_col = client.get_or_create_collection(name=self.collection_name)
        except Exception as e:
            logger.debug("Chroma init skip: %s", e)
            self._chroma_col = None

    def observe(
        self,
        symbol: str,
        dlog_p: float,
        d_obi: float,
        d_ofi: float,
        ts: Optional[float] = None,
    ) -> None:
        if not symbol:
            return
        try:
            pt = [float(dlog_p), float(d_obi), float(d_ofi)]
        except (TypeError, ValueError):
            return
        for v in pt:
            if v != v:
                return
        ts = float(ts) if ts is not None else time.time()
        st = self._states.setdefault(symbol, _SymbolState())
        # cumulative path (signature depends on increments but we store absolute)
        if st.path:
            prev = st.path[-1][1]
            st.path.append((ts, [prev[i] + pt[i] for i in range(3)]))
        else:
            st.path.append((ts, pt))
        cutoff = ts - self.window_sec
        while st.path and st.path[0][0] < cutoff:
            st.path.popleft()
        self._stats["samples"] += 1

    def _compute_signature(self, symbol: str) -> Optional[List[float]]:
        st = self._states.get(symbol)
        if st is None or len(st.path) < 4:
            return None
        pts = [p for (_, p) in st.path]
        if self._iisig_prepare is not None and _NP_OK:
            try:
                arr = np.array(pts, dtype=float)  # type: ignore
                sig = iisignature.logsig(arr, self._iisig_prepare)
                return [float(x) for x in sig]
            except Exception as e:
                logger.debug("iisignature compute fall back: %s", e)
        return _depth2_signature(pts)

    def maybe_publish(self, symbol: str, ts: Optional[float] = None) -> Optional[Dict[str, Any]]:
        st = self._states.get(symbol)
        if st is None:
            return None
        ts = float(ts) if ts is not None else time.time()
        if (ts - st.last_publish_ts) < self.publish_interval:
            return None
        sig = self._compute_signature(symbol)
        if not sig:
            return None
        st.last_publish_ts = ts
        st.last_signature = sig
        self._stats["computes"] += 1
        # similarity search
        sim = 0.0
        match_label: Optional[str] = None
        self._ensure_chroma()
        if self._chroma_col is not None:
            try:
                res = self._chroma_col.query(query_embeddings=[sig], n_results=5)
                # chromadb returns cosine distance by default; convert to similarity
                if res and res.get("distances") and res["distances"]:
                    dists = res["distances"][0]
                    metas = res.get("metadatas", [[]])[0]
                    if dists:
                        sim = max(0.0, 1.0 - min(dists))
                        if metas:
                            match_label = (metas[0] or {}).get("label")
            except Exception as e:
                logger.debug("Chroma query skip: %s", e)
        st.last_similarity = sim
        st.last_match_label = match_label
        if self.signal_bus is not None:
            try:
                self.signal_bus.publish(
                    symbol=symbol, channel=self.ORACLE_CHANNEL_NAME, value=sim,
                    source="path_signature_engine",
                    extra={"match_label": match_label} if match_label else None,
                )
            except Exception as e:
                logger.debug("PathSig signal_bus skip: %s", e)
        self._stats["publishes"] += 1
        out = {"symbol": symbol, "ts": ts, "similarity": sim, "match_label": match_label, "signature_dim": len(sig)}
        if sim >= self.min_similarity and match_label and self.event_bus is not None:
            self._stats["matches"] += 1
            try:
                from event_bus import EventType, Event
                asyncio.create_task(
                    self.event_bus.publish(
                        Event(type=EventType.PATH_SIGNATURE_MATCH, source="path_signature_engine", data=out)
                    )
                )
            except Exception as e:
                logger.debug("PathSig event skip: %s", e)
        return out

    def snapshot(self, symbol: str) -> Optional[Dict[str, Any]]:
        st = self._states.get(symbol)
        if st is None:
            return None
        return {
            "symbol": symbol,
            "similarity": st.last_similarity,
            "match_label": st.last_match_label,
            "signature_dim": len(st.last_signature),
            "path_len": len(st.path),
        }

    def all_snapshots(self) -> Dict[str, Dict[str, Any]]:
        return {sym: self.snapshot(sym) for sym in self._states}

    def oracle_channel_value(self, symbol: str) -> Optional[float]:
        st = self._states.get(symbol)
        return None if st is None else st.last_similarity

    async def health_check(self) -> Dict[str, Any]:
        return {
            "healthy": self._initialized, "symbols": len(self._states),
            "iisignature": _IISIG_OK, "chroma": self._chroma_col is not None, **self._stats,
        }

    def metrics(self) -> Dict[str, Any]:
        return {
            "path_sig_samples_total": self._stats["samples"],
            "path_sig_computes_total": self._stats["computes"],
            "path_sig_matches_total": self._stats["matches"],
            "path_sig_publishes_total": self._stats["publishes"],
            "path_sig_symbols_active": len(self._states),
        }


_instance: Optional[PathSignatureEngine] = None


def get_path_signature(
    event_bus: Any = None, feature_store: Any = None, signal_bus: Any = None, **kwargs: Any,
) -> PathSignatureEngine:
    global _instance
    if _instance is None:
        _instance = PathSignatureEngine(
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
