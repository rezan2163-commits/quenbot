"""
Technical Indicators Module
============================
RSI, MACD, ATR, Bollinger Bands, OBV, VWAP
Numpy-only, lightweight, no external TA library needed.
"""
import numpy as np
from typing import Dict, Any, Optional, List


def rsi(prices: np.ndarray, period: int = 14) -> Optional[float]:
    """Relative Strength Index (Wilder's smoothing)"""
    if len(prices) < period + 1:
        return None
    deltas = np.diff(prices)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100.0 - (100.0 / (1.0 + rs)))


def macd(prices: np.ndarray, fast: int = 12, slow: int = 26,
         signal_period: int = 9) -> Optional[Dict[str, float]]:
    """MACD (12,26,9) - returns macd_line, signal_line, histogram"""
    if len(prices) < slow + signal_period:
        return None

    def ema(data, period):
        alpha = 2 / (period + 1)
        result = np.zeros_like(data, dtype=np.float64)
        result[0] = data[0]
        for i in range(1, len(data)):
            result[i] = alpha * data[i] + (1 - alpha) * result[i - 1]
        return result

    ema_fast = ema(prices, fast)
    ema_slow = ema(prices, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal_period)
    histogram = macd_line - signal_line

    return {
        'macd': float(macd_line[-1]),
        'signal': float(signal_line[-1]),
        'histogram': float(histogram[-1]),
        'trend': 'bullish' if histogram[-1] > 0 else 'bearish',
        'crossover': bool(histogram[-1] > 0 and histogram[-2] <= 0),
        'crossunder': bool(histogram[-1] < 0 and histogram[-2] >= 0),
    }


def atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
        period: int = 14) -> Optional[float]:
    """Average True Range"""
    if len(closes) < period + 1:
        return None
    tr = np.maximum(
        highs[1:] - lows[1:],
        np.maximum(
            np.abs(highs[1:] - closes[:-1]),
            np.abs(lows[1:] - closes[:-1])
        )
    )
    # Wilder's smoothing
    atr_val = np.mean(tr[:period])
    for i in range(period, len(tr)):
        atr_val = (atr_val * (period - 1) + tr[i]) / period
    return float(atr_val)


def bollinger_bands(prices: np.ndarray, period: int = 20,
                     std_dev: float = 2.0) -> Optional[Dict[str, float]]:
    """Bollinger Bands (20, 2)"""
    if len(prices) < period:
        return None
    sma = np.mean(prices[-period:])
    std = np.std(prices[-period:], ddof=1)
    upper = sma + std_dev * std
    lower = sma - std_dev * std
    current = float(prices[-1])

    # %B: 0 = lower band, 1 = upper band
    width = upper - lower
    pct_b = (current - lower) / width if width > 0 else 0.5

    return {
        'upper': float(upper),
        'middle': float(sma),
        'lower': float(lower),
        'bandwidth': float(width / sma) if sma > 0 else 0,
        'pct_b': float(pct_b),
        'squeeze': bool(width / sma < 0.02) if sma > 0 else False,
    }


def obv(prices: np.ndarray, volumes: np.ndarray) -> Optional[Dict[str, float]]:
    """On Balance Volume"""
    if len(prices) < 2 or len(volumes) < 2:
        return None
    n = min(len(prices), len(volumes))
    prices = prices[:n]
    volumes = volumes[:n]

    obv_values = np.zeros(n, dtype=np.float64)
    obv_values[0] = volumes[0]
    for i in range(1, n):
        if prices[i] > prices[i - 1]:
            obv_values[i] = obv_values[i - 1] + volumes[i]
        elif prices[i] < prices[i - 1]:
            obv_values[i] = obv_values[i - 1] - volumes[i]
        else:
            obv_values[i] = obv_values[i - 1]

    # OBV trend (son 10 periyot)
    recent = obv_values[-min(10, n):]
    trend = 'bullish' if recent[-1] > recent[0] else 'bearish'

    return {
        'obv': float(obv_values[-1]),
        'trend': trend,
        'change': float(obv_values[-1] - obv_values[-2]) if n > 1 else 0,
    }


def vwap(prices: np.ndarray, volumes: np.ndarray) -> Optional[float]:
    """Volume Weighted Average Price"""
    if len(prices) < 1 or len(volumes) < 1:
        return None
    n = min(len(prices), len(volumes))
    prices = prices[:n]
    volumes = volumes[:n]
    total_vol = np.sum(volumes)
    if total_vol == 0:
        return float(np.mean(prices))
    return float(np.sum(prices * volumes) / total_vol)


