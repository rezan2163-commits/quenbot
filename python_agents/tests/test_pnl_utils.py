"""Tests for the direction-aware P&L utility."""
import math

import pytest

from utils.pnl import classify_signal_outcome, compute_signal_pnl_pct, is_profitable


def test_long_profit():
    pnl = compute_signal_pnl_pct({
        "direction": "long",
        "entry_price": 100.0,
        "exit_price": 105.0,
    })
    assert pnl is not None and math.isclose(pnl, 5.0, abs_tol=1e-9)
    assert classify_signal_outcome({
        "direction": "long", "entry_price": 100, "exit_price": 105,
    }) == "profit"


def test_long_loss():
    pnl = compute_signal_pnl_pct({
        "direction": "long",
        "entry_price": 100.0,
        "exit_price": 95.0,
    })
    assert pnl is not None and math.isclose(pnl, -5.0, abs_tol=1e-9)
    assert not is_profitable({
        "direction": "long", "entry_price": 100, "exit_price": 95,
    })


def test_short_profit_exit_below_entry():
    """When price falls after a short entry, it's a WIN for the short."""
    pnl = compute_signal_pnl_pct({
        "direction": "short",
        "entry_price": 100.0,
        "exit_price": 90.0,
    })
    assert pnl is not None and math.isclose(pnl, 10.0, abs_tol=1e-9)
    assert classify_signal_outcome({
        "direction": "short", "entry_price": 100, "exit_price": 90,
    }) == "profit"


def test_short_loss_exit_above_entry():
    """When price rises after a short entry, it's a LOSS for the short."""
    pnl = compute_signal_pnl_pct({
        "direction": "short",
        "entry_price": 100.0,
        "exit_price": 110.0,
    })
    assert pnl is not None and math.isclose(pnl, -10.0, abs_tol=1e-9)
    assert classify_signal_outcome({
        "direction": "short", "entry_price": 100, "exit_price": 110,
    }) == "loss"


def test_pending_when_no_exit_or_current_price():
    pnl = compute_signal_pnl_pct({"direction": "long", "entry_price": 100.0})
    assert pnl is None
    assert classify_signal_outcome({"direction": "long", "entry_price": 100}) == "pending"


def test_zero_entry_is_pending():
    assert compute_signal_pnl_pct({
        "direction": "long", "entry_price": 0, "exit_price": 10,
    }) is None
    assert classify_signal_outcome({
        "direction": "long", "entry_price": 0, "exit_price": 10,
    }) == "pending"


def test_missing_direction_defaults_to_long():
    pnl = compute_signal_pnl_pct({"entry_price": 100, "exit_price": 110})
    assert pnl is not None and math.isclose(pnl, 10.0, abs_tol=1e-9)


def test_reads_position_bias_from_metadata():
    pnl = compute_signal_pnl_pct({
        "entry_price": 200.0,
        "exit_price": 190.0,
        "metadata": {"position_bias": "short"},
    })
    # Short + price fell 5% = +5% P&L
    assert pnl is not None and math.isclose(pnl, 5.0, abs_tol=1e-9)


def test_null_inputs_return_none():
    assert compute_signal_pnl_pct({}) is None
    assert compute_signal_pnl_pct({"entry_price": None, "exit_price": None}) is None


def test_current_price_used_when_no_exit():
    pnl = compute_signal_pnl_pct({
        "direction": "long",
        "entry_price": 100.0,
        "current_price": 102.0,
    })
    assert pnl is not None and math.isclose(pnl, 2.0, abs_tol=1e-9)


@pytest.mark.parametrize("direction_raw,expected_sign", [
    ("LONG", 1),
    ("Long", 1),
    ("buy", 1),  # unknown → defaults to long
    ("SHORT", -1),
    ("Sell", -1),
    ("down", -1),
    ("bear", -1),
])
def test_direction_normalization(direction_raw, expected_sign):
    pnl = compute_signal_pnl_pct({
        "direction": direction_raw,
        "entry_price": 100.0,
        "exit_price": 110.0,
    })
    # Price rose 10% ; long = +10, short = -10
    assert pnl is not None and math.isclose(pnl, 10.0 * expected_sign, abs_tol=1e-9)
