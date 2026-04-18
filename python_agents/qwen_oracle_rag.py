"""
qwen_oracle_rag.py — Oracle Brain için ChromaDB retrieval katmanı (§11)
==========================================================================
`oracle_reasoning` koleksiyonuna ReasoningTrace yazar; benzer bağlamsal
izleri okur. ChromaDB yoksa graceful degraded moda düşer (in-memory ring).
"""
from __future__ import annotations

import logging
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

from qwen_oracle_schemas import ReasoningTrace

logger = logging.getLogger(__name__)

try:
    import chromadb  # type: ignore
    from chromadb.config import Settings  # type: ignore
    _HAS_CHROMA = True
except Exception:
    chromadb = None  # type: ignore
    _HAS_CHROMA = False


class OracleReasoningRAG:
    def __init__(
        self,
        collection_name: str = "oracle_reasoning",
        top_k: int = 5,
        persist_dir: Optional[str] = None,
        inmem_capacity: int = 2048,
    ) -> None:
        self.collection_name = collection_name
        self.top_k = int(top_k)
        self._persist_dir = persist_dir
        self._client: Any = None
        self._coll: Any = None
        self._backend: str = "disabled"
        self._inmem: Deque[Tuple[str, str, Dict[str, Any]]] = deque(maxlen=int(inmem_capacity))
        self._stats = {"writes": 0, "queries": 0, "errors": 0}

    def initialize(self) -> None:
        if not _HAS_CHROMA:
            self._backend = "inmem"
            logger.info("OracleRAG: chromadb yok, in-memory fallback aktif")
            return
        try:
            if self._persist_dir:
                self._client = chromadb.PersistentClient(path=self._persist_dir)
            else:
                self._client = chromadb.Client(Settings(anonymized_telemetry=False))
            self._coll = self._client.get_or_create_collection(self.collection_name)
            self._backend = "chroma"
            logger.info("OracleRAG: chroma collection=%s ready", self.collection_name)
        except Exception as e:
            logger.warning("OracleRAG chroma init fail: %s — in-memory fallback", e)
            self._client = None
            self._coll = None
            self._backend = "inmem"

    def add_trace(self, trace: ReasoningTrace) -> None:
        doc = trace.rag_document()
        meta = {
            "symbol": trace.symbol,
            "ts": trace.ts,
            "shadow": trace.shadow,
        }
        if self._backend == "chroma" and self._coll is not None:
            try:
                self._coll.add(documents=[doc], metadatas=[meta], ids=[trace.trace_id])
                self._stats["writes"] += 1
                return
            except Exception as e:
                self._stats["errors"] += 1
                logger.debug("OracleRAG chroma add fail: %s", e)
        # Fallback
        self._inmem.append((trace.trace_id, doc, meta))
        self._stats["writes"] += 1

    def query(self, text: str, symbol: Optional[str] = None, k: Optional[int] = None) -> List[Dict[str, Any]]:
        k = int(k or self.top_k)
        if self._backend == "chroma" and self._coll is not None:
            try:
                where: Dict[str, Any] = {}
                if symbol:
                    where["symbol"] = symbol
                res = self._coll.query(
                    query_texts=[text], n_results=k, where=where or None,
                )
                out: List[Dict[str, Any]] = []
                ids = (res.get("ids") or [[]])[0]
                docs = (res.get("documents") or [[]])[0]
                metas = (res.get("metadatas") or [[]])[0]
                dists = (res.get("distances") or [[]])[0] if res.get("distances") else [None] * len(ids)
                for i, did in enumerate(ids):
                    out.append({
                        "id": did,
                        "document": docs[i] if i < len(docs) else "",
                        "metadata": metas[i] if i < len(metas) else {},
                        "distance": dists[i] if i < len(dists) else None,
                    })
                self._stats["queries"] += 1
                return out
            except Exception as e:
                self._stats["errors"] += 1
                logger.debug("OracleRAG chroma query fail: %s", e)
        # In-memory fallback: basit substring + symbol filter
        q = text.lower()
        scored: List[Tuple[float, Dict[str, Any]]] = []
        for tid, doc, meta in list(self._inmem):
            if symbol and meta.get("symbol") != symbol:
                continue
            score = sum(1 for tok in q.split() if tok and tok in doc.lower())
            scored.append((-float(score), {"id": tid, "document": doc, "metadata": meta, "distance": None}))
        scored.sort(key=lambda x: x[0])
        self._stats["queries"] += 1
        return [x[1] for x in scored[:k]]

    def stats(self) -> Dict[str, Any]:
        return {"backend": self._backend, "inmem_size": len(self._inmem), **self._stats}


_instance: Optional[OracleReasoningRAG] = None


def get_oracle_rag(**kwargs: Any) -> OracleReasoningRAG:
    global _instance
    if _instance is None:
        _instance = OracleReasoningRAG(**kwargs)
        _instance.initialize()
    return _instance


def _reset_for_tests() -> None:
    global _instance
    _instance = None
