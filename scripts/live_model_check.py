#!/usr/bin/env python3
"""Phase E's live-model quality check: does the model pipeline teach, and can that be judged?

Run (one command, from the repository root, with the key configured in the console first)::

    backend/.venv/bin/python scripts/live_model_check.py

What it drives
--------------
Twelve topics -- two of them inside the authored bank, ten well outside it -- through the frozen
pipeline, once per profile:

    get_pack(topic, level, language)          Phase B: skeleton -> items -> verification
    write_lesson(pack, concept, snapshot)     Phase C, moment 1: what the learner reads
    explain_after_wrong(pack, item, answer)   Phase C, moment 2: start from *their* answer
    reteach(pack, concept, snapshot, memory)  Phase C, moment 3: the same idea, another way

Those modules are not merged yet, so the harness codes against the frozen contract and loads
``backend/tests/fixtures/model/contract_stub.py`` in their place (``--pipeline real`` uses the
real ones when they exist). A run says which it used.

Then two kinds of verdict, kept apart on purpose:

* **gate flags** -- things that are simply wrong wherever they appear: an answer printed in the
  stem, a developer word in learner copy, an unrendered ``{placeholder}``, markup or a link, an
  item that failed verification being used to ask a question, a lesson that contradicts its own
  item's key, or a judge score with no quote behind it.
* **measures** -- rates with a stated threshold: how much of the automated check list passed, how
  much of the content the model actually produced rather than a fallback, whether each lesson
  opened with the profile's lead format and stayed inside its step size, whether the text is in
  the profile's language, whether moment 2 quotes the learner's own answer, and the judge's
  scores. Each threshold is a number in ``THRESHOLDS`` below, not a feeling.

The judge is a **separate call with a separate prompt** that sees the artefacts after the run and
scores five things 1-5: correctness, explains why, uses the learner's actual answer, fits the
profile, no leakage. Every score must carry a quote from the artefact, and a quote the artefact
does not contain is not evidence -- the score is dropped and the run says so.

Honesty rules this file keeps
-----------------------------
* It refuses to run without a key configured through the console (``adapt config set-key``,
  checked with ``adapt config test``), and it never prints the key or any fragment of it. The
  report and the fixtures are passed through the app's own ``scrub_text`` before they are written.
* It never invents a price: token and latency figures are measured, and a money estimate appears
  only when the operator supplies real prices with ``--price-in``/``--price-out``.
* It never claims a live run happened when it did not. ``--selftest`` drives the whole thing with
  a scripted fake, stamps the report accordingly, and is what the test suite uses.
* The live run itself has **not** been made here (no key in this sandbox); the checked-in
  ``MODEL_QUALITY_REPORT.md`` says so, and it is the template this script replaces.

Exit codes: 0 passed, 1 failed a flag or a threshold, 2 refused to run (no key, bad usage),
3 incomplete (the per-run call limit or the daily quota stopped the run).
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import hashlib
import importlib.util
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FIXTURE_DIR = BACKEND / "tests" / "fixtures" / "model"
STUB_PATH = FIXTURE_DIR / "contract_stub.py"
FIXTURE_FAKE = FIXTURE_DIR / "scripted_model.py"
REPORT_PATH = ROOT / "MODEL_QUALITY_REPORT.md"
SELFTEST_REPORT_PATH = Path("/tmp/adapt-live-check-selftest.md")

#: The twelve topics the brief names. Two of them (`photosynthesis`, `probability`) are also in
#: the authored bank; the rest are deliberately outside it, because a pipeline that only works on
#: its own content has not been tested.
TOPICS: tuple[dict[str, Any], ...] = (
    {"topic": "quantum chromodynamics", "authored": False},
    {"topic": "how to bake bread", "authored": False},
    {"topic": "Kiswahili verbs", "authored": False},
    {"topic": "sorting algorithms", "authored": False},
    {"topic": "supply and demand", "authored": False},
    {"topic": "the French Revolution", "authored": False},
    {"topic": "discrete maths (sets)", "authored": False},
    {"topic": "derivatives", "authored": False},
    {"topic": "constitutional law", "authored": False},
    {"topic": "essay writing", "authored": False},
    {"topic": "photosynthesis", "authored": True},
    {"topic": "probability", "authored": True},
)

#: The level every topic is asked for. One level for all twelve keeps the comparison between
#: topics honest; the level lever is exercised by the two profiles instead.
LEVEL = 3

#: Two profiles, chosen so that every lever the judge scores is under load: step size, lead
#: format, tone and language. The second one asks for Kiswahili, which is the only way to see
#: whether the language lever is honoured at all rather than assumed.
PROFILES: tuple[dict[str, Any], ...] = (
    {
        "name": "worked example first",
        "language": "en",
        "tone": "plain",
        "lead_format": "worked_example",
        "step_size": "large",
        "max_words_per_block": 90,
    },
    {
        "name": "picture first",
        "language": "sw",
        "tone": "warm",
        "lead_format": "visual",
        "step_size": "small",
        "max_words_per_block": 45,
    },
)

#: Stated thresholds, not tuned ones. The first two are gates of their own: fewer than twelve
#: completed topics is not a verdict on the pipeline, and a run the model did not actually answer
#: is a run of the fallback.
THRESHOLDS: dict[str, float] = {
    "topics_completed": len(TOPICS),
    "automated_pass_rate": 0.90,
    "model_sourced_rate": 0.75,
    "judge_mean": 3.5,
    "judge_min_criterion": 3.0,
    "judge_no_leakage_floor": 4.0,
    "verified_item_rate": 0.5,
    "learner_answer_echoed_rate": 0.90,
    "reteach_differs_rate": 0.90,
    "step_budget_ok_rate": 0.95,
    "lead_format_ok_rate": 0.90,
    "language_ok_rate": 0.90,
}

#: Words that may never reach a learner, whatever the topic. Split in two on purpose: the first
#: group is unambiguous developer text, the second group is words that also have honest uses
#: ("the endpoint of an interval", "a model of how prices settle", "a token in a sentence"), so
#: they are reported with their context as notes rather than failing the run.
DEVELOPER_WORDS = (
    (re.compile(r"\bAPI keys?\b", re.I), "API key"),
    (re.compile(r"\bgemini\b", re.I), "Gemini"),
    (re.compile(r"\buvicorn\b", re.I), "uvicorn"),
    (re.compile(r"ignored[_\- ]?keys?", re.I), "ignored_keys"),
    (re.compile(r"\bGET /|\bPOST /"), "GET / or POST /"),
    (re.compile(r"\bAI ?[123]\b"), "AI 1/2/3"),
)
AMBIGUOUS_WORDS = (
    (re.compile(r"\bproviders?\b", re.I), "provider"),
    (re.compile(r"\bmodels?\b", re.I), "model"),
    (re.compile(r"\bendpoints?\b", re.I), "endpoint"),
    (re.compile(r"\btokens?\b", re.I), "token"),
)
#: What a credential looks like if a model ever pastes one into the text a learner reads. A key
#: is not "developer text" -- it is a secret that has escaped -- so it gets its own flag, and the
#: patterns are narrow (a provider prefix, or a named credential with 16+ characters after it) so
#: that ordinary prose cannot trip them.
CREDENTIAL_SHAPED = (
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}"), "a Google API key"),
    (re.compile(r"\bsk-[A-Za-z0-9]{16,}"), "a provider secret key"),
    (
        re.compile(
            r"(?i)\b(?:api[_\- ]?key|access[_\- ]?token|client[_\- ]?secret|bearer)\b[\s:=]+[A-Za-z0-9_\-.]{16,}"
        ),
        "a named credential and its value",
    ),
)
PLACEHOLDER = re.compile(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}")
MARKUP = re.compile(r"(https?://|\*\*|^\s*#{1,6}\s|</?[a-zA-Z][^>]*>)", re.M)

#: A stop-word ratio, not a language detector. It is here to catch a lesson that answered in the
#: wrong language outright, and it reports "unknown" rather than guessing when neither list wins.
EN_MARKERS = frozenset("the and of is to that it you a in for with are this as be one so because".split())
SW_MARKERS = frozenset(
    "na ya kwa ni wa hii kila kama lakini zaidi sana moja mbili watu lugha neno sentensi dunia wazo "
    "kuu jinsi huu kuna una ina tuna wanafunzi kufanya vitu ile hapa somo hatua mfano kanuni vitu nini "
    "maneno yako mwenyewe kwanza jibu umejibu".split()
)

JUDGE_CRITERIA = ("correctness", "explains_why", "uses_learner_answer", "fits_profile", "no_leakage")
JUDGE_PURPOSE = "judge"
JUDGE_SCHEMA: dict[str, Any] = {
    **{name: "int" for name in JUDGE_CRITERIA},
    **{f"{name}_evidence": "str" for name in JUDGE_CRITERIA},
    "notes": "str",
}
JUDGE_SYSTEM = """You are an independent judge of teaching text written by a different system for a learner.

