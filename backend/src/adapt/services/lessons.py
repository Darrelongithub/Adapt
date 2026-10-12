"""The server half of the interactive engine: which scenes exist, where a learner stopped, and
what they predicted.

Three rules hold this module together, and they are the reason it is a service rather than a
route with queries in it:

* **Frames come from the simulation, once.** ``open_run`` computes the whole trace from the stored
  spec and hands it over; the player is then a pure reducer over that list (play/pause/step/scrub
  cost the server nothing, and a paused frame survives a refresh because the position is a row).
* **A prediction's answer is never stored, only recomputed.** ``grade`` runs the simulation again.
  So the frames the learner is holding cannot contain the answer, and the "they predicted it"
  claim in the database was produced by the same code that drew the animation.
* **What a learner changes becomes a scene of their own.** Re-running with different variables
  does not edit the built-in lesson and does not vanish at refresh: it derives a stored spec
  (validated exactly like AI 2's) and starts a fresh run, so the old one and its predictions
  remain on the record.

Telemetry gets the *sequence* (that is what AI 1 reasons over); ``LessonRun`` gets the
*summaries* (that is what the dashboards and the review screen read without replaying events).
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db.models import LessonRun, LessonScene, LearningSession, NarrationCache, utcnow
from ..learning import scenespec, sims
from . import telemetry as TEL

log = logging.getLogger("adapt.lessons")

#: how much of the learner's own prediction log is kept on the run row. The full sequence is in
#: LearnerEvent; this is the review screen, bounded so one row cannot grow without limit.
MAX_PREDICTION_LOG = 120
#: a frame position is data, not trust: clamped to the trace the spec actually produced
ACTIONS = ("play", "pause", "step", "back", "seek", "replay")
CACHE_TTL_DAYS = 30
#: appended to a scene a learner derived by changing its numbers
SUFFIX = " (your variables)"


@dataclass
class Opened:
    """What a player needs to start: the frames, and where this learner left off."""

    run_id: str
    scene_id: str
    spec_id: str
    kind: str
    title: str
    intent: str
    framing: dict[str, str]
    citations: list[dict[str, str]]
    params: dict[str, Any]
    frames: list[dict[str, Any]] = field(default_factory=list)
    frame_index: int = 0
    resumed: bool = False
    origin: str = "builtin"

    def as_dict(self) -> dict[str, Any]:
        prompts = {f["i"]: f["prompt"] for f in self.frames if f.get("prompt")}
        return {
            "run_id": self.run_id,
            "scene_id": self.scene_id,
            "spec_id": self.spec_id,
            "kind": self.kind,
            "title": self.title,
            "intent": self.intent,
            "framing": self.framing,
            "citations": self.citations,
            "params": self.params,
            "frames": self.frames,
            "frame_index": self.frame_index,
            "frame_count": len(self.frames),
            "prediction_frames": sorted(prompts),
            "resumed": self.resumed,
            "origin": self.origin,
            "renderer": {
                # the honest fallback is decided here, not guessed at in the browser: an unknown
                # kind still opens, still narrates, and says there is nothing to draw
                "available": self.kind in sims.KINDS,
                "note": (
                    "rendered frame by frame from the simulation's trace"
                    if self.kind in sims.KINDS
                    else f"no renderer for scene kind '{self.kind}': the narration plays, the stage stays empty"
                ),
            },
        }


# --------------------------------------------------------------------------- #
# scenes
# --------------------------------------------------------------------------- #


def ensure_scenes(s: Session) -> dict[str, Any]:
    """Seed the built-in lessons. Idempotent by content id, so a restart never churns ids.

    Ids churning matters: ``LessonRun.scene_id`` points at them, and a reseed that minted new
    rows would strand every learner's history on a deleted scene.
    """
    created = 0
    refreshed = 0
    for spec in scenespec.builtin_specs():
        row = s.execute(select(LessonScene).where(LessonScene.spec_id == spec["id"])).scalar_one_or_none()
        if row is None:
            s.add(
                LessonScene(
                    spec_id=spec["id"],
                    kind=spec["kind"],
                    title=spec["title"],
                    origin="builtin",
                    spec=spec,
                    frame_count=int(spec.get("frame_count") or 0),
                    prediction_count=len(spec.get("prediction_frames") or []),
                )
            )
            created += 1
            continue
        if row.spec != spec or row.title != spec["title"]:
            # same content id, different body: the validator changed the derivation (new limits,
            # new narration). Keep the id so runs stay attached, refresh what a player sees.
            row.spec = spec
            row.title = spec["title"]
            row.kind = spec["kind"]
            row.frame_count = int(spec.get("frame_count") or 0)
            row.prediction_count = len(spec.get("prediction_frames") or [])
            row.updated_at = utcnow()
            refreshed += 1
    s.commit()
    total = int(s.execute(select(func.count()).select_from(LessonScene)).scalar_one() or 0)
    return {"created": created, "refreshed": refreshed, "total": total}


def store_spec(s: Session, spec: dict[str, Any], *, origin: str, student_id: str | None = None, derived_from: str | None = None, note: dict[str, Any] | None = None) -> LessonScene:
    """Persist an already-validated spec, reusing the row if *this learner* already has it.

    Shared scenes (builtin, ai2) are one row per content id; a learner's copy is a row of
    theirs, so the second person to derive the same numbers gets their own lesson in their own
    list instead of silently writing into the first person's.
    """
    conds = [LessonScene.spec_id == spec["id"]]
    conds.append(LessonScene.author_student_id == student_id if origin == "learner" else LessonScene.author_student_id.is_(None))
    row = s.execute(select(LessonScene).where(*conds)).scalar_one_or_none()
    if row is not None:
        if note is not None and row.learner_note != note:
            row.learner_note = note
            s.commit()
        return row
    row = LessonScene(
        spec_id=spec["id"],
        kind=spec["kind"],
        title=spec["title"],
        origin=origin,
        author_student_id=student_id,
        derived_from=derived_from,
        spec=spec,
        frame_count=int(spec.get("frame_count") or 0),
        prediction_count=len(spec.get("prediction_frames") or []),
        learner_note=note,
    )
    s.add(row)
    s.flush()
    s.commit()
    return row


def get_scene(s: Session, ident: str) -> LessonScene | None:
    """Look a scene up by row id or by its content id."""
    row = s.get(LessonScene, ident)
    if row is not None:
        return row
    return s.execute(select(LessonScene).where(LessonScene.spec_id == ident)).scalar_one_or_none()


def visible_scenes(s: Session, student_id: str, *, kind: str | None = None, limit: int = 60) -> list[LessonScene]:
    """Built-ins, AI-authored ones, and this learner's own versions. Nobody else's."""
    shared = LessonScene.origin.in_(["builtin", "ai2"]) & LessonScene.author_student_id.is_(None)
    mine = LessonScene.author_student_id == student_id
    where = shared | mine
    if kind:
        where = where & (LessonScene.kind == kind)
    rows = (
        s.execute(
            select(LessonScene)
            .where(where)
            .order_by(LessonScene.origin.desc(), LessonScene.created_at)
            .limit(max(1, min(limit, 200)))
        )
        .scalars()
        .all()
    )
    return list(rows)


def scene_card(s: Session, row: LessonScene, *, student_id: str | None = None) -> dict[str, Any]:
    spec = dict(row.spec or {})
    last: LessonRun | None = None
    if student_id:
        last = s.execute(
            select(LessonRun)
            .where(LessonRun.scene_id == row.id, LessonRun.student_id == student_id)
            .order_by(LessonRun.last_seen_at.desc())
        ).scalars().first()
    return {
        "id": row.id,
        "spec_id": row.spec_id,
        "kind": row.kind,
        "title": row.title,
        "origin": row.origin,
        "intent": spec.get("intent") or "",
        "framing": spec.get("framing") or {"intro": "", "outro": ""},
        "concepts": spec.get("concepts") or [],
        "citations": spec.get("citations") or [],
        "params": spec.get("params") or {},
        "frame_count": row.frame_count or len(spec.get("narration") or []),
        "prediction_count": row.prediction_count or len(spec.get("prediction_frames") or []),
        "has_renderer": row.kind in sims.KINDS,
        "last_run": run_summary(last) if last else None,
    }


def kinds_table() -> dict[str, Any]:
    """What the engine can draw, straight from the modules that draw it."""
    return sims.catalogue()


# --------------------------------------------------------------------------- #
# runs: position, playback, predictions
# --------------------------------------------------------------------------- #


def run_for(s: Session, run_id: str, student_id: str) -> LessonRun | None:
    """Fetch a run *and* check it belongs to this learner. Ownership is never a client claim."""
    row = s.get(LessonRun, run_id)
    if row is None or row.student_id != student_id:
        return None
    return row


def scene_for_run(s: Session, run: LessonRun) -> LessonScene | None:
    return s.get(LessonScene, run.scene_id)


def open_run(
    s: Session,
    *,
    student_id: str,
    scene_ident: str,
    session_id: str | None = None,
    topic_id: str | None = None,
) -> Opened | dict[str, Any]:
    """Start a lesson, or pick up the one already in progress.

    Returns a dict with ``ok: False`` rather than raising when the scene does not exist: the route
    turns that into a 404 with a useful note, and the service stays usable from the CLI.
    """
    scene = get_scene(s, scene_ident)
    if scene is None:
        return {"ok": False, "reason": "no such scene", "next": "GET /api/v1/lessons lists the ones that exist"}
    spec = dict(scene.spec or {})
    try:
        frames = sims.frames_for(spec["kind"], spec.get("params") or {})
    except Exception as exc:  # a stored spec that no longer plays is a bug to report, not a blank page
        return {"ok": False, "reason": f"this scene's simulation no longer runs: {exc}", "spec_id": spec.get("id")}

    run = s.execute(
        select(LessonRun)
        .where(LessonRun.scene_id == scene.id, LessonRun.student_id == student_id, LessonRun.status == "active")
        .order_by(LessonRun.last_seen_at.desc())
    ).scalars().first()
    resumed = run is not None
    if run is None:
        sess = _session_for(s, student_id=student_id, session_id=session_id)
        run = LessonRun(
            student_id=student_id,
            scene_id=scene.id,
            session_id=sess.id if sess else None,
            topic_id=topic_id,
            status="active",
            frame_index=0,
            params=dict(spec.get("params") or {}),
        )
        s.add(run)
        s.flush()
        s.commit()
    frame_index = max(0, min(int(run.frame_index or 0), len(frames) - 1))
    TEL.append_event(
        s,
        session_id=run.session_id or "",
        student_id=student_id,
        type="scene_opened",
        payload={"scene_id": scene.id, "kind": scene.kind, "frame_count": len(frames), "resumed": resumed},
    )
    run.last_seen_at = utcnow()
    s.commit()
    return Opened(
        run_id=run.id,
        scene_id=scene.id,
        spec_id=scene.spec_id,
        kind=scene.kind,
        title=scene.title,
        intent=str(spec.get("intent") or ""),
        framing=dict(spec.get("framing") or {}),
        citations=list(spec.get("citations") or []),
        params=dict(spec.get("params") or {}),
        frames=frames,
        frame_index=frame_index,
        resumed=resumed,
        origin=scene.origin,
    )


def save_progress(
    s: Session,
    *,
    run: LessonRun,
    frame_index: int,
    action: str = "step",
    speed: float | None = None,
    seconds: float | None = None,
) -> dict[str, Any]:
    """Where the learner is now. Called on pause, on a seek and on finish -- not on every frame."""
    scene = scene_for_run(s, run)
    total = _frame_count(s, scene)
    target = max(0, min(int(frame_index), max(0, total - 1)))
    action = action if action in ACTIONS else "step"
    advanced = target > int(run.frame_index or 0)
    run.frame_index = target
    run.furthest_frame = max(int(run.furthest_frame or 0), target)
    run.frames_viewed = int(run.frames_viewed or 0) + (1 if advanced or action in {"replay", "step"} else 0)
    if action == "pause":
        run.pauses = int(run.pauses or 0) + 1
    if action == "replay":
        run.replays = int(run.replays or 0) + 1
    if action == "seek" or action == "back":
        run.seeks = int(run.seeks or 0) + 1
    if seconds is not None and seconds >= 0:
        run.seconds_watched = int(run.seconds_watched or 0) + min(int(seconds), 1800)
    run.last_seen_at = utcnow()
    if run.session_id:
        TEL.append_event(
            s,
            session_id=run.session_id,
            student_id=run.student_id,
            type="scene_frame",
            payload={"scene_id": run.scene_id, "frame": target, "action": action, "speed": speed} if speed else {"scene_id": run.scene_id, "frame": target, "action": action},
        )
    s.commit()
    return {
        "ok": True,
        "frame_index": run.frame_index,
        "furthest_frame": run.furthest_frame,
        "frames_viewed": run.frames_viewed,
        "frame_count": total,
        "at_end": run.frame_index >= max(0, total - 1),
    }


def predict(
    s: Session,
    *,
    run: LessonRun,
    frame_index: int,
    choice: Any,
    ms_taken: int | None = None,
) -> dict[str, Any]:
    """Record what the learner said would happen, and check it against the simulation."""
    scene = scene_for_run(s, run)
    if scene is None:
        return {"ok": False, "reason": "the scene this run belongs to is gone"}
    spec = dict(scene.spec or {})
    index = int(frame_index)
    try:
        graded = sims.grade_prediction(spec["kind"], spec.get("params") or {}, index, choice)
    except (KeyError, ValueError) as exc:
        return {"ok": False, "reason": str(exc), "frame": index}
    prompt = graded.get("prompt") or {}
    correct = bool(graded.get("correct"))
    run.predictions_total = int(run.predictions_total or 0) + 1
    run.predictions_correct = int(run.predictions_correct or 0) + (1 if correct else 0)
    run.last_seen_at = utcnow()
    log_rows = list((run.payload or {}).get("predictions") or [])
    entry = {
        "frame": index,
        "prompt": prompt.get("type"),
        "said": graded.get("said"),
        "expected": graded.get("expected"),
        "correct": correct,
        "at": utcnow().isoformat(timespec="seconds"),
    }
    log_rows = (log_rows + [entry])[-MAX_PREDICTION_LOG:]
    run.payload = {"predictions": log_rows}  # EncryptedJSON: the learner's own words stay private
    if run.session_id:
        TEL.append_event(
            s,
            session_id=run.session_id,
            student_id=run.student_id,
            type="scene_prediction",
            payload={
                "scene_id": scene.id,
                "frame": index,
                "prompt": str(prompt.get("type") or ""),
                "correct": correct,
                "ms_taken": ms_taken,
            },
        )
    s.commit()
    return {
        "ok": True,
        "correct": correct,
        "expected": graded.get("expected"),
        "said": graded.get("said"),
        "why": graded.get("why"),
        "frame": index,
        "prompt": prompt,
        "totals": {"predictions": run.predictions_total, "correct": run.predictions_correct},
    }


def change_variables(
    s: Session,
    *,
    run: LessonRun,
    params: dict[str, Any],
    note: str | None = None,
    topic_id: str | None = None,
) -> dict[str, Any]:
    """Re-run the scene with the learner's numbers, as a stored scene of their own.

    The built-in spec is never edited: this derives a new spec through the same validator AI 2
    writes through, so a hand-typed list is refused for exactly the reasons an AI-authored one
    would be (unsorted data, a program that jumps into its own data, a shape too big to animate).
    """
    scene = scene_for_run(s, run)
    if scene is None:
        return {"ok": False, "reason": "the scene this run belongs to is gone"}
    base = dict(scene.spec or {})
    base_title = str(base.get("title") or "A scene")
    # one suffix, however many times they press re-run: "(your variables) (your variables)" is
    # how a learner ends up with six copies of the same list under six different ids
    title = base_title if base_title.endswith(SUFFIX) else f"{base_title}{SUFFIX}"
    root = scene.derived_from or base.get("id")
    draft = {
        "kind": base.get("kind"),
        "title": title,
        "intent": base.get("intent") or "",
        "framing": base.get("framing") or {},
        "concepts": base.get("concepts") or [],
        "citations": base.get("citations") or [],
        "params": params or {},
    }
    result = scenespec.validate(draft)
    if not result.ok:
        return {
            "ok": False,
            "errors": result.errors,
            "warnings": result.warnings,
            "note": "the scene was not changed: nothing you typed was applied",
        }
    new_scene = store_spec(
        s,
        result.spec,
        origin="learner",
        student_id=run.student_id,
        derived_from=root,
        note={"why": (note or "")[:400] or None, "from_spec_id": root} if note else None,
    )
    # close the current run rather than mutating it under the learner: their predictions were about
    # the previous variables, and a review screen that mixes the two would be lying about both
    finish(s, run=run, status="stopped")
    opened = open_run(s, student_id=run.student_id, scene_ident=new_scene.id, session_id=run.session_id, topic_id=topic_id or run.topic_id)
    if isinstance(opened, dict):  # pragma: no cover - the scene we just stored must play
        return opened
    new_run = s.get(LessonRun, opened.run_id)
    if new_run is not None:
        new_run.param_changes = int(new_run.param_changes or 0) + 1
        if new_run.session_id:
            TEL.append_event(
                s,
                session_id=new_run.session_id,
                student_id=new_run.student_id,
                type="scene_variables",
                payload={"scene_id": new_scene.id, "changed": len(params or {}), "replayed": True},
            )
        s.commit()
    out = opened.as_dict()
    out.update({"ok": True, "derived_from": root, "warnings": result.warnings, "changed": sorted(params or {})})
    return out


def finish(s: Session, *, run: LessonRun, status: str = "finished") -> dict[str, Any]:
    if run.status != "active":
        return {"ok": True, "already": True, "run_id": run.id, "status": run.status}
    run.status = status if status in {"finished", "stopped"} else "finished"
    run.finished_at = utcnow()
    run.last_seen_at = run.finished_at
    scene = scene_for_run(s, run)
    total = _frame_count(s, scene)
    if run.session_id:
        TEL.append_event(
            s,
            session_id=run.session_id,
            student_id=run.student_id,
            type="scene_finished",
            payload={
                "scene_id": run.scene_id,
                "frames_viewed": int(run.frames_viewed or 0),
                "seconds": int(run.seconds_watched or 0),
                "predictions": int(run.predictions_total or 0),
                "correct": int(run.predictions_correct or 0),
            },
        )
        TEL.refresh_session_stats(s, session_id=run.session_id)
    s.commit()
    return {"ok": True, "run_id": run.id, "status": run.status, "coverage": round(min(1.0, (run.frames_viewed or 0) / max(1, total)), 3)}


def review(s: Session, *, run: LessonRun) -> dict[str, Any]:
    """What happened, in the learner's terms: each prediction, what they said, what the machine did."""
    scene = scene_for_run(s, run)
    spec = dict(scene.spec or {}) if scene else {}
    frames: list[dict[str, Any]] = []
    try:
        frames = sims.frames_for(spec["kind"], spec.get("params") or {}) if spec else []
    except Exception as exc:  # a broken scene must still show its history
        log.warning("review could not replay %s: %s", spec.get("id"), exc)
    prompts = {f["i"]: f.get("prompt") for f in frames if f.get("prompt")}
    entries = list((run.payload or {}).get("predictions") or [])
    return {
        "run_id": run.id,
        "scene": scene_card(s, scene, student_id=run.student_id) if scene else None,
        "status": run.status,
        "frame_index": run.frame_index,
        "furthest_frame": run.furthest_frame,
        "frames_viewed": run.frames_viewed,
        "frame_count": len(frames) or run.frame_count if scene else 0,
        "predictions": [
            {
                **e,
                "question": (prompts.get(int(e.get("frame", -1))) or {}).get("question"),
                "choices": (prompts.get(int(e.get("frame", -1))) or {}).get("choices"),
            }
            for e in entries
        ],
        "totals": {
            "predictions": int(run.predictions_total or 0),
            "correct": int(run.predictions_correct or 0),
            "accuracy": round((run.predictions_correct or 0) / max(1, run.predictions_total or 1), 3),
            "chance": 1 / 3,
            "replays": int(run.replays or 0),
            "pauses": int(run.pauses or 0),
            "param_changes": int(run.param_changes or 0),
            "seconds_watched": int(run.seconds_watched or 0),
        },
        "skipped_predictions": sorted(set(prompts) - {int(e.get("frame", -1)) for e in entries}),
    }


