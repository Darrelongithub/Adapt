"""AI 2's teaching surface: answers, hints, examples, resources, materials.

Note what is *not* here: no endpoint that changes a persistent strategy. "Explain it
more simply" is an in-the-moment request and is executed immediately -- the approval
gate exists for changes the app keeps making after the screen is closed, not for
asking a question. The distinction is the blueprint's, and it is the reason the demo
does not need a consent form to say "please try a diagram".
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import db_session, llm_guard, rate, require_csrf, shared_bank_write, student_from_request
from ...security.middleware import BodySizeGuard
from ...assessment import bank
from ...config import get_settings
from ...db.models import Item, Student, Topic
from ...services import attempts as A
from ...services import current

router = APIRouter(prefix="/api/v1", tags=["tutor"])

# Largest ``.numbas``/JSON package the importer will read, enforced while streaming.
PACKAGE_CAP_BYTES = 4 * 1024 * 1024


class ExplainBody(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    topic_id: str | None = None
    session_id: str | None = None
    item_id: str | None = None  # the question shown when the learner asked
    history: list[dict[str, str]] = Field(default_factory=list, max_length=8)
    spoken: bool = False


class HintBody(BaseModel):
    # ``item_id`` is optional here because the path already names the item. It is
    # accepted for clients that build one body shape for every tutor call, and a
    # mismatch between path and body is refused: that mismatch means the UI asked
    # for a hint on a question that is not the one on screen.
    item_id: str | None = None
    hints_used: int = Field(default=0, ge=0, le=8)
    session_id: str | None = None


class TeachbackBody(BaseModel):
    item_id: str | None = None
    text: str = Field(min_length=1, max_length=4000)


def _same_item(body_item_id: str | None, item_id: str) -> None:
    if body_item_id and body_item_id != item_id:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "item_mismatch",
                "path_item_id": item_id,
                "body_item_id": body_item_id,
                "note": "the item in the URL and the item in the body must be the same question",
            },
        )


class WorkedExampleBody(BaseModel):
    session_id: str | None = None
    step: int = Field(default=1, ge=1, le=50)


class PracticeBody(BaseModel):
    topic_id: str | None = None
    count: int = Field(default=4, ge=1, le=12)
    note: str | None = Field(default=None, max_length=300)


class ResourceBody(BaseModel):
    query: str = Field(min_length=2, max_length=240)
    providers: list[str] | None = None
    limit: int = Field(default=6, ge=1, le=12)


@router.post("/explain")
@router.post("/tutor/message")
async def explain(
    body: ExplainBody,
    s: Session = Depends(db_session),
    student: Student = Depends(rate("llm")),
    _guard: None = Depends(llm_guard),
) -> dict[str, Any]:
    svcs = current()
    history = [(h.get("role", "student"), h.get("text", "")) for h in body.history if isinstance(h, dict)]
    reply = await svcs.ai2.explain(
        student.id,
        body.question,
        topic_id=body.topic_id,
        session_id=body.session_id,
        history=history,
        want_spoken=body.spoken,
        # which question the learner is looking at, so the reply talks about that one
        item_id=body.item_id,
    )
    if body.session_id:
        # The metric is only real if the server records what the server showed: four
        # of AI 1's pattern detectors read ``explanation_opened`` and nothing else
        # emits it.
        from ...services import telemetry as TEL

        TEL.append_event(
            s,
            session_id=body.session_id,
            student_id=student.id,
            type="explanation_opened",
            payload={"chars": len(reply.text or "")},
            item_id=body.item_id,
            topic_id=body.topic_id,
        )
        s.commit()
    return {
        **reply.as_dict(),
        "provider_note": (
            "answer written by Gemini using your own material as the source"
            if reply.provider == "gemini"
            else "answer extracted from your course material (no model call needed)"
        ),
        "privacy": {
            "sent_to_model": bool(reply.llm.get("prompt_chars")),
            "redacted_fields": int(reply.llm.get("redacted_fields") or 0),
            "what_was_sent": "a pseudonymised summary: topic mastery, hypotheses, approved adaptations, the question text",
        },
    }


@router.get("/items/{item_id}")
def get_item(item_id: str, s: Session = Depends(db_session), student: Student = Depends(student_from_request)) -> dict[str, Any]:
    """An item *without* its answer. The private payload never leaves the server."""
    payload = A.item_payload(s, item_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="unknown item")
    from ...ai.ai2 import public_item

    rendered = bank.render_item_for(payload, seed=bank.variant_seed(student.id, item_id))
    return {**public_item(rendered), "answer_available": False}


@router.get("/items/{item_id}/reveal")
def reveal(item_id: str, s: Session = Depends(db_session), student: Student = Depends(rate("mutation"))) -> dict[str, Any]:
    """Solution + answer, after the attempt exists.

    Reading it is not recorded: there is no ``answer_revealed`` event type in the
    allowlist, and inventing one is a schema change (the client would have to send
    it, and AI 1 would have to decide what a reveal-before-trying means). Until then
    this route is honest about being a read of the private payload, not a measurement.
    """
    payload = A.item_payload(s, item_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="unknown item")
    # ``student.id`` and nothing else: a seed that falls back to None would reveal the
    # answer to a different variant than the one on the learner's screen.
    rendered = bank.render_item_for(payload, seed=bank.variant_seed(student.id, item_id))
    return {
        "steps": rendered.get("solution_steps") or [],
        "answer": (rendered.get("answer_spec") or {}).get("value"),
        "correct_index": (rendered.get("answer_spec") or {}).get("correct_index"),
        "choices": rendered.get("choices"),
        "why_it_is_ok_to_look": "you asked; nothing here is a persistent change",
    }


@router.post("/items/{item_id}/hint")
def hint(
    item_id: str,
    body: HintBody,
    s: Session = Depends(db_session),
    student: Student = Depends(rate("mutation")),
) -> dict[str, Any]:
    _same_item(body.item_id, item_id)
    payload = A.item_payload(s, item_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="unknown item")
    # Rendered with the learner's variant seed, or the hint would quote numbers from a
    # different version of the question than the one on their screen -- and it would
    # quote them as raw ``{b}`` placeholders, which is what this call used to do.
    rendered = bank.render_item_for(payload, seed=bank.variant_seed(student.id, item_id))
    out = current().ai2.hint(student.id, rendered, hints_used=body.hints_used)
    if body.session_id:
        from ...services import telemetry as TEL

        TEL.append_event(
            s,
            session_id=body.session_id,
            student_id=student.id,
            type="hint_opened",
            # "hint_no" is the field the telemetry schema defines; an unknown key
            # would be dropped on ingest and AI 1 would count a hint that was never
            # recorded.
            payload={"hint_no": body.hints_used + 1},
            item_id=item_id,
        )
        s.commit()
    return {**out, "item_id": item_id}


@router.post("/items/{item_id}/teachback")
async def teachback(
    item_id: str,
    body: TeachbackBody,
    s: Session = Depends(db_session),
    student: Student = Depends(rate("llm")),
) -> dict[str, Any]:
    _same_item(body.item_id, item_id)
    payload = A.item_payload(s, item_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="unknown item")
    # Rendered first, like every other learner-facing path: the reply quotes numbers
    # from the item back at the learner, and an unrendered payload would send ``{a}``
    # to the model -- whose answer then describes a question nobody was shown.
    rendered = bank.render_item_for(payload, seed=bank.variant_seed(student.id, item_id))
    return await current().ai2.review_teachback(student.id, rendered, body.text)


def _worked_example(s: Session, student: Student, item_id: str) -> dict[str, Any]:
    payload = A.item_payload(s, item_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="unknown item")
    rendered = bank.render_item_for(payload, seed=bank.variant_seed(student.id, item_id))
    steps = rendered.get("solution_steps") or []
    return {
        "steps": [{"index": i + 1, "text": (st.get("text") if isinstance(st, dict) else str(st)), "why": (st.get("why") if isinstance(st, dict) else None)} for i, st in enumerate(steps)],
        "item_id": item_id,
        "note": "worked examples are faded on purpose: we add steps back when you ask, and remove them when you no longer need them",
    }


@router.get("/items/{item_id}/worked-example")
def worked_example(item_id: str, s: Session = Depends(db_session), student: Student = Depends(student_from_request)) -> dict[str, Any]:
    """Read the steps without recording anything (a link, a reload, a prefetch)."""
    return _worked_example(s, student, item_id)


@router.post("/items/{item_id}/worked-example")
def worked_example_opened(
    item_id: str,
    body: WorkedExampleBody,
    s: Session = Depends(db_session),
    student: Student = Depends(rate("mutation")),
) -> dict[str, Any]:
    """The same steps, plus the event the fading decision is measured on.

    Two shapes on purpose: opening an example is the thing AI 1 counts, so the
    client calls *this* when the learner actually sees it. The GET above stays a
    plain read so a prefetch cannot look like a request for help.
    """
    out = _worked_example(s, student, item_id)
    if body.session_id:
        from ...services import telemetry as TEL

        TEL.append_event(
            s,
            session_id=body.session_id,
            student_id=student.id,
            type="example_opened",
            payload={"step": body.step},
            item_id=item_id,
        )
        s.commit()
    return out


@router.post("/practice")
async def practice(
    body: PracticeBody,
    s: Session = Depends(db_session),
    student: Student = Depends(rate("mutation")),
) -> dict[str, Any]:
    items = await current().ai2.generate_practice(student.id, topic_id=body.topic_id, count=body.count, note=body.note or "")
    return {"items": items}


@router.post("/resources/search")
async def search_resources(
    body: ResourceBody,
    s: Session = Depends(db_session),
    student: Student = Depends(rate("mutation")),
) -> dict[str, Any]:
    svcs = current()
    if svcs.resources is None:
        raise HTTPException(status_code=503, detail="resource search is not configured")
    # ``limit_per_provider``, not ``limit``: the service's parameter is the per-provider cap
    # (it ranks and de-duplicates across providers afterwards). Passing ``limit`` raised
    # TypeError straight through the route, so every resource search was a 500 -- and since
    # nothing in the app called this endpoint yet, nothing noticed.
    resp = await svcs.resources.search(body.query, limit_per_provider=body.limit, providers=body.providers)
    data = resp.as_dict() if hasattr(resp, "as_dict") else dict(resp)
    data["retrieval_note"] = (
        "These were found by a search; that is not a claim that they are correct for you. "
        "Check against your own course notes before you trust a source."
    )
    return data


@router.get("/resources/status")
def resources_status(s: Session = Depends(db_session), student: Student = Depends(student_from_request)) -> dict[str, Any]:
    svcs = current()
    return {"providers": svcs.resources.status() if svcs.resources else [], "settings": {"cache_ttl_hours": get_settings().resource_cache_ttl_hours}}


@router.get("/syllabus")
def syllabus(s: Session = Depends(db_session), student: Student = Depends(student_from_request)) -> dict[str, Any]:
    """A study plan from the curriculum graph + current mastery + review schedule."""
    graph, mastery, out = current().ai1.plan(student.id)
    titles = {t.id: t.title for t in s.execute(select(Topic)).scalars().all()}
    return {
        "path": out.get("path"),
        "gaps": out.get("gaps"),
        "remediation": out.get("remediation"),
        "retention": out.get("retention"),
        "bkt": out.get("bkt"),
        "notes": out.get("notes"),
        "topic_titles": titles,
        "how_to_read_it": (
            "Order comes from the prerequisite graph and from what is about to be forgotten, not from a "
            "chapter number. Topics you already hold are left out on purpose."
        ),
    }


@router.get("/materials")
def materials(topic_id: str | None = None, s: Session = Depends(db_session), student: Student = Depends(student_from_request)) -> dict[str, Any]:
    from ...db.models import LearningMaterial

    q = select(LearningMaterial).order_by(LearningMaterial.title)
    if topic_id:
        q = q.where(LearningMaterial.topic_id == topic_id)
    rows = s.execute(q.limit(60)).scalars().all()
    return {
        "materials": [
            {
                "id": m.id,
                "title": m.title,
                "kind": m.kind,
                "topic_id": m.topic_id,
                "word_count": m.word_count,
                "excerpt": (m.body or "")[:400],
            }
            for m in rows
        ],
        "note": "AI 2 answers from these; that is why a wrong answer here is our fault, not the model's",
    }


@router.get("/numbas/export")
async def numbas_export(
    topic_id: str | None = None,
    s: Session = Depends(db_session),
    student: Student = Depends(rate("numbas_export")),
) -> dict[str, Any]:
    """Write the bank out as a ``.numbas`` package so a human can edit the questions."""
    from ...assessment import numbas as NB

    st = get_settings()
    out_dir = st.numbas_export_dir or (st.data_dir / "numbas")
    out_dir.mkdir(parents=True, exist_ok=True)
    q = select(Item).where(Item.active.is_(True))
    if topic_id:
        q = q.where(Item.topic_id == topic_id)
    titles = {t.id: t.title for t in s.execute(select(Topic)).scalars().all()}
    items = []
    for it in s.execute(q).scalars().all():
        # Every field the importer needs to hand the row back unchanged. An export that
        # omits ``numbas_ref`` mints a new identity each time, so "export, edit, import"
        # would add a second copy of the bank instead of updating it; omitting the
        # variables would leave a parametric item quoting ``{a}`` to a learner.
        items.append(
            {
                "id": it.id,
                "topic_id": it.topic_id,
                "numbas_ref": it.numbas_ref,
                "kind": it.kind,
                "stem": it.stem,
                "stem_template": it.stem_template,
                "prompt_context": it.prompt_context,
                "visual_spec": it.visual_spec,
                "experience_mode": it.experience_mode,
                "choices": it.choices,
                "answer_spec": it.answer_spec,
                "solution_steps": it.solution_steps,
                "hints": it.hints,
                "rubric": it.rubric,
                "difficulty": it.difficulty,
                "est_seconds": it.est_seconds,
                "tags": it.tags,
                # The report below tells the operator what the learner can actually be
                # asked; an inactive row (an ungradeable part type, an unrendered
                # template) belongs on the "needs the runtime" side of that line.
                "active": it.active,
            }
        )
    exam, report = NB.exam_export(items, title="ADAPT adaptive assessment", topic_titles=titles)
    path = NB.write_numbas_zip(out_dir / "adapt_bank.numbas", exam, report=report)
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "items": len(items),
        "report": report.as_dict(),
        "runnable": NB.supported_subset(items),
        "note": "edit in the Numbas editor, then POST /numbas/import to bring the changes back into the item bank",
    }


@router.post("/numbas/import")
async def numbas_import(
    request: Request,
    s: Session = Depends(db_session),
    student: Student = Depends(require_csrf),
    _bank: None = Depends(shared_bank_write),
) -> dict[str, Any]:
    """Accept a Numbas exam/question export (JSON) and upsert it into the bank.

    Imported items are tagged ``source="numbas"`` and keyed by a checksum of the
    question, so re-importing the same document does not create duplicates -- which
    matters because a teacher edits, exports, imports, edits again.
    """
    import json
    import tempfile

    from ...assessment import numbas as NB
    from ...db.models import Topic as _T

    # Capped while it streams (see security.middleware.BodySizeGuard): checking the
    # length after ``await request.body()`` means a chunked upload -- one whose
    # Content-Length the middleware cannot see -- has to be allowed into RAM first.
    try:
        raw = await BodySizeGuard(PACKAGE_CAP_BYTES).read(request)
    except ValueError as exc:  # the guard's own refusal, in the API's language
        raise HTTPException(status_code=413, detail=f"package too large (max {PACKAGE_CAP_BYTES} bytes)") from exc
    data: Any
    tmp: str | None = None
    try:
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            # a .numbas upload is a zip: hand it to the reader by path
            with tempfile.NamedTemporaryFile(suffix=".numbas", delete=False) as fh:
                fh.write(raw)
                tmp = fh.name
            data = tmp
        try:
            items, report = NB.import_exam(data)
        except Exception as exc:
            # BadZipFile, a missing key, an unexpected encoding -- all of them mean the
            # same thing to a caller: the thing you uploaded is not a package this build
            # can read. Naming the exception class is enough; the message can quote the
            # bytes we were handed, and a 500 for bad input is not the API's fault.
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "unreadable_package",
                    "note": f"not a .numbas package this build can read ({type(exc).__name__})",
                    "next": "GET /api/v1/numbas/export, edit the questions, POST that file back",
                },
            ) from exc
    finally:
        # An uploaded package is somebody's question bank. It must not sit readable in
        # /tmp forever because a validation error happened halfway through.
        if tmp is not None:
            import os

            try:
                os.unlink(tmp)
            except OSError:  # pragma: no cover - already gone
                pass
    topics = list(s.execute(select(_T)).scalars().all())
    topic_ids = {t.id for t in topics}
    topic_by_code = {t.code: t.id for t in topics}
    known_codes = ", ".join(sorted(topic_by_code)[:20])
    stored, updated = 0, 0
    for payload in items:
        # A question has to land on a topic that exists. Our own export writes the topic
        # *id* in ``topic_id`` and in ``adapt-topic:<id>``; a hand-edited or foreign
        # package more often names the human-readable *code*, which used to be inserted
        # as if it were an id and blew up on the foreign key -- a 500 for bad input.
        # An item we cannot place is skipped with its name, never attached to whichever
        # topic happened to be first (that would corrupt an unrelated topic's evidence).
        ref_hint = str(payload.get("topic_id") or "").strip()
        tag = str((payload.get("tags") or [""])[0] or "")
        tag_ref = tag.split(":", 1)[1].strip() if tag.startswith("adapt-topic:") else ""
        topic_id = (
            ref_hint if ref_hint in topic_ids else topic_by_code.get(ref_hint) or topic_by_code.get(tag_ref)
        )
        if topic_id is None:
            named = ref_hint or tag_ref or "no topic given"
            report.warnings.append(
                f"{payload.get('stem', '')[:40] or 'item'}: skipped -- {named!r} is not a topic here. "
                f"Tag the question ``adapt-topic:<topic code>``, one of: {known_codes}"
            )
            continue
        # Identity first. Our own export says which row the question came from
        # (``exported_from_item_id``) -- content is exactly what an editor changes, so
        # matching on the checksum would create a second item per edit. A foreign
        # package has no such hint, so it falls back to the ref and then the checksum.
        ref = str(payload.get("numbas_ref") or "") or NB.checksum(payload)
        origin = str(payload.get("exported_from_item_id") or "") or None
        existing = None
        if origin:
            existing = s.get(Item, origin)
            if existing is not None:
                payload = {k: v for k, v in payload.items() if k != "exported_from_item_id"}
        if existing is None:  # ``first()`` not ``scalar_one_or_none()``: a bank that
            # already holds duplicates -- which the previous version of this route
            # created -- must not answer a re-import with MultipleResultsFound.
            existing = s.execute(select(Item).where(Item.numbas_ref == ref)).scalars().first()
        fields = {
            "kind": payload.get("kind", "text"),
            "stem": payload.get("stem", ""),
            "stem_template": payload.get("stem_template"),
            "experience_mode": payload.get("experience_mode", "practice"),
            "prompt_context": payload.get("prompt_context"),
            "choices": payload.get("choices"),
            "answer_spec": payload.get("answer_spec") or {},
            "solution_steps": payload.get("solution_steps"),
            "hints": payload.get("hints"),
            "rubric": payload.get("rubric"),
            "difficulty": float(payload.get("difficulty", 0.5)),
            "est_seconds": int(payload.get("est_seconds", 90)),
            "tags": payload.get("tags") or [],
            # A part type this build cannot grade is imported for the operator to look
            # at, not handed to a learner: every answer to it would be marked wrong,
            # which would corrupt mastery, spacing and the item's own calibration.
            "active": not payload.get("needs_numbas_runtime"),
        }
        clean, dirty = NB.renders_clean({"stem_template": payload.get("stem_template"), **fields})
        if not clean:
            # Never serve a template. The item is stored for the operator to fix, but it
            # does not become a question anybody is asked.
            fields["active"] = False
            report.warnings.append(
                f"{payload.get('stem', '')[:40] or ref}: left inactive -- unrendered field(s) {dirty} still contain "
                "{placeholders} and the item carries no variables to fill them"
            )
        if existing is None:
            s.add(Item(topic_id=topic_id, numbas_ref=ref, source="numbas", **fields))
            stored += 1
        else:
            for k, v in fields.items():
                setattr(existing, k, v)
            updated += 1
    s.commit()
    return {"imported": stored, "updated": updated, "report": report.as_dict()}
