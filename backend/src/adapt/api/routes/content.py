"""The content engine's API (§7): a topic's breakdown, and one concept's teaching pack.

**Why this route exists at all.** The engine is deterministic and needs no database, so every gate and
test can run it directly — but a screen cannot. The subject round's screen, the lesson plan and the
reteach loop all want "teach me this concept", and without a route the only way to reach the pack is
to be the orchestrator. This is that way in.

**What it refuses to do.** It never writes. A GET that mutates is how a refresh becomes a
generation bill and how a cache stops being a cache, so the route reads: the cache is filled by the
orchestrator's turn cycle (which is AI 2's job in the write matrix), and this route reports whether a
pack exists, generating one in memory when it does not and saying plainly that nothing was stored.

Answers never reach the browser. The matched pair is the part a learner answers, so its key is
stripped by ``subjects.games.public_task`` — the same function the skill round uses, so "the answer
key is not sent" means the same thing here as there.
"""

from __future__ import annotations

import random
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ...assessment import worked as W
from ...content.breakdown import breakdown_for
from ...content.pack import build_concept_pack
from ...learning.seeds import stable_seed
from ...subjects.games import public_task
from ..deps import rate, student_from_request

router = APIRouter(prefix="/api/v1/content", tags=["content"])


def _public_pair(pair: dict[str, Any], *, seed: int) -> dict[str, Any]:
    """The pair with its key removed, by the repo's own stripper — in the shape it understands.

    ``public_task`` removes ``answer``, ``pairs``, ``steps``, ``why`` and the rest of the private
    fields, but its list is written for the *skill round's* task shape and does not know the bank
    shape's ``answer_spec``. Handing it a bank-shaped item therefore sent ``answer_spec:
    {"correct_index": 0}`` straight to the browser — the answer, in the payload, under a name the
    stripper had never been told about.

    So the item is converted to the choice-task shape first, and then stripped. That also buys the
    skill round's other guarantee for free: the options are shuffled by a seed derived from
    (learner, item), so two learners are not answering a shared key by position and an "always pick
    the second one" habit cannot score.
    """
    out: dict[str, Any] = dict(pair)
    for leg in ("A", "B"):
        item = pair.get(leg)
        if not isinstance(item, dict):
            continue
        choices = [c for c in (item.get("choices") or []) if isinstance(c, dict)]
        answer = (item.get("answer_spec") or {}).get("correct_index")
        task = {
            **item,
            "kind": "choice",
            "options": [str(c.get("text") or "") for c in choices],
            "answer": int(answer) if isinstance(answer, int) else 0,
        }
        task.pop("choices", None)
        task.pop("answer_spec", None)
        out[leg] = _shuffled_public(task, seed=seed, leg=leg)
    return out


def _shuffled_public(task: dict[str, Any], *, seed: int, leg: str) -> dict[str, Any]:
    """``public_task`` with the option order shuffled per learner and item, the way the skill round does.

    ``public_task`` shuffles with its own ``_rng(task_id, seed, "shuffle")``, which is seeded from the
    run; here there is no run, so the seed is derived from (learner, concept) and the shuffle happens
    here instead. The property is the same one the skill round relies on: two learners are not
    answering a shared key by position.
    """
    rng = random.Random(stable_seed("content", seed, str(task.get("id") or leg)))
    positions = list(range(len(task.get("options") or [])))
    rng.shuffle(positions)
    public = public_task({**task, "id": str(task.get("id") or f"pk_{leg}")})
    if task.get("options"):
        public["options"] = [task["options"][p] for p in positions]
    return public


@router.get("/breakdown", dependencies=[Depends(rate("content"))])
def topic_breakdown(
    topic: str = Query(..., min_length=2, max_length=80),
    _student=Depends(student_from_request),
) -> dict[str, Any]:
    """A topic's concepts, prerequisites, misconceptions and objectives (§7.1).

    A topic with no authored chain returns ``available: false`` with the reason, which is the honest
    answer the placement ladder gives too. There is no "we made some up" path.
    """
    breakdown = breakdown_for(topic)
    payload = breakdown.as_dict()
    payload["concepts"] = [
        {k: v for k, v in c.as_dict().items() if k != "kit"} for c in breakdown.concepts
    ]
    return payload


@router.get("/pack", dependencies=[Depends(rate("content"))])
def concept_pack(
    topic: str = Query(..., min_length=2, max_length=80),
    concept: str = Query(..., min_length=2, max_length=80),
    _student=Depends(student_from_request),
) -> dict[str, Any]:
    """One concept's three variants, its matched pair, its hints and its checklist (§7.2).

    The worked example and the hints are teaching material and are sent whole. The pair's key is not:
    ``A`` and ``B`` arrive with the answer stripped, and the server keeps it for grading — the same
    rule the skill round follows, for the same reason.
    """
    breakdown = breakdown_for(topic)
    if not breakdown.available:
        raise HTTPException(status_code=404, detail=breakdown.reason)
    wanted = breakdown.concept(concept)
    if wanted is None:
        raise HTTPException(
            status_code=404,
            detail=f"{topic!r} has no concept called {concept!r}; its concepts are "
            f"{', '.join(c.id for c in breakdown.concepts)}",
        )
    pack = build_concept_pack(wanted, topic_key=breakdown.topic_key, topic_title=breakdown.title)
    payload = pack.as_dict()
    payload["pair"] = _public_pair(payload["pair"], seed=stable_seed(_student.id, wanted.id))
    payload["stored"] = False  # this route reads; the orchestrator's turn cycle is what caches
    return payload


@router.get("/hint", dependencies=[Depends(rate("content"))])
def graded_hint(
    topic: str = Query(..., min_length=2, max_length=80),
    concept: str = Query(..., min_length=2, max_length=80),
    index: int = Query(0, ge=0, le=9),
    _student=Depends(student_from_request),
) -> dict[str, Any]:
    """The nth graded hint for a concept: nudge, then method, then near-answer.

    ``index`` is clamped by :func:`assessment.worked.hint_grade` rather than here, so a request for
    hint 9 returns the third hint and says which grade it is — a hint ladder that 404s on the last
    rung is a hint ladder a learner cannot finish. (The bound is 9 rather than 2 so that clamping is
    reachable at all: a ``le=2`` made every out-of-range request a 422 and the clamp dead code.)
    """
    breakdown = breakdown_for(topic)
    wanted = breakdown.concept(concept) if breakdown.available else None
    if wanted is None:
        raise HTTPException(status_code=404, detail=f"no concept {concept!r} for topic {topic!r}")
    pack = build_concept_pack(wanted, topic_key=breakdown.topic_key, topic_title=breakdown.title)
    # The graded ladder is the first three hints. The pack's fourth entry is the authored explanation
    # of the answer, which is *feedback after an attempt* and not a rung a learner may climb to —
    # serving it here would hand over the answer to anyone who asked for a ninth hint.
    ladder = pack.hints[: len(W.HINT_GRADES)]
    if not ladder:
        raise HTTPException(status_code=404, detail=f"{concept!r} has no authored hints")
    position = min(index, len(ladder) - 1)
    hint = ladder[position]
    return {
        "concept": wanted.id,
        "grade": W.hint_grade(position),
        "index": position,
        "of": len(ladder),
        "text": hint["text"],
        "source": hint.get("source", ""),
    }
