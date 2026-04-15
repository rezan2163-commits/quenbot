"""
Signature Engine — Shape-Matching Intelligence
================================================
Converts real-time price movements into geometric signatures
and matches them against 40GB+ of historical data using
DTW + FFT + Cosine hybrid similarity.

Architecture:
  Scout → extract_live_signature() → query_vault() → SignatureMatch
  PatternMatcher calls this instead of raw Euclidean distance.

A SignatureMatch carries full provenance:
  - similarity score (0..1)
  - historical timestamp (when this shape last occurred)
  - historical price context (entry price, volume ratio)
  - direction & magnitude of the historical outcome
  - signature_id for dashboard display
"""
import hashlib
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any, Tuple

import numpy as np

from similarity_engine import (
    hybrid_similarity,
    dtw_similarity,
    fft_spectral_similarity,
    cosine_sim,
    _z_normalize,
)

logger = logging.getLogger(__name__)

# ──────────────────── Configuration ────────────────────
MATCH_THRESHOLD = 0.60          # %60 minimum similarity
TOP_K = 8                       # max matches to return
SIGNATURE_VECTOR_DIM = 60       # default normalized_profile length
POLYFIT_DEGREE = 5              # polynomial coefficients for shape descriptor
PEAK_TROUGH_WINDOW = 5          # local extrema detection window
MAX_QUERY_BATCH = 2000          # max DB rows per query batch

# Weight tuning for shape-aware hybrid score
W_DTW_SHAPE = 0.45              # shape alignment (primary)
W_FFT_FREQ = 0.25               # frequency content
W_COSINE_DIR = 0.15             # directional similarity
W_POLY_SHAPE = 0.15             # polynomial coefficient match


# ──────────────────── Data Classes ────────────────────
@dataclass
class ShapeDescriptor:
    """Mathematical description of a price curve's geometry."""
    normalized_profile: np.ndarray      # raw normalized price vector
    poly_coefficients: np.ndarray       # polynomial fit coefficients
    peak_count: int = 0                 # number of local maxima
    trough_count: int = 0               # number of local minima
    curvature_sign: int = 0             # +1 convex, -1 concave, 0 mixed
    amplitude: float = 0.0             # max - min of normalized
    direction: str = 'neutral'          # 'long' or 'short'
    change_pct: float = 0.0            # net change percentage