def compute_all_indicators(prices: np.ndarray,
                            volumes: Optional[np.ndarray] = None,
                            highs: Optional[np.ndarray] = None,
                            lows: Optional[np.ndarray] = None) -> Dict[str, Any]:
    """Tüm indikatörleri hesapla, feature vector için"""
    result = {}

    # RSI
    rsi_val = rsi(prices)
    result['rsi'] = rsi_val

    # MACD
    macd_val = macd(prices)
    result['macd'] = macd_val

    # Bollinger
    bb = bollinger_bands(prices)
    result['bollinger'] = bb

    # ATR (eğer OHLC verisi varsa)
    if highs is not None and lows is not None:
        atr_val = atr(highs, lows, prices)
        result['atr'] = atr_val
        result['atr_ratio'] = atr_val / float(prices[-1]) if atr_val and prices[-1] > 0 else None
    else:
        # Basit volatilite tahmini (ATR yerine)
        if len(prices) >= 14:
            rolling_range = np.max(prices[-14:]) - np.min(prices[-14:])
            result['atr'] = float(rolling_range / 14)
            result['atr_ratio'] = float(rolling_range / (14 * prices[-1])) if prices[-1] > 0 else None
        else:
            result['atr'] = None
            result['atr_ratio'] = None

    # Volume indicators
    if volumes is not None and len(volumes) > 1:
        obv_val = obv(prices, volumes)
        result['obv'] = obv_val
        vwap_val = vwap(prices, volumes)
        result['vwap'] = vwap_val
    else:
        result['obv'] = None
        result['vwap'] = None

    # Trend summary
    result['trend_summary'] = _summarize_trend(result)

    return result


def _summarize_trend(indicators: Dict) -> Dict[str, Any]:
    """Tüm indikatörlerden genel trend özeti çıkar"""
    bullish_count = 0
    bearish_count = 0
    total = 0

    rsi_val = indicators.get('rsi')
    if rsi_val is not None:
        total += 1
        if rsi_val > 50:
            bullish_count += 1
        else:
            bearish_count += 1

    macd_data = indicators.get('macd')
    if macd_data:
        total += 1
        if macd_data['trend'] == 'bullish':
            bullish_count += 1
        else:
            bearish_count += 1

    bb_data = indicators.get('bollinger')
    if bb_data:
        total += 1
        if bb_data['pct_b'] > 0.5:
            bullish_count += 1
        else:
            bearish_count += 1

    obv_data = indicators.get('obv')
    if obv_data:
        total += 1
        if obv_data['trend'] == 'bullish':
            bullish_count += 1
        else:
            bearish_count += 1

    if total == 0:
        return {'trend': 'neutral', 'strength': 0, 'bullish': 0, 'bearish': 0}

    ratio = bullish_count / total
    trend = 'bullish' if ratio > 0.6 else ('bearish' if ratio < 0.4 else 'neutral')
    strength = abs(ratio - 0.5) * 2  # 0-1

    return {
        'trend': trend,
        'strength': round(strength, 3),
        'bullish': bullish_count,
        'bearish': bearish_count,
        'total': total,
    }


def build_indicator_vector(indicators: Dict) -> np.ndarray:
    """
    İndikatörlerden 8-boyutlu normalize feature vector oluştur.
    Similarity engine'de kullanılır.
    """
    features = []

    # 1. RSI (normalize 0-1)
    rsi_val = indicators.get('rsi')
    features.append(rsi_val / 100.0 if rsi_val is not None else 0.5)

    # 2. MACD histogram (tanh normalize)
    macd_data = indicators.get('macd')
    if macd_data:
        features.append(np.tanh(macd_data['histogram'] * 100))
    else:
        features.append(0.0)

    # 3. MACD trend direction (binary)
    if macd_data:
        features.append(1.0 if macd_data['trend'] == 'bullish' else 0.0)
    else:
        features.append(0.5)

    # 4. Bollinger %B
    bb = indicators.get('bollinger')
    features.append(bb['pct_b'] if bb else 0.5)

    # 5. Bollinger bandwidth (volatilite)
    features.append(min(bb['bandwidth'] * 10, 1.0) if bb else 0.5)

    # 6. ATR ratio
    atr_r = indicators.get('atr_ratio')
    features.append(min(atr_r * 50, 1.0) if atr_r is not None else 0.5)

    # 7. OBV trend
    obv_data = indicators.get('obv')
    features.append(1.0 if obv_data and obv_data['trend'] == 'bullish' else 0.0)

    # 8. Overall trend strength
    ts = indicators.get('trend_summary', {})
    strength = ts.get('strength', 0)
    direction = 1.0 if ts.get('trend') == 'bullish' else (0.0 if ts.get('trend') == 'bearish' else 0.5)
    features.append(direction * strength)

    return np.array(features, dtype=np.float64)
