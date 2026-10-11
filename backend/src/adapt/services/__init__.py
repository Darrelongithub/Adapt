"""Application services: the wiring between the two AIs, the store, and the API.

Architectural rule the blueprint asks for and this module makes concrete: **AI 1 and
AI 2 do not talk to each other.** They share the SQLite database and nothing else.
AI 1 writes traces, evidence, hypotheses and proposals; AI 2 reads the profile plus
the *approved* adaptations table. A message bus between them would let one AI's
transient reasoning leak into the other's teaching, and would make it impossible to
answer "what exactly did AI 2 know when it decided this?" -- the DB row can.

:func:`build` returns one :class:`AppServices` object that owns the engine, the two
AIs, the resource service, the rate limiter and the voice backends, so the FastAPI
app, the CLI and the tests all construct the system the same way.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from sqlalchemy.orm import Session

from ..ai.ai1 import LearningIntelligence
from ..ai.ai2 import EducationalIntelligence
from ..ai.llm import GeminiLLM
from ..config import Settings, get_settings
from ..db.engine import Database, get_db, set_db
from ..resources.service import ResourceService
from ..security.ratelimit import build_limiter
from ..voice import pipeline as VP
from ..voice import stt as STT
from ..voice import tts as TTS

log = logging.getLogger("adapt.services")


@dataclass
class AppServices:
    settings: Settings
    db: Database
    ai1: LearningIntelligence
    ai2: EducationalIntelligence
    resources: ResourceService | None
    limiter: Any
    _reply_fn: Callable[..., Any] | None = field(default=None, repr=False)

    # -- sessions ---------------------------------------------------------- #
    def session(self) -> Session:
        """A raw ORM session. The caller owns it: close it, or use :meth:`scope`."""
        return self.db.sessionmaker()

    def scope(self) -> Iterator[Session]:
        """``with services.scope() as s:`` -- commits on success, rolls back on error."""
        return self.db.session()

    # -- voice ------------------------------------------------------------- #
    def make_conversation(
        self,
        student_id: str,
        *,
        send: Callable[[dict[str, Any]], Any],
        send_audio: Callable[[bytes], Any] | None = None,
        gender: str = "female",
        session_id: str | None = None,
    ) -> VP.VoiceConversation:
        """Build one live voice conversation for a websocket.

        The engines are chosen by capability, not by wishful configuration: if
        faster-whisper's model or torch is missing, STT degrades to accepting
        browser dictation; if Kokoro cannot load, TTS degrades to a ``speak_unavailable``
        message so the browser can use its own speechSynthesis. The learner keeps a
        working conversation either way, and ``status()`` says which engine answered.
        """
        from ..voice.vad import build_vad

        return VP.VoiceConversation(
            settings=self.settings,
            vad=build_vad(self.settings),
            stt=STT.get_stt(self.settings),
            tts=TTS.get_tts(self.settings),
            reply=self.reply_for(student_id, session_id=session_id),
            send=send,
            send_audio=send_audio,
            session_id=session_id or f"voice_{student_id[-8:]}",
            gender=gender if gender in ("male", "female") else "female",
        )

    def reply_for(self, student_id: str, *, session_id: str | None = None) -> Callable[..., Any]:
        async def _reply(text: str, ctx: dict[str, Any]) -> tuple[str, dict[str, Any]]:
            return await self.ai2.turn(text, student_id=student_id, session_id=session_id)

        return _reply

    def record_voice_turn(self, *, student_id: str, ws_session: str | None, direction: str, **kw: Any) -> None:
        """Voice metrics only -- transcript text is written by AI 2's own recording."""
        from ..db.models import LearningSession, VoiceTurn

        with self.db.session() as s:
            s.add(VoiceTurn(student_id=student_id, ws_session=ws_session, direction=direction, **kw))
            if ws_session:
                sess = s.get(LearningSession, ws_session)
                if sess is not None:
                    sess.voice_turns = (sess.voice_turns or 0) + 1
            s.commit()

    # -- status ------------------------------------------------------------ #
    def status(self) -> dict[str, Any]:
        st = {
            "mode": self.settings.mode,
            "database": {
                "path": str(self.settings.db_file),
                "file_encrypted": self.db.file_encrypted(),
                "plaintext_header": self.db.header_is_plaintext(),
                "driver": self.db.driver.as_dict() if getattr(self.db, "driver", None) else None,
            },
            "llm": (
                self.ai1.llm.available()
                if getattr(self.ai1, "llm", None) is not None
                else {
                    "configured": False,
                    "provider": "none",
                    "note": "no API key: AI 1 phrases hypotheses from templates, AI 2 teaches from your materials",
                }
            ),
            "resources": ({"providers": self.resources.status()} if self.resources else {"providers": []}),
            "voice": {
                "stt": STT.capabilities(self.settings),
                "tts": TTS.capabilities(self.settings),
            },
            "rate_limits": self.limiter.snapshot() if self.limiter else None,
        }
        return st

    def quotas(self) -> dict[str, Any]:
        """Today's Gemini spending vs. the free-tier budget, from the audit table."""
        from sqlalchemy import func, select

        from ..db.models import LlmCall

        day = utcnow_day()
        with self.db.session() as s:
            rows = s.execute(
                select(LlmCall.purpose, LlmCall.status, func.count(), func.sum(LlmCall.tokens_in + LlmCall.tokens_out))
                .where(LlmCall.day == day)
                .group_by(LlmCall.purpose, LlmCall.status)
            ).all()
        by_purpose: dict[str, dict[str, Any]] = {}
        total_calls = 0
        total_tokens = 0
        for purpose, status, n, tok in rows:
            entry = by_purpose.setdefault(purpose or "?", {"calls": 0, "tokens": 0, "by_status": {}})
            entry["calls"] += int(n or 0)
            entry["tokens"] += int(tok or 0)
            entry["by_status"][status or "?"] = entry["by_status"].get(status or "?", 0) + int(n or 0)
            total_calls += int(n or 0)
            total_tokens += int(tok or 0)
        return {
            "day": day,
            "budget": {
                "calls": self.settings.gemini_daily_call_budget,
                "tokens": self.settings.gemini_daily_token_budget,
                "models": [self.settings.gemini_model_ai1, self.settings.gemini_model_ai2],
            },
            "used": {"calls": total_calls, "tokens": total_tokens},
            "by_purpose": by_purpose,
            "calls_left": max(0, self.settings.gemini_daily_call_budget - total_calls),
        }

    async def aclose(self) -> None:
        if self.resources is not None:
            await self.resources.aclose()
        if getattr(self.ai1, "llm", None) is not None:
            closer = getattr(self.ai1.llm, "aclose", None)
            if closer is not None:
                await closer()
        self.db.dispose()