Score five things 1-5, where 3 is "acceptable", 4 is "good" and 5 is "nothing to change":

1. correctness - is everything stated true, and free of invented numbers, names or sources?
2. explains_why - does it say *why* the idea works, or does it only restate what to do?
3. uses_learner_answer - does it start from what this learner actually answered?
4. fits_profile - is it in the profile's language, the profile's tone, opening with the profile's lead format, inside the profile's step size?
5. no_leakage - does any text give away the answer to a question the learner is about to be asked, or reveal that this is machine-written by naming a model, a provider or an API?

For every score you must quote the exact words from the artefact that decide it. A score with no
quote is not accepted, and a quote that is not in the artefact is not evidence. Judge only what is
in front of you: do not assume anything about material you cannot see, and do not reward length.
Answer only with the requested fields, as JSON."""


# --------------------------------------------------------------------------- the model layer --


class ModelUnavailable(RuntimeError):
    """The model did not answer, and the answer will not arrive by retrying."""

    def __init__(self, status: str, detail: str = "") -> None:
        super().__init__(f"{status}: {detail}")
        self.status = status


class BudgetStop(RuntimeError):
    """The run's own per-run call limit is spent. Not a quality verdict: an incomplete run."""


class Refused(RuntimeError):
    """The run will not start: the operator asked for something this checkout cannot do."""


#: A refusal that stops the run because nothing about the *content* was ever measured.
_STOP_STATUSES = ("quota", "disabled", "throttled")
#: A refusal that names a configuration problem rather than a model-quality one: the id in
#: the routing table is not callable, so no topic in this run could have been answered.
_CONFIGURATION_STATUSES = ("model_unavailable", "consent_required")


def _stop_message(exc: "ModelUnavailable", topic: str) -> str:
    """Say what stopped the run, in the words that describe that failure and not another.

    One generic "the model could not be reached" used to cover a spent budget, an empty
    candidate list, a filtered prompt and a renamed model id alike. Those four need four
    different actions from whoever reads the report -- wait, change the prompt, change the
    model -- so the report says which it was.
    """
    if exc.status in _STOP_STATUSES:
        return f"the configured budget or pacing refused a call before '{topic}' ({exc.status})"
    if exc.status in _CONFIGURATION_STATUSES:
        return (
            f"a configuration problem stopped the run before '{topic}': the model this "
            f"deployment names is not callable ({exc})"
        )
    return f"the model could not answer '{topic}': {exc}"


CallRecord = dict[str, Any]


def recording_model(
    model: Callable[..., dict[str, Any]],
    log: list[CallRecord],
    *,
    topic: str = "",
    profile: str = "",
    max_calls: int | None = None,
) -> Callable[..., dict[str, Any]]:
    """Wrap a model so every call is measured, and keep the *raw* reply for the fixtures.

    This is the only place that stands between a model call and the run's accounting, so the
    token and latency figures in the report are measurements of the calls the pipeline made and
    not an estimate of what it probably made.
    """

    def wrapper(purpose: str, system: str, user: str, schema: Mapping[str, Any], max_tokens: int) -> dict[str, Any]:
        if max_calls is not None and len(log) >= max_calls:
            raise BudgetStop(f"the run's per-run call limit of {max_calls} is spent")
        started = time.monotonic()
        record: CallRecord = {
            "purpose": purpose,
            "prompt_key": prompt_key(purpose, system, user),
            "topic": topic,
            "profile": profile,
            "tokens_in": _estimate_tokens((system, user)),
            "tokens_out": 0,
            "latency_ms": 0,
        }
        try:
            reply = model(purpose, system, user, schema, max_tokens)
        except BudgetStop:
            raise
        except ModelUnavailable as exc:
            record.update({"refused": True, "error": exc.status, "latency_ms": int((time.monotonic() - started) * 1000)})
            log.append(record)
            raise
        except Exception as exc:  # a scripted fake, a transport bug, a parse fault
            record.update(
                {"refused": True, "error": f"{type(exc).__name__}: {exc}"[:200], "latency_ms": int((time.monotonic() - started) * 1000)}
            )
            log.append(record)
            raise
        record.update(
            {
                "refused": False,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "reply": reply,
                "raw_text": getattr(model, "last_text", None) or getattr(model, "last_raw_text", None),
                "tokens_out": _estimate_tokens((json.dumps(reply, ensure_ascii=False),)),
            }
        )
        log.append(record)
        return reply

    return wrapper


def prompt_key(purpose: str, system: str, user: str) -> str:
    """A short digest of one request, so a fixture can be replayed by content, not by order."""
    payload = f"{purpose}\n{system}\n{user}".encode()
    return hashlib.sha256(payload).hexdigest()[:20]


def _estimate_tokens(parts: Sequence[str]) -> int:
    """The app's own proxy (``ai.llm.estimate_tokens``): ~3.6 characters per token."""
    return max(1, int(sum(len(p or "") for p in parts) / 3.6))


class LiveModel:
    """The frozen five-argument callable, over ``adapt.ai.llm`` as it is today.

    ``ai/llm.py`` is frozen and async; the contract the pipeline codes against is synchronous.
    One event loop is kept open for the whole run, because the client inside ``GeminiLLM`` is
    bound to the loop that made it.
    """

    def __init__(self, settings: Any, *, role: str = "ai2") -> None:
        from adapt.ai.llm import GeminiLLM

        self.llm = GeminiLLM(settings)
        self.loop = asyncio.new_event_loop()
        self.role = role
        self.last_raw_text: str | None = None

    def __call__(
        self,
        purpose: str,
        system: str,
        user: str,
        schema: Mapping[str, Any],
        max_tokens: int,
    ) -> dict[str, Any]:
        result = self.loop.run_until_complete(
            self.llm.complete(
                system=system,
                user=user,
                purpose=purpose,
                role=self.role,
                temperature=0.2,
                max_output_tokens=max(64, int(max_tokens)),
                response_spec=dict(schema) if schema else None,
            )
        )
        self.last_raw_text = result.text or None
        if not result.ok:
            raise ModelUnavailable(result.status, result.error or "")
        if not isinstance(result.data, dict):
            raise ModelUnavailable("parse_error", "the reply was not a JSON object")
        return result.data

    def close(self) -> None:
        try:
            self.loop.run_until_complete(self.llm.aclose())
        finally:
            self.loop.close()


