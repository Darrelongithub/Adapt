"""Scene narrator — the learner's voice beside the simulation.

**Not AI 3.** This module answers "why did that happen?" about a simulation frame: it narrates a
scene. It was called ``ai3`` until the functionality round; the real AI 3 is the *adaptation layer*
(plans, deliveries, teaching memory — see ``adapt.state.orchestrator``), and a codebase where "AI 3"
means two different things is a codebase where a learner-facing sentence gets attributed to the
wrong component. ``tests/test_state_foundation.py`` asserts the old module name is gone.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from ..learning import scenespec, sims
from ..security.redact import redact_payload
from . import static_context as SC

log = logging.getLogger("adapt.ai3")

#: the most a frame's answer may run to. Longer than one screen of narration, shorter than an essay
MAX_ANSWER = 900
#: how many frames around the question go into the prompt
CONTEXT_FRAMES = 2
#: a spec the model is asked to write may not exceed these, and the validator re-checks them
DRAFT_KINDS = sims.KINDS


@dataclass
class Answer:
    text: str
    source: str  # simulation|cache|llm|template
    frame: int
    citations: list[dict[str, str]] = field(default_factory=list)
    model: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    ok: bool = True
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        # `text`, not `answer`, and the same source words `services/lessons.Explanation` uses.
        # The endpoint answers a "why?" from two code paths, and a client must not have to read
        # both to know which key holds the sentence. `answer` stays as an alias.
        return {
            "text": self.text,
            "answer": self.text,
            "source": {"simulation": "deterministic"}.get(self.source, self.source),
            "frame": self.frame,
            "citations": self.citations,
            "model": self.model,
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "ok": self.ok,
            "note": self.note,
        }


class SceneNarrator:
    """The narration half of the teaching loop.

    ``llm`` is the *same* client the tutor uses, so one quota guard covers both voices; ``sf`` is a
    session factory used for the LLM-call audit row, which is how "the scene narrator cost 2 calls today" is
    answerable without log files.
    """

    def __init__(self, *, llm: Any = None, sf: Callable[[], Any] | None = None, settings: Any = None) -> None:
        self.llm = llm
        self._sf = sf
        self.settings = settings

    # -- availability ------------------------------------------------------- #
    def status(self) -> dict[str, Any]:
        llm_ok = False
        info: dict[str, Any] = {}
        if self.llm is not None:
            try:
                info = dict(self.llm.available() if callable(getattr(self.llm, "available", None)) else {})
                llm_ok = bool(info.get("configured"))
            except Exception as exc:  # a broken status probe must not break the lesson
                info = {"error": str(exc)[:160]}
        return {
            # ``role`` is the audit label written into ``llm_calls`` rows and is kept for
            # continuity with calls already recorded; ``component`` is the name a human reads.
            "role": "ai3",
            "component": "scene narrator",
            "model_configured": llm_ok,
            "llm": info,
            "answers_from_simulation": sorted(DRAFT_KINDS),
            "note": (
                "most asks are answered from the trace with no model call at all; "
                + ("the model is available for the rest" if llm_ok else "no model configured, so the simulation's own answer is the answer")
            ),
        }

    # -- why ---------------------------------------------------------------- #
    async def why(
        self,
        *,
        spec: dict[str, Any],
        frame_index: int,
        question: str,
        student_id: str | None = None,
        deterministic: str | None = None,
    ) -> Answer:
        """Answer 'why this frame?' about one simulation.

        ``deterministic`` is what the simulation already knew (the caller checks the cache and the
        sim's own ``explain`` first, because those are not this class's business to repeat).
        """
        citations = [dict(c) for c in (spec.get("citations") or []) if isinstance(c, dict)]
        if deterministic:
            return Answer(text=deterministic, source="simulation", frame=int(frame_index), citations=citations)
        if self.llm is None or not getattr(self.llm, "configured", True):
            return Answer(
                text=_template_answer(spec, frame_index, question),
                source="template",
                frame=int(frame_index),
                citations=citations,
                ok=False,
                note="a written-for-you explanation is not available, so this is the simulation's own wording rather than an explanation of it",
            )
        prompt = self._prompt(spec, frame_index, question)
        safe, dropped, _kinds = redact_payload({"q": prompt}, max_chars=7000)  # _audit records the count, not the kinds
        try:
            res = await self.llm.complete(
                system="",
                user=safe["q"],
                cache_prefix=SC.block("narrator"),
                purpose="ai3_narrate",  # audit label: see ``status`` for why this keeps the old name
                role="ai3",
                student_id=student_id or "anon",
                temperature=0.2,
                max_output_tokens=320,
                response_spec={"answer": "str", "why_not_more": "str", "frame": "int"},
            )
        except Exception as exc:  # a model that throws must not take the lesson down with it
            log.info("ai3 call failed: %s", exc)
            return Answer(
                text=_template_answer(spec, frame_index, question),
                source="template",
                frame=int(frame_index),
                citations=citations,
                ok=False,
                note=f"the model could not be reached ({type(exc).__name__}); the frame still plays",
            )
        await self._audit(res, student_id, len(prompt), dropped)
        text = ""
        frame_used = int(frame_index)
        if res.ok and isinstance(res.data, dict):
            text = str(res.data.get("answer") or "").strip()
            try:
                frame_used = int(res.data.get("frame", frame_index))
            except (TypeError, ValueError):
                frame_used = int(frame_index)
        elif res.ok:
            text = (res.text or "").strip()
        if not text:
            return Answer(
                text=_template_answer(spec, frame_index, question),
                source="template",
                frame=int(frame_index),
                citations=citations,
                ok=False,
                note=f"the model returned {res.status or 'nothing'}; the frame still plays",
            )
        text = _clean(text)
        if not text:
            return Answer(
                text=_template_answer(spec, frame_index, question),
                source="template",
                frame=int(frame_index),
                citations=citations,
                ok=False,
                note="the model's answer was refused by the plain text check",
            )
        return Answer(
            text=text,
            source="llm",
            frame=frame_used,
            citations=citations,
            model=getattr(res, "model", None),
            tokens_in=int(getattr(res, "tokens_in", 0) or 0),
            tokens_out=int(getattr(res, "tokens_out", 0) or 0),
        )

    def _prompt(self, spec: dict[str, Any], frame_index: int, question: str) -> str:
        kind = str(spec.get("kind") or "")
        frames: list[dict[str, Any]] = []
        try:
            frames = sims.frames_for(kind, spec.get("params") or {})
        except Exception as exc:
            log.info("ai3 could not replay %s: %s", kind, exc)
        index = max(0, min(int(frame_index), max(0, len(frames) - 1)))
        window = frames[max(0, index - CONTEXT_FRAMES) : index + CONTEXT_FRAMES + 1]
        payload = {
            "scene": {"kind": kind, "title": spec.get("title"), "intent": spec.get("intent"), "concepts": spec.get("concepts") or []},
            "parameters": {k: v for k, v in (spec.get("params") or {}).items() if k != "values" or not isinstance(v, list) or len(v) <= 24},
            "asked_about_frame": index,
            "frames": [{"i": f["i"], "kind": f["kind"], "says": f["narration"], "state": _compact(f.get("state"))} for f in window],
            "question": question[:600],
            "citations_available": [c.get("ref") for c in (spec.get("citations") or []) if isinstance(c, dict)][:4],
        }
        return json.dumps(payload, separators=(",", ":"), default=str)[:7000]

    # -- audit ---------------------------------------------------------------- #
    async def _audit(self, res: Any, student_id: str | None, prompt_chars: int, dropped: int) -> None:
        if self._sf is None or res is None:
            return
        from ..db.models import LlmCall, utcnow

        try:
            with self._sf() as s:
                s.add(
                    LlmCall(
                        student_id=student_id or None,
                        purpose="ai3_narrate",
                        role="ai3",
                        model=getattr(res, "model", None) or "?",
                        status=res.status if getattr(res, "ok", False) else (getattr(res, "status", None) or "error"),
                        tokens_in=int(getattr(res, "tokens_in", 0) or 0),
                        tokens_out=int(getattr(res, "tokens_out", 0) or 0),
                        latency_ms=int(getattr(res, "latency_ms", 0) or 0),
                        redacted_fields=int(dropped or 0),
                        prompt_chars=int(prompt_chars or 0),
                        tier=getattr(res, "tier", None),
                        cached_tokens=int(getattr(res, "cached_tokens", 0) or 0),
                        day=utcnow().date().isoformat(),
                    )
                )
                s.commit()
        except Exception:  # accounting must never break a reply
            log.debug("ai3 call accounting failed", exc_info=True)


def _compact(state: Any) -> Any:
    """Keep the state a model can quote from, without shipping a whole array of 24 numbers."""
    if not isinstance(state, dict):
        return state
    out: dict[str, Any] = {}
    for k, v in state.items():
        if isinstance(v, list) and len(v) > 10:
            out[k] = {"first": v[:6], "length": len(v), "truncated": True}
        elif isinstance(v, dict) and len(json.dumps(v, default=str)) > 400:
            out[k] = "…"
        else:
            out[k] = v
    return out


def _clean(text: str) -> str:
    """Plain text only. An answer that came back with markup is not shown to a learner.

    The scene stage renders text, never HTML, so this is defence in depth rather than a
    formatting preference: it also refuses the template-shaped things a model sometimes emits.
    """
    flat = " ".join(str(text).split())
    for probe in ("</", "<s", "{{", "```", "javascript:"):
        if probe in flat.lower():
            return ""
    if scenespec.BAD_TEXT.search(flat):
        return ""
    return flat[:MAX_ANSWER].strip()


def _template_answer(spec: dict[str, Any], frame_index: int, question: str) -> str:
    """What can truthfully be said with no model: the frame's own words, and where to look."""
    kind = str(spec.get("kind") or "")
    try:
        frames = sims.frames_for(kind, spec.get("params") or {})
    except Exception:
        frames = []
    index = max(0, min(int(frame_index), max(0, len(frames) - 1)))
    here = frames[index] if frames else None
    prev = frames[index - 1] if frames and index > 0 else None
    parts = [
        f"Frame {index} of {len(frames) or '?'}: {here['narration'] if here else 'the scene could not be replayed'}"
    ]
    if prev and here:
        parts.append(f"It follows from the previous step ({prev['cause'] or prev['kind']}), which is the only reason it happens.")
    parts.append(
        "That is all the simulation can tell you about it right now — the model that would paraphrase it further is not answering, "
        "so nothing has been invented to fill the gap."
    )
    if question.strip():
        parts.append(f"You asked: {' '.join(question.split())[:160]}")
    return " ".join(parts)[:MAX_ANSWER]


__all__ = ["Answer", "SceneNarrator", "_clean", "_template_answer"]
