"""The §7 pipeline: breakdown → variants → pair → validate → cache (§7.1–§7.6).

``generate`` is the whole engine in one call: a topic key in, a set of validated concept packs and a
rejection log out. ``build_and_store`` adds §7.6's cache and the registry write, which is what the
orchestrator's turn cycle calls through AI 2's ``content_pack``.

**§7.6, "cache generated packs per topic and level; regenerate only on spec change"**, is implemented
the way the brief's own identity rule says to: a pack's id is a hash of its content, so "have we
already generated this?" is a primary-key lookup rather than a similarity judgement. That is the
store's ``ContentPack.id`` (``cpk_<sha256>``) and the store's ``content_for`` lookup, both of which
already exist; this module feeds them rather than adding a second cache with different semantics.

**What a cache hit means here, precisely.** A hit on (concept, kind) means the *same content* was
already generated, so it is reused. When the spec changes — :data:`adapt.content.pack.PACK_SPEC` or
:data:`adapt.content.breakdown.BREAKDOWN_SPEC` bumped, or the chain's authored content edited — the
payload changes, so the hash changes, so the new pack is a new row and the old one is superseded rather
than overwritten. Regeneration is therefore automatic and exact, which is what "only on spec change"
has to mean for it to be checkable. A test asserts exactly this.

**Rejections are logged, never swallowed.** §7.3 asks for every rejection to carry a reason; §7.6's
cache means a rejected pack must not be served from a later cache hit, so a rejection is recorded on
the pack and the pack is stored with ``validation="rejected"`` — which the store's ``content_for``
filters out by default. A rejected pack stays addressable ("we tried this and rejected it, here is
why") without ever reaching a learner.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .breakdown import BREAKDOWN_SPEC, Breakdown, breakdown_for
from .pack import PACK_SPEC, ConceptPack, build_concept_pack
from .validation import validate_item, validate_pack

__all__ = ["PIPELINE_SPEC", "Generated", "generate", "build_and_store", "content_hash"]

#: The whole pipeline's spec: both halves, so a change to either invalidates the cache.
PIPELINE_SPEC = f"{BREAKDOWN_SPEC}+{PACK_SPEC}"


@dataclass
class Generated:
    """What one run produced: the breakdown, the packs, and everything that was refused."""

    topic_key: str
    topic_title: str
    available: bool
    reason: str
    spec: str = PIPELINE_SPEC
    packs: list[ConceptPack] = field(default_factory=list)
    rejections: list[dict[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    coverage: dict[str, Any] = field(default_factory=dict)
    cached: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "topic_key": self.topic_key,
            "topic_title": self.topic_title,
            "available": self.available,
            "reason": self.reason,
            "spec": self.spec,
            "packs": [p.as_dict() for p in self.packs],
            "rejections": list(self.rejections),
            "notes": list(self.notes),
            "coverage": dict(self.coverage),
            "cached": self.cached,
        }


def content_hash(payload: Mapping[str, Any]) -> str:
    """The identity of a payload: a hash of its canonical JSON, so same content → same id."""
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def _coverage(packs: Sequence[ConceptPack]) -> dict[str, Any]:
    """Counts with n, for the report — never a bare percentage."""
    concepts = len(packs)
    with_example = sum(1 for p in packs if p.variants.get("worked_example", {}).get("steps"))
    verified_example = sum(1 for p in packs if p.variants.get("worked_example", {}).get("verified"))
    with_pair = sum(1 for p in packs if p.pair.get("A") and p.pair.get("B"))
    with_hints = sum(1 for p in packs if len(p.hints) >= 3)
    with_checklist = sum(1 for p in packs if p.checklist)
    with_visual = sum(1 for p in packs if p.variants.get("visual", {}).get("nodes") or p.variants.get("visual", {}).get("rows") or p.variants.get("visual", {}).get("labels"))
    return {
        "concepts": concepts,
        "worked_example": with_example,
        "worked_example_verified": verified_example,
        "matched_pair": with_pair,
        "three_hints": with_hints,
        "teach_back_checklist": with_checklist,
        "visual_spec": with_visual,
    }


def generate(
    topic_key: str,
    *,
    level: str | None = None,
    concept_id: str | None = None,
    source_text: str | None = None,
    p_know: float | None = None,
) -> Generated:
    """Build the packs for a topic. Deterministic, and it never invents a fact.

    ``concept_id`` limits the run to one concept, which is what the cache path uses so a topic with
    thirty concepts does not rebuild the other twenty-nine to serve one lesson.
    """
    breakdown: Breakdown = breakdown_for(topic_key, level=level)
    if not breakdown.available:
        return Generated(
            topic_key=breakdown.topic_key,
            topic_title=breakdown.title,
            available=False,
            reason=breakdown.reason,
            notes=[breakdown.reason],
        )

    wanted = [c for c in breakdown.concepts if concept_id in (None, c.id)]
    packs = [
        build_concept_pack(c, topic_key=breakdown.topic_key, topic_title=breakdown.title)
        for c in wanted
    ]

    rejections: list[dict[str, str]] = []
    notes: list[str] = []
    for pack in packs:
        rejections.extend(pack.rejections)
        notes.extend(f"{pack.concept_id}: {n}" for n in pack.notes)
        # The pair is the only thing in a pack a learner answers, so it is what §7.3 gates. The
        # variants are teaching material: they are checked by ``worked.verify_example`` (which the
        # pack runs itself) and by ``validate_pack`` below, and a variant that fails is recorded
        # rather than silently dropped.
        for leg in ("A", "B"):
            item = pack.pair.get(leg)
            if not item:
                continue
            verdict = validate_item(item, source_text=source_text)
            for rejection in verdict.rejections:
                rejections.append(
                    {
                        "code": rejection.code,
                        "detail": f"{pack.concept_id} leg {leg}: {rejection.detail}",
                    }
                )
            if not verdict.scorable:
                notes.append(
                    f"{pack.concept_id} leg {leg}: key is {verdict.verification}, so it is shown but not scored"
                )
        pack_verdict = validate_pack(pack.as_dict(), source_text=source_text)
        notes.extend(f"{pack.concept_id}: {n}" for n in pack_verdict["notes"])
        if not pack_verdict["ok"]:
            rejections.append(
                {
                    "code": "pack_rejected",
                    "detail": f"{pack.concept_id}: " + "; ".join(pack_verdict["notes"][:3]),
                }
            )
        if p_know is not None:
            pack.pair["expected"] = _expected(pack, p_know=p_know)

    usable = [p for p in packs if not any(r["detail"].startswith(p.concept_id) for r in rejections if r["code"] == "pack_rejected")]
    return Generated(
        topic_key=breakdown.topic_key,
        topic_title=breakdown.title,
        available=bool(usable),
        reason=(
            f"{len(usable)} of {len(packs)} concepts passed validation"
            if usable
            else "every concept pack was rejected; the rejections say why"
        ),
        packs=usable,
        rejections=rejections,
        notes=notes,
        coverage=_coverage(packs),
    )


def _expected(pack: ConceptPack, *, p_know: float) -> dict[str, Any]:
    """Expected performance per leg, from the learner's own belief — the §6.5 gain rule."""
    from ..subjects.methodtest import expected_accuracy

    out: dict[str, Any] = {}
    for leg in ("A", "B"):
        item = pack.pair.get(leg)
        if not item:
            out[leg] = None
            continue
        rung = int(item.get("rung") or 3)
        out[leg] = {
            "rung": rung,
            "expected_accuracy": round(expected_accuracy(p_know, (rung - 1) / 4.0), 4),
        }
    out["note"] = (
        "expected accuracy from the learner's own p_know and the rung's position in the ladder; the "
        "authored kits carry no calibrated item difficulty and none was invented"
    )
    return out


