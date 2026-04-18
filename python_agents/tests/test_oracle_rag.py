"""test_oracle_rag.py — §11 OracleReasoningRAG tests (in-memory backend)."""
from __future__ import annotations

import pytest

from qwen_oracle_schemas import ReasoningTrace, OracleDirective, OracleObservation
from qwen_oracle_rag import OracleReasoningRAG, get_oracle_rag, _reset_for_tests as _reset_rag


@pytest.fixture(autouse=True)
def _reset():
    _reset_rag()
    yield
    _reset_rag()


def _mk_trace(symbol: str = "BTCUSDT", resp: str = "") -> ReasoningTrace:
    obs = OracleObservation(symbol=symbol, channels={"ofi_hurst": 0.8})
    d = OracleDirective(symbol=symbol, action="BIAS_DIRECTION", rationale="strong ofi")
    return ReasoningTrace(symbol=symbol, observation=obs.to_dict(),
                          directive=d.to_dict(), response=resp)


def test_singleton_and_backend():
    r = get_oracle_rag()
    r2 = get_oracle_rag()
    assert r is r2
    assert r._backend in ("chroma", "inmem")


def test_add_trace_increments_stats():
    r = OracleReasoningRAG()
    r.initialize()
    r.add_trace(_mk_trace())
    s = r.stats()
    assert s["writes"] >= 1


def test_query_filter_by_symbol_inmem():
    r = OracleReasoningRAG()
    r.initialize()
    # Force inmem for deterministic test
    r._backend = "inmem"
    r.add_trace(_mk_trace(symbol="BTCUSDT", resp="bullish ofi hurst"))
    r.add_trace(_mk_trace(symbol="ETHUSDT", resp="bearish entropy"))
    r.add_trace(_mk_trace(symbol="BTCUSDT", resp="mirror strong"))
    btc = r.query("ofi", symbol="BTCUSDT", k=5)
    eth = r.query("entropy", symbol="ETHUSDT", k=5)
    assert all(h["metadata"]["symbol"] == "BTCUSDT" for h in btc)
    assert all(h["metadata"]["symbol"] == "ETHUSDT" for h in eth)
    assert len(btc) >= 1
    assert len(eth) >= 1


def test_query_top_k_limit_inmem():
    r = OracleReasoningRAG(top_k=2)
    r.initialize()
    r._backend = "inmem"
    for i in range(6):
        r.add_trace(_mk_trace(resp=f"ofi signal iteration {i}"))
    hits = r.query("ofi")
    assert len(hits) <= 2


def test_stats_shape():
    r = OracleReasoningRAG()
    r.initialize()
    s = r.stats()
    assert "backend" in s and "writes" in s and "queries" in s
