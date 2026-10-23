"""The integration door: one HTTP endpoint other apps call to plug Adapt in as an engine.

This is the surface the pitch names "Adapt plugged into GenLink": any app on the
network sends **learner activity** and **content**, and gets **adapted content**
back — one lesson plan, laid out for *that* learner, with the receipts for every
layout decision. It is deliberately the smallest thing that can carry the claim:

* **One route, one method.** ``POST /api/v1/integration/adapt``. Not a WebSocket,
  not a session, not a subscription: a plain request/response any HTTP client can
  make, in any language, with no Adapt SDK.
* **Stateless.** No database row is read or written, no learner account exists, no
  cookie is set. The answer is a pure function of the request body, so the door
  keeps working even when everything stateful is degraded, and 50 callers cannot
  deadlock on each other.
* **Deterministic and local.** The response is built by the template adapter
  (AI 3's :class:`~adapt.ai.adapter.LessonAdapter`) — the same code path that
  teaches in simple mode. It never calls Gemini or any network model, so a slow,
  throttled or out-of-quota model cannot fail, delay or half-answer this endpoint.
  ``engine.local_only`` in the response is ``true`` by construction, not by luck.
* **Separate from the learner API.** Nothing here signs anyone in, touches the
  approval gate, or reads learner history. It lives under its own prefix
  (``/api/v1/integration``) and shares no dependencies with the routes the web
  app uses.

What the caller sends, and what happens to it:

``activity``
    A list of recent learner events from the calling app (attempts, hints). The
    endpoint derives a coarse, honest profile from them — step size from recent
    accuracy, a guess-first flag from fast wrong answers, a fatigue flag from
    slowing-and-slipping answers, a hint policy from help-seeking, a confidence
    prompt from calibration — and every derived lever carries a ``why`` that
    names the numbers it came from. The derivation rules are documented in
    ``docs/INTEGRATION.md`` and are code, not a model's opinion.
``content``
    The material to adapt, in any pack envelope the adapter already reads
    (``explanation`` prose, ``worked_example`` steps, ``questions``,
    ``visual`` spec, ``facts``, ``answer_values``, ``hints``). A ``concept`` or
    ``title`` alone is accepted too: the lesson then says plainly that no checked
    material exists yet instead of inventing prose.
``learner``
    An opaque id (echoed, never stored), an optional language, and optional
    explicit ``levers`` / ``preferences`` / ``teaching_memory``. Explicit levers
    always win over activity-derived ones, because a caller that measured
    something properly should not be overruled by this endpoint's coarse read.

Security: the endpoint is open on the LAN by design — that is what "any app can
call it" means for the demo — and the per-IP token bucket in the middleware
still paces it like every other route. An operator who wants a shared secret
sets ``ADAPT_INTEGRATION_TOKEN`` and callers send it as
``Authorization: Bearer <token>`` (or ``X-Adapt-Integration-Token``). On a
public tunnel with ``ADAPT_DEMO_PASSPHRASE`` set, the existing passphrase header
is honoured too. The token is read from the environment per request, never
committed, never logged, and compared in constant time.
"""

from __future__ import annotations

import hmac
import logging
import os
import time
from statistics import median
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from ... import __version__
from ...ai.adapter import AdaptationError, adapter
from ...security import auth

log = logging.getLogger("adapt.api.integration")

router = APIRouter(prefix="/api/v1/integration", tags=["integration"])

#: An answer this fast is a guess, not a retrieval — the same reading the
#: baseline's early-guessing signal makes, applied to the caller's events.
FAST_ANSWER_MS = 2500
#: Share of timed answers that must be fast-and-wrong before the lesson asks
#: the learner to commit to a prediction before it teaches.
EARLY_GUESS_SHARE = 0.4
#: A hint asked for within this window reads as "help-seeking early".
EARLY_HINT_MS = 30_000
#: How many scored answers a coarse signal needs before it moves a lever.
MIN_N_FOR_STEP = 4
MIN_N_FOR_LARGE_STEP = 6
MIN_N_FOR_TOPIC_RUNG = 5
MIN_N_FOR_FATIGUE = 6


# --------------------------------------------------------------------------- #
# the request shape
# --------------------------------------------------------------------------- #