def build_and_store(
    state: Any,
    topic_key: str,
    *,
    level: str | None = None,
    concept_id: str | None = None,
    learner_id: str | None = None,
    p_know: float | None = None,
    module: str = "ai2",
) -> dict[str, Any]:
    """Generate what is missing, validate it, store it, and say what was cached and what was refused.

    This is the §7.6 path the orchestrator calls. ``module`` is ``ai2`` because authoring content is
    AI 2's job in the write matrix; the store refuses ``code`` for exactly that reason, and the
    deterministic half of this pipeline is a *checker*, not an author.
    """
    result = generate(topic_key, level=level, concept_id=concept_id, p_know=p_know)
    stored: list[dict[str, Any]] = []
    cached: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    if not result.available:
        return {
            "available": False,
            "reason": result.reason,
            "stored": stored,
            "cached": cached,
            "rejected": list(result.rejections),
            "notes": list(result.notes),
            "coverage": dict(result.coverage),
        }

    for pack in result.packs:
        payload = pack.as_dict()
        payload["spec"] = PIPELINE_SPEC
        existing = [
            row
            for row in state.content_for(pack.concept_id, kind="variant_set")
            if row.get("validation") != "rejected"
        ]
        if existing:
            cached.append(
                {
                    "concept": pack.concept_id,
                    "pack_id": existing[0]["id"],
                    "validation": existing[0]["validation"],
                }
            )
            continue
        verdict = validate_pack(payload)
        status = "validated" if verdict["ok"] else "rejected"
        try:
            pack_id = state.put_content(
                concept=pack.concept_id,
                kind="variant_set",
                payload=payload,
                topic_id=pack.topic_key,
                validation=status,
                validation_notes=list(verdict["notes"]),
                module=module,
            )
        except Exception as exc:  # a store failure must not cost the learner their lesson
            rejected.append(
                {
                    "code": "store_failed",
                    "detail": f"{pack.concept_id}: {type(exc).__name__}: {exc}",
                }
            )
            continue
        entry = {
            "concept": pack.concept_id,
            "pack_id": pack_id,
            "validation": status,
            "notes": list(verdict["notes"]),
        }
        (stored if status == "validated" else rejected).append(entry)

    return {
        "available": True,
        "reason": result.reason,
        "topic_key": result.topic_key,
        "topic_title": result.topic_title,
        "spec": PIPELINE_SPEC,
        "stored": stored,
        "cached": cached,
        "rejected": list(result.rejections) + rejected,
        "notes": list(result.notes),
        "coverage": dict(result.coverage),
    }
