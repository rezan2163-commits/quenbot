"""Tests for module_registry — structural integrity of the declared catalog."""

from __future__ import annotations

import pytest

from event_bus import EventType
from module_registry import (
    MODULE_REGISTRY,
    VALID_ORGANS,
    VALID_SOURCES,
    VALID_STATES,
    get,
    known_event_signatures,
    list_by_organ,
    list_modules,
)


_ALL_EVENT_VALUES = {e.value for e in EventType}


def test_registry_is_non_empty():
    assert len(MODULE_REGISTRY) >= 42, (
        f"expected >=42 declared modules, got {len(MODULE_REGISTRY)}"
    )


def test_every_id_is_unique():
    ids = [m.id for m in list_modules()]
    assert len(ids) == len(set(ids)), f"duplicate ids: {ids}"


def test_all_required_organs_present():
    organs = {m.organ for m in list_modules()}
    # Every organ we declared should be represented at least once.
    expected = {"agent", "brain", "detector", "fusion", "learning", "safety", "runtime"}
    missing = expected - organs
    assert not missing, f"missing organs: {missing}"


@pytest.mark.parametrize("module", list_modules())
def test_module_has_valid_fields(module):
    assert module.organ in VALID_ORGANS, f"{module.id}: organ={module.organ}"
    assert module.heartbeat_source in VALID_SOURCES, (
        f"{module.id}: source={module.heartbeat_source}"
    )
    assert module.default_state in VALID_STATES, (
        f"{module.id}: state={module.default_state}"
    )
    assert module.display_name.strip(), f"{module.id}: empty display_name"
    assert module.description.strip(), f"{module.id}: empty description"
    assert module.expected_period_sec > 0, f"{module.id}: period<=0"


@pytest.mark.parametrize("module", list_modules())
def test_every_declared_event_is_real(module):
    for sig in module.event_signatures:
        assert sig in _ALL_EVENT_VALUES, (
            f"{module.id} declares unknown event {sig!r}"
        )


@pytest.mark.parametrize("module", list_modules())
def test_every_dependency_points_to_known_module(module):
    for dep in module.dependencies:
        assert dep in MODULE_REGISTRY, (
            f"{module.id} depends on unknown module {dep!r}"
        )


def test_flag_gated_modules_declare_flag_env():
    for m in list_modules():
        if m.default_state == "flag_gated":
            assert m.flag_env, f"{m.id} is flag_gated but has no flag_env"


def test_list_by_organ_preserves_coverage():
    grouped = list_by_organ()
    flat_ids = {m.id for ms in grouped.values() for m in ms}
    assert flat_ids == set(MODULE_REGISTRY.keys())


def test_get_returns_spec_or_none():
    first = next(iter(MODULE_REGISTRY))
    assert get(first) is MODULE_REGISTRY[first]
    assert get("__not_a_module__") is None


def test_known_event_signatures_is_subset_of_enum():
    sigs = known_event_signatures()
    assert sigs.issubset(_ALL_EVENT_VALUES)


def test_to_dict_round_trip():
    for m in list_modules():
        d = m.to_dict()
        assert d["id"] == m.id
        assert d["organ"] == m.organ
        assert isinstance(d["event_signatures"], list)
        assert isinstance(d["dependencies"], list)