class ActivityEvent(BaseModel):
    """One learner event from the calling app.

    Everything except the shape itself is optional: an app that only knows
    right/wrong sends ``correct``; an app that times answers adds
    ``response_ms``; an app that runs its own teaching methods adds ``method``
    and ``expected_success`` and the adapter's method ranking can use them.
    Unknown fields are ignored rather than refused — this door is for other
    people's apps, and their appends should not be our 422s.
    """

    model_config = ConfigDict(extra="ignore")

    id: str | None = Field(default=None, max_length=120)
    #: ``attempt`` (scored answer), ``hint`` (help asked for), or anything else
    #: the caller's app emits; only attempts and hints move the profile.
    kind: str = Field(default="attempt", max_length=32)
    topic_id: str | None = Field(default=None, max_length=120)
    correct: bool | None = None
    response_ms: int | None = Field(default=None, ge=0, le=86_400_000)
    hint_used: bool | None = None
    #: Free text; ``guessing`` / ``fairly sure`` / ``certain``-style words are
    #: bucketed for the calibration read.
    confidence: str | None = Field(default=None, max_length=40)
    #: The teaching method that preceded this attempt (the caller's own label).
    method: str | None = Field(default=None, max_length=40)
    #: The caller's prediction for this attempt, if it makes one. With ``method``
    #: and enough observations, the adapter ranks methods against expectation.
    expected_success: float | None = Field(default=None, ge=0.0, le=1.0)


class LearnerInfo(BaseModel):
    """Who the activity belongs to — as much or as little as the caller knows."""

    model_config = ConfigDict(extra="ignore")

    #: Opaque to Adapt: echoed in the response, never stored, never joined to
    #: anything. Two apps may even reuse one id; nothing here accumulates.
    id: str = Field(default="", max_length=200)
    #: ``en``, ``sw``/``kiswahili`` or ``mixed``: the language of the lesson's
    #: scaffolding (the questions and prompts around the caller's content).
    language: str | None = Field(default=None, max_length=16)
    #: Explicit levers, as plain values or ``{"value", "why", "source"}`` dicts —
    #: the shape the adapter itself reads. These win over activity-derived ones.
    levers: dict[str, Any] | None = None
    preferences: dict[str, Any] | None = None
    #: ``{"explained": [...], "examples_shown": [...], "asked": [...]}`` — what
    #: the caller's app already taught, so this lesson does not repeat it.
    teaching_memory: dict[str, Any] | None = None


class IntegrationAdaptBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    learner: LearnerInfo | None = None
    activity: list[ActivityEvent] = Field(default_factory=list, max_length=500)
    #: The material to adapt, in any pack envelope ``normalise_pack`` reads.
    content: dict[str, Any] | None = None
    concept: str = Field(default="", max_length=200)
    title: str = Field(default="", max_length=200)
    topic_id: str | None = Field(default=None, max_length=120)
    constraints: dict[str, Any] | None = None


# --------------------------------------------------------------------------- #
# the profile read: activity in, levers/flags/subject out — all code, no model
# --------------------------------------------------------------------------- #

_GUESS_WORDS = ("guess", "unsure", "not sure", "low")
_MID_WORDS = ("fairly", "somewhat", "medium", "half")
_SURE_WORDS = ("certain", "sure", "confident", "high")


def _confidence_bucket(raw: str | None) -> str | None:
    """``guessing`` / ``fairly sure`` / ``certain`` -> ``guess`` / ``middle`` / ``sure``."""
    s = str(raw or "").strip().lower()
    if not s:
        return None
    # "fairly sure" contains "sure"; the hedge decides, so middling wins first.
    if any(w in s for w in _MID_WORDS):
        return "middle"
    if any(w in s for w in _GUESS_WORDS):
        return "guess"
    if any(w in s for w in _SURE_WORDS):
        return "sure"
    return None


def _lever(value: Any, why: str) -> dict[str, Any]:
    return {"value": value, "why": why, "source": "measured"}


