"""One pure function: what level may we serve, and what stopped us short.

Three axes, never collapsed (NOTES.md, `ADR-002`):

* **entitlement** — what the contract bought (`tenants.tier`: core/plus/deep)
* **capability** — what this build can actually do for the construct
* **validation** — what the reliability gates have earned for it

The served level is the minimum of those three and of what the caller asked
for. `limited_by` names the axis that bound it, because "the host is entitled
to L3 but we have only validated L1" and "the host bought L1" need different
answers in the console and different words on the model card.

Pure on purpose: no database, no clock, no I/O. The ordering table in the tests
is the specification, and it is the thing that would otherwise drift in a
service method nobody could cover exhaustively.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

#: The level names are integers 0-3; 0 is "not available at all", which is a
#: real answer (`abstain` may still be served) and not an error.
MAX_LEVEL: Final = 3

Limiter = Literal["none", "requested", "entitlement", "capability", "validation"]

#: Order in which a tie is reported. Entitlement comes first because it is the
#: one a host can fix by buying, and telling them "validation" when they also
#: have no entitlement would send them to the wrong department. Validation is
#: last: it is *our* gap, so we name it only when it is the sole constraint.
TIE_ORDER: Final = ("entitlement", "capability", "validation")


@dataclass(frozen=True)
class LevelResolution:
    served: int
    limited_by: Limiter

    def as_dict(self) -> dict[str, object]:
        return {"served": self.served, "limited_by": self.limited_by}


def _check(name: str, value: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be an int level, got {value!r}")
    if not 0 <= value <= MAX_LEVEL:
        raise ValueError(f"{name} must be between 0 and {MAX_LEVEL}, got {value}")
    return value


def resolve_level(
    requested: int,
    entitlement: int,
    capability: int,
    validated: int,
) -> LevelResolution:
    """Serve the minimum of four levels, and say which one bound it.

    `validated` is the reliability gate — the one that produces the
    `not_validated` 200 response rather than an error.
    """
    axes = {
        "requested": _check("requested", requested),
        "entitlement": _check("entitlement", entitlement),
        "capability": _check("capability", capability),
        "validation": _check("validation", validated),
    }
    served = min(axes.values())
    if served == axes["requested"]:
        return LevelResolution(served=served, limited_by="none")
    for axis, value in axes.items():
        if axis == "requested":
            continue
        if value == served and axis in TIE_ORDER:
            return LevelResolution(served=served, limited_by=axis)  # type: ignore[arg-type]
    return LevelResolution(served=served, limited_by="none")  # pragma: no cover


def tier_entitlement(tier: str) -> int:
    """Map a tenant tier to its level entitlement.

    Basic confidence and uncertainty are **Core** (transparency is not a
    paywall); calibration analysis, strategy and probes are **Plus**;
    prospective calibration and longitudinal validation are **Deep**.
    """
    levels = {"core": 1, "plus": 2, "deep": 3}
    try:
        return levels[tier]
    except KeyError as exc:
        raise ValueError(f"unknown tier {tier!r}; expected one of {sorted(levels)}") from exc


#: What this build can do, per construct, at M1: L1 mastery, next-item choice,
#: spacing and worked examples. Versioned so "the code can do it" is a
#: published claim, not a sentence in a standup.
CAPABILITY_LEVEL: Final = 1

#: What the gates have earned at M1: G1 (deterministic replay), G2 (the guesser)
#: and G3 (every recommendation cites a gated claim) all run in CI.
VALIDATED_LEVEL: Final = 1

#: The gate set the validated level refers to, served with every level object.
VALIDATION_REF: Final = "gates/g1_g3@1"

#: The population the validation is stated for (ADR-002: validation is per
#: construct x profile x population, and a single global level would be a lie).
DEFAULT_PROFILE: Final = "adult_general"

#: The date the current validation level was earned.
VALIDATED_SINCE: Final = "2026-11-18"


def level_name(level: int) -> str:
    """`L2`, not `2`: the wire format is a name, the arithmetic is an int."""
    return f"L{level}"


def level_object(tier: str, *, requested: int = 1) -> dict[str, object]:
    """The ADR-002 `level` object served on `decide` and model responses.

    Seven fields, one of which (`limited_by`) is always present and always
    truthful: a Deep customer served L1 sees `limited_by: "validation"` rather
    than a silently lower number.
    """
    entitlement = tier_entitlement(tier)
    resolution = resolve_level(
        requested=requested,
        entitlement=entitlement,
        capability=CAPABILITY_LEVEL,
        validated=VALIDATED_LEVEL,
    )
    return {
        "requested": level_name(requested),
        "tier": tier,
        "entitlement_level": level_name(entitlement),
        "capability": level_name(CAPABILITY_LEVEL),
        "validated": level_name(VALIDATED_LEVEL),
        "served": level_name(resolution.served),
        "limited_by": None if resolution.limited_by == "none" else resolution.limited_by,
        "since": VALIDATED_SINCE,
        "validation_ref": VALIDATION_REF,
        "profile": DEFAULT_PROFILE,
    }


def flat_level_axes(tier: str) -> dict[str, object]:
    """The same three axes, flat, as INTEGRATION §6B spells them on mastery.

    Two shapes exist because two documents name them: §6B puts the axes at the
    top level of a mastery response, ADR-002 nests them under `level` for
    `decide`. Both come from `resolve_level`, so they cannot disagree about the
    served level — only about where the words sit.
    """
    entitlement = tier_entitlement(tier)
    resolution = resolve_level(
        requested=1, entitlement=entitlement, capability=CAPABILITY_LEVEL, validated=VALIDATED_LEVEL
    )
    return {
        "entitlement_level": level_name(entitlement),
        "capability_level": level_name(CAPABILITY_LEVEL),
        "validation_level": level_name(VALIDATED_LEVEL),
        "served": level_name(resolution.served),
        "limited_by": None if resolution.limited_by == "none" else resolution.limited_by,
    }


__all__ = [
    "CAPABILITY_LEVEL",
    "DEFAULT_PROFILE",
    "MAX_LEVEL",
    "TIE_ORDER",
    "VALIDATED_LEVEL",
    "VALIDATION_REF",
    "LevelResolution",
    "flat_level_axes",
    "level_name",
    "level_object",
    "resolve_level",
    "tier_entitlement",
]
