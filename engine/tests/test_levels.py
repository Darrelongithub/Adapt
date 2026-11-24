"""`resolve_level`: the ordering table is the specification.

Three axes, never collapsed — entitlement (contract), capability (code),
validation (gates) — plus what the caller asked for. Every combination is
enumerated, because the case that matters (entitled L3, capable L3, validated
L1 → served L1, `limited_by="validation"`) is exactly the one a service method
would get wrong after a refactor and no ordering test would catch.
"""

from __future__ import annotations

import itertools

import pytest

from adapt_engine.levels import (
    MAX_LEVEL,
    LevelResolution,
    resolve_level,
    tier_entitlement,
)


def test_the_case_that_matters() -> None:
    """Entitled L3, capable L3, validated L1 → L1, and validation is named.

    This is the `not_validated` 200: the contract allows it, the code can do it,
    we have not earned it yet. It must not be an error and must not be silent.
    """
    resolution = resolve_level(requested=3, entitlement=3, capability=3, validated=1)
    assert resolution == LevelResolution(served=1, limited_by="validation")
    assert resolution.as_dict() == {"served": 1, "limited_by": "validation"}


def test_entitlement_binds_before_capability() -> None:
    """A tie is reported as entitlement: it is the one the host can act on."""
    assert resolve_level(requested=3, entitlement=1, capability=1, validated=3) == (
        LevelResolution(served=1, limited_by="entitlement")
    )


def test_capability_is_our_gap_and_is_named_as_such() -> None:
    assert resolve_level(requested=3, entitlement=3, capability=2, validated=3) == (
        LevelResolution(served=2, limited_by="capability")
    )


def test_asking_for_less_is_not_a_limitation() -> None:
    """`limited_by="none"`: nothing bound the answer, the caller asked for L1."""
    assert resolve_level(requested=1, entitlement=3, capability=3, validated=3) == (
        LevelResolution(served=1, limited_by="none")
    )


def test_zero_is_a_level_not_an_error() -> None:
    """A construct that is not available at all still resolves, and abstains."""
    resolution = resolve_level(requested=3, entitlement=3, capability=0, validated=3)
    assert resolution == LevelResolution(served=0, limited_by="capability")
    assert resolve_level(requested=0, entitlement=3, capability=3, validated=3).limited_by == "none"


@pytest.mark.parametrize(
    ("requested", "entitlement", "capability", "validated"),
    list(itertools.product(range(MAX_LEVEL + 1), repeat=4)),
)
def test_every_ordering_resolves_to_the_minimum(
    requested: int, entitlement: int, capability: int, validated: int
) -> None:
    resolution = resolve_level(
        requested=requested,
        entitlement=entitlement,
        capability=capability,
        validated=validated,
    )
    expected_served = min(requested, entitlement, capability, validated)
    assert resolution.served == expected_served
    if expected_served == requested:
        assert resolution.limited_by == "none"
    else:
        # The named axis must actually be at the served level, and must be one
        # of the three real constraints.
        assert resolution.limited_by in {"entitlement", "capability", "validation"}
        named = {
            "entitlement": entitlement,
            "capability": capability,
            "validation": validated,
        }[resolution.limited_by]
        assert named == expected_served
        # And no earlier axis in the tie order is lower or equal without being
        # named, which is what makes the report deterministic.
        for axis in ("entitlement", "capability", "validation"):
            if axis == resolution.limited_by:
                break
            assert {"entitlement": entitlement, "capability": capability, "validation": validated}[
                axis
            ] > expected_served


def test_validation_alone_is_named_last() -> None:
    """Ties prefer entitlement, then capability, then validation."""
    tied = resolve_level(requested=3, entitlement=2, capability=2, validated=2)
    assert tied == LevelResolution(served=2, limited_by="entitlement")
    assert resolve_level(requested=3, entitlement=3, capability=2, validated=2).limited_by == (
        "capability"
    )


@pytest.mark.parametrize("value", [-1, MAX_LEVEL + 1, 99])
def test_out_of_range_levels_are_refused(value: int) -> None:
    with pytest.raises(ValueError):
        resolve_level(requested=value, entitlement=3, capability=3, validated=3)


def test_booleans_are_not_levels() -> None:
    with pytest.raises(TypeError):
        resolve_level(requested=True, entitlement=3, capability=3, validated=3)  # type: ignore[arg-type]


@pytest.mark.parametrize(("tier", "level"), [("core", 1), ("plus", 2), ("deep", 3)])
def test_tiers_map_to_entitlement(tier: str, level: int) -> None:
    assert tier_entitlement(tier) == level


def test_an_unknown_tier_is_refused() -> None:
    with pytest.raises(ValueError):
        tier_entitlement("platinum")


def test_the_ordering_table_is_exhaustive() -> None:
    """4 levels, 4 axes, 4**4 combinations — 256, all covered above."""
    assert len(list(itertools.product(range(MAX_LEVEL + 1), repeat=4))) == 4**4