def derive_profile(events: list[ActivityEvent], *, topic_id: str | None = None) -> dict[str, Any]:
    """Read a coarse, honest profile from the caller's activity events.

    Every rule here is a stated threshold over counts — the same spirit as the
    baseline's lever derivation, but smaller, because the caller's app owns the
    real measurement and can send explicit levers to overrule this read. The
    output feeds :meth:`LessonAdapter.plan` directly:

    * ``levers`` — step size, hint policy, confidence prompting;
    * ``evidence`` — attempt rows (ids, correctness, method, expectation) and
      flags (``early_guessing``, ``fatigued``);
    * ``subject`` — accuracy and count for the requested topic, plus a coarse
      ladder rung when enough answers exist;
    * ``notes`` — one human-readable line per decision, naming its numbers.
    """
    attempts = [e for e in events if e.correct is not None]
    hint_events = [e for e in events if e.kind == "hint" or e.hint_used]
    n = len(attempts)
    correct_n = sum(1 for e in attempts if e.correct)
    accuracy = (correct_n / n) if n else None

    levers: dict[str, Any] = {}
    flags: dict[str, Any] = {}
    notes: list[str] = []

    # -- step size, from recent accuracy ------------------------------------- #
    if accuracy is not None:
        if n >= MIN_N_FOR_STEP and accuracy < 0.5:
            levers["step_size"] = _lever(
                "small",
                f"{correct_n} of the last {n} answers the app sent were right, "
                "so explanation steps are kept small",
            )
            notes.append(f"step_size=small ({correct_n}/{n} recent answers right)")
        elif n >= MIN_N_FOR_LARGE_STEP and accuracy >= 0.85:
            levers["step_size"] = _lever(
                "large",
                f"{correct_n} of the last {n} answers the app sent were right, "
                "so explanation steps can be longer",
            )
            notes.append(f"step_size=large ({correct_n}/{n} recent answers right)")

    # -- early guessing, from fast wrong answers ------------------------------ #
    timed = [e for e in attempts if e.response_ms is not None]
    if len(timed) >= 3:
        fast_wrong = [e for e in timed if not e.correct and e.response_ms <= FAST_ANSWER_MS]
        if len(fast_wrong) / len(timed) >= EARLY_GUESS_SHARE:
            flags["early_guessing"] = True
            notes.append(
                f"early_guessing: {len(fast_wrong)} of {len(timed)} timed answers were wrong "
                f"within {FAST_ANSWER_MS} ms, so the lesson asks for a prediction first"
            )

    # -- fatigue, from slowing-and-slipping answers --------------------------- #
    if len(timed) >= MIN_N_FOR_FATIGUE:
        head = timed[: len(timed) // 2]
        tail = timed[-3:]
        wrong_tail = sum(1 for e in tail if not e.correct)
        med_head = median(e.response_ms for e in head)
        med_tail = median(e.response_ms for e in tail)
        if med_head > 0 and med_tail >= 1.5 * med_head and wrong_tail >= 2:
            flags["fatigued"] = True
            notes.append(
                f"fatigued: the last answers slowed to {med_tail:.0f} ms (from {med_head:.0f} ms) "
                "and slipped, so the lesson plans a break instead of marking the learner down"
            )

    # -- hint policy, from help-seeking --------------------------------------- #
    if attempts:
        if hint_events:
            hint_times = [e.response_ms for e in hint_events if e.response_ms is not None]
            if hint_times and min(hint_times) < EARLY_HINT_MS:
                levers["hint_policy"] = _lever(
                    "offer early",
                    f"the app reported help asked for after {min(hint_times) // 1000}s, "
                    "so hints are offered early",
                )
                notes.append("hint_policy=offer early (help was sought quickly)")
            else:
                levers["hint_policy"] = _lever(
                    "on request",
                    "the app reported help-seeking, but not in a hurry, so hints stay on request",
                )
                notes.append("hint_policy=on request")
        elif n >= MIN_N_FOR_STEP:
            levers["hint_policy"] = _lever(
                "offer after a first wrong attempt",
                f"no help was sought across {n} answers, so a hint is offered "
                "after a first wrong attempt",
            )
            notes.append("hint_policy=offer after a first wrong attempt (no help sought)")

    # -- confidence prompting, from calibration ------------------------------- #
    conf = [(bool(e.correct), _confidence_bucket(e.confidence)) for e in attempts if e.confidence]
    sure = [ok for ok, bucket in conf if bucket == "sure"]
    guess = [ok for ok, bucket in conf if bucket == "guess"]
    if len(sure) >= 2 and sum(1 for ok in sure if not ok) / len(sure) >= 0.5:
        wrong_sure = sum(1 for ok in sure if not ok)
        levers["confidence_prompting"] = _lever(
            "before_each_answer",
            f"the app's learner felt sure and was wrong {wrong_sure} of {len(sure)} times, "
            "so the lesson asks how sure they are before answering",
        )
        notes.append(
            f"confidence_prompting=before_each_answer (sure but wrong {wrong_sure}/{len(sure)})"
        )
    elif len(guess) >= 2 and sum(1 for ok in guess if ok) / len(guess) >= 0.5:
        right_guess = sum(1 for ok in guess if ok)
        levers["confidence_prompting"] = _lever(
            "evidence_of_success",
            f"the app's learner was right {right_guess} of {len(guess)} times while guessing, "
            "so the close shows that evidence",
        )
        notes.append(
            f"confidence_prompting=evidence_of_success "
            f"(right while guessing {right_guess}/{len(guess)})"
        )

    # -- evidence rows for the adapter ---------------------------------------- #
    rows: list[dict[str, Any]] = []
    for i, e in enumerate(attempts):
        row: dict[str, Any] = {
            "id": e.id or f"int_ev_{i:03d}",
            "correct": bool(e.correct),
        }
        if e.method:
            row["method"] = e.method
        if e.expected_success is not None:
            row["expected_success"] = e.expected_success
        if e.topic_id:
            row["topic_id"] = e.topic_id
        if e.response_ms is not None:
            row["response_ms"] = e.response_ms
        rows.append(row)

    # -- subject row: accuracy on *this* topic, and a coarse rung -------------- #
    subject: dict[str, Any] | None = None
    topic_attempts = attempts if not topic_id else [e for e in attempts if e.topic_id == topic_id]
    if topic_attempts:
        t_n = len(topic_attempts)
        t_correct = sum(1 for e in topic_attempts if e.correct)
        t_acc = t_correct / t_n
        subject = {"p_know": round(t_acc, 3), "n": t_n}
        if t_n >= MIN_N_FOR_TOPIC_RUNG:
            subject["rung"] = min(5, max(0, int(t_acc * 5 + 0.5)))
            notes.append(
                f"topic accuracy {t_correct}/{t_n} places the learner "
                f"at rung {subject['rung']} of 5 on this topic"
            )

    if not attempts:
        notes.append(
            "no scored activity was sent, so the default profile is used "
            "and the lesson says what it did not measure"
        )

    return {
        "levers": levers,
        "evidence": {"rows": rows, "flags": flags},
        "subject": subject,
        "notes": notes,
        "counts": {
            "events": len(events),
            "attempts": n,
            "correct": correct_n,
            "hints": len(hint_events),
        },
    }


def _clean_teaching_memory(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    """Keep only the three known keys, and only as lists of strings.

    The adapter iterates these; a caller sending ``{"explained": 5}`` would
    otherwise turn somebody else's type bug into our 500.
    """
    if not isinstance(raw, dict):
        return None
    known = ("explained", "examples_shown", "asked")
    cleaned = {
        key: [str(x) for x in value]
        for key, value in raw.items()
        if key in known and isinstance(value, (list, tuple))
    }
    return cleaned or None


def _clean_constraints(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    """Pass constraints through, except the one the adapter int()-converts.

    ``derive_limits`` tightens the word cap with ``int(constraints[...])``; a
    non-numeric value would raise, so a value that is not a number is dropped
    rather than forwarded.
    """
    if not isinstance(raw, dict):
        return None
    out = dict(raw)
    cap = out.get("max_words_per_block")
    if cap is not None:
        try:
            out["max_words_per_block"] = int(cap)
        except (TypeError, ValueError):
            out.pop("max_words_per_block")
    return out or None


# --------------------------------------------------------------------------- #
# the door guard
# --------------------------------------------------------------------------- #

def _provided_token(request: Request) -> str | None:
    header = request.headers.get("x-adapt-integration-token")
    if header:
        return header.strip() or None
    raw = request.headers.get("authorization") or ""
    if raw.lower().startswith("bearer "):
        return raw[7:].strip() or None
    return None


def _authorized(request: Request) -> tuple[bool, str]:
    """Who may call: the integration token when the operator set one, else the
    demo passphrase on a protected tunnel, else open (the LAN demo's point).

    The token is read per request rather than captured at import, so an operator
    can arm the door by exporting one variable, and a constant-time compare
    means the refusal leaks nothing about the secret's length or prefix.
    """
    token = os.environ.get("ADAPT_INTEGRATION_TOKEN") or ""
    if token:
        provided = _provided_token(request)
        return (bool(provided) and hmac.compare_digest(token, provided or ""), "integration_token")
    if auth.demo_passphrase_required():
        return (
            auth.check_demo_passphrase(request.headers.get("x-adapt-demo-passphrase")),
            "demo_passphrase",
        )
    return True, "open"


# --------------------------------------------------------------------------- #
# the endpoint
# --------------------------------------------------------------------------- #

@router.post("/adapt")
def adapt_for_app(body: IntegrationAdaptBody, request: Request) -> dict[str, Any]:
    """Send learner activity and content; get the adapted lesson back.

    The whole call is one deterministic pass through the template adapter: no
    model, no database, no session. The response carries the plan under
    ``adapted_content`` and the profile this endpoint read under
    ``derived_profile``, so the calling app can show *why* the lesson looks the
    way it does without trusting a sentence we wrote about ourselves.
    """
    ok, scheme = _authorized(request)
    if not ok:
        log.warning("integration door: refused an unauthorized call (scheme=%s)", scheme)
        detail: dict[str, Any] = {"error": "integration_unauthorized"}
        # Worded without a literal "Authorization: Bearer x" sample: the app's
        # privacy boundary (security/secrecy.py) redacts anything shaped like a
        # credential assignment in a response body, and a redacted hint helps
        # nobody. The header names alone are enough, and they survive the scrub.
        detail["next"] = (
            "send the value of ADAPT_INTEGRATION_TOKEN as a bearer token in the "
            "Authorization header, or in X-Adapt-Integration-Token"
            if scheme == "integration_token"
            else "send the operator's phrase in X-ADAPT-Demo-Passphrase"
        )
        raise HTTPException(status_code=401, detail=detail)

    started = time.monotonic()
    learner = body.learner or LearnerInfo()

    # The pack: the caller's content, with the top-level concept/title/topic_id
    # folded in as defaults. Copied, never mutated — the body belongs to the caller.
    pack: dict[str, Any] = dict(body.content or {})
    if body.topic_id and not pack.get("topic_id"):
        pack["topic_id"] = body.topic_id
    if body.concept and not pack.get("concept"):
        pack["concept"] = body.concept
    if body.title and not pack.get("title"):
        pack["title"] = body.title

    if not pack and not body.concept and not body.title:
        # Refused here rather than passed to the adapter: with nothing named, the
        # adapter's honest fallback would teach "This concept", and a placeholder
        # lesson is how a bug in the *caller's* app goes unnoticed. An empty
        # request is a bad request, said plainly.
        raise HTTPException(
            status_code=422,
            detail={
                "error": "adaptation_refused",
                "detail": (
                    "nothing to adapt: the request carried no content, no concept and no title"
                ),
                "next": "send content (a pack), or at least a concept or title, to adapt",
            },
        )

    derived = derive_profile(body.activity, topic_id=str(pack.get("topic_id") or "") or None)

    # Explicit levers win over the activity read: the calling app measured its
    # learner properly; this endpoint's read is the coarse one.
    levers: dict[str, Any] = {**derived["levers"], **(learner.levers or {})}
    preferences: dict[str, Any] = {}
    if learner.language:
        preferences["language"] = learner.language
    preferences.update(learner.preferences or {})

    try:
        plan = adapter.plan(
            levers=levers or None,
            subject_map=derived["subject"],
            content_pack=pack or None,
            recent_evidence=derived["evidence"],
            preferences=preferences or None,
            constraints=_clean_constraints(body.constraints),
            concept=body.concept,
            title=body.title,
            teaching_memory=_clean_teaching_memory(learner.teaching_memory),
        )
    except AdaptationError as exc:
        # A refusal, not a crash: the adapter says what it needs, and we pass
        # the sentence on with the shape of a usable request beside it.
        raise HTTPException(
            status_code=422,
            detail={
                "error": "adaptation_refused",
                "detail": str(exc),
                "next": "send content (a pack), or at least a concept or title, to adapt",
            },
        ) from exc
    except Exception as exc:
        # The whole call is pure computation over the request body — no model,
        # no database, no network — so anything raised here is the caller's
        # data meeting a shape the pack normaliser could not read (e.g.
        # ``worked_example.steps`` that is not a list). That is a 422 with the
        # exception *type* in it (never its message: values may carry learner
        # text), and the traceback goes to the operator's log, not to the caller.
        log.exception("integration door: unreadable request refused")
        raise HTTPException(
            status_code=422,
            detail={
                "error": "adaptation_refused",
                "detail": (
                    f"the request could not be adapted ({type(exc).__name__} while reading it)"
                ),
                "next": "check the content pack's shape against docs/INTEGRATION.md",
            },
        ) from exc

    elapsed_ms = int((time.monotonic() - started) * 1000)
    return {
        "ok": True,
        "engine": {
            "name": "adapt",
            "version": __version__,
            "mode": plan.get("mode"),
            # True by construction: this endpoint has no model wired to it, so a
            # provider outage degrades nothing here. The claim is checkable —
            # the whole call answered in elapsed_ms without network. (Spelled
            # ``local_only`` rather than anything containing "llm" or "model":
            # the privacy boundary in security/secrecy.py drops such keys from
            # every response body, and a dropped flag reads as a hidden one.)
            "local_only": True,
        },
        "learner_id": learner.id or None,
        "request_id": getattr(request.state, "request_id", None),
        "elapsed_ms": elapsed_ms,
        "derived_profile": {
            **derived["counts"],
            "accuracy": round(derived["counts"]["correct"] / derived["counts"]["attempts"], 3)
            if derived["counts"]["attempts"]
            else None,
            "levers": levers,
            "flags": derived["evidence"]["flags"],
            "subject": derived["subject"],
            "notes": derived["notes"],
        },
        "adapted_content": plan,
    }
