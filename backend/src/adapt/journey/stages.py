"""The path, in order: the one copy of the stage list.

The frozen contract (round 2, Part 0) names the eight stage ids and their order:

    account, preferences, games, subject, subject_round, snapshot, first_lesson, practice

Every screen, the progress bar, the resume rule and the API read this tuple. Nothing
else in the codebase is allowed to write the sequence out again -- six screens each
holding their own idea of "step 4" is exactly how a path stops being one path.

**What each stage is derived from.** A stage's status is never a stored cursor. It is
derived from the row that proves the work happened, and an explicit *skip* recorded by
:mod:`adapt.journey.state` is the only stored value. The evidence each stage reads:

===============  ==========================================================
stage            proven by
===============  ==========================================================
account          the learner row itself (a guest is an account too)
preferences      the baseline run's ``completion["questions"]`` flag
games            a baseline run that was sealed (``status == "complete"``)
subject          a subject round opened for a topic the learner chose
subject_round    a subject round the service marked complete
snapshot         a mind snapshot row exists (that is what "noticed" is stored as)
first_lesson     a lesson run, or a lesson-mode session, for this learner
practice         a scored attempt outside placement
===============  ==========================================================

Two consequences worth being explicit about, because they are choices:

* **The snapshot can be done before the subject round is.** The mind-check screen
  seals its snapshot when the games end, which is earlier than this path's order.
  Saying so is more honest than pretending the learner has not seen it, and the
  shell reads "where you are" as the first stage still to do, so the path still
  points forward.
* **Placement is not a stage.** The eight unaided questions stay reachable from the
  journey shell, but they are measurement rather than a step on the path, and a
  stage list that counted them would make "practice" mean two different things.
"""

from __future__ import annotations

#: The stage ids, in order. Frozen by the shared header; do not reorder.
STAGES: tuple[str, ...] = (
    "account",
    "preferences",
    "games",
    "subject",
    "subject_round",
    "snapshot",
    "first_lesson",
    "practice",
)

STAGE_IDS: tuple[str, ...] = STAGES

#: id → position, for the progress bar and for "which stage is this?".
STAGE_INDEX: dict[str, int] = {stage: i for i, stage in enumerate(STAGES)}

#: The statuses a stage row may hold. ``todo`` is the absence of a row, so only
#: ``done`` and ``skipped`` are ever written.
STATUSES: tuple[str, ...] = ("todo", "done", "skipped")

#: The statuses a client may *set*. ``todo`` is not one of them: un-asking a question
#: already answered is not a state change, it is a rewrite of history.
SETTABLE_STATUSES: tuple[str, ...] = ("done", "skipped")

STAGE_BY_ID: dict[str, int] = STAGE_INDEX


def is_stage(stage: str) -> bool:
    """Whether ``stage`` is one of the frozen ids. Case-sensitive on purpose."""
    return stage in STAGE_INDEX


def first_todo(statuses: dict[str, str]) -> str:
    """The stage the learner is on: the first not-done, not-skipped one.

    When every stage is done or skipped the last stage is returned, so a finished path
    still answers "where am I?" rather than returning nothing.
    """
    for stage in STAGES:
        if statuses.get(stage, "todo") == "todo":
            return stage
    return STAGES[-1]


__all__ = [
    "SETTABLE_STATUSES",
    "STAGES",
    "STAGE_BY_ID",
    "STAGE_IDS",
    "STAGE_INDEX",
    "STATUSES",
    "first_todo",
    "is_stage",
]
