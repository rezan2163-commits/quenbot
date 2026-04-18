"""test_oracle_signal_bus.py — §9 Oracle Signal Bus testleri."""
from __future__ import annotations

import time

import pytest

from oracle_signal_bus import OracleSignalBus, get_oracle_signal_bus, _reset_for_tests


@pytest.fixture(autouse=True)
def _reset():
    _reset_for_tests()
    yield
    _reset_for_tests()


def test_register_and_publish_basic():
    bus = OracleSignalBus()
    bus.register_channel("bocpd_consensus", "bocpd_detector")
    bus.publish("BTCUSDT", "bocpd_consensus", 0.75, source="bocpd_detector")

    assert bus.read("BTCUSDT") == {"bocpd_consensus": 0.75}
    assert bus.registered_channels() == ["bocpd_consensus"]
    assert bus.channel_owner("bocpd_consensus") == "bocpd_detector"


def test_publish_metadata_and_age():
    bus = OracleSignalBus()
    bus.publish("ETHUSDT", "hawkes_branching_ratio", -0.3, source="hawkes", quality=0.8)
    md = bus.read_with_metadata("ETHUSDT")
    assert "hawkes_branching_ratio" in md
    entry = md["hawkes_branching_ratio"]
    assert entry["value"] == -0.3
    assert entry["quality"] == 0.8
    assert entry["source"] == "hawkes"
    assert entry["age_s"] >= 0.0


def test_healthy_channels_age_filter():
    bus = OracleSignalBus()
    bus.publish("BTCUSDT", "fresh_chan", 0.1)
    bus.publish("BTCUSDT", "stale_chan", 0.2)
    # Manually age stale_chan
    bus._channels["BTCUSDT"]["stale_chan"].updated_at = time.time() - 120.0
    healthy = bus.healthy_channels("BTCUSDT", max_age_s=30.0)
    assert "fresh_chan" in healthy
    assert "stale_chan" not in healthy


def test_publish_rejects_nan_and_invalid():
    bus = OracleSignalBus()
    bus.publish("BTCUSDT", "x", float("nan"))
    bus.publish("BTCUSDT", "x", "not-a-number")  # type: ignore[arg-type]
    bus.publish("", "x", 1.0)
    bus.publish("BTCUSDT", "", 1.0)
    assert bus.read("BTCUSDT") == {}


def test_overwrite_warns_but_works(caplog):
    bus = OracleSignalBus()
    bus.register_channel("ch1", "owner_a")
    with caplog.at_level("WARNING"):
        bus.register_channel("ch1", "owner_b")
    assert bus.channel_owner("ch1") == "owner_b"


def test_read_subset_channels():
    bus = OracleSignalBus()
    bus.publish("BTCUSDT", "a", 1.0)
    bus.publish("BTCUSDT", "b", 2.0)
    assert bus.read("BTCUSDT", channels=["b"]) == {"b": 2.0}
    assert bus.read("BTCUSDT", channels=["nope"]) == {}


def test_all_snapshots_multi_symbol():
    bus = OracleSignalBus()
    bus.publish("BTCUSDT", "x", 0.5)
    bus.publish("ETHUSDT", "x", 0.7)
    snaps = bus.all_snapshots()
    assert snaps["BTCUSDT"] == {"x": 0.5}
    assert snaps["ETHUSDT"] == {"x": 0.7}


def test_metrics_increment():
    bus = OracleSignalBus()
    bus.publish("BTCUSDT", "x", 1.0)
    bus.publish("BTCUSDT", "x", 2.0)
    bus.read("BTCUSDT")
    m = bus.metrics()
    assert m["oracle_bus_publishes_total"] == 2
    assert m["oracle_bus_reads_total"] == 1
    assert m["oracle_bus_symbols_active"] == 1


def test_singleton_returns_same_instance():
    a = get_oracle_signal_bus()
    b = get_oracle_signal_bus()
    assert a is b