def utcnow_day() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).date().isoformat()


def build(
    settings: Settings | None = None,
    *,
    db: Database | None = None,
    llm: Any = None,
    with_resources: bool = True,
    limiter: Any = None,
) -> AppServices:
    """Construct the whole backend. Safe to call repeatedly: the DB is shared.

    ``limiter`` is passed by ``create_app`` so that the per-request middleware and the
    services throttle through the *same* bucket table. Built separately they each kept
    their own counters, which made ``/api/v1/quotas`` report an ``active_buckets`` count
    that had nothing to do with the buckets actually refusing requests.
    """
    settings = settings or get_settings()
    database = db or get_db()
    limiter = limiter if limiter is not None else build_limiter(multiplier=settings.rate_limit_multiplier)
    model = llm
    if model is None and settings.gemini_api_key:
        model = GeminiLLM(settings, limiter=limiter)
    elif model is None and settings.allow_local_fallback:
        model = None  # AI 2 will use the local tutor; AI 1 will phrase hypotheses locally
    ai1 = LearningIntelligence(database.session, settings=settings, llm=model, limiter=limiter)
    resources: ResourceService | None = None
    if with_resources:
        resources = ResourceService(settings=settings, session_factory=database.session, limiter=limiter)
    ai2 = EducationalIntelligence(database.session, ai1=ai1, settings=settings, llm=model, resources=resources)
    return AppServices(settings=settings, db=database, ai1=ai1, ai2=ai2, resources=resources, limiter=limiter)


def attach(services: AppServices) -> None:
    """Publish the constructed services as the process-wide handle the API uses."""
    global _CURRENT
    _CURRENT = services
    set_db(services.db)


_CURRENT: AppServices | None = None


def current() -> AppServices:
    global _CURRENT
    if _CURRENT is None:
        _CURRENT = build()
        set_db(_CURRENT.db)
    return _CURRENT


__all__ = ["AppServices", "attach", "build", "current", "utcnow_day"]