def run_summary(row: LessonRun | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "run_id": row.id,
        "status": row.status,
        "frame_index": int(row.frame_index or 0),
        "furthest_frame": int(row.furthest_frame or 0),
        "frames_viewed": int(row.frames_viewed or 0),
        "predictions": int(row.predictions_total or 0),
        "correct": int(row.predictions_correct or 0),
        "param_changes": int(row.param_changes or 0),
        "seconds_watched": int(row.seconds_watched or 0),
        "last_seen_at": row.last_seen_at.isoformat() if row.last_seen_at else None,
    }


def active_run(s: Session, *, student_id: str, scene_id: str) -> LessonRun | None:
    return s.execute(
        select(LessonRun)
        .where(LessonRun.student_id == student_id, LessonRun.scene_id == scene_id, LessonRun.status == "active")
        .order_by(LessonRun.last_seen_at.desc())
    ).scalars().first()


def student_runs(s: Session, student_id: str, *, limit: int = 20, include_finished: bool = True) -> list[dict[str, Any]]:
    conds = [LessonRun.student_id == student_id]
    if not include_finished:
        conds.append(LessonRun.status == "active")
    rows = s.execute(select(LessonRun).where(*conds).order_by(LessonRun.last_seen_at.desc()).limit(limit)).scalars().all()
    out = []
    for r in rows:
        scene = scene_for_run(s, r)
        out.append({**(run_summary(r) or {}), "spec_id": scene.spec_id if scene else None, "kind": scene.kind if scene else None, "title": scene.title if scene else None})
    return out


