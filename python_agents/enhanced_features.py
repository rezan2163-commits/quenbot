"""
enhanced_features.py — Beynin tek pencereden derin görüşü
============================================================
Brain'in her karar anında `fetch(symbol)` çağırarak o sembolün tüm yan
mekanizmalardan (microstructure, HMM rejim, fingerprint, bandit, conformal,
drift, autopsy kuralları) toplanmış zengin özellik vektörünü aldığı
tek-giriş-noktası adaptörü.

Herhangi bir alt modül düşse bile `fetch` iskelet ile güvenli döner (hiç
biri None değil — default 0/boş dict). Bu sayede brain kararları
"hangi mekanizma konuştu" bilgisiyle zenginleşir.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def build_feature_snapshot(symbol: str) -> Dict[str, Any]:
    """Brain/strategist bu fonksiyonu çağırır; tüm mekanizmalardan derlenmiş snapshot alır."""
    out: Dict[str, Any] = {"symbol": symbol}

    # microstructure
    try:
        from microstructure import get_microstructure_engine
        snap = get_microstructure_engine().snapshot(symbol)
        if snap:
            out["microstructure"] = snap
            out["obi"] = snap.get("obi") or 0.0
            out["vpin"] = snap.get("vpin") or 0.0
            out["kyle_lambda"] = snap.get("kyle_lambda") or 0.0
            out["spread_bps"] = snap.get("spread_bps") or 0.0
            out["aggressor_buy_ratio"] = snap.get("aggressor_buy_ratio") or 0.5
            out["trade_intensity"] = snap.get("trade_intensity") or 0.0
    except Exception as e:
        logger.debug(f"microstructure snapshot skipped: {e}")

    # HMM regime
    try:
        from hmm_regime import get_hmm_detector
        r = get_hmm_detector().current_regime(symbol)
        if r:
            out["regime"] = r
            out["regime_trend_prob"] = r.get("trend_prob") or 0.0
            out["regime_vol_prob"] = r.get("vol_prob") or 0.0
            out["regime_name"] = r.get("regime", "unknown")
    except Exception as e:
        logger.debug(f"regime snapshot skipped: {e}")

    # fingerprint
    try:
        from iceberg_detector import get_iceberg_detector
        fp = get_iceberg_detector().fingerprint(symbol)
        if fp:
            out["fingerprint"] = fp
            out["fingerprint_score"] = fp.get("fingerprint_score") or 0.0
    except Exception as e:
        logger.debug(f"fingerprint snapshot skipped: {e}")

    return out


def feature_vector_for_meta_labeler(
    *, confidence: float, snapshot: Dict[str, Any],
    hist_accuracy: float = 0.5, hist_avg_pnl: float = 0.0,
) -> Dict[str, float]:
    """Meta-labeler için düz sözlük üret."""
    return {
        "confidence": float(confidence),
        "obi": float(snapshot.get("obi", 0) or 0),
        "vpin": float(snapshot.get("vpin", 0) or 0),
        "kyle_lambda": float(snapshot.get("kyle_lambda", 0) or 0),
        "aggressor_buy_ratio": float(snapshot.get("aggressor_buy_ratio", 0.5) or 0.5),
        "spread_bps": float(snapshot.get("spread_bps", 0) or 0),
        "trade_intensity": float(snapshot.get("trade_intensity", 0) or 0),
        "regime_trend_prob": float(snapshot.get("regime_trend_prob", 0) or 0),
        "regime_vol_prob": float(snapshot.get("regime_vol_prob", 0) or 0),
        "hist_accuracy": float(hist_accuracy),
        "hist_avg_pnl": float(hist_avg_pnl),
    }


async def evaluate_signal_with_meta(
    *, confidence: float, symbol: str,
    hist_accuracy: float = 0.5, hist_avg_pnl: float = 0.0,
    drift_monitor=None,
) -> Dict[str, Any]:
    """Meta-labeler sonucu + drift gözlemi + conformal bandı ile tek karar."""
    snap = build_feature_snapshot(symbol)
    fv = feature_vector_for_meta_labeler(
        confidence=confidence, snapshot=snap,
        hist_accuracy=hist_accuracy, hist_avg_pnl=hist_avg_pnl,
    )

    # drift observe
    if drift_monitor is not None:
        try:
            for k in ("obi", "vpin", "spread_bps", "trade_intensity"):
                drift_monitor.observe(k, fv[k])
            drift_monitor.observe("confidence", fv["confidence"])
        except Exception as e:
            logger.debug(f"drift observe skipped: {e}")

    decision = {"accept": True, "proba": confidence, "reason": "default", "version": 0}
    try:
        from meta_labeler import get_meta_labeler
        decision = get_meta_labeler().predict(fv)
    except Exception as e:
        logger.debug(f"meta_labeler predict skipped: {e}")

    try:
        from conformal import get_conformal
        lo, hi, q = get_conformal().predict_interval(confidence)
    except Exception:
        lo, hi, q = max(0.0, confidence - 0.2), min(1.0, confidence + 0.2), 0.2

    return {
        "snapshot": snap,
        "feature_vector": fv,
        "meta": decision,
        "conformal": {"lo": lo, "hi": hi, "q": q},
    }
