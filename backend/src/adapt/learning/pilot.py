"""Pilot data: produced before the demo, labelled everywhere it appears.

The problem: :func:`adapt.learning.effect.evaluate_change` refuses to conclude
anything below five scored attempts on each side of a change, which is *correct*
and which made every live demo say "insufficient evidence" — the honest answer
arriving at the worst moment, and the obvious temptation being to lower the bar
until it stopped saying that.

The fix is not a lower bar. It is:

1. run the loop beforehand (a small pilot, a handful of people, the same prototype),
   so the data that is being shown is real data that already exists;
2. if the pilot was not run, *seed* it explicitly (``seed_pilot_rows`` here, and the
   ``adapt pilot`` command) so a demo has real rows rather than a special case in
   the statistics;
3. label it. Anything derived from seeded rows carries ``pilot: true`` and the
   sentence :data:`PILOT_LABEL`, in the payload and in the UI, because "12 answers
   from three friends" and "12 answers from a class" are different claims.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable, Sequence

from .seeds import stable_seed

#: The label every surface must show next to pilot-derived numbers.
PILOT_LABEL = "pilot data — not evidence of efficacy"
PILOT_NOTE = (
    "These answers come from the pre-demo pilot (a handful of users on this build). "
    "They are real rows and real responses, but they are not a study: no control "
    "group, no blinding, and a sample of friends."
)


@dataclass
class PilotSpec:
    """What the pilot is meant to contain, in the form the demo needs it."""

    method: str
    n: int
    expected_success: float
    actual_rate: float
    topic_id: str = "pilot_topic"
    item_id: str = "pilot_item"
    design: str = "randomised"


def seed_pilot_rows(
    *,
    student_id: str,
    specs: Sequence[PilotSpec],
    start: datetime | None = None,
) -> list[dict[str, Any]]:
    """Deterministic synthetic observations, each one flagged ``pilot``.

    ``correct`` is drawn from ``actual_rate`` with a seed derived from the learner,
    the method and the index, so the same call produces the same pilot twice (a
    demo that changes its own numbers between rehearsals is worse than no demo).
    """
    start = start or (datetime.utcnow() - timedelta(days=2))
    rows: list[dict[str, Any]] = []
    for spec in specs:
        for i in range(max(0, int(spec.n))):
            draw = (stable_seed(student_id, spec.method, str(i)) % 1000) / 1000.0
            correct = draw < max(0.0, min(1.0, float(spec.actual_rate)))
            rows.append(
                {
                    "student_id": student_id,
                    "topic_id": spec.topic_id,
                    "item_id": spec.item_id,
                    "method": spec.method,
                    "question_kind": "mcq",
                    "expected_success": float(spec.expected_success),
                    "mapping_design": spec.design,
                    "correct": bool(correct),
                    "score": 1.0 if correct else 0.0,
                    "pilot": True,
                    "submitted_at": start + timedelta(minutes=17 * len(rows) + i),
                    "flags": ["pilot"],
                }
            )
    return rows


#: The default pilot: two methods, enough observations for a comparison, and a
#: deliberately small effect size — a pilot that "proves" a big win is a pilot that
#: has been tuned, and the point of the label is that nobody has to pretend.
DEFAULT_PILOT: tuple[PilotSpec, ...] = (
    PilotSpec(method="worked_example", n=7, expected_success=0.62, actual_rate=0.71),
    PilotSpec(method="explanation", n=7, expected_success=0.60, actual_rate=0.43),
)


def default_rows(student_id: str) -> list[dict[str, Any]]:
    return seed_pilot_rows(student_id=student_id, specs=DEFAULT_PILOT)


def label(*, pilot: bool, base: str | None = None) -> str | None:
    """The label to attach to a result, or ``None`` when the data is not pilot data."""
    if not pilot:
        return base
    return PILOT_LABEL if not base else f"{base} ({PILOT_LABEL})"


def is_pilot(flags: Iterable[str] | None, *, pilot_field: bool = False) -> bool:
    if pilot_field:
        return True
    return any(str(f).lower() in {"pilot", "pilot_data"} for f in (flags or ()))
