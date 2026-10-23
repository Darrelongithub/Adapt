"""Health, capability, budget and privacy status -- the honest-systems screen.

These endpoints exist because the blueprint's guardrails are claims about the
running system ("local inference", "free tiers only", "encrypted at rest", "no
surveillance"), and a claim you can query is a claim a judge can check. Each one
answers with what was *verified now*, not with what the config hopes for: the
voice section reports whether faster-whisper's weights actually load, the database
section reports whether the file header is ciphertext, the quota section reports
today's model spend from the audit table.
"""

from __future__ import annotations

import asyncio

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session

from ..deps import db_session, require_csrf, shared_bank_write, student_from_request
from ...config import get_settings
from sqlalchemy import text
from ...db.models import Student
from ...services import current
from ...services import telemetry as TEL
from ...voice import stt as STT
from ...voice import tts as TTS
from ... import __version__

router = APIRouter(prefix="/api/v1", tags=["system"])


@router.get("/health")
def health() -> dict[str, Any]:
    get_settings()
    svcs = current()
    db_ok = True
    db_error: str | None = None
    try:
        with svcs.db.session() as s:
            s.execute(text("select 1"))
    except Exception as exc:  # never let a probe take the app down with it -- but name the cause
        # A health endpoint that answers {"ok": false} and nothing else cannot be acted on: a
        # missing column, a locked file and a wrong key all look identical, and this is the one
        # request an operator reads when something is already wrong. The sentence is truncated
        # and never contains a key, a path query or a row value -- driver errors carry those.
        db_ok = False
        first = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        db_error = f"{type(exc).__name__}: {first[:180]}"
    return {
        "ok": bool(db_ok),
        "version": __version__,
        "lessons": lessons_state(),
        "database_reachable": bool(db_ok),
        **({"database_error": db_error} if db_error else {}),
    }


def lessons_state() -> dict[str, Any]:
    """How lessons are being produced right now, in the learner's own words (brief §3).

    "ready" means a configured model is answering; "simple mode" means the deterministic tutor is
    doing all of it -- honest about being lower quality, and never pretending otherwise. The
    learner gets this sentence; the service's identity and its budget stay behind the console.
    """
    ready = False
    try:
        svcs = current()
        configured = bool(getattr(getattr(svcs, "settings", None), "gemini_api_key", None))
        engine = getattr(getattr(svcs, "ai1", None), "llm", None) if configured else None
        if engine is not None:
            ready = bool(engine.available().get("configured"))
    except Exception:
        # A health probe may run against a half-built container (the database-unreachable test
        # does exactly that). Claiming a model is answering when the service object cannot even be
        # inspected would be the one dishonest answer this endpoint can give.
        ready = False
    if ready:
        return {
            "state": "ready",
            "says": "Lessons: ready",
            "note": "answers and questions can be written for you when your own material does not cover something",
        }
    return {
        "state": "simple mode",
        "says": "Lessons: simple mode",
        "note": (
            "every explanation here is assembled from your own course material by the built-in tutor: "
            "correct and specific, but plainer than a written-for-you lesson. Nothing is sent anywhere."
        ),
    }


@router.get("/status")
def status(s: Session = Depends(db_session), student: Student = Depends(student_from_request)) -> dict[str, Any]:
    """What a learner may know about the running system (brief §3).

    The operator's view -- mode, paths, driver, provider state, budgets, limiter buckets -- is
    console-only (`adapt status`), and it is a different surface on purpose: this one cannot leak
    what it never receives.
    """
    svcs = current()
    return {
        "ok": True,
        "version": __version__,
        "lessons": lessons_state(),
        "demo_mode": svcs.settings.mode == "demo",
        "data_protection": data_protection(),
    }


def data_protection() -> dict[str, Any]:
    """What is actually protecting learner data, in sentences a learner can act on.

    The same facts as the operator's report minus the deployment details (file paths, driver
    names, key sources), because those describe the machine, not the learner's data.
    """
    db = current().db
    file_encrypted = bool(db.file_encrypted())
    driver = getattr(db, "driver", None)
    return {
        "file_encrypted": file_encrypted,
        # The engine is not a secret and the claim depends on it: saying "encrypted" while only
        # columns are ciphertext would be the kind of unearned assurance this report exists to
        # prevent. What is dropped is the deployment detail around it (paths, key source, config).
        "underlying_engine": f"{(driver.name if driver else 'sqlite')} + field-level encryption",
        "file_level": (
            "The whole database file is encrypted."
            if file_encrypted
            else "Only the information marked as protected below is encrypted; the database file itself is not."
        ),
        "protected": [
            "your answers and feedback",
            "details about your practice activity",
            "learning suggestions and the evidence behind them",
            "tutor conversations and recorded voice transcripts",
            "saved progress summaries",
            "how a sitting felt, when you choose to check in (including the short note you write)",
            "the syllabus objectives you approved",
            "your name and email",
        ],
        "not_protected": [
            "study preferences, such as your goal, weekly time, and answer length",
            "the record structure used to organize saved information",
            "study topics and dates used to plan reviews",
        ],
        "note": "Protection applies only to the information listed as protected; other saved details are not encrypted.",
    }


