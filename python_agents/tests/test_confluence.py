"""ConfluenceEngine için unit testler.

- Boş sinyal → skor ≈ σ(bias) = σ(-1.2) ≈ 0.23
- Tüm sinyaller pozitif → skor > 0.5, direction=up
- Tüm sinyaller negatif → skor < 0.5, direction=down
- Weight reload working
- explain() kısa özet döndürür
"""
from __future__ import annotations

import asyncio
import json
import math
import tempfile
from pathlib import Path

import pytest

from confluence_engine import (
    DEFAULT_WEIGHTS,
    ConfluenceEngine,
    _sigmoid,
    load_weights,
    save_weights,
)


def test_sigmoid_monotonic():
    assert _sigmoid(-10) < _sigmoid(0) < _sigmoid(10)
    assert math.isclose(_sigmoid(0), 0.5, rel_tol=1e-9)


def test_load_weights_default_creates_file():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "w.json"
        w = load_weights(str(p))
        assert p.exists()
        for k in DEFAULT_WEIGHTS:
            assert k in w
        assert math.isclose(w["bias"], DEFAULT_WEIGHTS["bias"])


def test_save_and_reload_weights():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "w.json"
        mod = dict(DEFAULT_WEIGHTS)
        mod["bias"] = -0.5
        assert save_weights(str(p), mod)
        w2 = load_weights(str(p))
        assert math.isclose(w2["bias"], -0.5)


@pytest.mark.asyncio
async def test_confluence_no_signals_negative_bias():
    with tempfile.TemporaryDirectory() as tmp:
        eng = ConfluenceEngine(weights_path=str(Path(tmp) / "w.json"))
        res = await eng.compute("NOPE_NOT_TRACKED")
        # hiçbir alt-motor snapshot yok → skor σ(bias)
        expected = _sigmoid(DEFAULT_WEIGHTS["bias"])
        assert math.isclose(res.score, expected, rel_tol=1e-6)
        assert res.direction in ("down", "neutral")
        assert "microstructure" in res.missing_signals or "ofi" in res.missing_signals


@pytest.mark.asyncio
async def test_confluence_monotonic_in_positive_signal():
    """Tek sinyali artırınca skor monoton artmalı (Naive Bayes garantisi)."""
    with tempfile.TemporaryDirectory() as tmp:
        weights = dict(DEFAULT_WEIGHTS)
        weights["bias"] = 0.0
        p = Path(tmp) / "w.json"
        save_weights(str(p), weights)
        eng = ConfluenceEngine(weights_path=str(p))

        # compute() içindeki _collect_signals'ı monkey-patch edelim
        async def _fake_signals(sigma):
            return (
                {"ofi_hurst_2h": sigma, "ofi_zscore_24h": 0.0,
                 "vpin_zscore": 0.0, "kyle_lambda_zscore": 0.0,
                 "iceberg_fingerprint": 0.0, "signature_coherence": 0.0,
                 "obi_drift_vs_price": 0.0, "aggressor_divergence": 0.0},
                [],
            )

        scores = []
        for s in [-2.0, -1.0, 0.0, 1.0, 2.0]:
            eng._collect_signals = lambda sym, _s=s: asyncio.get_event_loop().run_until_complete(_fake_signals(_s)) if False else _fake_signals_sync(_s)  # noqa
            res = await eng.compute("BTCUSDT")
            scores.append(res.score)
        # monoton artış
        for a, b in zip(scores, scores[1:]):
            assert a <= b + 1e-9


def _fake_signals_sync(sigma):
    return (
        {"ofi_hurst_2h": sigma, "ofi_zscore_24h": 0.0,
         "vpin_zscore": 0.0, "kyle_lambda_zscore": 0.0,
         "iceberg_fingerprint": 0.0, "signature_coherence": 0.0,
         "obi_drift_vs_price": 0.0, "aggressor_divergence": 0.0},
        [],
    )


@pytest.mark.asyncio
async def test_confluence_direction_up_and_down():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "w.json"
        eng = ConfluenceEngine(weights_path=str(p))
        eng._collect_signals = lambda sym: _fake_signals_sync(+3.0)
        res = await eng.compute("A")
        assert res.direction == "up"
        assert res.score > 0.5

        eng._collect_signals = lambda sym: _fake_signals_sync(-3.0)
        res = await eng.compute("B")
        assert res.direction == "down"
        assert res.score < 0.5


@pytest.mark.asyncio
async def test_confluence_explain_top3():
    with tempfile.TemporaryDirectory() as tmp:
        eng = ConfluenceEngine(weights_path=str(Path(tmp) / "w.json"))
        eng._collect_signals = lambda sym: _fake_signals_sync(2.0)
        await eng.compute("XYZ")
        ex = eng.explain("XYZ")
        assert ex is not None
        assert ex["direction"] in ("up", "down", "neutral")
        assert len(ex["top"]) <= 3
