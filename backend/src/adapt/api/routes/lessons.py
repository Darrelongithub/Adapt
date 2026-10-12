"""The interactive learning engine's API: play a scene, change its variables, predict, ask why.

The division of labour is deliberate and is the whole reason this file stays short:

* the **backend simulation** computes the steps (``learning/sims``);
* a **spec** is data, validated before it is ever stored (``learning/scenespec``), so nothing here
  accepts markup, code, or a "renderer" the server would obey;
* the **player** in the browser is a reducer over the frame list this API hands it once -- play,
  pause, step, scrub and replay cost no requests, so a lesson does not become 56 calls;
* the **position and the predictions** are written back, because "restart must not lose the
  lesson" and "AI 1 gets evidence about what the learner could foresee" are both storage promises.

Every route below belongs to one learner's own rows. There is no endpoint that takes a learner id:
the session cookie decides whose run is opened, refused, or resumed.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...learning import scenespec, sims
from ...services import current
from ...services import lessons as L
from ...db.models import Student
from ..deps import db_session, rate, student_from_request

router = APIRouter(prefix="/api/v1/lessons", tags=["lessons"])


class OpenBody(BaseModel):
    scene_id: str | None = None
    spec_id: str | None = None
    session_id: str | None = None
    topic_id: str | None = None


class ProgressBody(BaseModel):
    frame_index: int = Field(ge=0, le=4000)
    action: str = Field(default="step", max_length=12)
    speed: float | None = Field(default=None, ge=0.1, le=4.0)
    seconds: float | None = Field(default=None, ge=0, le=3600)


class PredictBody(BaseModel):
    frame_index: int = Field(ge=0, le=4000)
    choice: Any = None
    ms_taken: int | None = Field(default=None, ge=0, le=3600 * 1000)


class VariablesBody(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)
    note: str | None = Field(default=None, max_length=400)
    topic_id: str | None = None


class ExplainBody(BaseModel):
    frame_index: int = Field(ge=0, le=4000)
    question: str = Field(default="", max_length=600)


class AuthorBody(BaseModel):
    request: str = Field(min_length=3, max_length=400)
    kind: str | None = Field(default=None, max_length=32)
    store: bool = False


class SpecBody(BaseModel):
    """Validate a spec without storing it -- the editor's 'check this' button."""

    spec: dict[str, Any]


def _require_run(s: Session, run_id: str, student: Student):
    run = L.run_for(s, run_id, student.id)
    if run is None:
        # 404 rather than 403: an id that is not yours and an id that does not exist must look the
        # same from outside, or the endpoint becomes a way to enumerate other learners' runs.
        raise HTTPException(status_code=404, detail={"error": "no_such_run", "next": "POST /api/v1/lessons/open first"})
    return run


def _scene_or_404(s: Session, run) -> Any:
    scene = L.scene_for_run(s, run)
    if scene is None:
        raise HTTPException(status_code=410, detail={"error": "scene_removed", "note": "the lesson this run belongs to is gone; your history stays in the run row"})
    return scene


@router.get("")
def list_lessons(
    request: Request,
    kind: str | None = None,
    student: Student = Depends(student_from_request),
    s: Session = Depends(db_session),
) -> dict[str, Any]:
    """What can be played here, with where the learner left off on each one."""
    rows = L.visible_scenes(s, student.id, kind=kind)
    return {
        "scenes": [L.scene_card(s, r, student_id=student.id) for r in rows],
        "kinds": L.kinds_table(),
        "counts": {"scenes": len(rows), "engine_kinds": len(sims.KINDS)},
        "note": (
            "every scene here was validated before it was stored: the frames are computed by the "
            "backend simulation, and a scene carries nothing to execute"
        ),
    }


@router.get("/kinds")
def kinds() -> dict[str, Any]:
    """The declared model of each scene kind -- what an editor is allowed to set, and its limits."""
    table = L.kinds_table()
    return {
        "kinds": table,
        "limits": {
            "title": scenespec.TITLE_MAX,
            "intent": scenespec.INTENT_MAX,
            "framing": scenespec.FRAMING_MAX,
            "concepts": scenespec.MAX_CONCEPTS,
            "citations": scenespec.MAX_CITATIONS,
            "narration_chars_per_frame": sims.MAX_NARRATION,
        },
    }


