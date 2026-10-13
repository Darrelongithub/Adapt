"""The derived learner model: a pure function of the evidence log.

The brief requires that a full rebuild equals the incrementally built model, and the only way to
guarantee that for good is to make the model a *fold over the log* rather than a running total kept
beside it. So ``build_model(rows)`` is pure: same rows in, same model out, always. The incremental
path calls it with every row so far; ``adapt rebuild-model`` calls it with every row there is. They
cannot disagree, because they are the same function.

What the fold does, per controlled observation, in time order:

* reads the item's format and takes the guess rate that format deserves (``learning.bkt``);
* moves ``p_know`` by one bounded BKT step (one answer is one piece of evidence, and the cap is
  what makes "0.20 → 0.03 on one miss" impossible);
* keeps the count, the correct count and the time, so every downstream surface can print n, an
  interval and an early-signal label next to the estimate.

Observational evidence moves the estimate by its weight (≈0.4) and is held separately: it is
evidence about the session — a stall, a re-ask — and the model says so rather than averaging it in.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from ..learning import bkt as B


def _ordered(rows: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The fold order: by time, then by id, so two runs cannot disagree on a tie."""
    return sorted(rows, key=lambda r: (str(r.get("occurred_at") or ""), str(r.get("id") or "")))


def build_model(
    rows: Sequence[Mapping[str, Any]],
    *,
    titles: Mapping[str, str] | None = None,
    previous: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Fold the evidence log into the derived model.

    ``previous`` is the model this one supersedes. It is used for exactly one thing: carrying
    forward a topic's *title* when the current fold has no evidence for it yet, so the model is not
    a function of the bank's current titles (which would make a rebuild after a bank edit produce a
    different model from the same evidence and break the equality claim).
    """
    titles = dict(titles or {})
    for row in (previous or {}).get("topics") or ():
        if isinstance(row, Mapping) and row.get("topic_id") and row.get("title"):
            titles.setdefault(str(row["topic_id"]), str(row["title"]))

    per_topic: dict[str, dict[str, Any]] = {}
    methods: dict[str, int] = {}
    kinds: dict[str, int] = {}
    observational: dict[str, int] = {}
    scored = 0

    for row in _ordered(rows):
        kind = str(row.get("kind") or "")
        topic_id = row.get("topic_id")
        weight = float(row.get("weight") or 0.0)
        source = str(row.get("source_type") or "controlled")
        if source != "controlled":
            observational[kind] = observational.get(kind, 0) + 1
        if kind == "method_assigned":
            key = str(row.get("method") or (row.get("payload") or {}).get("method") or "unknown")
            methods[key] = methods.get(key, 0) + 1
        correct = row.get("correct")
        if correct is None or not topic_id:
            continue
        st = per_topic.setdefault(
            str(topic_id),
            {
                "topic_id": str(topic_id),
                "p_know": float(B.BKTParams().p_init),
                "n": 0,
                "correct": 0,
                "last_at": None,
                "kinds": {},
            },
        )
        st["n"] += 1
        st["correct"] += 1 if correct else 0
        st["last_at"] = row.get("occurred_at") or st["last_at"]
        st["kinds"][kind] = st["kinds"].get(kind, 0) + 1
        kinds[kind] = kinds.get(kind, 0) + 1
        scored += 1
        step = _expected_step(
            float(st["p_know"]), bool(correct), kind=kind, choices=row.get("choices")
        )
        if weight >= 1.0:
            st["p_know"] = step[0]
        else:
            # An observational scored row is a weaker fact: move part of the way the full step
            # would have moved, in proportion to its weight.
            st["p_know"] = float(st["p_know"]) + max(0.0, weight) * (step[0] - float(st["p_know"]))
        st["last_p_guess_used"] = step[1]

    topics: list[dict[str, Any]] = []
    for tid, st in sorted(per_topic.items()):
        lo, hi = B.credible_interval(float(st["p_know"]), int(st["n"]), int(st["correct"]))
        topics.append(
            {
                "topic_id": tid,
                "title": titles.get(tid, str(st.get("title") or tid)),
                "p_know": round(float(st["p_know"]), 4),
                "band": B.BKTParams().band(float(st["p_know"]), observations=int(st["n"])),
                "n": int(st["n"]),
                "correct": int(st["correct"]),
                "ci": [round(lo, 3), round(hi, 3)],
                "early": int(st["n"]) < B.EARLY_SIGNAL_N,
                "last_at": st["last_at"],
                "kinds": dict(st["kinds"]),
            }
        )

    from ..learning.failure import outcome_report

    return {
        "method_evidence": outcome_report(rows),
        "model_kind": "derived_from_evidence",
        "topics": topics,
        "methods_seen": dict(sorted(methods.items())),
        "observational": dict(sorted(observational.items())),
        "scored_observations": scored,
        "kinds_seen": dict(sorted(kinds.items())),
        "fold": "bkt.update_capped per controlled observation, weighted blend for observational",
    }


def _expected_step(p_know: float, correct: bool, *, kind: str, choices: Any) -> tuple[float, float]:
    params = B.params_for_kind(kind or None, choices=choices)
    bounded, note = B.update_capped(p_know, correct, params, kind=kind or None, choices=choices)
    return bounded, float(note.get("p_guess_used") or params.p_guess)


def evidence_summary(model: Mapping[str, Any]) -> dict[str, Any]:
    """The two or three numbers a screen may show about the evidence behind a model."""
    topics = model.get("topics") or []
    total = sum(int(t.get("n") or 0) for t in topics if isinstance(t, Mapping))
    return {
        "topics": len(topics),
        "observations": total,
        "scored_observations": int(model.get("scored_observations") or 0),
        "kinds": dict(model.get("kinds_seen") or {}),
        "observational": dict(model.get("observational") or {}),
    }
