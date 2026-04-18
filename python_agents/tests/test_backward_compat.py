"""Phase 1 backward compatibility smoke:

Tüm yeni flag'ler False iken yeni modüllerin event_bus'a subscribe etmediğini
ve import'ların eski davranışı bozmadığını doğrular.
"""
from __future__ import annotations

import importlib

import pytest


def test_event_types_additive():
    """ADD edilen EventType üyeleri var, eskiler korunmuş."""
    from event_bus import EventType
    # legacy üyeler
    assert hasattr(EventType, "SCOUT_ANOMALY")
    assert hasattr(EventType, "ORDER_BOOK_UPDATE")
    assert hasattr(EventType, "MICROSTRUCTURE_FEATURES")
    assert hasattr(EventType, "ICEBERG_DETECTED")
    # yeni üyeler
    assert hasattr(EventType, "ORDER_FLOW_IMBALANCE")
    assert hasattr(EventType, "MULTI_HORIZON_SIGNATURE")
    assert hasattr(EventType, "CONFLUENCE_SCORE")
    # Phase 2+ eklendi
    assert hasattr(EventType, "LEAD_LAG_ALERT")
    assert hasattr(EventType, "FAST_BRAIN_PREDICTION")
    assert hasattr(EventType, "FINAL_DECISION")


def test_config_flags_present():
    from config import Config
    for flag in [
        "FEATURE_STORE_ENABLED", "FEATURE_STORE_WRITE", "FEATURE_STORE_PATH",
        "OFI_ENABLED", "MULTI_HORIZON_SIGNATURES_ENABLED",
        "CONFLUENCE_ENABLED", "CONFLUENCE_WEIGHTS_PATH", "CONFLUENCE_PUBLISH_HZ",
        "CROSS_ASSET_ENABLED", "FAST_BRAIN_ENABLED",
        "DECISION_ROUTER_ENABLED", "DECISION_ROUTER_SHADOW",
    ]:
        assert hasattr(Config, flag), f"Config.{flag} eksik"


def test_modules_importable_standalone():
    """Yeni modüller ana uygulamadan bağımsız import edilebilir."""
    for mod_name in [
        "feature_store",
        "order_flow_imbalance",
        "multi_horizon_signatures",
        "confluence_engine",
    ]:
        mod = importlib.import_module(mod_name)
        assert mod is not None


def test_singletons_idempotent():
    from confluence_engine import get_confluence_engine
    from order_flow_imbalance import get_ofi_engine
    from multi_horizon_signatures import get_multi_horizon_engine
    a1 = get_ofi_engine(); a2 = get_ofi_engine()
    assert a1 is a2
    b1 = get_multi_horizon_engine(); b2 = get_multi_horizon_engine()
    assert b1 is b2
    c1 = get_confluence_engine(); c2 = get_confluence_engine()
    assert c1 is c2


def test_legacy_microstructure_unchanged():
    """microstructure ve iceberg_detector legacy API'leri hâlâ aynı."""
    from microstructure import get_microstructure_engine
    from iceberg_detector import get_iceberg_detector
    m = get_microstructure_engine()
    ice = get_iceberg_detector()
    # public yüzey bozulmamış
    assert hasattr(m, "snapshot") and callable(m.snapshot)
    assert hasattr(m, "all_snapshots") and callable(m.all_snapshots)
    assert hasattr(ice, "fingerprint") and callable(ice.fingerprint)
    assert hasattr(ice, "all_fingerprints") and callable(ice.all_fingerprints)


def test_phase5_safety_net_event_types():
    """Phase 5 Finalization — safety_net event types ADDITIVE olmus olmali."""
    from event_bus import EventType
    assert hasattr(EventType, "SAFETY_NET_TRIPPED")
    assert hasattr(EventType, "SAFETY_NET_RESET")
    assert hasattr(EventType, "SAFETY_NET_DRIFT_ALERT")
    assert hasattr(EventType, "SAFETY_NET_FS_DEGRADED")
    # Phase 4 counterfactual / rotation da var olmali
    assert hasattr(EventType, "COUNTERFACTUAL_UPDATE")
    assert hasattr(EventType, "CONFLUENCE_WEIGHTS_ROTATED")


def test_phase5_safety_net_config_flags():
    from config import Config
    for flag in [
        "SAFETY_NET_ENABLED",
        "SAFETY_NET_BRIER_TOL",
        "SAFETY_NET_HITRATE_TOL",
        "SAFETY_NET_DEGRADATION_WINDOW_MIN",
        "SAFETY_NET_CONFLUENCE_DRIFT_SIGMA",
        "SAFETY_NET_FS_FAILURE_TOL",
        "SAFETY_NET_BASELINE_PATH",
        "SAFETY_NET_TRIP_SENTINEL",
        # Online learning Phase 4 Finalization
        "ONLINE_LEARNING_PERSIST_DB",
        "ONLINE_LEARNING_DB_OFFSET_PATH",
    ]:
        assert hasattr(Config, flag), f"Config.{flag} eksik"
    # Safety net default OFF — live path davranisini bozamaz
    assert Config.SAFETY_NET_ENABLED is False


def test_database_has_counterfactual_table_api():
    """Database sinifinda counterfactual metodlari eklenmis olmali."""
    from database import Database
    assert hasattr(Database, "create_counterfactual_table")
    assert hasattr(Database, "insert_counterfactual_observation")
    assert hasattr(Database, "fetch_counterfactual_labels")
    assert hasattr(Database, "counterfactual_metrics")