@dataclass
class SignatureMatch:
    """Complete provenance record for a single historical match."""
    signature_id: str                   # unique ID (e.g., SIG-BTC-15m-a7f3)
    similarity: float                   # composite score (0..1)
    dtw_score: float                    # DTW component
    fft_score: float                    # FFT component
    cosine_score: float                 # cosine component
    poly_score: float                   # polynomial shape match

    # Historical context ("When & Where")
    historical_timestamp: Optional[datetime] = None
    historical_price: float = 0.0
    historical_end_price: float = 0.0
    historical_volume_ratio: float = 0.0
    historical_direction: str = 'neutral'
    historical_change_pct: float = 0.0
    historical_symbol: str = ''
    historical_timeframe: str = ''

    # DB reference
    db_signature_id: Optional[int] = None
    source: str = 'historical_signatures'

    @property
    def pattern_name(self) -> str:
        """Human-readable pattern label for dashboard."""
        pct = abs(self.historical_change_pct * 100)
        if pct >= 5:
            size = 'MAJOR'
        elif pct >= 2:
            size = 'MODERATE'
        else:
            size = 'MINOR'
        return f"{self.historical_direction.upper()}_{size}_MOVE"

    @property
    def match_label(self) -> str:
        """Dashboard display label."""
        return f"BULLISH_MATCH" if self.historical_direction == 'long' else "BEARISH_MATCH"

    def to_context_string(self) -> str:
        """Human-readable explanation for Qwen/chat."""
        ts = self.historical_timestamp
        ts_str = ts.strftime('%Y-%m-%d %H:%M') if ts else 'bilinmiyor'
        return (
            f"Bu hareket, {ts_str} tarihindeki ${self.historical_price:,.2f} "
            f"fiyat seviyesinde görülen bir harekete %{self.similarity * 100:.1f} "
            f"benzerlik gösteriyor. O zaman {self.historical_direction} yönünde "
            f"%{abs(self.historical_change_pct * 100):.2f} hareket gerçekleşmişti."
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for API/dashboard."""
        return {
            'signature_id': self.signature_id,
            'similarity': round(self.similarity, 4),
            'similarity_pct': f"{self.similarity * 100:.1f}%",
            'dtw_score': round(self.dtw_score, 4),
            'fft_score': round(self.fft_score, 4),
            'cosine_score': round(self.cosine_score, 4),
            'poly_score': round(self.poly_score, 4),
            'pattern_name': self.pattern_name,
            'match_label': self.match_label,
            'historical_timestamp': self.historical_timestamp.isoformat() if self.historical_timestamp else None,
            'historical_price': round(self.historical_price, 4),
            'historical_end_price': round(self.historical_end_price, 4),
            'historical_volume_ratio': round(self.historical_volume_ratio, 4),
            'historical_direction': self.historical_direction,
            'historical_change_pct': round(self.historical_change_pct, 6),
            'historical_symbol': self.historical_symbol,
            'historical_timeframe': self.historical_timeframe,
            'context': self.to_context_string(),
            'source': self.source,
        }


# ──────────────────── Shape Descriptor Extraction ────────────────────

def extract_shape_descriptor(prices: np.ndarray) -> ShapeDescriptor:
    """
    Extract geometric shape descriptor from a price series.
    Captures: normalized profile, polynomial fit, peaks/troughs, curvature.
    """
    if len(prices) < 4:
        return ShapeDescriptor(
            normalized_profile=np.array([]),
            poly_coefficients=np.array([]),
        )

    # Normalize: percentage change from first price
    first = prices[0]
    if first <= 0:
        first = 1e-8
    normalized = (prices - first) / first

    # Direction & change
    change_pct = float(normalized[-1])
    direction = 'long' if change_pct > 0.001 else ('short' if change_pct < -0.001 else 'neutral')

    # Polynomial fit — captures the "shape DNA"
    x = np.linspace(0, 1, len(normalized))
    try:
        poly_coeffs = np.polyfit(x, normalized, min(POLYFIT_DEGREE, len(normalized) - 1))
    except (np.linalg.LinAlgError, ValueError):
        poly_coeffs = np.zeros(POLYFIT_DEGREE + 1)

    # Peak & trough detection
    peaks, troughs = _detect_extrema(normalized, PEAK_TROUGH_WINDOW)

    # Curvature sign (2nd derivative of polynomial at midpoint)
    curvature_sign = 0
    if len(poly_coeffs) >= 3:
        second_deriv = np.polyder(poly_coeffs, 2)
        mid_val = np.polyval(second_deriv, 0.5)
        curvature_sign = 1 if mid_val > 0.001 else (-1 if mid_val < -0.001 else 0)

    return ShapeDescriptor(
        normalized_profile=normalized.astype(np.float64),
        poly_coefficients=poly_coeffs.astype(np.float64),
        peak_count=len(peaks),
        trough_count=len(troughs),
        curvature_sign=curvature_sign,
        amplitude=float(np.max(normalized) - np.min(normalized)),
        direction=direction,
        change_pct=change_pct,
    )


def _detect_extrema(arr: np.ndarray, window: int) -> Tuple[List[int], List[int]]:
    """Detect local peaks and troughs in array."""
    peaks = []
    troughs = []
    n = len(arr)
    for i in range(window, n - window):
        local = arr[max(0, i - window): i + window + 1]
        if arr[i] == np.max(local):
            peaks.append(i)
        elif arr[i] == np.min(local):
            troughs.append(i)
    return peaks, troughs


# ──────────────────── Signature Similarity ────────────────────

def compute_signature_similarity(
    live: ShapeDescriptor,
    historical_profile: np.ndarray,
    historical_poly: Optional[np.ndarray] = None,
) -> Tuple[float, float, float, float, float]:
    """
    Compute shape-aware similarity between live and historical signatures.

    Returns: (composite_score, dtw_score, fft_score, cosine_score, poly_score)
    """
    live_vec = live.normalized_profile
    hist_vec = historical_profile

    if len(live_vec) < 4 or len(hist_vec) < 4:
        return 0.0, 0.0, 0.0, 0.0, 0.0

    # Align lengths (take the shorter)
    min_len = min(len(live_vec), len(hist_vec))
    lv = live_vec[-min_len:]
    hv = hist_vec[-min_len:]

    # Component scores
    dtw = dtw_similarity(lv, hv, window=max(5, min_len // 6))
    fft = fft_spectral_similarity(lv, hv)
    cos = cosine_sim(_z_normalize(lv), _z_normalize(hv))

    # Polynomial shape match
    poly = 0.0
    if historical_poly is not None and len(live.poly_coefficients) > 0:
        poly = _poly_similarity(live.poly_coefficients, historical_poly)

    # Composite score
    composite = (
        W_DTW_SHAPE * dtw +
        W_FFT_FREQ * fft +
        W_COSINE_DIR * cos +
        W_POLY_SHAPE * poly
    )
    composite = float(min(max(composite, 0.0), 1.0))

    return composite, dtw, fft, cos, poly


def _poly_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Similarity between polynomial coefficient vectors."""
    try:
        # Align dimensions
        max_len = max(len(a), len(b))
        a_pad = np.zeros(max_len)
        b_pad = np.zeros(max_len)
        a_pad[:len(a)] = a
        b_pad[:len(b)] = b

        # Cosine similarity of coefficients
        norm_a = np.linalg.norm(a_pad)
        norm_b = np.linalg.norm(b_pad)
        if norm_a < 1e-10 or norm_b < 1e-10:
            return 0.0
        return float(max(0, np.dot(a_pad, b_pad) / (norm_a * norm_b)))
    except Exception:
        return 0.0


# ──────────────────── Signature ID Generation ────────────────────

def generate_signature_id(symbol: str, timeframe: str, db_id: Any) -> str:
    """Generate a human-readable unique signature ID."""
    short = symbol.replace('USDT', '').replace('/', '')[:6]
    tf = timeframe.replace('min', 'm').replace('hour', 'h')[:3]
    hash_part = hashlib.md5(f"{symbol}:{timeframe}:{db_id}".encode()).hexdigest()[:4]
    return f"SIG-{short}-{tf}-{hash_part}"


# ──────────────────── Vault Query (DB + ChromaDB) ────────────────────

async def query_signature_vault(
    live_descriptor: ShapeDescriptor,
    symbol: str,
    timeframe: str,
    db,
    vector_store=None,
    min_similarity: float = MATCH_THRESHOLD,
    top_k: int = TOP_K,
) -> List[SignatureMatch]:
    """
    Query both PostgreSQL historical_signatures and ChromaDB for matches.

    1. Fetch candidate signatures from DB (same symbol + timeframe)
    2. Compute shape-aware similarity for each
    3. Filter by threshold, sort, return top-k with full provenance
    """
    matches: List[SignatureMatch] = []
    t0 = time.monotonic()

    # ── Step 1: DB candidates ──
    try:
        candidates = await _fetch_db_candidates(db, symbol, timeframe)
    except Exception as e:
        logger.warning("Signature vault DB query failed: %s", e)
        candidates = []

    # ── Step 2: Score each candidate ──
    for cand in candidates:
        hist_profile = cand.get('normalized_profile')
        if hist_profile is None:
            continue

        if isinstance(hist_profile, list):
            hist_profile = np.array(hist_profile, dtype=np.float64)

        if len(hist_profile) < 4:
            continue

        # Extract polynomial from historical profile for shape matching
        x = np.linspace(0, 1, len(hist_profile))
        try:
            hist_poly = np.polyfit(x, hist_profile, min(POLYFIT_DEGREE, len(hist_profile) - 1))
        except Exception:
            hist_poly = None

        composite, dtw, fft, cos, poly = compute_signature_similarity(
            live_descriptor, hist_profile, hist_poly
        )

        if composite < min_similarity:
            continue

        # Build historical timestamp
        hist_ts = cand.get('created_at') or cand.get('timestamp')
        if isinstance(hist_ts, str):
            try:
                hist_ts = datetime.fromisoformat(hist_ts)
            except ValueError:
                hist_ts = None

        # Volume ratio
        vol_profile = cand.get('volume_profile')
        if isinstance(vol_profile, list) and len(vol_profile) > 0:
            vol_ratio = float(vol_profile[-1]) / (float(np.mean(vol_profile)) + 1e-10)
        else:
            vol_ratio = cand.get('buy_ratio', 0.5)

        match = SignatureMatch(
            signature_id=generate_signature_id(symbol, timeframe, cand.get('id', 0)),
            similarity=composite,
            dtw_score=dtw,
            fft_score=fft,
            cosine_score=cos,
            poly_score=poly,
            historical_timestamp=hist_ts,
            historical_price=float(cand.get('start_price', 0)),
            historical_end_price=float(cand.get('end_price', 0)),
            historical_volume_ratio=vol_ratio,
            historical_direction=cand.get('direction', 'neutral'),
            historical_change_pct=float(cand.get('change_pct', 0)),
            historical_symbol=symbol,
            historical_timeframe=timeframe,
            db_signature_id=cand.get('id'),
            source='historical_signatures',
        )
        matches.append(match)

    # ── Step 3: ChromaDB secondary search ──
    if vector_store and len(matches) < top_k:
        try:
            chroma_matches = await _query_chromadb(
                vector_store, live_descriptor, symbol, timeframe,
                min_similarity, top_k - len(matches)
            )
            matches.extend(chroma_matches)
        except Exception as e:
            logger.debug("ChromaDB signature query failed: %s", e)

    # ── Step 4: Sort and filter ──
    matches.sort(key=lambda m: m.similarity, reverse=True)
    matches = matches[:top_k]

    elapsed = (time.monotonic() - t0) * 1000
    if matches:
        logger.info(
            "🔍 Signature vault: %s %s — %d matches (best=%.1f%%) in %.0fms",
            symbol, timeframe, len(matches),
            matches[0].similarity * 100, elapsed
        )

    return matches


async def _fetch_db_candidates(db, symbol: str, timeframe: str) -> List[Dict]:
    """Fetch historical signatures from PostgreSQL."""
    query = """
        SELECT id, symbol, timeframe, direction, change_pct,
               start_price, end_price, normalized_profile,
               volume_profile, buy_ratio, created_at
        FROM historical_signatures
        WHERE symbol = $1 AND timeframe = $2
        ORDER BY created_at DESC
        LIMIT $3
    """
    try:
        rows = await db.pool.fetch(query, symbol, timeframe, MAX_QUERY_BATCH)
        candidates = []
        for row in rows:
            d = dict(row)
            # Parse normalized_profile from JSON/array
            profile = d.get('normalized_profile')
            if isinstance(profile, str):
                import json
                try:
                    profile = json.loads(profile)
                except Exception:
                    profile = None
            d['normalized_profile'] = profile
            # Parse volume_profile
            vp = d.get('volume_profile')
            if isinstance(vp, str):
                import json
                try:
                    vp = json.loads(vp)
                except Exception:
                    vp = None
            d['volume_profile'] = vp
            candidates.append(d)
        return candidates
    except Exception as e:
        logger.warning("DB signature fetch error: %s", e)
        return []


async def _query_chromadb(
    vector_store,
    live: ShapeDescriptor,
    symbol: str,
    timeframe: str,
    min_sim: float,
    limit: int,
) -> List[SignatureMatch]:
    """Query ChromaDB for vector-similar signatures."""
    matches = []
    try:
        # Build embedding from live descriptor
        embedding = vector_store._build_embedding(
            list(live.normalized_profile[:60]),
            []  # no volumes needed for query
        )
        if embedding is None:
            return []

        results = vector_store.pattern_collection.query(
            query_embeddings=[embedding],
            n_results=min(limit * 3, 50),  # over-fetch for filtering
            where={"symbol": symbol},
            include=["embeddings", "metadatas", "documents", "distances"],
        )

        if not results or not results.get('ids') or not results['ids'][0]:
            return []

        for i, doc_id in enumerate(results['ids'][0]):
            meta = results['metadatas'][0][i] if results.get('metadatas') else {}
            distance = results['distances'][0][i] if results.get('distances') else 1.0

            # ChromaDB cosine distance → similarity
            chroma_sim = max(0, 1.0 - distance)
            if chroma_sim < min_sim:
                continue

            # Parse historical timestamp
            ts_raw = meta.get('timestamp') or meta.get('created_at')
            hist_ts = None
            if ts_raw:
                try:
                    hist_ts = datetime.fromisoformat(str(ts_raw))
                except (ValueError, TypeError):
                    pass

            match = SignatureMatch(
                signature_id=generate_signature_id(symbol, timeframe, doc_id),
                similarity=chroma_sim,
                dtw_score=0.0,  # ChromaDB doesn't do DTW
                fft_score=0.0,
                cosine_score=chroma_sim,
                poly_score=0.0,
                historical_timestamp=hist_ts,
                historical_price=float(meta.get('start_price', 0)),
                historical_end_price=float(meta.get('end_price', 0)),
                historical_volume_ratio=float(meta.get('buy_ratio', 0.5)),
                historical_direction=meta.get('direction', 'neutral'),
                historical_change_pct=float(meta.get('magnitude', 0)),
                historical_symbol=symbol,
                historical_timeframe=meta.get('timeframe', timeframe),
                db_signature_id=None,
                source='chromadb',
            )
            matches.append(match)

    except Exception as e:
        logger.debug("ChromaDB query error: %s", e)

    return matches


# ──────────────────── Live Signature Extraction ────────────────────

def extract_live_signature(trades: List[Dict], window: int = SIGNATURE_VECTOR_DIM) -> Optional[ShapeDescriptor]:
    """
    Extract a live shape descriptor from recent trades.
    Called by ScoutAgent/PatternMatcher when a potential move is detected.
    """
    if not trades or len(trades) < 10:
        return None

    prices = np.array([float(t.get('price', t.get('p', 0))) for t in trades[-window:]], dtype=np.float64)
    prices = prices[prices > 0]

    if len(prices) < 10:
        return None

    return extract_shape_descriptor(prices)


# ──────────────────── Match Summary for Decision Context ────────────────────

def build_match_context(matches: List[SignatureMatch]) -> Dict[str, Any]:
    """
    Build a context dictionary from signature matches for DecisionCore.
    """
    if not matches:
        return {
            'signature_match_count': 0,
            'best_signature_similarity': 0.0,
            'signature_direction_consensus': 'neutral',
            'signature_context': 'Yeterli benzerlik bulunamadı.',
            'signature_matches': [],
        }

    best = matches[0]

    # Direction consensus (weighted vote)
    long_weight = sum(m.similarity for m in matches if m.historical_direction == 'long')
    short_weight = sum(m.similarity for m in matches if m.historical_direction == 'short')
    total_weight = long_weight + short_weight
    if total_weight > 0:
        if long_weight / total_weight > 0.6:
            consensus = 'long'
        elif short_weight / total_weight > 0.6:
            consensus = 'short'
        else:
            consensus = 'mixed'
    else:
        consensus = 'neutral'

    # Average magnitude
    avg_magnitude = float(np.mean([abs(m.historical_change_pct) for m in matches]))

    return {
        'signature_match_count': len(matches),
        'best_signature_similarity': best.similarity,
        'best_signature_id': best.signature_id,
        'best_signature_context': best.to_context_string(),
        'best_match_label': best.match_label,
        'signature_direction_consensus': consensus,
        'signature_avg_magnitude': avg_magnitude,
        'signature_context': best.to_context_string(),
        'signature_matches': [m.to_dict() for m in matches[:3]],
    }


async def persist_signature_matches(db, matches: List[SignatureMatch],
                                     symbol: str, timeframe: str,
                                     current_price: float) -> int:
    """Save signature matches to DB for dashboard/API consumption."""
    if not matches or db is None:
        return 0

    saved = 0
    query = """
        INSERT INTO signature_matches
            (symbol, timeframe, direction, similarity,
             dtw_score, fft_score, cosine_score, poly_score,
             matched_signature_id, match_label, pattern_name,
             historical_timestamp, historical_price, historical_end_price,
             historical_volume_ratio, context_string, current_price)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17)
    """
    for m in matches[:5]:  # Top 5 only
        try:
            await db.pool.execute(
                query,
                symbol, timeframe, m.historical_direction, m.similarity,
                m.dtw_score, m.fft_score, m.cosine_score, m.poly_score,
                m.db_signature_id, m.match_label, m.signature_id,
                m.historical_timestamp, m.historical_price, m.historical_end_price,
                m.historical_volume_ratio, m.to_context_string(), current_price,
            )
            saved += 1
        except Exception as e:
            logger.debug("Failed to persist signature match: %s", e)
    return saved