def evidence_summary(s: Session, student_id: str, *, since: datetime | None = None) -> dict[str, Any]:
    """Totals the dashboard and AI 1's evidence panel read, computed from run rows only."""
    conds = [LessonRun.student_id == student_id]
    if since is not None:
        conds.append(LessonRun.started_at >= since)
    rows = s.execute(select(LessonRun).where(*conds)).scalars().all()
    by_kind: dict[str, dict[str, int]] = {}
    for r in rows:
        scene = scene_for_run(s, r)
        kind = scene.kind if scene else "?"
        slot = by_kind.setdefault(kind, {"runs": 0, "finished": 0, "predictions": 0, "correct": 0, "frames": 0})
        slot["runs"] += 1
        slot["finished"] += 1 if r.status == "finished" else 0
        slot["predictions"] += int(r.predictions_total or 0)
        slot["correct"] += int(r.predictions_correct or 0)
        slot["frames"] += int(r.frames_viewed or 0)
    made = sum(v["predictions"] for v in by_kind.values())
    right = sum(v["correct"] for v in by_kind.values())
    return {
        "runs": len(rows),
        "finished": sum(1 for r in rows if r.status == "finished"),
        "predictions": made,
        "correct": right,
        "accuracy": round(right / made, 3) if made else None,
        "chance": 1 / 3,
        "by_kind": by_kind,
        "note": (
            "accuracy here is measured on frames before the simulation shows what happens next. "
            "It is not a mastery number and is not fed into one."
        ),
    }