@router.post("/open")
def open_lesson(
    body: OpenBody,
    request: Request,
    student: Student = Depends(rate("lesson_play")),
    s: Session = Depends(db_session),
) -> dict[str, Any]:
    """Start a scene, or resume the one already open for this learner."""
    ident = body.scene_id or body.spec_id
    if not ident:
        raise HTTPException(status_code=422, detail={"error": "scene_id_or_spec_id_required"})
    opened = L.open_run(s, student_id=student.id, scene_ident=ident, session_id=body.session_id, topic_id=body.topic_id)
    if isinstance(opened, dict):
        status = 404 if "no such scene" in opened.get("reason", "") else 422
        raise HTTPException(status_code=status, detail={"error": "cannot_open_scene", **opened})
    out = opened.as_dict()
    out["ok"] = True
    return out


@router.get("/runs")
def my_runs(
    request: Request,
    limit: int = 20,
    student: Student = Depends(student_from_request),
    s: Session = Depends(db_session),
) -> dict[str, Any]:
    return {"runs": L.student_runs(s, student.id, limit=max(1, min(limit, 100)))}


@router.get("/evidence")
def lesson_evidence(
    request: Request,
    student: Student = Depends(student_from_request),
    s: Session = Depends(db_session),
) -> dict[str, Any]:
    """What the engine has measured about this learner, and what it has cost.

    The ``not_evidence`` keys are here because a number that *looks* like mastery and is not
    should say so on the same screen.
    """
    return {
        "student_id": student.id,
        "summary": L.evidence_summary(s, student.id),
        "narration_cache": L.cache_stats(s),
        "not_evidence": [
            "prediction accuracy is not fed into BKT or into a mastery number",
            "frames watched is attention proxy at best; it is not reported as understanding",
        ],
    }


@router.post("/validate")
def validate_spec(body: SpecBody, student: Student = Depends(student_from_request)) -> dict[str, Any]:
    """Run the validator on a draft without storing it. Errors are the point of the call."""
    result = scenespec.validate(body.spec)
    return {
        "ok": result.ok,
        "errors": result.errors,
        "warnings": result.warnings,
        "spec": result.spec,
        "as_dict": result.as_dict(),
    }


@router.post("/author")
async def author_lesson(
    body: AuthorBody,
    request: Request,
    student: Student = Depends(rate("lesson_author")),
    s: Session = Depends(db_session),
) -> dict[str, Any]:
    """Ask AI 2 for a scene about something. It returns a *validated* spec or the reasons.

    ``store=true`` keeps the approved result as a lesson this learner can reopen; a rejected draft
    is never stored, not even as a draft, because a half-valid scene is the thing the validator
    exists to prevent.
    """
    svcs = current()
    out = await svcs.ai2.author_scene(request=body.request, kind=body.kind, student_id=student.id)
    if out.get("ok") and body.store and out.get("draft"):
        scene = L.store_spec(s, out["draft"], origin="ai2", student_id=student.id)
        out["stored"] = L.scene_card(s, scene, student_id=student.id)
        # byte-identical content is one lesson, not two: with no model, the "draft" *is* a
        # built-in scene, and claiming to have authored it would be a lie about the row
        out["already_existed"] = scene.origin != "ai2"
        out["stored_note"] = (
            "stored as a new lesson of yours"
            if scene.origin == "ai2"
            else f"this is already stored as the {scene.origin} lesson '{scene.title}'; nothing was duplicated"
        )
    elif out.get("ok"):
        out["stored"] = None
        out["next"] = "send store=true to keep it as a lesson you can reopen"
    return out


@router.get("/run/{run_id}")
def get_run(
    run_id: str,
    request: Request,
    student: Student = Depends(student_from_request),
    s: Session = Depends(db_session),
) -> dict[str, Any]:
    """The review view: where they are, what they predicted, what actually happened."""
    run = _require_run(s, run_id, student)
    return {"ok": True, **L.review(s, run=run)}


@router.post("/run/{run_id}/progress")
def progress(
    run_id: str,
    body: ProgressBody,
    request: Request,
    student: Student = Depends(rate("lesson_play")),
    s: Session = Depends(db_session),
) -> dict[str, Any]:
    """Persist the position. Sent on pause, seek, replay and finish -- not on every frame."""
    run = _require_run(s, run_id, student)
    _scene_or_404(s, run)
    return L.save_progress(
        s,
        run=run,
        frame_index=body.frame_index,
        action=body.action,
        speed=body.speed,
        seconds=body.seconds,
    )