def load_module(path: Path, name: str) -> Any:
    """Import a module from an explicit path (used for the stub and the scripted fake)."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses look themselves up here while the module is built
    spec.loader.exec_module(module)
    return module


class _Fields(dict):
    """A plain dict that also answers attribute lookups from the object it was built out of.

    Phases B and C own the frozen contracts and may return dataclasses or pydantic models. The
    harness reads fields (``pack.get("items")``) and the report and the fixtures have to be
    JSON-serialisable, so a value is read through this; the *original* object is still what goes
    back to the pipeline, so a writer that expects its own type keeps getting it.
    """

    def __init__(self, fields: Mapping[str, Any], original: Any) -> None:
        super().__init__(fields)
        object.__setattr__(self, "_original", original)

    def __getattr__(self, name: str) -> Any:
        original = object.__getattribute__(self, "_original")
        return getattr(original, name)


def _wrap(value: Any) -> Any:
    """A value from the pipeline, readable as data and serialisable, without changing it."""
    if isinstance(value, (str, bytes, int, float, bool)) or value is None:
        return value
    if isinstance(value, _Fields):
        return value
    if isinstance(value, Mapping):
        return _Fields({str(k): _wrap(v) for k, v in value.items()}, value)
    if hasattr(value, "_asdict"):  # a namedtuple
        return _Fields({str(k): _wrap(v) for k, v in value._asdict().items()}, value)
    dump = getattr(value, "model_dump", None)  # pydantic
    if callable(dump):
        try:
            return _Fields({str(k): _wrap(v) for k, v in dict(dump()).items()}, value)
        except Exception:
            pass
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _Fields({field.name: _wrap(getattr(value, field.name)) for field in dataclasses.fields(value)}, value)
    if isinstance(value, (list, tuple, set)):
        return [_wrap(item) for item in value]
    return value


def load_pipeline(choice: str) -> tuple[Any, str]:
    """Phase B/C if they exist in this checkout, the contract stub otherwise."""
    if choice in ("auto", "real"):
        try:
            skeleton = load_module_from_package("adapt.content.skeleton")
            writer = load_module_from_package("adapt.ai.lesson_writer")
            module = type(sys)("live_check_pipeline")
            module.get_pack = skeleton.get_pack
            module.write_lesson = writer.write_lesson
            module.explain_after_wrong = writer.explain_after_wrong
            module.reteach = writer.reteach
            return module, "real (Phase B/C modules in this checkout)"
        except Exception as exc:
            if choice == "real":
                raise Refused(
                    f"--pipeline real was asked for, but importing them failed: {exc} "
                    "(needs adapt.content.skeleton and adapt.ai.lesson_writer)"
                ) from exc
    return load_module(STUB_PATH, "live_check_contract_stub"), "local contract stub (Phase B/C not merged)"


def load_module_from_package(dotted: str) -> Any:
    if str(BACKEND / "src") not in sys.path:
        sys.path.insert(0, str(BACKEND / "src"))
    return importlib.import_module(dotted)


# --------------------------------------------------------------------------- the run itself --


def profile_snapshot(profile: Mapping[str, Any]) -> dict[str, Any]:
    """What a lesson is written for, in the shape the stub reads (see handoff-e.md for the note).

    ``levers``/``prefs``/``memory`` are the three the frozen contract names; the flat copies of
    the same facts are what a reader of this file needs to see, and what the stub reads.
    """
    snapshot: dict[str, Any] = {
        **profile,
        "levers": {
            "step_size": {"value": profile["step_size"], "why": "stated for this run", "source": "stated"},
            "lead_format_prior": {
                "value": profile["lead_format"],
                "why": "stated for this run",
                "source": "stated",
            },
            "language": {"value": profile["language"], "why": "stated for this run", "source": "stated"},
        },
        "prefs": {"language": profile["language"], "tone": profile["tone"]},
        "memory": {},
        "evidence": [],
        "rung": LEVEL,
    }
    return snapshot


def wrong_answer(item: Mapping[str, Any], concept: Mapping[str, Any]) -> str:
    """A wrong answer of the kind a learner gives: the misconception, if the item names one."""
    key = _norm(str(item.get("key") or ""))
    for option in item.get("options") or []:
        text = str(option.get("text") or "")
        if text and _norm(text) != key:
            return text
    for word in concept.get("misconceptions") or []:
        if word.get("wrong_belief"):
            return str(word["wrong_belief"])
    return "I am not sure, I would guess the first thing that came to mind."


def run_topic(
    entry: Mapping[str, Any],
    profiles: Sequence[Mapping[str, Any]],
    pipeline: Any,
    model_for: Callable[[str, str], Callable[..., dict[str, Any]]],
    *,
    level: int = LEVEL,
) -> dict[str, Any]:
    """One topic through the whole pipeline, once per profile."""
    topic = str(entry["topic"])
    out: dict[str, Any] = {
        "topic": topic,
        "authored": bool(entry.get("authored")),
        "packs": {},
        # the pipeline's own objects, kept so a later call gets what the module made, not a copy
        "_raw_packs": {},
        "lessons": [],
        "moments": {},
    }
    for profile in profiles:
        language = str(profile["language"])
        snapshot = profile_snapshot(profile)
        topic_model = model_for(topic, str(profile["name"]))
        if language not in out["packs"]:
            raw_pack = pipeline.get_pack(topic, level=level, language=language, model=topic_model)
            out["_raw_packs"][language] = raw_pack
            out["packs"][language] = _wrap(raw_pack)
        raw_pack = out["_raw_packs"][language]
        pack = out["packs"][language]
        concept_id = str((pack.get("concepts") or [{}])[0].get("id") or "")
        plan = pipeline.write_lesson(raw_pack, concept_id, snapshot, model=topic_model)
        out["lessons"].append(
            {"profile": dict(profile), "plan": _wrap(plan), "memory": snapshot["memory"], "model": topic_model}
        )
    # The three moments: the lesson above is the first. Two more, on the first profile's pack.
    first = out["lessons"][0]
    pack = out["packs"][str(first["profile"]["language"])]
    raw_pack = out["_raw_packs"][str(first["profile"]["language"])]
    concept_id = str((pack.get("concepts") or [{}])[0].get("id") or "")
    concept = _concept(pack, concept_id)
    item = _first_verified(pack)
    if item is not None:
        answer = wrong_answer(item, concept)
        out["moments"]["explain_wrong"] = {
            "item_id": item["id"],
            "learner_answer": answer,
            "explanation": _wrap(
                pipeline.explain_after_wrong(
                    raw_pack,
                    str(item["id"]),
                    answer,
                    profile_snapshot(first["profile"]),
                    first["memory"],
                    model=first["model"],
                )
            ),
        }
    memory = first["memory"]
    out["moments"]["reteach"] = [
        _wrap(pipeline.reteach(raw_pack, concept_id, profile_snapshot(first["profile"]), memory, model=first["model"])),
        _wrap(pipeline.reteach(raw_pack, concept_id, profile_snapshot(first["profile"]), memory, model=first["model"])),
    ]
    return out


# ------------------------------------------------------------------------------- the checks --


def learner_text(
    packs: Mapping[str, Mapping[str, Any]],
    lessons: Sequence[Mapping[str, Any]],
    moments: Mapping[str, Any],
) -> list[tuple[str, str]]:
    """Every string a learner could read, with where it came from (for the report's context)."""
    out: list[tuple[str, str]] = []
    for language, pack in packs.items():
        out += _pack_text(pack, language)
    for entry in lessons:
        for block in entry["plan"].get("blocks") or []:
            out.append((f"lesson[{entry['profile']['name']}].{block.get('type')}", str(block.get("body") or "")))
            if block.get("answer"):
                out.append((f"lesson[{entry['profile']['name']}].answer", str(block["answer"])))
    explanation = (moments.get("explain_wrong") or {}).get("explanation") or {}
    for block in explanation.get("blocks") or []:
        out.append(("moment2", str(block.get("body") or "")))
    for reteach in moments.get("reteach") or []:
        for block in reteach.get("blocks") or []:
            out.append(("moment3", str(block.get("body") or "")))
    return out


def _pack_text(pack: Mapping[str, Any], language: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for concept in pack.get("concepts") or []:
        for field in ("key_idea", "why_it_works"):
            if concept.get(field):
                out.append((f"{language}.concept.{field}", str(concept[field])))
        for fact in concept.get("key_facts") or []:
            out.append((f"{language}.concept.key_fact", str(fact.get("statement") or "")))
        for word in concept.get("misconceptions") or []:
            for field in ("wrong_belief", "why_tempting", "correction"):
                if word.get(field):
                    out.append((f"{language}.misconception.{field}", str(word[field])))
    for item in pack.get("items") or []:
        out.append((f"{language}.item.stem", str(item.get("stem") or "")))
        out.append((f"{language}.item.key", str(item.get("key") or "")))
        for option in item.get("options") or []:
            out.append((f"{language}.item.option", str(option.get("text") or "")))
        for hint in item.get("hints") or []:
            out.append((f"{language}.item.hint", str(hint)))
        for line in item.get("teachback") or []:
            out.append((f"{language}.item.teachback", str(line)))
    return out


def check_topic(result: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """The automated checks. Returns (checks, gate flags, notes)."""
    checks: list[dict[str, Any]] = []
    flags: list[str] = []
    notes: list[str] = []

    def check(name: str, ok: bool, detail: str = "", kind: str = "pipeline") -> bool:
        checks.append({"name": name, "ok": bool(ok), "detail": str(detail)[:300], "kind": kind})
        return bool(ok)

    # -- the pack --------------------------------------------------------- #
    packs = result["packs"]
    for language, pack in packs.items():
        concepts = pack.get("concepts") or []
        check(f"{language}: a skeleton with concepts", bool(concepts), f"{len(concepts)} concept(s)")
        check(
            f"{language}: the pack says where it came from",
            pack.get("source") in ("authored", "model", "simple"),
            str(pack.get("source")),
        )
        if pack.get("source") == "simple":
            notes.append(f"{language}: the pack fell back to 'simple' (nothing pretends to know the topic)")
        ids = [str(c.get("id")) for c in concepts]
        check(f"{language}: concept ids are unique", len(ids) == len(set(ids)), ", ".join(ids))
        for concept in concepts:
            check(
                f"{language}: '{concept.get('name')}' says the key idea in a sentence",
                len(str(concept.get("key_idea") or "").split()) >= 8,
                str(concept.get("key_idea"))[:120],
            )
        known_words = {str(w.get("id")) for c in concepts for w in (c.get("misconceptions") or [])}
        known_facts = {str(f.get("id")) for c in concepts for f in (c.get("key_facts") or [])}
        for concept in concepts:
            for word in concept.get("misconceptions") or []:
                check(
                    f"{language}: a misconception says why it is tempting",
                    bool(str(word.get("why_tempting") or "").strip()),
                    str(word.get("id")),
                )
        for item in pack.get("items") or []:
            for option in item.get("options") or []:
                if option.get("misconception_id"):
                    check(
                        f"{language}: option {option.get('id')} points at a real misconception",
                        str(option["misconception_id"]) in known_words,
                        str(option["misconception_id"]),
                    )
            for fact_id in item.get("fact_ids") or []:
                check(
                    f"{language}: item {item.get('id')} cites a real fact",
                    str(fact_id) in known_facts,
                    str(fact_id),
                )
            check(
                f"{language}: item {item.get('id')} is marked computed, cross_checked or unverified",
                item.get("verification") in ("computed", "cross_checked", "unverified"),
                str(item.get("verification")),
            )
    return checks, flags, notes


def check_lessons(result: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    checks: list[dict[str, Any]] = []
    flags: list[str] = []
    notes: list[str] = []
    items = {str(i.get("id")): i for pack in result["packs"].values() for i in pack.get("items") or []}

    def check(name: str, ok: bool, detail: str = "", kind: str = "general") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": str(detail)[:300], "kind": kind})

    def flag(name: str, detail: str) -> None:
        flags.append(f"{name}: {detail[:200]}")

    for entry in result["lessons"]:
        where = f"{entry['profile']['language']}/{entry['profile']['name']}"
        plan = entry["plan"]
        blocks = plan.get("blocks") or []
        check(f"{where}: the lesson has blocks", bool(blocks), f"{len(blocks)} block(s)")
        if not blocks:
            flag("no_content", f"{where}: the lesson arrived empty")
            continue
        check(
            f"{where}: it says whether a model wrote it or the app did",
            plan.get("source") in ("written_for_you", "simple"),
            str(plan.get("source")),
        )
        budget = int(entry["profile"]["max_words_per_block"])
        over = [b for b in blocks if len(str(b.get("body") or "").split()) > budget]
        check(
            f"{where}: every block is inside the profile's step size ({budget} words)",
            not over,
            f"{len(over)} over: " + "; ".join(f"{len(str(b.get('body') or '').split())}w" for b in over[:3]),
            kind="step_budget",
        )
        lead = str(entry["profile"]["lead_format"])
        check(
            f"{where}: it opens with the profile's lead format ({lead})",
            str(blocks[0].get("type")) == lead,
            str(blocks[0].get("type")),
            kind="lead_format",
        )
        check(
            f"{where}: every explanation block is followed by a check",
            all(
                any(str(later.get("type")) in ("check", "predict") for later in blocks[index + 1 : index + 2])
                for index, block in enumerate(blocks)
                if str(block.get("type")) in ("text", "visual", "worked_example", "explain")
            ),
            " ".join(str(b.get("type")) for b in blocks),
        )
        body_text = "\n\n".join(str(b.get("body") or "") for b in blocks)
        guess = classify_language(body_text)
        check(
            f"{where}: the learner reads the profile's language ({entry['profile']['language']})",
            guess in ("unknown", entry["profile"]["language"]),
            f"looks like {guess}",
            kind="language",
        )
        # no answer may appear before the question that asks for it
        for index, block in enumerate(blocks):
            if str(block.get("type")) not in ("check", "predict"):
                continue
            # only a block that names its item can be checked against a key: a bare
            # "say the idea in your own words" has no answer this run knows about
            item = items.get(str(block.get("item_id")))
            if item is None:
                continue
            key = _norm(str(item.get("key") or ""))
            if len(key) < 8:
                continue
            earlier = "\n".join(str(b.get("body") or "") for b in blocks[:index])
            if key in _norm(earlier):
                flag("answer_leak", f"{where}: block {index + 1} asks a question whose answer is above it")
            if block.get("answer") and _norm(str(block["answer"])) != key:
                flag(
                    "key_mismatch",
                    f"{where}: the check block answers '{str(block['answer'])[:60]}' but the item's key is '{str(item.get('key'))[:60]}'",
                )
            if str(item.get("verification")) == "unverified":
                flag("unverified_item_used", f"{where}: block {index + 1} asks an item that failed verification")
    return checks, flags, notes


def check_text(result: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    """The words themselves: developer text, placeholders, markup, and printed answers."""
    checks: list[dict[str, Any]] = []
    flags: list[str] = []
    notes: list[str] = []
    pieces = learner_text(result["packs"], result["lessons"], result["moments"])

    def check(name: str, ok: bool, detail: str = "", kind: str = "general") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": str(detail)[:300], "kind": kind})

    dev_hits: list[str] = []
    soft_hits: list[str] = []
    for where, text in pieces:
        if not text:
            continue
        for pattern, label in DEVELOPER_WORDS:
            match = pattern.search(text)
            if match:
                dev_hits.append(f"{label} in {where}: …{_context(text, match.start())}…")
        for pattern, label in AMBIGUOUS_WORDS:
            match = pattern.search(text)
            if match:
                soft_hits.append(f"{label} in {where}: …{_context(text, match.start())}…")
    check("no developer text in learner copy", not dev_hits, " | ".join(dev_hits[:3]))
    if dev_hits:
        flags.extend(f"developer_text: {hit}" for hit in dev_hits[:3])
    for hit in soft_hits[:3]:
        notes.append(f"check the word's use — {hit}")

    # a credential in copy: the run must never print one, and learner text must not carry one
    credential_hits: list[str] = []
    for where, text in pieces:
        for pattern, label in CREDENTIAL_SHAPED:
            match = pattern.search(text)
            if match:
                credential_hits.append(f"{where}: {label} — …{_context(text, match.start(), width=12)}…")
    check("no credential-shaped string in learner copy", not credential_hits, " | ".join(credential_hits[:3]))
    if credential_hits:
        flags.extend(f"credential_in_copy: {hit}" for hit in credential_hits[:3])

    placeholders = [f"{where}: {text[:80]}" for where, text in pieces if PLACEHOLDER.search(text)]
    check("no unrendered placeholder reaches a learner", not placeholders, " | ".join(placeholders[:3]))
    if placeholders:
        flags.append(f"placeholder: {placeholders[0]}")

    markup = [f"{where}: {MATCH.group(0)[:40]}" for where, text in pieces if (MATCH := MARKUP.search(text))]
    check("no markup or links in what the learner reads", not markup, " | ".join(markup[:3]))
    if markup:
        flags.append(f"markup: {markup[0]}")

    # an answer printed in the question that asks for it
    leaks: list[str] = []
    for pack in result["packs"].values():
        for item in pack.get("items") or []:
            key = _norm(str(item.get("key") or ""))
            if len(key) < 12:
                continue
            if key in _norm(str(item.get("stem") or "")):
                leaks.append(f"{item.get('id')}: the stem contains the key")
    check("no answer is printed in the question's own stem", not leaks, " | ".join(leaks[:3]))
    if leaks:
        flags.extend(f"answer_leak: {leak}" for leak in leaks[:3])
    return checks, flags, notes


def check_moments(result: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    checks: list[dict[str, Any]] = []
    flags: list[str] = []
    notes: list[str] = []
    moments = result["moments"]

    def check(name: str, ok: bool, detail: str = "", kind: str = "general") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": str(detail)[:300], "kind": kind})

    explain = moments.get("explain_wrong")
    if explain:
        text = "\n\n".join(str(b.get("body") or "") for b in (explain["explanation"].get("blocks") or []))
        answer = str(explain.get("learner_answer") or "")
        head = _norm(answer)[:30]
        check(
            "moment 2 starts from the answer the learner actually gave",
            bool(head) and head in _norm(text),
            f"answer '{answer[:60]}'",
            kind="learner_answer",
        )
        check("moment 2 says what the idea is for", bool(text.strip()), text[:120])
    else:
        notes.append("no verified item was available, so moment 2 was not exercised")
    reteach = moments.get("reteach") or []
    if len(reteach) == 2:
        first_method = str(reteach[0].get("method") or "")
        second_method = str(reteach[1].get("method") or "")
        first_text = " ".join(str(b.get("body") or "") for b in (reteach[0].get("blocks") or []))
        second_text = " ".join(str(b.get("body") or "") for b in (reteach[1].get("blocks") or []))
        differs = (
            first_method != second_method
            or str(reteach[0].get("analogy_id") or "") != str(reteach[1].get("analogy_id") or "")
            or _jaccard(first_text, second_text) < 0.8
        )
        check(
            "moment 3 is a different explanation the second time",
            differs,
            f"{first_method} -> {second_method}",
            kind="reteach_differs",
        )
    else:
        flags.append("no_content: moment 3 was not exercised twice")
    return checks, flags, notes


def classify_language(text: str) -> str:
    """A stop-word ratio. `unknown` when neither list is clearly ahead -- that is not a verdict."""
    words = [w.strip(".,;:!?()\"'").lower() for w in text.split()]
    words = [w for w in words if w]
    if len(words) < 12:
        return "unknown"
    english = sum(1 for w in words if w in EN_MARKERS) / len(words)
    kiswahili = sum(1 for w in words if w in SW_MARKERS) / len(words)
    if max(english, kiswahili) < 0.06:
        return "unknown"
    if kiswahili > english * 1.5:
        return "sw"
    if english > kiswahili * 1.5:
        return "en"
    return "unknown"


ARTEFACT_MARK = "---- ARTEFACT ----"
END_ARTEFACT_MARK = "---- END ARTEFACT ----"


def artefact_text(result: Mapping[str, Any]) -> str:
    """Everything the judge is allowed to quote: the teaching text, and the learner's answer."""
    pieces: list[str] = []
    for entry in result["lessons"]:
        pieces += [str(block.get("body") or "") for block in entry["plan"].get("blocks") or []]
    explain = result["moments"].get("explain_wrong") or {}
    if explain:
        pieces.append(str(explain.get("learner_answer") or ""))
        pieces += [str(block.get("body") or "") for block in (explain.get("explanation") or {}).get("blocks") or []]
    for reteach in result["moments"].get("reteach") or []:
        pieces += [str(block.get("body") or "") for block in reteach.get("blocks") or []]
    return "\n\n".join(p for p in pieces if p)


def judge_prompt(result: Mapping[str, Any]) -> str:
    """The judge sees the artefacts, the profiles they were written for, and nothing else.

    The artefact region is marked so that a quote can be checked against the teaching text
    alone: a quote of the profile line would be a quote of the *instructions*, not evidence.
    """
    lines = [f"TOPIC: {result['topic']}", "", "WHAT EACH PROFILE ASKED FOR:"]
    for entry in result["lessons"]:
        profile = entry["profile"]
        lines.append(
            f"- {profile['name']}: language {profile['language']}, {profile['step_size']} steps of at most "
            f"{profile['max_words_per_block']} words, lead format {profile['lead_format']}, tone {profile['tone']}"
        )
    lines += ["", ARTEFACT_MARK]
    for entry in result["lessons"]:
        lines += [f"LESSON FOR {entry['profile']['name'].upper()}:", ""]
        lines += [str(block.get("body") or "") for block in entry["plan"].get("blocks") or []]
        lines.append("")
    explain = result["moments"].get("explain_wrong") or {}
    if explain:
        lines += [f"THE LEARNER ANSWERED: {explain.get('learner_answer')}", "", "AFTER THAT WRONG ANSWER:", ""]
        lines += [str(block.get("body") or "") for block in (explain.get("explanation") or {}).get("blocks") or []]
        lines.append("")
    for reteach in result["moments"].get("reteach") or []:
        lines += [f"THE LEARNER SAID THEY DID NOT UNDERSTAND (method {reteach.get('method')}):", ""]
        lines += [str(block.get("body") or "") for block in reteach.get("blocks") or []]
        lines.append("")
    lines += [END_ARTEFACT_MARK, "", "Score the five criteria, quoting the teaching text above for each."]
    return "\n".join(lines)


def parse_judge(reply: Mapping[str, Any], haystack: str) -> tuple[dict[str, Any], list[str]]:
    """Scores plus the quotes behind them; a score whose quote is not in the artefact is refused."""
    scores: dict[str, Any] = {}
    problems: list[str] = []
    for name in JUDGE_CRITERIA:
        raw_score = reply.get(name)
        quote = str(reply.get(f"{name}_evidence") or "").strip()
        try:
            score = int(raw_score)
        except (TypeError, ValueError):
            problems.append(f"{name}: no score in the reply")
            continue
        if not 1 <= score <= 5:
            problems.append(f"{name}: score {score} is outside 1-5")
            continue
        if len(quote) < 12:
            problems.append(f"{name}: no quote behind the score")
            continue
        if not quote_is_evidence(quote, haystack):
            problems.append(f"{name}: the quote is not in the artefact: '{quote[:60]}'")
            continue
        scores[name] = {"score": score, "evidence": quote}
    return scores, problems


def quote_is_evidence(quote: str, haystack: str) -> bool:
    """Is this quote really a piece of the artefact? Normalised here, so callers pass it raw."""
    q = _norm(quote)
    text = _norm(haystack)
    if len(q) < 12:
        return False
    if q in text:
        return True
    head = q[: max(20, int(len(q) * 0.6))]
    return head in text


# ------------------------------------------------------------------------------- reporting --


def build_report(
    *,
    mode: str,
    pipeline_label: str,
    verdict: str,
    results: Sequence[Mapping[str, Any]],
    checks: Sequence[Mapping[str, Any]],
    flags: Sequence[str],
    notes: Sequence[str],
    judge_problems: Sequence[str],
    calls: Sequence[Mapping[str, Any]],
    topics_planned: int,
    incomplete: str,
    partial: bool,
    thresholds_failed: Sequence[str],
    fixtures: Sequence[str],
    prices: Mapping[str, float] | None,
) -> str:
    """The report the owner reads, and the file the brief names: MODEL_QUALITY_REPORT.md."""
    passed = [c for c in checks if c["ok"]]
    phases = [r for r in results if r.get("lessons")]
    model_sourced = [r for r in phases if any(p.get("source") == "model" for p in r["packs"].values())]
    scored = [v for r in phases for v in (r.get("judge_scores") or {}).values()]
    criteria_means = {
        name: (
            sum(v["score"] for v in scored if v["name"] == name) / max(1, len([v for v in scored if v["name"] == name]))
            if any(v["name"] == name for v in scored)
            else 0.0
        )
        for name in JUDGE_CRITERIA
    }
    tokens_in = sum(int(c.get("tokens_in") or 0) for c in calls)
    tokens_out = sum(int(c.get("tokens_out") or 0) for c in calls)
    latencies = sorted(int(c.get("latency_ms") or 0) for c in calls)
    median = latencies[len(latencies) // 2] if latencies else 0
    refused = [c for c in calls if c.get("refused")]

    lines: list[str] = []
    lines.append("# MODEL_QUALITY_REPORT.md")
    lines.append("")
    lines.append("<!-- written by scripts/live_model_check.py; a live run replaces this file -->")
    lines.append("")
    if mode == "self-test":
        lines.append(
            "> **Self-test, not a model run.** A scripted fake answered every call below. No model "
            "was contacted, and the scores are the fake's, not a reading of anything."
        )
    else:
        lines.append(
            "> **Live run.** Every reply below came from the model configured in the console. The "
            "key is never printed and never reaches this file."
        )
    lines.append("")
    lines.append(f"* generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    lines.append(f"* pipeline: {pipeline_label}")
    lines.append(f"* topics: {len(phases)} of {topics_planned} completed")
    lines.append(f"* calls: {len(calls)} ({len(refused)} refused, where the pipeline fell back)")
    lines.append(f"* tokens: {tokens_in} in, {tokens_out} out (the app's own ~3.6 characters/token proxy)")
    lines.append(f"* latency: median {median} ms per call")
    if prices:
        cost = (tokens_in / 1_000_000) * float(prices.get("in", 0.0)) + (tokens_out / 1_000_000) * float(
            prices.get("out", 0.0)
        )
        lines.append(f"* cost: about {cost:.4f} at the price list you supplied (per 1M tokens)")
    else:
        lines.append(
            "* cost: no price was supplied, so no money figure is invented here — pass `--price-in` / "
            "`--price-out` with your provider's current prices to get one"
        )
    if incomplete:
        lines.append(f"* **incomplete:** {incomplete}")
    elif partial:
        lines.append(
            f"* **partial:** {len(phases)} of the {int(THRESHOLDS['topics_completed'])} topics the brief names. "
            "A verdict on a subset is a verdict on a subset."
        )
    lines.append("")
    lines.append(f"## Verdict: {verdict}")
    lines.append("")
    if thresholds_failed:
        for line in thresholds_failed:
            lines.append(f"* below threshold: {line}")
    if verdict == "PASS":
        lines.append("* every stated threshold was met and no gate flag was raised")
    elif verdict == "INCOMPLETE":
        lines.append("* the run stopped before it earned a verdict; what it did cover is above")
    lines.append("")
    lines.append("## Gate flags (things that are wrong wherever they appear)")
    lines.append("")
    if flags:
        for flag in flags:
            lines.append(f"* {flag}")
    else:
        lines.append("* none")
    lines.append("")
    lines.append("## Measures, against the thresholds in the script")
    lines.append("")
    lines.append("| measure | value | threshold |")
    lines.append("| --- | --- | --- |")
    lines.append(
        f"| automated checks passed | {len(passed)}/{len(checks)} ({(len(passed) / len(checks) if checks else 0):.0%}) "
        f"| {THRESHOLDS['automated_pass_rate']:.0%} |"
    )
    lines.append(
        f"| topics the model built content for | {len(model_sourced)}/{len(phases)} "
        f"| {THRESHOLDS['model_sourced_rate']:.0%} |"
    )
    items_all = [i for r in phases for pack in (r.get("packs") or {}).values() for i in pack.get("items") or []]
    if items_all:
        verified_items = sum(1 for i in items_all if i.get("verification") in ("computed", "cross_checked"))
        lines.append(
            f"| items that passed verification | {verified_items}/{len(items_all)} "
            f"| {THRESHOLDS['verified_item_rate']:.0%} |"
        )
    for kind, rate, threshold in measure_rows(checks):
        lines.append(f"| {kind.replace('_', ' ')} | {rate:.0%} | {threshold:.0%} |")
    for name in JUDGE_CRITERIA:
        floor = THRESHOLDS["judge_no_leakage_floor"] if name == "no_leakage" else THRESHOLDS["judge_min_criterion"]
        if name == "correctness":
            floor = THRESHOLDS["judge_mean"]
        lines.append(f"| judge · {name} | mean {criteria_means[name]:.2f} | {floor:.1f} |")
    lines.append("")
    lines.append("## Per topic")
    lines.append("")
    lines.append("| topic | in the authored bank | pack | items | lesson sources | moment 2 | moment 3 | judge | flags |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for r in results:
        pack_sources = ", ".join(f"{k}:{v.get('source')}" for k, v in (r.get("packs") or {}).items()) or "—"
        items = [i for pack in (r.get("packs") or {}).values() for i in pack.get("items") or []]
        verified = sum(1 for i in items if i.get("verification") in ("computed", "cross_checked"))
        lessons = ", ".join(str(e["plan"].get("source")) for e in r.get("lessons") or []) or "—"
        judge_here = r.get("judge_mean")
        lines.append(
            "| {} | {} | {} | {}/{} verified | {} | {} | {} | {} | {} |".format(
                r["topic"],
                "yes" if r.get("authored") else "no",
                pack_sources,
                verified,
                len(items),
                lessons,
                "shown" if (r.get("moments") or {}).get("explain_wrong") else "not exercised",
                f"{len((r.get('moments') or {}).get('reteach') or [])} way(s)",
                f"{judge_here:.2f}" if isinstance(judge_here, float) else "—",
                "; ".join(r.get("flags") or []) or "—",
            )
        )
    lines.append("")
    lines.append("## Why a call was refused, and where a fallback happened")
    lines.append("")
    if refused:
        for record in refused[:20]:
            lines.append(f"* {record.get('purpose')} ({record.get('topic') or '—'}): {record.get('error')}")
    else:
        lines.append("* nothing was refused: every call the pipeline made was answered")
    for note in notes:
        lines.append(f"* note: {note}")
    lines.append("")
    lines.append("## Rejections and fallbacks, in the pipeline's own words")
    lines.append("")
    fallbacks = [
        f"{r['topic']}/{language}: the pack came back '{pack.get('source')}'"
        for r in phases
        for language, pack in (r.get("packs") or {}).items()
        if pack.get("source") != "model"
    ]
    if fallbacks:
        for line in fallbacks:
            lines.append(f"* {line}")
    else:
        lines.append("* every pack the pipeline returned was built by the model (no topic fell back)")
    lines.append("")
    lines.append("## The judge, per criterion")
    lines.append("")
    for name in JUDGE_CRITERIA:
        rows = [v for v in scored if v["name"] == name]
        lines.append(f"### {name}: mean {criteria_means[name]:.2f} over {len(rows)} score(s)")
        lines.append("")
        for row in rows[:3]:
            lines.append(f"* {row['score']}/5 — \"{row['evidence'][:160]}\"")
        lines.append("")
    if judge_problems:
        lines.append("### Scores refused by the harness")
        lines.append("")
        lines.append("A score without a quote from the artefact behind it is not evidence, so it is dropped:")
        lines.append("")
        for problem in judge_problems:
            lines.append(f"* {problem}")
        lines.append("")
    if fixtures:
        lines.append("## Saved fixtures")
        lines.append("")
        lines.append("Sanitised replies, one file per topic, in the fixture directory. They replay")
        lines.append("through `scripted_model.load_fixture`, so a later run can assert against them")
        lines.append("without a key:")
        lines.append("")
        for name in fixtures:
            lines.append(f"* {name}")
        lines.append("")
    lines.append("## How to run this for real")
    lines.append("")
    lines.append("```")
    lines.append("cd backend && .venv/bin/activate      # or call backend/.venv/bin/python directly")
    lines.append("adapt config set-key                 # hidden prompt; the key is never an argument")
    lines.append("adapt config test                    # one minimal request: pass or fail")
    lines.append("python scripts/live_model_check.py   # from the repository root")
    lines.append("```")
    lines.append("")
    lines.append(
        "The run makes about 13 calls per topic (two packs, two lessons, three moments, one judge). "
        "When the configured daily budget refuses a call it stops, says so, and exits 3 — it does "
        "not report a verdict it did not earn."
    )
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------------- main --


def main(
    argv: Sequence[str] | None = None,
    *,
    model_factory: Callable[[], Callable[..., dict[str, Any]]] | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=None, help=f"where the report goes (default {REPORT_PATH.name})")
    parser.add_argument("--topics", default="all", help="comma-separated topics, or 'all'")
    parser.add_argument("--profiles", default="all", help="comma-separated profile names, or 'all'")
    parser.add_argument("--pipeline", choices=("auto", "stub", "real"), default="auto")
    parser.add_argument("--max-calls", type=int, default=400, help="stop the run after this many calls")
    parser.add_argument("--save-fixtures", action="store_true", help="write the replies into the fixture directory")
    parser.add_argument("--fixture-dir", default=str(FIXTURE_DIR))
    parser.add_argument("--price-in", type=float, default=None, help="price per 1M input tokens, for a cost estimate")
    parser.add_argument("--price-out", type=float, default=None, help="price per 1M output tokens")
    parser.add_argument("--allow-partial", action="store_true", help="judge a run that did not finish every topic")
    parser.add_argument("--selftest", action="store_true", help="drive the run with the scripted fake; no key needed")
    parser.add_argument("--variant", default="good", help="which scripted variant the self-test uses")
    parser.add_argument("--force", action="store_true", help="allow a self-test to overwrite the report in the repository")
    parser.add_argument("--json", action="store_true", help="print the run record as JSON as well")
    parser.add_argument("--quiet", action="store_true", help="only print the verdict line")
    args = parser.parse_args(list(argv) if argv is not None else None)

    def say(line: str = "") -> None:
        # stdout goes into logs and CI output, so it passes the same secrecy pass the report does:
        # a flag can quote the model's own words, and the model is not the only thing that can
        # echo a credential back
        if not args.quiet:
            print(_scrub(line, settings), flush=True)

    settings = None
    mode = "live"
    if args.selftest:
        mode = "self-test"
    else:
        settings = _load_settings()
        if settings is None:
            print(
                "refused: no credential is configured for this copy.\n"
                "Configure it in the console first, then come back:\n"
                "    adapt config set-key      # hidden prompt; stored owner-only, never an argument\n"
                "    adapt config test         # one minimal request: pass or fail",
                file=sys.stderr,
            )
            return 2

    chosen = _choose_topics(args.topics)
    if chosen is None:
        print(f"refused: --topics must name topics from this list: {', '.join(e['topic'] for e in TOPICS)}", file=sys.stderr)
        return 2
    profiles = _choose_profiles(args.profiles)
    if profiles is None:
        print(f"refused: --profiles must be one of: {', '.join(p['name'] for p in PROFILES)}", file=sys.stderr)
        return 2

    out_path = Path(args.out) if args.out else (SELFTEST_REPORT_PATH if args.selftest else REPORT_PATH)
    if args.selftest and out_path.resolve() == REPORT_PATH.resolve() and not args.force:
        print(
            "refused: a self-test may not overwrite MODEL_QUALITY_REPORT.md — pass --out elsewhere "
            "(or --force if you really mean it)",
            file=sys.stderr,
        )
        return 2

    # The self-test drives the run with the scripted fake, and both the fake's replies and the
    # red-team assertions are written against the pass-through contract stub. Once Phases B and C
    # are merged, `auto` resolves to the real modules, whose validators would reject the
    # stub-shaped replies before the harness's own checks ever saw them — the red-team tests
    # would then all fail for one reason (fallback), which is not what they test. So a self-test
    # stays on the stub unless an explicit --pipeline says otherwise; the live run (no
    # --selftest) still gets the real pipeline from `auto`. Phase G pinned this while integrating.
    pipeline_choice = args.pipeline
    if args.selftest and pipeline_choice == "auto":
        pipeline_choice = "stub"

    try:
        pipeline, pipeline_label = load_pipeline(pipeline_choice)
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    if settings is not None:
        from adapt.security.secrecy import install_log_redaction

        install_log_redaction(settings)

    live_model: LiveModel | None = None
    if model_factory is not None:
        factory: Callable[[], Callable[..., dict[str, Any]]] = model_factory
    elif args.selftest:
        scripted = load_module(FIXTURE_FAKE, "live_check_scripted_model")

        def fake_factory() -> Callable[..., dict[str, Any]]:
            return scripted.make(args.variant)

        factory = fake_factory
    else:
        assert settings is not None
        live_model = LiveModel(settings)

        def live_factory() -> Callable[..., dict[str, Any]]:
            return live_model

        factory = live_factory

    if live_model is None:
        # a factory was injected (the tests): this harness did not contact the console's model,
        # and nothing it writes may claim that one answered
        mode = "self-test"

    calls: list[CallRecord] = []

    def model_for(topic: str, profile: str) -> Callable[..., dict[str, Any]]:
        return recording_model(factory(), calls, topic=topic, profile=profile, max_calls=args.max_calls)

    results: list[dict[str, Any]] = []
    incomplete = ""
    for index, entry in enumerate(chosen, start=1):
        topic = str(entry["topic"])
        try:
            result = run_topic(entry, profiles, pipeline, model_for)
        except BudgetStop as exc:
            incomplete = f"the per-run call limit stopped the run before '{topic}': {exc}"
            break
        except ModelUnavailable as exc:
            incomplete = _stop_message(exc, topic)
            break
        except Exception as exc:  # a broken pipeline, or a stub that met an unexpected shape
            incomplete = f"the pipeline raised before '{topic}': {type(exc).__name__}: {exc}"
            break

        checks, flags, notes = check_topic(result)
        for extra in (check_lessons(result), check_text(result), check_moments(result)):
            checks += extra[0]
            flags += extra[1]
            notes += extra[2]
        result["checks"] = checks
        result["flags"] = flags
        result["notes"] = notes

        # The judge: a separate call, a separate prompt, over the finished artefacts.
        prompt = judge_prompt(result)
        # the haystack is the artefact, not the prompt: the profile line is an instruction, and a
        # quote of an instruction is not evidence about the teaching text
        haystack = artefact_text(result)
        judge_reply: Mapping[str, Any] = {}
        judge_problems: list[str] = []
        try:
            judge_call = recording_model(factory(), calls, topic=topic, profile="judge", max_calls=args.max_calls)
            judge_reply = judge_call(JUDGE_PURPOSE, JUDGE_SYSTEM, prompt, JUDGE_SCHEMA, 1200)
        except BudgetStop as exc:
            incomplete = f"the per-run call limit stopped the run at the judge for '{topic}': {exc}"
            break
        except ModelUnavailable as exc:
            if exc.status in _STOP_STATUSES or exc.status in _CONFIGURATION_STATUSES:
                incomplete = _stop_message(exc, f"{topic} (judge call)")
                break
            # A judge that cannot score is a gap in the report, not a stop: the artefacts
            # were produced and the automated checks still ran, so the run keeps its verdict
            # and says out loud which part is missing.
            judge_problems.append(f"{topic}: the judge call failed ({exc.status})")
        except Exception as exc:
            judge_problems.append(f"{topic}: the judge call failed ({type(exc).__name__}: {exc})")
        scores, problems = parse_judge(judge_reply, haystack) if judge_reply else ({}, [])
        result["judge_scores"] = {name: {"name": name, **value} for name, value in scores.items()}
        result["judge_problems"] = judge_problems + problems
        result["judge_mean"] = (
            sum(v["score"] for v in result["judge_scores"].values()) / len(result["judge_scores"])
            if result["judge_scores"]
            else None
        )
        results.append(result)
        say(
            f"[{index}/{len(chosen)}] {topic}: pack "
            + ", ".join(f"{k}={v.get('source')}" for k, v in result["packs"].items())
            + f" · {len(result['flags'])} flag(s)"
            + (f" · judge {result['judge_mean']:.2f}" if result["judge_mean"] else "")
        )

    if live_model is not None:
        live_model.close()

    flags = [flag for r in results for flag in r.get("flags") or []]
    notes = [note for r in results for note in r.get("notes") or []]
    judge_problems = [problem for r in results for problem in r.get("judge_problems") or []]
    checks = [c for r in results for c in r.get("checks") or []]
    scored = [v for r in results for v in (r.get("judge_scores") or {}).values()]

    fixtures: list[str] = []
    if args.save_fixtures or (mode == "live" and results):
        fixtures = save_fixtures(results, calls, Path(args.fixture_dir), settings, live=live_model is not None)

    thresholds_failed = _threshold_failures(checks, results, scored, judge_problems)
    partial = len(results) < int(THRESHOLDS["topics_completed"])
    if partial and not incomplete and not args.allow_partial:
        incomplete = (
            f"only {len(results)} of the {int(THRESHOLDS['topics_completed'])} topics the brief names ran "
            "(--allow-partial is how you ask for a verdict on fewer)"
        )
    if incomplete:
        verdict = "INCOMPLETE"
    elif flags or judge_problems or thresholds_failed or not results:
        verdict = "FAIL"
    else:
        verdict = "PASS"
    report = _scrub(
        build_report(
            mode=mode,
            pipeline_label=pipeline_label,
            verdict=verdict,
            results=results,
            checks=checks,
            flags=flags,
            notes=notes,
            judge_problems=judge_problems,
            calls=calls,
            topics_planned=len(chosen),
            incomplete=incomplete,
            partial=partial,
            thresholds_failed=thresholds_failed,
            fixtures=fixtures,
            prices={"in": args.price_in or 0.0, "out": args.price_out or 0.0}
            if (args.price_in or args.price_out)
            else None,
        ),
        settings,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")

    say(f"\n{verdict} — {len(results)}/{len(chosen)} topics · {len(calls)} calls · report: {out_path}")
    for line in thresholds_failed:
        say(f"below threshold: {line}")
    if flags:
        say(f"gate flags: {len(flags)} (first: {flags[0][:120]})")
    if judge_problems:
        say(f"judge: {len(judge_problems)} score(s) refused")
    if incomplete:
        say(f"incomplete: {incomplete}")

    if args.json:
        record = {
            "mode": mode,
            "pipeline": pipeline_label,
            "verdict": verdict,
            "topics": [
                {"topic": r["topic"], "flags": r.get("flags") or [], "judge_mean": r.get("judge_mean")} for r in results
            ],
            "calls": len(calls),
            "tokens_in": sum(int(c.get("tokens_in") or 0) for c in calls),
            "tokens_out": sum(int(c.get("tokens_out") or 0) for c in calls),
            "flags": flags,
            "judge_problems": judge_problems,
            "thresholds_failed": thresholds_failed,
            "incomplete": incomplete,
            "partial": partial,
            "report": str(out_path),
        }
        print(_scrub(json.dumps(record, indent=2, ensure_ascii=False), settings))

    if incomplete and not args.allow_partial:
        return 3
    return 0 if verdict == "PASS" else 1  # a partial run that asked for a verdict gets one


#: Each measure keeps its own threshold, so "the language lever was ignored on a fifth of the
#: lessons" fails the run for that reason, not as a mysterious drop in an overall rate.
MEASURE_THRESHOLDS: dict[str, float] = {
    "step_budget": THRESHOLDS["step_budget_ok_rate"],
    "lead_format": THRESHOLDS["lead_format_ok_rate"],
    "language": THRESHOLDS["language_ok_rate"],
    "learner_answer": THRESHOLDS["learner_answer_echoed_rate"],
    "reteach_differs": THRESHOLDS["reteach_differs_rate"],
}


def measure_rows(checks: Sequence[Mapping[str, Any]]) -> list[tuple[str, float, float]]:
    """(measure, rate, threshold) for every kind of check that has a threshold of its own."""
    out: list[tuple[str, float, float]] = []
    for kind, threshold in MEASURE_THRESHOLDS.items():
        rows = [c for c in checks if c.get("kind") == kind]
        if not rows:
            continue
        out.append((kind, sum(1 for c in rows if c["ok"]) / len(rows), threshold))
    return out


def _threshold_failures(
    checks: Sequence[Mapping[str, Any]],
    results: Sequence[Mapping[str, Any]],
    scored: Sequence[Mapping[str, Any]],
    judge_problems: Sequence[str],
) -> list[str]:
    """Every stated threshold, in one place, so "pass" is a list and not a judgement call."""
    out: list[str] = []
    if checks:
        rate = sum(1 for c in checks if c["ok"]) / len(checks)
        if rate < THRESHOLDS["automated_pass_rate"]:
            out.append(f"automated checks passed {rate:.0%} (want {THRESHOLDS['automated_pass_rate']:.0%})")
    for kind, rate, threshold in measure_rows(checks):
        if rate < threshold:
            out.append(f"{kind.replace('_', ' ')} {rate:.0%} (want {threshold:.0%})")
    items = [i for r in results for pack in (r.get("packs") or {}).values() for i in pack.get("items") or []]
    if items:
        verified = sum(1 for i in items if i.get("verification") in ("computed", "cross_checked"))
        rate = verified / len(items)
        if rate < THRESHOLDS["verified_item_rate"]:
            out.append(
                f"items that passed verification {verified}/{len(items)} (want {THRESHOLDS['verified_item_rate']:.0%})"
            )
    with_content = [r for r in results if r.get("packs")]
    if with_content:
        rate = sum(1 for r in with_content if any(p.get("source") == "model" for p in r["packs"].values())) / len(
            with_content
        )
        if rate < THRESHOLDS["model_sourced_rate"]:
            out.append(f"model-built content {rate:.0%} (want {THRESHOLDS['model_sourced_rate']:.0%})")
    floors = {
        "correctness": THRESHOLDS["judge_mean"],
        "explains_why": THRESHOLDS["judge_min_criterion"],
        "uses_learner_answer": THRESHOLDS["judge_min_criterion"],
        "fits_profile": THRESHOLDS["judge_min_criterion"],
        "no_leakage": THRESHOLDS["judge_no_leakage_floor"],
    }
    for name, floor in floors.items():
        rows = [v for v in scored if v["name"] == name]
        if not rows:
            out.append(f"judge {name}: nothing was scored")
            continue
        mean = sum(v["score"] for v in rows) / len(rows)
        if mean < floor:
            out.append(f"judge {name} {mean:.2f} (want {floor:.2f})")
    if judge_problems:
        out.append(f"{len(judge_problems)} judge score(s) were refused for having no quote behind them")
    return out


def save_fixtures(
    results: Sequence[Mapping[str, Any]],
    calls: Sequence[Mapping[str, Any]],
    directory: Path,
    settings: Any,
    *,
    live: bool,
) -> list[str]:
    """Sanitised replies, one file per topic: regression material that needs no key to replay.

    Everything passes through the app's own ``redact_text`` (identity-shaped strings) and
    ``scrub_text`` (configured secrets, including the key itself) before it touches the disk.
    """
    directory.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for result in results:
        topic = str(result["topic"])
        records = [c for c in calls if c.get("topic") == topic]
        payload = {
            "topic": topic,
            "level": LEVEL,
            "saved_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "model": "live" if live else "scripted-fake",
            "note": (
                "replies from a live run, sanitised"
                if live
                else "replies from the scripted fake: a fixture of the harness, not of a model"
            ),
            "profiles": [dict(entry["profile"]) for entry in result.get("lessons") or []],
            "packs": result.get("packs") or {},
            "lessons": [{"profile": e["profile"]["name"], "plan": e["plan"]} for e in result.get("lessons") or []],
            "moments": result.get("moments") or {},
            "judge": result.get("judge_scores") or {},
            "checks": result.get("checks") or [],
            "flags": result.get("flags") or [],
            "responses": [
                {
                    "purpose": record.get("purpose"),
                    "prompt_key": record.get("prompt_key"),
                    "refused": bool(record.get("refused")),
                    "error": record.get("error"),
                    "reply": record.get("reply"),
                    "raw_text": record.get("raw_text"),
                    "tokens_in": record.get("tokens_in"),
                    "tokens_out": record.get("tokens_out"),
                    "latency_ms": record.get("latency_ms"),
                }
                for record in records
            ],
        }
        text = _scrub(json.dumps(payload, indent=1, ensure_ascii=False, default=str), settings, redact_identity=True)
        name = f"{_slug(topic)}.json"
        (directory / name).write_text(text + "\n", encoding="utf-8")
        written.append(name)
    return written


def _load_settings() -> Any | None:
    """Settings with the managed values applied, or None when no credential is configured."""
    if str(BACKEND / "src") not in sys.path:
        sys.path.insert(0, str(BACKEND / "src"))
    try:
        from adapt.config import get_settings
        from adapt.config_secrets import settings_with_managed_values

        settings = settings_with_managed_values(get_settings())
    except Exception:
        return None
    if not getattr(settings, "gemini_api_key", None):
        return None
    return settings


def _choose_topics(spec: str) -> list[dict[str, Any]] | None:
    if spec.strip().lower() in ("all", ""):
        return list(TOPICS)
    wanted = {part.strip().lower() for part in spec.split(",") if part.strip()}
    known = {str(entry["topic"]).lower(): entry for entry in TOPICS}
    if not wanted <= set(known):
        return None
    return [entry for entry in TOPICS if str(entry["topic"]).lower() in wanted]


def _choose_profiles(spec: str) -> list[dict[str, Any]] | None:
    if spec.strip().lower() in ("all", ""):
        return list(PROFILES)
    wanted = {part.strip().lower() for part in spec.split(",") if part.strip()}
    known = {str(profile["name"]).lower(): profile for profile in PROFILES}
    if not wanted <= set(known):
        return None
    return [profile for profile in PROFILES if str(profile["name"]).lower() in wanted]


def _scrub(text: str, settings: Any | None, *, redact_identity: bool = False) -> str:
    """The app's own two passes, so nothing configured can reach a file this run writes."""
    if str(BACKEND / "src") not in sys.path:
        sys.path.insert(0, str(BACKEND / "src"))
    if redact_identity:
        try:
            from adapt.security.redact import redact_text

            text = redact_text(text, max_chars=max(6000, len(text) + 1000)).text
        except Exception:
            pass
    try:
        from adapt.security.secrecy import scrub_text

        text = scrub_text(text, settings)
    except Exception:
        pass
    return text


def _slug(text: str) -> str:
    out = "".join(c.lower() if c.isalnum() else "-" for c in text)
    while "--" in out:
        out = out.replace("--", "-")
    return out.strip("-") or "topic"


def _norm(text: str) -> str:
    return " ".join(str(text).lower().split())


def _context(text: str, index: int, width: int = 45) -> str:
    start = max(0, index - width)
    return " ".join(text[start : index + width].split())


def _jaccard(a: str, b: str) -> float:
    left = {w for w in _norm(a).split() if len(w) > 3}
    right = {w for w in _norm(b).split() if len(w) > 3}
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _concept(pack: Mapping[str, Any], concept_id: str) -> Mapping[str, Any]:
    for concept in pack.get("concepts") or []:
        if str(concept.get("id")) == str(concept_id):
            return concept
    concepts = pack.get("concepts") or [{}]
    return concepts[0]


def _first_verified(pack: Mapping[str, Any]) -> Mapping[str, Any] | None:
    for item in pack.get("items") or []:
        if str(item.get("verification")) in ("computed", "cross_checked"):
            return item
    return None


if __name__ == "__main__":  # pragma: no cover - the tests call main() directly
    raise SystemExit(main())