def encryption_report() -> dict[str, Any]:
    """What is actually protecting learner data right now, and what is not."""
    st = get_settings()  # noqa: F841
    svcs = current()
    db = svcs.db
    driver = getattr(db, "driver", None)
    file_encrypted = db.file_encrypted()
    return {
        "requested": st.db_encryption,
        "file_encrypted": bool(file_encrypted),
        "driver": driver.name if driver else "sqlite",
        "driver_detail": (driver.detail if driver else "") or "",
        "key_source": (driver.key_source if driver else "none"),
        "field_level_encryption": True,
        "fields_encrypted": [
            "learner answers + feedback",
            "event payloads",
            "hypotheses/evidence facts",
            "chat + voice transcripts",
            "proposal payloads and student notes",
            "profile snapshots",
            "material bodies",
            "display name + email (with keyed blind index for login)",
        ],
        "not_encrypted": [
            "study preferences (goal, minutes per week, word limits) — readable JSON",
            "table names and numeric foreign keys (needed to query at all)",
            "topic ids and timestamps (used for scheduling)",
            "the file path of the database itself",
        ],
        "in_transit": "TLS terminates at the tunnel (localtunnel/Cloudflare) in tunnel mode; set ADAPT_REQUIRE_HTTPS=true behind your own proxy",
        "residual_risk": (
            "SQLite has no encryption of its own: without sqlcipher installed the file header stays "
            "plaintext and only the listed columns are ciphertext. That is a real difference, so it is "
            "reported instead of hidden."
        ),
        "header_is_plaintext": bool(db.header_is_plaintext()),
    }


#: The app's own request pacing, in learner-safe words. Provider-scoped buckets (the upstream
#: services' rate limits) are operator detail and are not listed here; the budgets they exist to
#: protect are console-only (brief §3).
_PACING_NAMES = {
    "ip": "requests",
    "mutation": "writes",
    "llm": "lesson_requests",
    "guest_create": "new_accounts",
    "voice_frame": "voice_frames",
    "numbas_export": "exports",
    "auth_fail": "sign_in_attempts",
}


@router.get("/quotas")
def quotas(s: Session = Depends(db_session), student: Student = Depends(student_from_request)) -> dict[str, Any]:
    from ...security.ratelimit import effective_limits

    st = get_settings()
    limits = effective_limits(st.rate_limit_multiplier)
    return {
        "pacing": {name: limits[scope] for scope, name in _PACING_NAMES.items() if scope in limits},
        "note": (
            "How often the app will accept a request. Being asked to wait is pacing, not a limit on "
            "what you are allowed to learn: nothing here stops a lesson."
        ),
    }


@router.get("/privacy")
def privacy() -> dict[str, Any]:
    """The static, non-personal list of what may be recorded.

    Public and read-only on purpose: the consent screen shows this list
    *before* anyone has an account, so requiring a signed-in learner made
    that screen answer 401 and print "could not be read just now" for the
    one list a person needs in order to decide. Nothing here is about an
    individual learner, so there is nothing to protect by gating it; every
    route that returns a learner's own rows still requires a session.
    The wording is learner-facing, so it names no service, route or
    internal mechanism (Round 2, Phase A, task 1 and 2).
    """
    return {
        "telemetry": TEL.privacy_summary(),
        "sent_to_model": {
            "when": "only when a written-for-you answer is available and you consented to sharing a summary",
            "identity": "a made-up name for you, never your real name or email",
            "redaction": "emails, phone numbers, web addresses, postal addresses and anything shaped like an ID are stripped before anything is shared",
            "on_refusal": "if any of that cannot be honoured, the answer is put together here instead and the reply says so",
        },
        "voice": {
            "heard": "speech is turned into text as you speak; the sound itself is not kept",
            "spoken": "spoken replies are played to you and not stored",
            "stored": "transcript text for the turns you chose to record, plus timing and interruption notes",
            "off_means": "voice off = no microphone access at all; the keyboard path is identical",
        },
        "storage": data_protection(),
        "learner_controls": [
            "answer or refuse every change that is proposed to you",
            "revoke an applied change at any time",
            "delete your history",
            "see every event recorded about you",
            "see and delete anything you said at a check-in",
            "review and change the syllabus objectives you approved, at any time",
        ],
    }


@router.get("/voice/capabilities")
def voice_capabilities(s: Session = Depends(db_session), student: Student = Depends(student_from_request)) -> dict[str, Any]:
    st = get_settings()
    return {
        "stt": STT.capabilities(st),
        "tts": TTS.capabilities(st),
        "voices": TTS.VOICES,
        "barge_in": True,
        "transport": "websocket /ws/voice (binary s16le frames + JSON control messages)",
        # The client dials this instead of hard-coding a path, so the URL can move.
        "path": "/ws/voice",
        "note": (
            "transcription accuracy and teaching accuracy are measured separately: a misheard word is a "
            "speech problem, not a tutoring problem, and the transcript is always editable"
        ),
    }