@router.post("/run/{run_id}/predict")
def prediction(
    run_id: str,
    body: PredictBody,
    request: Request,
    student: Student = Depends(rate("lesson_predict")),
    s: Session = Depends(db_session),
) -> dict[str, Any]:
    """'I think the next frame will...' -- checked against the simulation, not against a stored key."""
    run = _require_run(s, run_id, student)
    _scene_or_404(s, run)
    out = L.predict(s, run=run, frame_index=body.frame_index, choice=body.choice, ms_taken=body.ms_taken)
    if not out.get("ok"):
        raise HTTPException(status_code=422, detail={"error": "cannot_grade_prediction", **out})
    return out


@router.post("/run/{run_id}/variables")
def change_variables(
    run_id: str,
    body: VariablesBody,
    request: Request,
    student: Student = Depends(rate("lesson_play")),
    s: Session = Depends(db_session),
) -> dict[str, Any]:
    """Re-run the scene with new numbers. A refusal means nothing was changed."""
    run = _require_run(s, run_id, student)
    _scene_or_404(s, run)
    out = L.change_variables(s, run=run, params=body.params, note=body.note, topic_id=body.topic_id)
    if not out.get("ok"):
        # 422 with the validator's reasons: the learner typed it, so they get told what to fix
        return {"ok": False, "errors": out.get("errors") or [out.get("reason", "refused")], "warnings": out.get("warnings", []), "note": out.get("note", "the scene was not changed"), "run_id": run_id}
    return out


@router.post("/run/{run_id}/explain")
async def explain_frame(
    run_id: str,
    body: ExplainBody,
    request: Request,
    student: Student = Depends(rate("lesson_explain")),
    s: Session = Depends(db_session),
) -> dict[str, Any]:
    """Answer 'why this frame?' -- from the trace first, from the cache second, from the model last.

    A deterministic answer is preferred even when a model is configured: it cannot contradict the
    animation the learner is watching, and it is the reason a 56-frame lesson is not 56 requests.
    """
    run = _require_run(s, run_id, student)
    scene = _scene_or_404(s, run)
    first = L.explain(s, scene=scene, frame_index=body.frame_index, question=body.question)
    if not first.needs_model:
        await _note_explained(s, run, frame_index=first.frame, source=first.source, cached=first.cached)
        return {"ok": True, **first.as_dict()}
    svcs = current()
    answer = await svcs.ai3.why(
        spec=dict(scene.spec or {}),
        frame_index=body.frame_index,
        question=body.question,
        student_id=student.id,
    )
    if answer.source == "llm":
        # only a model's words are worth caching: a template answer from a moment when the key was
        # missing must not outlive that moment
        L.store_explanation(
            s,
            cache_key=first.key,
            spec_id=scene.spec_id,
            kind=scene.kind,
            frame_index=body.frame_index,
            question=body.question,
            text=answer.text,
            source="llm",
            model=answer.model,
            tokens_in=answer.tokens_in,
            tokens_out=answer.tokens_out,
        )
    await _note_explained(s, run, frame_index=body.frame_index, source=answer.source, cached=False)
    return {"ok": answer.ok, **answer.as_dict()}


@router.post("/run/{run_id}/finish")
def finish_run(
    run_id: str,
    request: Request,
    status: str = "finished",
    student: Student = Depends(rate("lesson_play")),
    s: Session = Depends(db_session),
) -> dict[str, Any]:
    out = L.finish(s, run=_require_run(s, run_id, student), status=status)
    run = _require_run(s, run_id, student)
    return {"ok": True, **out, "review": L.review(s, run=run)}


async def _note_explained(s: Session, run, *, frame_index: int, source: str, cached: bool) -> None:
    """Telemetry about the ask, so 'the learner wanted to know why' is part of the record."""
    if not run.session_id:
        return
    from ...services import telemetry as TEL

    TEL.append_event(
        s,
        session_id=run.session_id,
        student_id=run.student_id,
        type="scene_explained",
        payload={"scene_id": run.scene_id, "frame": frame_index, "source": source, "cached": cached},
    )
    s.commit()


__all__ = ["router"]