# --------------------------------------------------------------------------- #
# narration: AI 3's cache in front of the model
# --------------------------------------------------------------------------- #


def cache_key(spec_id: str, frame_index: int, question: str = "") -> str:
    q = " ".join((question or "").lower().split())
    digest = hashlib.sha256(f"{spec_id}|{int(frame_index)}|{q}".encode()).hexdigest()
    return digest[:32]


@dataclass
class Explanation:
    """The answer to 'why this frame?', plus how it was obtained.

    ``needs_model`` is the only case where a model call is even considered, and it is decided by
    the simulation declining to answer -- not by a preference for fluent prose.
    """

    text: str
    source: str  # cache|deterministic|template|llm
    frame: int
    cached: bool = False
    needs_model: bool = False
    question: str = ""
    key: str = ""
    model: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    citations: list[dict[str, str]] = field(default_factory=list)
    #: why there is no better answer than this one; the model branch fills it, and the
    #: deterministic branch leaves it empty so both responses carry the same keys
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "note": self.note,
            "source": self.source,
            "frame": self.frame,
            "cached": self.cached,
            "needs_model": self.needs_model,
            "question": self.question,
            "model": self.model,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "citations": self.citations,
        }


def explain(s: Session, *, scene: LessonScene, frame_index: int, question: str = "") -> Explanation:
    """Look up 'why' in this order: already answered, the simulation knows, nothing else does.

    Two of the three paths cost no model call, which is what keeps a 56-frame lesson from
    becoming 56 requests to Gemini.
    """
    spec = dict(scene.spec or {})
    index = max(0, min(int(frame_index), max(0, int(scene.frame_count or 1) - 1)))
    key = cache_key(scene.spec_id, index, question)
    row = s.get(NarrationCache, key)
    if row is not None and (row.expires_at is None or row.expires_at > utcnow()):
        row.hits = int(row.hits or 0) + 1
        s.commit()
        return Explanation(text=row.text, source=row.source or "cache", frame=index, cached=True, question=question, key=key, model=row.model, citations=list(spec.get("citations") or []))

    text: str | None = None
    source = "deterministic"
    try:
        mod = sims.module_for(spec.get("kind") or "")
    except KeyError:
        mod = None
    if mod is not None and question.strip():
        answer = mod.explain(spec.get("params") or {}, index, question) if hasattr(mod, "explain") else None
        if answer:
            text = str(answer.get("answer") or "") or None
    if text is None and not question.strip():
        # the narration of the frame itself is the answer to "what am I looking at"
        frames = _frames_of(s, scene)
        if frames and index < len(frames):
            text = str(frames[index].get("narration") or "")
            source = "deterministic"
    if text is None:
        return Explanation(
            text="",
            source="none",
            frame=index,
            needs_model=True,
            question=question,
            key=key,
            citations=list(spec.get("citations") or []),
        )
    store_explanation(s, cache_key=key, spec_id=scene.spec_id, kind=spec.get("kind") or scene.kind, frame_index=index, question=question, text=text, source=source)
    return Explanation(text=text, source=source, frame=index, question=question, key=key, citations=list(spec.get("citations") or []))


