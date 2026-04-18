"""Target ETA estimation helpers.

Two code paths:

1. ``estimate_eta_seconds`` — closed-form ATR-rate formula. Cheap, deterministic,
   always available as a fallback.
2. ``refine_eta_with_oracle`` — optional post-processing using Oracle Stack
   state (Hawkes branching compresses ETA, Wasserstein drift widens p80).
   Oracle inputs are all optional; missing values leave the ETA untouched.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Optional, Tuple

VelocityBasis = str  # 'historical_similar_patterns' | 'atr_rate' | ...


def estimate_eta_seconds(
    entry_price: float,
    target_price: float,
    atr_per_minute: float,
    directional_bias: float = 0.5,
) -> Tuple[float, float]:
    """Estimate p50/p80 seconds to hit ``target_price`` from ``entry_price``.

    ``atr_per_minute`` is the average true range (price units) per minute.
    ``directional_bias`` is the fraction of ATR that tends to flow in the
    signal's direction (typical range 0.3–0.7).

    Returns ``(p50_seconds, p80_seconds)``. When inputs are invalid the
    function returns ``(inf, inf)`` to signal an undefined ETA.
    """
    try:
        entry = float(entry_price)
        target = float(target_price)
        atr_pm = float(atr_per_minute)
        bias = float(directional_bias)
    except (TypeError, ValueError):
        return (math.inf, math.inf)

    if entry <= 0 or target <= 0 or atr_pm <= 0 or bias <= 0:
        return (math.inf, math.inf)

    distance_abs = abs(target - entry)
    if distance_abs <= 0:
        return (0.0, 0.0)

    # Convert ATR to percentage velocity for scale-invariance.
    velocity_price_per_min = atr_pm * bias
    p50_min = distance_abs / velocity_price_per_min
    p80_min = p50_min * 1.6  # conservative fanout
    return (p50_min * 60.0, p80_min * 60.0)


def refine_eta_with_oracle(
    p50_seconds: float,
    p80_seconds: float,
    *,
    hawkes_branching_ratio: Optional[float] = None,
    wasserstein_drift_zscore: Optional[float] = None,
) -> Tuple[float, float]:
    """Apply optional Oracle-Stack refinements to a baseline ETA pair.

    - Higher Hawkes branching ratios indicate self-exciting order flow and
      compress ETA by up to 30 %.
    - Large Wasserstein drift (|z| > 2) widens p80 by 50 % to reflect
      heightened uncertainty.
    """
    p50 = float(p50_seconds)
    p80 = float(p80_seconds)

    if hawkes_branching_ratio is not None:
        try:
            branching = max(0.0, min(1.0, float(hawkes_branching_ratio)))
            factor = 1.0 - 0.3 * branching
            p50 *= factor
            p80 *= factor
        except (TypeError, ValueError):
            pass

    if wasserstein_drift_zscore is not None:
        try:
            z = abs(float(wasserstein_drift_zscore))
            if z > 2.0:
                p80 *= 1.5
        except (TypeError, ValueError):
            pass

    return (p50, p80)


def build_eta_bundle(
    *,
    entry_price: float,
    target_price: float,
    atr_per_minute: Optional[float],
    directional_bias: float = 0.5,
    similar_patterns: Optional[Mapping[str, Any]] = None,
    oracle_state: Optional[Mapping[str, Any]] = None,
) -> Optional[dict]:
    """Assemble the signal-card ETA metadata bundle.

    Preference order for velocity basis:
      1. historical similar patterns (when ``similar_patterns`` contains
         ``avg_time_to_target_seconds``).
      2. ATR-rate fallback.

    Returns ``None`` when there is no usable signal to compute an ETA.
    """
    basis: VelocityBasis = "atr_rate"
    p50: float = math.inf
    p80: float = math.inf
    confidence = 0.4

    if isinstance(similar_patterns, Mapping):
        avg_tt = similar_patterns.get("avg_time_to_target_seconds")
        p80_tt = similar_patterns.get("p80_time_to_target_seconds") or avg_tt
        count = similar_patterns.get("count", 0) or 0
        try:
            if avg_tt is not None and float(avg_tt) > 0 and int(count) >= 3:
                p50 = float(avg_tt)
                p80 = float(p80_tt) if p80_tt else p50 * 1.6
                basis = "historical_similar_patterns"
                # Confidence grows with sample size up to 0.85 at n>=30.
                confidence = min(0.85, 0.45 + 0.013 * float(count))
        except (TypeError, ValueError):
            pass

    if basis == "atr_rate" and atr_per_minute:
        p50, p80 = estimate_eta_seconds(
            entry_price=entry_price,
            target_price=target_price,
            atr_per_minute=atr_per_minute,
            directional_bias=directional_bias,
        )
        confidence = 0.55

    if not math.isfinite(p50) or not math.isfinite(p80):
        return None

    if isinstance(oracle_state, Mapping):
        p50, p80 = refine_eta_with_oracle(
            p50,
            p80,
            hawkes_branching_ratio=oracle_state.get("hawkes_branching_ratio"),
            wasserstein_drift_zscore=oracle_state.get("wasserstein_drift_zscore"),
        )

    return {
        "p50_seconds": round(max(p50, 0.0), 2),
        "p80_seconds": round(max(p80, 0.0), 2),
        "basis": basis,
        "confidence": round(confidence, 3),
    }
