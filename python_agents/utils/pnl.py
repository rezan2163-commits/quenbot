"""Direction-aware P&L utilities.

Single source of truth for converting raw entry/exit prices into a signed
P&L percentage that honors the trade direction (long vs short).

The dashboard mirrors this logic in ``dashboard/src/lib/pnl.ts``; keep the
two in sync.
"""
from __future__ import annotations

from typing import Any, Literal, Mapping, Optional

OutcomeBucket = Literal["profit", "loss", "pending"]


def _coerce_direction(raw: Any) -> str:
    """Normalize a direction-ish value to ``'long'`` or ``'short'``."""
    if raw is None:
        return "long"
    text = str(raw).strip().lower()
    if text in ("short", "sell", "down", "bear"):
        return "short"
    return "long"


def _coerce_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num != num:  # NaN guard
        return None
    return num


def compute_signal_pnl_pct(signal: Mapping[str, Any]) -> Optional[float]:
    """Return the direction-adjusted realized P&L percentage.

    ``signal`` may use any of the following keys:
      - ``direction``: ``'long'`` or ``'short'`` (falls back to ``position_bias``
        inside ``metadata`` when absent).
      - ``entry_price``: entry price; falls back to ``price`` and
        ``metadata.entry_price``.
      - ``exit_price`` (preferred) or ``current_price``: realized or latest
        reference price. Also reads ``metadata.exit_price`` and
        ``metadata.current_price_at_signal`` as last-resort fallbacks.

    Returns ``None`` when either entry or reference price is missing,
    otherwise the signed return in percent (positive = profit).
    """
    metadata = signal.get("metadata") if isinstance(signal, Mapping) else None
    if not isinstance(metadata, Mapping):
        metadata = {}

    entry = _coerce_float(
        signal.get("entry_price")
        or metadata.get("entry_price")
        or signal.get("price")
    )
    if entry is None or entry <= 0:
        return None

    ref = _coerce_float(
        signal.get("exit_price")
        or metadata.get("exit_price")
        or signal.get("current_price")
        or metadata.get("current_price_at_signal")
    )
    if ref is None:
        return None

    direction = _coerce_direction(
        signal.get("direction") or metadata.get("direction") or metadata.get("position_bias")
    )

    raw_pct = ((ref - entry) / entry) * 100.0
    return -raw_pct if direction == "short" else raw_pct


def classify_signal_outcome(signal: Mapping[str, Any]) -> OutcomeBucket:
    """Bucket a signal into ``'profit'``, ``'loss'`` or ``'pending'``.

    ``'pending'`` is returned when we cannot compute a realized P&L yet
    (missing exit/current price or zero entry).
    """
    pnl = compute_signal_pnl_pct(signal)
    if pnl is None:
        return "pending"
    if pnl > 0:
        return "profit"
    if pnl < 0:
        return "loss"
    return "pending"


def is_profitable(signal: Mapping[str, Any]) -> bool:
    """Convenience wrapper: ``True`` when the signal realized a positive P&L."""
    pnl = compute_signal_pnl_pct(signal)
    return pnl is not None and pnl > 0