def store_explanation(
    s: Session,
    *,
    cache_key: str,
    spec_id: str,
    kind: str,
    frame_index: int,
    question: str,
    text: str,
    source: str,
    model: str | None = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
) -> NarrationCache:
    row = s.get(NarrationCache, cache_key)
    if row is None:
        row = NarrationCache(key=cache_key, spec_id=spec_id, kind=kind, frame_index=int(frame_index))
        s.add(row)
    row.text = text[:2000]
    row.source = source
    row.model = model
    row.tokens_in = int(tokens_in)
    row.tokens_out = int(tokens_out)
    row.question_hash = hashlib.sha256(" ".join((question or "").lower().split()).encode()).hexdigest()[:16]
    row.created_at = utcnow()
    row.expires_at = utcnow() + timedelta(days=CACHE_TTL_DAYS)
    s.commit()
    return row


def cache_stats(s: Session) -> dict[str, Any]:
    rows = s.execute(select(NarrationCache)).scalars().all()
    by_source: dict[str, int] = {}
    for r in rows:
        by_source[r.source or "?"] = by_source.get(r.source or "?", 0) + 1
    return {
        "entries": len(rows),
        "hits": sum(int(r.hits or 0) for r in rows),
        "by_source": by_source,
        "model_calls_saved": sum(int(r.hits or 0) for r in rows if r.source != "llm"),
        "ttl_days": CACHE_TTL_DAYS,
    }


