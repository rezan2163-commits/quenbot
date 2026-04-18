"""Signal reasoning bundle builder.

Collects human-readable "why was this signal issued?" triggers from whatever
context fields already live inside the strategist / pattern-matcher metadata
blob. Purely additive — missing inputs just yield fewer triggers.
"""
from __future__ import annotations

from typing import Any, Iterable, List, Mapping, Optional


def _coerce_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num != num:
        return None
    return num


def _clip(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _push_trigger(
    out: List[dict],
    *,
    label: str,
    strength: float,
    category: str,
) -> None:
    if not label:
        return
    out.append({
        "label": label,
        "strength": round(_clip(float(strength)), 4),
        "category": category,
    })


def build_reasoning_bundle(
    metadata: Optional[Mapping[str, Any]],
    *,
    regime: Optional[str] = None,
    similar_patterns: Optional[Mapping[str, Any]] = None,
) -> dict:
    """Build the ``metadata.reasoning`` bundle rendered by the signal card.

    Inputs are all optional. The bundle always contains the three top-level
    summary scores (``confluence_score``, ``ifi_score``, ``data_density``)
    and a ``triggers`` list sorted by strength desc.
    """
    meta: Mapping[str, Any] = metadata or {}
    triggers: List[dict] = []

    # ── Confluence / Bayesian fusion ───────────────────────────────
    confluence = _coerce_float(meta.get("confluence_score") or meta.get("quality_score"))
    if confluence is not None and confluence > 0:
        _push_trigger(
            triggers,
            label=f"Confluence fusion {confluence:.2f}",
            strength=confluence,
            category="confluence",
        )

    # ── Indicator / pattern match evidence ────────────────────────
    pattern_reason = meta.get("brain_reasoning") or meta.get("reason") or meta.get("pattern_type")
    if pattern_reason:
        _push_trigger(
            triggers,
            label=str(pattern_reason)[:160],
            strength=_coerce_float(meta.get("similarity") or meta.get("avg_similarity") or 0.6) or 0.6,
            category="indicator",
        )

    # ── Hawkes microstructure ─────────────────────────────────────
    hawkes = _coerce_float(meta.get("hawkes_branching_ratio"))
    if hawkes is not None and hawkes > 0:
        label_suffix = " (iceberg accumulation)" if hawkes > 0.6 else ""
        _push_trigger(
            triggers,
            label=f"Hawkes branching {hawkes:.2f}{label_suffix}",
            strength=hawkes,
            category="microstructure",
        )

    # ── BOCPD changepoint ─────────────────────────────────────────
    bocpd = _coerce_float(meta.get("bocpd_changepoint_prob") or meta.get("changepoint_prob"))
    if bocpd is not None and bocpd > 0:
        _push_trigger(
            triggers,
            label=f"BOCPD changepoint probability {bocpd:.2f}",
            strength=bocpd,
            category="changepoint",
        )

    # ── IFI / factor graph ────────────────────────────────────────
    ifi = _coerce_float(meta.get("ifi_score") or meta.get("factor_graph_score"))
    if ifi is not None and ifi > 0:
        _push_trigger(
            triggers,
            label=f"Factor graph IFI {ifi:.2f}",
            strength=ifi,
            category="factor_graph",
        )

    # ── MAMIS ensemble alignment ──────────────────────────────────
    mamis = meta.get("mamis_ensemble")
    if isinstance(mamis, Mapping) and mamis.get("aligned"):
        m_conf = _coerce_float(mamis.get("mamis_confidence")) or 0.5
        _push_trigger(
            triggers,
            label=f"MAMIS alignment ({mamis.get('pattern_type') or 'pattern'})",
            strength=m_conf,
            category="microstructure",
        )

    triggers.sort(key=lambda t: t["strength"], reverse=True)

    bundle: dict = {
        "triggers": triggers,
        "confluence_score": round(confluence, 4) if confluence is not None else None,
        "ifi_score": round(ifi, 4) if ifi is not None else None,
        "regime": regime or meta.get("regime"),
        "data_density": _coerce_float(meta.get("data_density")),
    }

    if isinstance(similar_patterns, Mapping):
        count = similar_patterns.get("count") or 0
        try:
            if int(count) > 0:
                bundle["similar_patterns"] = {
                    "count": int(count),
                    "avg_realized_pct": _coerce_float(similar_patterns.get("avg_realized_pct")),
                    "win_rate": _coerce_float(similar_patterns.get("win_rate")),
                }
        except (TypeError, ValueError):
            pass

    return bundle
