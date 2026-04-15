from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import chromadb
import numpy as np
import pandas as pd

from qwen_models import ErrorObservation, LearningExperience, MarketFeatureSnapshot, PatternMatchCandidate

logger = logging.getLogger("quenbot.vector_memory")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ensure_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return _utc_now()


class ExperienceVectorStore:
    def __init__(self, storage_path: Optional[str] = None):
        self.storage_path = storage_path or os.getenv(
            "QUENBOT_VECTOR_DB_PATH",
            os.path.join(os.path.dirname(__file__), ".chroma"),
        )
        os.makedirs(self.storage_path, exist_ok=True)
        self._client = chromadb.PersistentClient(path=self.storage_path)
        self._patterns = self._client.get_or_create_collection(
            name="pattern_snapshots",
            metadata={"hnsw:space": "cosine"},
        )
        self._experiences = self._client.get_or_create_collection(
            name="learning_experiences",
            metadata={"hnsw:space": "cosine"},
        )
        self._errors = self._client.get_or_create_collection(
            name="error_observations",
            metadata={"hnsw:space": "cosine"},
        )
        self.enabled = True

    def build_feature_snapshot(
        self,
        symbol: str,
        prices: List[float],
        volumes: Optional[List[float]] = None,
        *,
        timeframe: str = "15m",
        market_type: str = "spot",
        exchange: str = "mixed",
        metadata: Optional[Dict[str, Any]] = None,
        observed_at: Optional[datetime] = None,
    ) -> MarketFeatureSnapshot:
        price_series = [float(p) for p in prices if p is not None]
        volume_series = [float(v) for v in (volumes or []) if v is not None]
        vector = self._build_embedding(price_series, volume_series)
        buy_ratio = float((metadata or {}).get("buy_ratio", 0.5))
        change_pct = 0.0
        volatility = 0.0
        if price_series:
            base = max(price_series[0], 1e-8)
            change_pct = (price_series[-1] - price_series[0]) / base
            volatility = (max(price_series) - min(price_series)) / base

        return MarketFeatureSnapshot(
            symbol=symbol,
            timeframe=timeframe,
            market_type=market_type,
            exchange=exchange,
            observed_at=observed_at or _utc_now(),
            price_series=price_series,
            volume_series=volume_series,
            change_pct=change_pct,
            buy_ratio=buy_ratio,
            volatility=volatility,
            feature_vector=vector,
            metadata=metadata or {},
        )

    def _build_embedding(self, prices: List[float], volumes: List[float]) -> List[float]:
        if not prices:
            return [0.0] * 12

        price_series = pd.Series(prices, dtype="float64")
        returns = price_series.pct_change().replace([np.inf, -np.inf], 0.0).fillna(0.0)
        rolling_mean = price_series.rolling(window=min(5, len(price_series)), min_periods=1).mean()
        rolling_std = returns.rolling(window=min(5, len(returns)), min_periods=1).std().fillna(0.0)

        volume_series = pd.Series(volumes or [1.0] * len(prices), dtype="float64")
        if len(volume_series) < len(price_series):
            volume_series = volume_series.reindex(range(len(price_series)), fill_value=float(volume_series.iloc[-1] if len(volume_series) else 1.0))

        features = np.array([
            float(price_series.iloc[-1] / max(price_series.iloc[0], 1e-8) - 1.0),
            float(returns.mean()),
            float(returns.std(ddof=0)),
            float(price_series.max() / max(price_series.min(), 1e-8) - 1.0),
            float(price_series.iloc[-1] - rolling_mean.iloc[-1]),
            float(rolling_std.iloc[-1]),
            float(volume_series.mean()),
            float(volume_series.std(ddof=0) if len(volume_series) > 1 else 0.0),
            float(volume_series.iloc[-1] / max(volume_series.mean(), 1e-8)),
            float(np.polyfit(np.arange(len(price_series)), price_series.to_numpy(), deg=1)[0]) if len(price_series) > 1 else 0.0,
            float(np.abs(returns).mean()),
            float(np.sign(price_series.iloc[-1] - price_series.iloc[0])),
        ], dtype=np.float64)

        norm = np.linalg.norm(features)
        if norm > 0:
            features = features / norm
        return features.astype(float).tolist()

    def upsert_pattern_snapshot(
        self,
        snapshot: MarketFeatureSnapshot,
        *,
        reference_id: Optional[str] = None,
        direction: Optional[str] = None,
        magnitude: Optional[float] = None,
    ) -> str:
        doc_id = reference_id or f"{snapshot.symbol}:{snapshot.timeframe}:{int(snapshot.observed_at.timestamp())}"
        meta = {
            "symbol": snapshot.symbol,
            "timeframe": snapshot.timeframe,
            "market_type": snapshot.market_type.value if hasattr(snapshot.market_type, "value") else str(snapshot.market_type),
            "exchange": snapshot.exchange.value if hasattr(snapshot.exchange, "value") else str(snapshot.exchange),
            "observed_at": int(snapshot.observed_at.timestamp()),
            "change_pct": float(snapshot.change_pct),
            "buy_ratio": float(snapshot.buy_ratio),
            "volatility": float(snapshot.volatility),
            "direction": direction or snapshot.metadata.get("direction", "neutral"),
            "magnitude": float(magnitude if magnitude is not None else snapshot.metadata.get("magnitude", snapshot.change_pct)),
        }
        self._patterns.upsert(
            ids=[doc_id],
            embeddings=[snapshot.feature_vector],
            metadatas=[meta],
            documents=[json.dumps(snapshot.metadata or {}, ensure_ascii=True)],
        )
        return doc_id

    def query_recent_pattern_matches(
        self,
        symbol: str,
        vector: List[float],
        *,
        timeframe: Optional[str] = None,
        max_age_hours: int = 24,
        min_similarity: float = 0.5,
        limit: int = 5,
    ) -> List[PatternMatchCandidate]:
        cutoff = int((_utc_now() - timedelta(hours=max_age_hours)).timestamp())
        predicates: List[Dict[str, Any]] = [
            {"symbol": symbol.upper()},
            {"observed_at": {"$gte": cutoff}},
        ]
        if timeframe:
            predicates.append({"timeframe": timeframe})
        where: Dict[str, Any] = {"$and": predicates} if len(predicates) > 1 else predicates[0]

        try:
            results = self._patterns.query(
                query_embeddings=[vector],
                n_results=max(limit, 1),
                where=where,
                include=["metadatas", "distances", "documents"],
            )
        except Exception as exc:
            logger.debug("Pattern vector query failed: %s", exc)
            return []

        matches: List[PatternMatchCandidate] = []
        ids = results.get("ids", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]
        docs = results.get("documents", [[]])[0]
        for idx, ref_id in enumerate(ids):
            distance = float(distances[idx] if idx < len(distances) else 1.0)
            similarity = max(0.0, min(1.0, 1.0 - distance))
            if similarity < min_similarity:
                continue
            meta = metas[idx] if idx < len(metas) else {}
            doc = docs[idx] if idx < len(docs) else "{}"
            try:
                payload = json.loads(doc or "{}")
            except json.JSONDecodeError:
                payload = {}
            matches.append(
                PatternMatchCandidate(
                    reference_id=ref_id,
                    similarity=similarity,
                    direction=str(meta.get("direction", "neutral")),
                    magnitude=float(meta.get("magnitude", meta.get("change_pct", 0.0)) or 0.0),
                    timeframe=str(meta.get("timeframe", timeframe or "15m")),
                    occurred_at=datetime.fromtimestamp(int(meta.get("observed_at", cutoff)), tz=timezone.utc),
                    metadata={**payload, **meta},
                )
            )
        return matches

    def record_experience(self, experience: LearningExperience) -> str:
        vector = self._build_embedding(
            [1.0, 1.0 + experience.pnl_pct / 100.0, 1.0 + experience.confidence],
            [len(experience.lessons) or 1, len(experience.context or {}) or 1, 1],
        )
        doc_id = f"exp:{experience.symbol}:{int(experience.occurred_at.timestamp())}"
        meta = {
            "symbol": experience.symbol,
            "action": experience.action.value,
            "outcome": experience.outcome,
            "pnl_pct": float(experience.pnl_pct),
            "confidence": float(experience.confidence),
            "occurred_at": int(experience.occurred_at.timestamp()),
        }
        self._experiences.upsert(
            ids=[doc_id],
            embeddings=[vector],
            metadatas=[meta],
            documents=[experience.model_dump_json()],
        )
        return doc_id

    def record_error(self, error: ErrorObservation) -> str:
        vector = self._build_embedding(
            [float(len(error.message)), float(len(error.context or {})), 1.0],
            [1.0, 1.0, 1.0],
        )
        doc_id = f"err:{error.source}:{int(error.observed_at.timestamp())}"
        meta = {
            "source": error.source,
            "error_type": error.error_type,
            "severity": error.severity,
            "observed_at": int(error.observed_at.timestamp()),
        }
        self._errors.upsert(
            ids=[doc_id],
            embeddings=[vector],
            metadatas=[meta],
            documents=[error.model_dump_json()],
        )
        return doc_id

    def build_learning_context(self, symbol: Optional[str] = None, limit: int = 5) -> str:
        where = {"symbol": symbol.upper()} if symbol else None
        try:
            results = self._experiences.get(where=where, limit=max(limit, 1), include=["metadatas", "documents"])
        except Exception as exc:
            logger.debug("Experience lookup failed: %s", exc)
            return "Vektor hafizasi erisilemedi"

        documents = results.get("documents", []) or []
        if not documents:
            return "Kayitli deneyim yok"

        summaries: List[str] = []
        for raw in documents[:limit]:
            try:
                item = json.loads(raw)
            except json.JSONDecodeError:
                continue
            lessons = "; ".join(item.get("lessons", [])[:2]) or item.get("reasoning", "")[:120]
            summaries.append(
                f"{item.get('symbol', '?')} {item.get('action', '?')} {item.get('outcome', '?')} pnl={item.get('pnl_pct', 0):+.2f}% | {lessons}"
            )
        return "\n".join(summaries) if summaries else "Kayitli deneyim yok"

    def get_stats(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "storage_path": self.storage_path,
            "pattern_count": self._patterns.count(),
            "experience_count": self._experiences.count(),
            "error_count": self._errors.count(),
        }


_store: Optional[ExperienceVectorStore] = None


def get_vector_store() -> ExperienceVectorStore:
    global _store
    if _store is None:
        _store = ExperienceVectorStore()
    return _store