def prune_expired(s: Session, *, now: datetime | None = None) -> int:
    """Drop cached narration past its TTL. Rows never expire on their own in SQLite."""
    when = now or utcnow()
    rows = s.execute(select(NarrationCache).where(NarrationCache.expires_at.is_not(None), NarrationCache.expires_at < when)).scalars().all()
    for r in rows:
        s.delete(r)
    if rows:
        s.commit()
    return len(rows)


# --------------------------------------------------------------------------- #
# internals
# --------------------------------------------------------------------------- #


def _session_for(s: Session, *, student_id: str, session_id: str | None) -> LearningSession | None:
    """The learner's current session, or a lesson one. Telemetry needs a session to be ordered in."""
    if session_id:
        sess = s.get(LearningSession, session_id)
        if sess is not None and sess.student_id == student_id:
            return sess
    sess = s.execute(
        select(LearningSession)
        .where(LearningSession.student_id == student_id, LearningSession.status == "active")
        .order_by(LearningSession.started_at.desc())
    ).scalars().first()
    if sess is not None:
        return sess
    from . import sessions as S

    return S.start_session(s, student_id, mode="lesson")


def _frame_count(s: Session, scene: LessonScene | None) -> int:
    if scene is None:
        return 0
    stored = int(scene.frame_count or 0)
    if stored > 0:
        return stored
    return len(_frames_of(s, scene))


def _frames_of(s: Session, scene: LessonScene) -> list[dict[str, Any]]:
    spec = dict(scene.spec or {})
    try:
        return sims.frames_for(spec.get("kind") or "", spec.get("params") or {})
    except Exception as exc:
        log.warning("scene %s cannot be replayed: %s", spec.get("id"), exc)
        return []


def as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else ([] if value is None else [value])


def json_safe(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


__all__ = [
    "ACTIONS",
    "Explanation",
    "Opened",
    "active_run",
    "as_list",
    "cache_key",
    "cache_stats",
    "change_variables",
    "ensure_scenes",
    "evidence_summary",
    "explain",
    "finish",
    "get_scene",
    "json_safe",
    "kinds_table",
    "open_run",
    "predict",
    "prune_expired",
    "review",
    "run_for",
    "run_summary",
    "save_progress",
    "scene_card",
    "scene_for_run",
    "store_explanation",
    "store_spec",
    "student_runs",
    "visible_scenes",
]
