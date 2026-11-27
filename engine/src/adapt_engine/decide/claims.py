"""The seeded claims: five facts, each with its boundary stated.

`AGENT_M1.md` §4 requires that every recommendation cite a claim, and G3 requires
that a claim resolves to a *gated* construct and that a claim below its evidence
floor is refused at write time. This module is the seed set: five constructs, one
claim each, chosen because each one drives exactly one action in `decide` v1.

Two deliberate constraints on the data:

* **`does_not_apply_when` is mandatory and non-empty.** `CLAIMS.md` §1 is blunt
  about why: "a claim without a stated boundary is how a learning-science engine
  turns into a horoscope." The worked-example claim's boundary is expertise
  reversal, and `decide` reads it — a learner at high mastery is not sent a
  worked example, because the claim itself says not to.
* **No DOIs I did not verify.** The in-tree record carries one DOI
  (Sweller & Cooper 1985) because `CLAIMS.md` does; the others carry
  author/year/venue references without a DOI field. A fabricated identifier is
  worse than an absent one: it looks checkable.

Seeding is idempotent and happens on first use (a migration cannot `INSERT` —
the repo's rule is that migrations are schema-only), so a tenant that calls
`decide` before any job has run still gets cited advice rather than an
abstention.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection


@dataclass(frozen=True, slots=True)
class SeedClaim:
    id: str
    construct: str
    statement: str
    certainty: str
    applies_when: tuple[str, ...]
    does_not_apply_when: tuple[str, ...]
    evidence_n: int
    evidence_floor: int
    sources: list[dict[str, Any]] = field(default_factory=list)


#: The seed set. `evidence_n` / `evidence_floor` are participants and studies at
#: the level of the claim (k), not trials of one learner: the floor is what makes
#: the claim admissible at all, and the CHECK on the table refuses a claim whose
#: evidence does not clear it (G3).
SEED_CLAIMS: tuple[SeedClaim, ...] = (
    SeedClaim(
        id="clm_0001",
        construct="worked_examples",
        statement=(
            "Worked examples outperform equivalent problem practice for novices acquiring a schema."
        ),
        certainty="moderate",
        applies_when=("novice", "procedural or well-structured skill", "high intrinsic load"),
        does_not_apply_when=("expert learners (expertise reversal)", "ill-structured problems"),
        evidence_n=1400,
        evidence_floor=200,
        sources=[
            {
                "ref": "Sweller & Cooper 1985",
                "doi": "10.1207/s1532690xci0201_3",
                "type": "primary",
            },
            {"ref": "meta-analysis of the worked-example effect", "type": "synthesis"},
        ],
    ),
    SeedClaim(
        id="clm_0002",
        construct="retrieval_practice",
        statement=(
            "Retrieving an answer improves later retention more than re-studying "
            "the same material for the same time."
        ),
        certainty="high",
        applies_when=("material already introduced", "recall is feasible"),
        does_not_apply_when=("no prior exposure", "pure speed drills with no delay"),
        evidence_n=8000,
        evidence_floor=500,
        sources=[{"ref": "Roediger & Karpicke 2006", "type": "primary"}],
    ),
    SeedClaim(
        id="clm_0003",
        construct="spacing",
        statement=(
            "Spreading practice across separated sessions produces better "
            "long-term retention than massing it into one session."
        ),
        certainty="high",
        applies_when=("retention over days or longer is the goal", "material repeated"),
        does_not_apply_when=("single-session performance is the only outcome",),
        evidence_n=12000,
        evidence_floor=500,
        sources=[{"ref": "Cepeda et al. 2006", "type": "synthesis"}],
    ),
    SeedClaim(
        id="clm_0004",
        construct="desirable_difficulty",
        statement=(
            "Conditions that slow acquisition but improve later performance "
            "(spacing, interleaving, generation) are desirable when the learner "
            "can still succeed."
        ),
        certainty="moderate",
        applies_when=("success rate stays above roughly 60%", "retrieval is possible"),
        does_not_apply_when=("success rate so low the learner disengages", "novice overload"),
        evidence_n=3000,
        evidence_floor=500,
        sources=[{"ref": "Bjork & Bjork 2011", "type": "synthesis"}],
    ),
    SeedClaim(
        id="clm_0005",
        construct="interleaving",
        statement=(
            "Interleaving related problem types improves discrimination between "
            "them, at the cost of performance during practice."
        ),
        certainty="moderate",
        applies_when=("problem types are confusable", "each type has been introduced"),
        does_not_apply_when=("the learner cannot yet execute any single type",),
        evidence_n=2500,
        evidence_floor=500,
        sources=[{"ref": "Rohrer & Taylor 2007", "type": "primary"}],
    ),
)


def ensure_seeded(conn: Connection, *, tenant_id: uuid.UUID) -> int:
    """Insert the seed set for a tenant if it has none. Returns rows written.

    One statement, `ON CONFLICT DO NOTHING`, so two concurrent first-calls cannot
    double-seed and a re-run after a partial failure converges.
    """
    rows = [
        {
            "tenant_id": tenant_id,
            "id": claim.id,
            "construct": claim.construct,
            "statement": claim.statement,
            "certainty": claim.certainty,
            "applies_when": list(claim.applies_when),
            "does_not_apply_when": list(claim.does_not_apply_when),
            "evidence_n": claim.evidence_n,
            "evidence_floor": claim.evidence_floor,
            "sources": _json(claim.sources),
        }
        for claim in SEED_CLAIMS
    ]
    result = conn.execute(
        text(
            """
            INSERT INTO claims
                (tenant_id, id, version, construct, statement, certainty,
                 applies_when, does_not_apply_when, evidence_n, evidence_floor,
                 sources, status)
            VALUES (:tenant_id, :id, 1, :construct, :statement, :certainty,
                    CAST(:applies_when AS text[]), CAST(:does_not_apply_when AS text[]),
                    :evidence_n, :evidence_floor, CAST(:sources AS jsonb), 'active')
            ON CONFLICT (tenant_id, id) DO NOTHING
            """
        ),
        rows,
    )
    return int(result.rowcount or 0)


def _json(value: Any) -> str:
    import json

    return json.dumps(value, sort_keys=True)


def cite(conn: Connection, *, tenant_id: uuid.UUID, construct: str) -> str | None:
    """The active claim id for a construct, or ``None`` when there is none.

    A `None` here is what turns a recommendation into an abstention: serving
    advice whose reason cannot be resolved is the one thing G3 forbids.
    """
    row = conn.execute(
        text(
            """
            SELECT id FROM claims
             WHERE tenant_id = :tenant_id AND construct = :construct AND status = 'active'
             ORDER BY id
             LIMIT 1
            """
        ),
        {"tenant_id": tenant_id, "construct": construct},
    ).one_or_none()
    return None if row is None else str(row.id)
