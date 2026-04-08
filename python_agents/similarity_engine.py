"""
Similarity Engine - Hybrid DTW + FFT + Cosine
===============================================
18-boyutlu feature vektörlerine DTW, FFT spectral ve Cosine 
similarity uygulayan hibrit motor.
"""
import logging
import math
from typing import List, Tuple, Optional

import numpy as np
from sklearn.metrics.pairwise import cosine_similarity as sk_cosine

logger = logging.getLogger(__name__)

# Similarity weights
W_COSINE = 0.3
W_DTW = 0.4
W_FFT = 0.3


def hybrid_similarity(vec_a: np.ndarray, vec_b: np.ndarray,
                       w_cosine: float = W_COSINE,
                       w_dtw: float = W_DTW,
                       w_fft: float = W_FFT) -> float:
    """
    Hibrit similarity: Cosine + DTW + FFT spectral.
    Her biri 0-1 arası, ağırlıklı ortalama.
    """
    cos_sim = cosine_sim(vec_a, vec_b)
    dtw_sim = dtw_similarity(vec_a, vec_b)
    fft_sim = fft_spectral_similarity(vec_a, vec_b)

    return w_cosine * cos_sim + w_dtw * dtw_sim + w_fft * fft_sim


def cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity (0-1 normalize)"""
    try:
        a_r = a.reshape(1, -1)
        b_r = b.reshape(1, -1)
        sim = sk_cosine(a_r, b_r)[0][0]
        return float(max(0, sim))  # Negatif cosine'i 0'a clamp
    except Exception:
        return 0.0


def dtw_similarity(a: np.ndarray, b: np.ndarray, window: int = 5) -> float:
    """
    Sakoe-Chiba banded DTW → similarity (0-1).
    O(n*w) complexity with banding.
    """
    try:
        n = len(a)
        m = len(b)
        if n == 0 or m == 0:
            return 0.0

        # Normalize
        a_norm = _z_normalize(a)
        b_norm = _z_normalize(b)

        # Banded DTW
        dtw_dist = _banded_dtw(a_norm, b_norm, window)

        # Distance → similarity (exponential decay)
        max_possible = math.sqrt(n + m) * 2  # Rough upper bound
        similarity = math.exp(-dtw_dist / max(max_possible, 1e-8))
        return float(min(max(similarity, 0), 1))

    except Exception:
        return 0.0


def _banded_dtw(a: np.ndarray, b: np.ndarray, window: int) -> float:
    """Sakoe-Chiba banded DTW distance"""
    n = len(a)
    m = len(b)
    w = max(window, abs(n - m))

    dtw_matrix = np.full((n + 1, m + 1), np.inf)
    dtw_matrix[0, 0] = 0.0

    for i in range(1, n + 1):
        j_start = max(1, i - w)
        j_end = min(m, i + w) + 1
        for j in range(j_start, j_end):
            cost = (a[i - 1] - b[j - 1]) ** 2
            dtw_matrix[i, j] = cost + min(
                dtw_matrix[i - 1, j],
                dtw_matrix[i, j - 1],
                dtw_matrix[i - 1, j - 1]
            )

    return math.sqrt(max(dtw_matrix[n, m], 0))


def fft_spectral_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """
    FFT-based spectral similarity.
    Frequency domain'de power spectrum karşılaştırması.
    """
    try:
        if len(a) < 4 or len(b) < 4:
            return 0.0

        # Make same length
        min_len = min(len(a), len(b))
        a_s = a[:min_len]
        b_s = b[:min_len]

        # Normalize
        a_n = _z_normalize(a_s)
        b_n = _z_normalize(b_s)

        # FFT → power spectrum
        fft_a = np.fft.rfft(a_n)
        fft_b = np.fft.rfft(b_n)

        power_a = np.abs(fft_a) ** 2
        power_b = np.abs(fft_b) ** 2

        # Normalize power spectra
        norm_a = np.linalg.norm(power_a)
        norm_b = np.linalg.norm(power_b)

        if norm_a < 1e-10 or norm_b < 1e-10:
            return 0.0

        power_a = power_a / norm_a
        power_b = power_b / norm_b

        # Cosine similarity of power spectra
        sim = float(np.dot(power_a, power_b))
        return max(0, min(sim, 1.0))

    except Exception:
        return 0.0


def _z_normalize(arr: np.ndarray) -> np.ndarray:
    """Z-score normalize"""
    std = np.std(arr)
    if std < 1e-10:
        return np.zeros_like(arr)
    return (arr - np.mean(arr)) / std


def find_best_matches(current_vec: np.ndarray,
                       historical_vecs: List[np.ndarray],
                       min_similarity: float = 0.3,
                       top_k: int = 10) -> List[Tuple[int, float]]:
    """
    Tüm historical vektörler arasından en iyi eşleşmeleri bul.
    Returns: [(index, similarity_score), ...]
    """
    if not historical_vecs:
        return []

    results = []
    for idx, hist_vec in enumerate(historical_vecs):
        if len(hist_vec) != len(current_vec):
            continue
        sim = hybrid_similarity(current_vec, hist_vec)
        if sim >= min_similarity:
            results.append((idx, sim))

    results.sort(key=lambda x: x[1], reverse=True)
    return results[:top_k]


def batch_cosine_similarity(current: np.ndarray,
                             historical: np.ndarray,
                             min_sim: float = 0.3) -> List[Tuple[int, float]]:
    """
    Hızlı batch cosine similarity (sklearn).
    Small datasets için hybrid yerine bu kullanılabilir.
    """
    try:
        current_r = current.reshape(1, -1)
        sims = sk_cosine(current_r, historical)[0]
        results = [(i, float(s)) for i, s in enumerate(sims) if s >= min_sim]
        results.sort(key=lambda x: x[1], reverse=True)
        return results
    except Exception:
        return []


def build_extended_vector(base_vector: np.ndarray,
                           indicator_vector: np.ndarray) -> np.ndarray:
    """
    Base snapshot vector (6-dim) + indicator vector (8-dim) + 
    computed features (4-dim) = 18-dim extended vector.
    """
    # Base: [price_change, buy_ratio, volatility, log_volume, log_trades, buy_sell_ratio]
    # Indicators: [rsi_norm, macd_hist, macd_trend, bb_pctb, bb_bw, atr_ratio, obv_trend, trend_strength]

    # Computed features
    price_change = base_vector[0] if len(base_vector) > 0 else 0
    buy_ratio = base_vector[1] if len(base_vector) > 1 else 0.5
    volatility = base_vector[2] if len(base_vector) > 2 else 0

    # 1. Momentum (price_change * volume_dir)
    momentum = price_change * (buy_ratio - 0.5) * 2

    # 2. Pressure score (buy ratio adjusted by volume)
    volume_factor = base_vector[3] / 10 if len(base_vector) > 3 else 0.5
    pressure = (buy_ratio - 0.5) * volume_factor

    # 3. Volatility-adjusted momentum
    vol_momentum = momentum / max(volatility, 0.001) if volatility > 0.001 else momentum

    # 4. Trend alignment (indicators vs price action)
    rsi_dir = indicator_vector[0] - 0.5 if len(indicator_vector) > 0 else 0
    price_dir = 1 if price_change > 0 else -1
    alignment = rsi_dir * price_dir

    computed = np.array([
        np.tanh(momentum * 10),
        np.tanh(pressure * 5),
        np.tanh(vol_momentum),
        alignment,
    ], dtype=np.float64)

    return np.concatenate([base_vector, indicator_vector, computed])
