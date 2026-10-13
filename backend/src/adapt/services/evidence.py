"""Server-side adapter from a stored failed attempt to the standalone recovery engine.

Shared attempt submission may call start_for_attempt directly; no dependency on HTTP or
AppServices wiring. The content engine supplies checked explicit pairs, not similar items.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..assessment.bank import render_item_for, variant_seed
from ..assessment.selector import expected_success_rate, gradeable
from ..db.models import Attempt, ContentPack, Item, TopicPrerequisite
from ..learning.bkt import params_for_kind
from ..learning.failure import FailureError, FailureLoop
from .attempts import item_payload


def start_for_attempt(svcs: Any, s: Session, student_id: str, attempt_id: str) -> dict[str, Any]:
    loop = FailureLoop(svcs.state)
    attempt = s.get(Attempt, attempt_id)
    if attempt is None or attempt.student_id != student_id:
        raise KeyError(attempt_id)
    try:
        return loop.view(student_id, attempt_id)
    except KeyError:
        pass
    if attempt.correct is not False or attempt.expected_success is None:
        raise FailureError("a scored failed attempt with a frozen prediction is required")
    # Content-engine handoff: validated packs only. The integrator maps its pack
    # schema to this explicit matched_pairs/variants contract (documented in EVIDENCE.md).
    pack, pair = None, None
    for row in svcs.state.content_for(attempt.topic_id):
        if row["validation"] != "validated":
            continue
        content = s.get(ContentPack, row["id"])
        payload = content.payload or {}
        for candidate in payload.get("matched_pairs", []):
            if candidate.get("a", {}).get("id") == attempt.item_id:
                pack, pair = payload, candidate
                break
        if pair:
            break
    if pair is None:
        raise FailureError(
            "no validated matched B question is available; no comparison was invented"
        )
    if not isinstance(pair.get("b"), dict) or not pair["b"].get("id"):
        raise FailureError("the matched B question is malformed")
    a = render_item_for(pair["a"], seed=variant_seed(student_id, pair["a"]["id"]))
    b = render_item_for(pair["b"], seed=variant_seed(student_id, pair["b"]["id"]))
    # Exact served A must be the validated A, not an unrelated same-id answer key.
    stored_a = item_payload(s, attempt.item_id)
    if stored_a is None:
        raise FailureError("the original question is unavailable")
    original = render_item_for(stored_a, seed=variant_seed(student_id, attempt.item_id))
    if any(a.get(k) != original.get(k) for k in ("kind", "stem", "answer_spec", "difficulty")):
        raise FailureError("matched A differs from the question actually answered")
    direct = sorted(
        s.scalars(
            select(TopicPrerequisite.prerequisite_id).where(
                TopicPrerequisite.topic_id == attempt.topic_id
            )
        ).all()
    )
    prerequisite = None
    if direct:
        # One question on a direct prerequisite, not an inference from low mastery.
        ids = s.scalars(
            select(Item.id)
            .where(Item.topic_id == direct[0], Item.active.is_(True))
            .order_by(Item.difficulty, Item.id)
        ).all()
        for item_id in ids:
            candidate = item_payload(s, item_id)
            if candidate and gradeable(candidate):
                prerequisite = render_item_for(candidate, seed=variant_seed(student_id, item_id))
                break
    recent = [
        {"correct": r.correct, "time_on_item_ms": r.time_on_item_ms}
        for r in s.scalars(
            select(Attempt)
            .where(Attempt.student_id == student_id, Attempt.session_id == attempt.session_id)
            .order_by(Attempt.submitted_at, Attempt.id)
        ).all()
    ]
    snapshot = svcs.state.get_snapshot(student_id)
    params = params_for_kind(
        b["kind"],
        choices=len(b.get("choices") or []) or None,
        base=svcs.ai1.params_for(student_id, attempt.topic_id, s),
    )
    expected_b = expected_success_rate(snapshot.p_know(attempt.topic_id), b["difficulty"], params)
    return loop.start(
        student_id,
        attempt_id=attempt.id,
        session_id=attempt.session_id,
        a=a,
        b=b,
        method=attempt.method,
        expected_a=attempt.expected_success,
        expected_b=expected_b,
        variants=pack.get("variants", {}),
        prerequisite=prerequisite,
        prerequisite_required=bool(direct),
        recent=recent,
    )
