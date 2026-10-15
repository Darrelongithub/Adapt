"""The scripted model Phase G drives the *integrated* pipeline with.

Phase E's ``scripted_model.py`` answers the contract stub's purposes. This file answers the
purposes the **real** Phase B and Phase C code passes, so one fake can drive both halves of the
frozen contracts against each other:

* ``content_skeleton`` / ``content_items`` / ``content_item_verifier`` (Phase B) are delegated
  to Phase B's own ``ScriptedModel`` in ``backend/tests/content_skeleton/fakes.py`` — the same
  fake B's validator tests use, so a pack it produces is a pack B's validator accepts.
* ``ai3_lesson_plan`` / ``ai3_explain_wrong`` / ``ai3_lesson_plan`` (Phase C) are answered by a
  small "parrot": it reads C's bounded JSON payload and proposes the smallest lesson that
  satisfies C's whole-plan validation — cited facts only, no numbers or proper names beyond
  the Pack's own, a check after the explanation, and the learner's own answer quoted back in
  the wrong-answer moment. When C's validator rejects a proposal (a new validation rule, a
  profile lever this parrot does not model), C falls back to its deterministic lesson — which
  is the contract's promise, and the integration run still proves the seam.

``FakeAsyncLLM`` wraps the callable in the async client shape the app's bridges expect
(``complete(**kwargs) -> LLMResult``), so it can be dropped into ``services.ai2.llm`` in a test
or in ``scripts/serve_with_fake_model.py`` without either route knowing the difference.

Nothing here is used by the application. A server started with it is for browser integration
runs only, and says so on startup.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from typing import Any, Callable, Mapping

_HERE = Path(__file__).resolve()
_TESTS = _HERE.parents[2]  # backend/tests
_B_FAKES = _TESTS / "content_skeleton" / "fakes.py"

_B_PURPOSES = ("content_skeleton", "content_items", "content_item_verifier")
_C_PURPOSES = ("ai3_lesson_plan", "ai3_explain_wrong", "ai3_reteach")

#: C's block word counter skips identifier keys; mirror it so `length` matches.
_SKIP_KEYS = {"id", "item_id", "kind"}


def _load_b_fake() -> Callable[..., dict[str, Any]]:
    spec = importlib.util.spec_from_file_location("content_skeleton_fakes_for_e2e", _B_FAKES)
    assert spec is not None and spec.loader is not None, f"cannot load {_B_FAKES}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ScriptedModel()


def _word_count(value: Any) -> int:
    if isinstance(value, str):
        return len(value.split())
    if isinstance(value, Mapping):
        return sum(
            _word_count(child) for key, child in value.items() if key not in _SKIP_KEYS
        )
    if isinstance(value, (list, tuple)):
        return sum(_word_count(child) for child in value)
    return 0


def _trim(text: str, limit: int) -> str:
    words = str(text).split()
    return " ".join(words[: max(1, limit)])


def _safe(text: str) -> str:
    """Strip markup-ish characters and digits the grounding check could not trace."""
    return re.sub(r"[*_`<>{}\[\]|#]", " ", str(text or "")).strip()


def _why(payload: Mapping[str, Any]) -> dict[str, Any]:
    evidence = payload.get("recent_evidence") or {}
    n = evidence.get("n")
    return {"evidence_ids": [], "n": n if isinstance(n, int) and not isinstance(n, bool) else 0}


def _facts(concept: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows = concept.get("key_facts") or []
    return [
        row
        for row in rows
        if isinstance(row, Mapping) and row.get("id") and str(row.get("statement") or "").strip()
    ]


def _check_block(
    payload: Mapping[str, Any], fact_ids: list[str]
) -> dict[str, Any] | None:
    """A one-question check from a verified multiple-choice item, options unmarked.

    C's validator requires the question to be the item's own stem, verbatim.
    """
    for item in payload.get("items") or []:
        if not isinstance(item, Mapping):
            continue
        stem = str(item.get("stem") or "").strip()
        options = [
            {"id": str(o.get("id") or ""), "text": str(o.get("text") or "")}
            for o in item.get("options") or []
            if isinstance(o, Mapping)
        ]
        if len(options) < 2 or not item.get("id") or not stem:
            continue
        if str(item.get("verification") or "") not in {"computed", "cross_checked"}:
            continue
        body = {
            "question": stem,
            "item_id": str(item.get("id")),
            "options": options,
        }
        return {
            "type": "check",
            "body": body,
            "fact_ids": list(fact_ids[:1]),
            "why": _why(payload),
            "length": _word_count(body),
        }
    return None


def _lesson_blocks(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    moment = str(payload.get("moment") or "initial")
    profile = payload.get("profile") or {}
    try:
        limit = max(8, int(profile.get("max_words_per_block") or 40))
    except (TypeError, ValueError):
        limit = 40
    concept = payload.get("concept") or {}
    facts = _facts(concept)
    why = _why(payload)
    fact_ids = [str(facts[0]["id"])] if facts else []
    if not fact_ids:
        # The initial moment withholds every fact whose text overlaps an item option
        # (anti-leak), so the payload can carry no key_facts at all. The items still
        # name the fact ids they rest on, and those ids are in the validator's
        # allowed set, so cite one of those.
        for candidate in payload.get("items") or []:
            if isinstance(candidate, Mapping):
                cited = [str(fid) for fid in candidate.get("fact_ids") or [] if fid]
                if cited:
                    fact_ids = cited[:1]
                    break

    blocks: list[dict[str, Any]] = []

    if moment == "wrong_answer":
        attempt = payload.get("attempt_context") or {}
        answer = _safe(attempt.get("learner_answer") or "that option")
        misconception = attempt.get("matched_misconception") or {}
        tempting = _safe(misconception.get("why_tempting") or "the shortcut looks like it saves a step")
        correction = _safe(misconception.get("correction") or "the same rule still applies here")
        text = _trim(
            f"You answered: {answer}. It feels right, because {tempting} "
            f"It does not hold here, because {correction}",
            limit,
        )
        kind = "explain"
    elif moment == "reteach":
        method = str(payload.get("method_selected_by_code") or "simpler_words")
        if method == "smaller_step":
            text = _trim(
                "This is the same idea in a smaller piece. Look at the start, because "
                "the rest follows from it.",
                min(limit, 18),
            )
        elif method == "example":
            text = _trim(
                "Here is the idea put to work. The start is set up, then the same rule "
                "is applied, because that is what the rule is for.",
                limit,
            )
        elif method == "visual":
            text = _trim(
                "Look at the idea as a line of steps. Each step sits after the last, "
                "because the order is the point.",
                limit,
            )
        else:
            text = _trim(
                "The same idea in plainer words. Each part follows the part before it, "
                "because the steps connect.",
                limit,
            )
        kind = "text"
    else:
        if facts:
            statement = str(facts[0]["statement"]).strip()
            if statement.endswith((".", "!", "?")):
                text = _trim(f"{statement} This is why the idea works.", limit)
            else:
                text = _trim(f"{statement}, and that is why this idea works.", limit)
        else:
            text = _trim(
                "Each step follows from the step before it, and that is why this idea works.",
                limit,
            )
        kind = "explain"

    blocks.append({"type": kind, "body": text, "fact_ids": fact_ids, "why": why, "length": _word_count(text)})
    check = _check_block(payload, fact_ids)
    if check is not None:
        blocks.append(check)
    return blocks


def make_e2e_model() -> Callable[..., dict[str, Any]]:
    """The frozen ``model(purpose, system, user, schema, max_tokens) -> dict`` callable."""
    b_fake = _load_b_fake()

    def call(
        purpose: str,
        system: str,
        user: str,
        schema: Mapping[str, Any] | None = None,
        max_tokens: int = 0,
    ) -> dict[str, Any]:
        if purpose in _B_PURPOSES:
            return b_fake(purpose, system, user, schema or {}, max_tokens)
        if purpose in _C_PURPOSES:
            payload = json.loads(user)
            return {"blocks": _lesson_blocks(payload)}
        raise AssertionError(f"the e2e model has no reply for purpose {purpose!r}")

    return call


class FakeAsyncLLM:
    """The async client shape the app's model bridges consume (``services.ai2.llm``)."""

    configured = True
    status = "ok"

    def __init__(self) -> None:
        self.brain = make_e2e_model()
        self.calls: list[str] = []

    async def complete(self, **kwargs: Any) -> Any:
        from adapt.ai.llm import LLMResult

        purpose = str(kwargs.get("purpose") or "")
        self.calls.append(purpose)
        reply = self.brain(
            purpose,
            str(kwargs.get("system") or ""),
            str(kwargs.get("user") or ""),
            kwargs.get("response_spec") or {},
            int(kwargs.get("max_output_tokens") or 0),
        )
        return LLMResult(ok=True, text=json.dumps(reply), data=reply)

    async def aclose(self) -> None:  # pragma: no cover - parity with the real client
        return None
