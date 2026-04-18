"""Shared helper utilities for signals, PnL, ETA, and reasoning bundles.

This package is additive — nothing here alters Oracle Stack, brain, strategist
core behaviour. Functions are pure and safe to import from any agent.
"""

from .pnl import (  # noqa: F401
    compute_signal_pnl_pct,
    classify_signal_outcome,
    is_profitable,
)
from .eta import (  # noqa: F401
    estimate_eta_seconds,
    build_eta_bundle,
    refine_eta_with_oracle,
)
from .reasoning import (  # noqa: F401
    build_reasoning_bundle,
)

__all__ = [
    "compute_signal_pnl_pct",
    "classify_signal_outcome",
    "is_profitable",
    "estimate_eta_seconds",
    "build_eta_bundle",
    "refine_eta_with_oracle",
    "build_reasoning_bundle",
]