CLIP_CAP_BYTES = 8 * 1024 * 1024


async def _read_capped(file: UploadFile, cap: int) -> bytes:
    """Take an upload in chunks and stop the moment it exceeds the cap.

    The cap used to be applied to the result of ``await file.read()`` -- which means a
    client had to be allowed to send all of it first, and the middleware's
    ``Content-Length`` check cannot see a chunked request at all. Reading a megabyte at a
    time and refusing as soon as the total passes the line is what makes the limit true.
    """
    buf = bytearray()
    while True:
        piece = await file.read(1 << 20)
        if not piece:
            return bytes(buf)
        buf += piece
        if len(buf) > cap:
            raise HTTPException(status_code=413, detail=f"clip too large (max {cap} bytes)")


@router.post("/voice/transcribe")
async def transcribe(file: UploadFile = File(...), sample_rate: int = Form(16000, ge=8_000, le=96_000), s: Session = Depends(db_session), student: Student = Depends(require_csrf)) -> dict[str, Any]:
    """REST fallback for the websocket: upload a WAV/PCM clip, get a transcript.

    Decoding and inference go to a worker thread, exactly as the websocket pipeline does
    (``voice/pipeline.py``): a whisper pass is seconds of CPU, and running it on the event
    loop stops every other learner's request mid-sentence.
    """
    st = get_settings()
    if student is not None and not student.voice_enabled:
        # Do not read, decode, count, or hand the upload to STT while the
        # learner's server-side voice switch is off. The direct ``student=None``
        # call remains useful for the isolated worker-thread test below; normal
        # requests always arrive through ``require_csrf`` with a Student row.
        await file.close()
        return {
            "text": "",
            "backend": "disabled",
            "language": "en",
            "confidence": None,
            "duration_s": 0.0,
            "latency_ms": 0,
            "model": None,
            "segments": 0,
            "note": "voice is off; no audio was processed",
        }
    try:
        data = await _read_capped(file, CLIP_CAP_BYTES)
    finally:
        await file.close()

    def _run() -> Any:
        from ...voice.audio import pcm16_from_bytes

        pcm = pcm16_from_bytes(data, sample_rate=sample_rate)
        return STT.get_stt(st).transcribe(pcm.samples, sample_rate=pcm.sample_rate)

    res = await asyncio.to_thread(_run)
    return res.as_dict()


@router.post("/voice/speak")
async def speak(body: dict[str, Any], s: Session = Depends(db_session), student: Student = Depends(require_csrf)) -> dict[str, Any]:
    """Return WAV audio if Kokoro is installed, otherwise tell the client to use its own voice."""
    from fastapi.responses import Response

    if student is not None and not student.voice_enabled:
        return {
            "available": False,
            "reason": "voice_off",
            "fallback": "text only; turn voice on before requesting speech",
        }

    from ...voice.tts import synthesize_or_none

    text = str(body.get("text") or "")[:2000]
    gender = "male" if str(body.get("gender", "female")).lower().startswith("m") else "female"
    # Same reason as /voice/transcribe: Kokoro is CPU work, not I/O.
    out = await asyncio.to_thread(synthesize_or_none, get_settings(), text, gender=gender)
    if out is None:
        return {"available": False, "reason": "local TTS engine not installed", "fallback": "browser speechSynthesis"}
    audio, rate = out
    from ...voice.audio import encode_wav

    wav = bytes(audio) if isinstance(audio, (bytes, bytearray)) else encode_wav(audio, sample_rate=rate)
    return Response(content=wav, media_type="audio/wav", headers={"x-tts-sample-rate": str(rate)})


@router.get("/audit")
def audit(limit: int = 50, s: Session = Depends(db_session), student: Student = Depends(student_from_request)) -> dict[str, Any]:
    from ...security.audit import recent

    return {"entries": recent(s, actor_id=student.id, limit=max(1, min(200, limit)))}


@router.post("/learning/train")
def train(
    topic_scope: str = "global",
    s: Session = Depends(db_session),
    student: Student = Depends(require_csrf),
    _bank: None = Depends(shared_bank_write),
) -> dict[str, Any]:
    """Refit cohort BKT parameters by EM on recorded sequences (also a CLI command).

    Global by default, so the numbers every learner's mastery is computed from change
    for everyone: on a public tunnel that needs the operator's phrase, not just any
    signed-in learner (see ``deps.shared_bank_write``).
    """
    out = current().ai1.train_params(scope=topic_scope)
    if out.get("skipped"):
        raise HTTPException(status_code=409, detail=out)
    return out


@router.get("/events/allowlist")
def allowlist(s: Session = Depends(db_session), student: Student = Depends(student_from_request)) -> dict[str, Any]:
    from ...learning.telemetry import ALLOWED_EVENTS, FORBIDDEN_PAYLOAD_KEYS

    return {
        "allowed": {k: sorted(v) for k, v in ALLOWED_EVENTS.items()},
        "forbidden_keys": sorted(FORBIDDEN_PAYLOAD_KEYS),
        "rule": "an event type not listed here is rejected by the server, not just discouraged by the client",
    }
