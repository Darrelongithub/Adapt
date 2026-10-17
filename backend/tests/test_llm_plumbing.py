"""Wiring between the AI modules and the rest of the app, tested without an API key.

Every LLM call path is skipped in CI because ``conftest.py`` pops the Gemini key: the app
is designed to fall back to local rules, which is the right product decision and the wrong
testing one. The consequence is that a call site can be broken in a way no test reaches --
and five of them were.

``redact_payload`` returns ``(sanitized, dropped, kinds)``. Five call sites unpacked two:

    safe, dropped = redact_payload({"q": user}, max_chars=7000)

which raises ``ValueError: too many values to unpack (expected 2)`` on the first request
that reaches it. Every LLM path in the app -- proposals (AI 1), the tutor reply, the
teach-back commentary, generated practice -- would have raised the moment a key was
configured, and nothing in 195 tests would have noticed. The first test here is the one
that notices; the second exercises the path that a learner actually sees.
"""

from __future__ import annotations

import ast
from pathlib import Path

from sqlalchemy import select

from adapt.security.redact import redact_payload

SRC = Path(__file__).resolve().parents[1] / "src" / "adapt"


def _redaction_unpacking_sites() -> list[tuple[str, int, int]]:
    """Every assignment from ``redact_payload(...)``: file, line, names unpacked."""
    found: list[tuple[str, int, int]] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            value = node.value
            if not (isinstance(value, ast.Call) and isinstance(value.func, ast.Name)):
                continue
            if value.func.id != "redact_payload":
                continue
            target = node.targets[0] if len(node.targets) == 1 else None
            count = len(target.elts) if isinstance(target, ast.Tuple) else 0
            found.append((str(path.relative_to(SRC.parent.parent)), node.lineno, count))
    return found


def test_redaction_call_sites_unpack_the_whole_contract():
    sites = _redaction_unpacking_sites()
    assert sites, "no call sites found -- this test has stopped testing anything"
    wrong = [(path, line, n) for path, line, n in sites if n != 3]
    assert not wrong, (
        "redact_payload returns (payload, dropped, kinds); these unpacked "
        f"something else, which raises on the first request that reaches them: {wrong}"
    )


def _llm_result_attributes_read_by_call_sites() -> list[tuple[str, int, str]]:
    """Every ``X.attr`` where ``X`` was assigned the result of ``<client>.complete(...)``.

    Same shape of check as the redaction one, for the same reason: a call site reading
    ``result.token_count`` (a field that does not exist) is invisible until a key is
    configured, because the line only runs when a model answered. Typos in a dataclass
    attribute are the second way a model path breaks on contact.
    """
    from dataclasses import fields

    from adapt.ai.llm import LLMResult

    # Fields *and* methods: a call site may legitimately ask a result for `as_dict()`, and a
    # test that flagged every method call would be ignored by the next person who ran it.
    known = {f.name for f in fields(LLMResult)} | {
        n for n in dir(LLMResult) if not n.startswith("_")
    }
    offenders: list[tuple[str, int, str]] = []
    for path in sorted(SRC.rglob("*.py")):
        if path.name == "llm.py":
            continue  # the client builds its own results; its field names are the definition
        tree = ast.parse(path.read_text(), filename=str(path))
        results = []
        for node in ast.walk(tree):
            target = None
            value = None
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target, value = node.targets[0], node.value
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                target, value = node.target, node.value
            if not isinstance(target, ast.Name) or not isinstance(value, ast.Await):
                continue
            call = value.value
            if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)):
                continue
            if call.func.attr != "complete":
                continue
            results.append((target.id, node.lineno))
        if not results:
            continue
        names = {name for name, _line in results}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute) or not isinstance(node.value, ast.Name):
                continue
            if node.value.id in names and node.attr not in known:
                offenders.append(
                    (str(path.relative_to(SRC.parent.parent)), node.lineno, f"{node.value.id}.{node.attr}")
                )
    assert known, "LLMResult has no fields -- this test has stopped testing anything"
    return offenders


def test_no_call_site_reads_a_result_field_that_does_not_exist():
    offenders = _llm_result_attributes_read_by_call_sites()
    assert not offenders, (
        "`LLMResult` has no such attribute, and the line only runs when a model answered, so "
        f"no keyless CI will ever notice it: {offenders}"
    )


#: Fields whose value is only meaningful on a call that succeeded. Reading one without
#: reading ``ok`` is the mistake this guards, and it is the mistake the client used to make on
#: the caller's behalf by answering ``ok=True`` with an empty ``text``.
TRUST_ONLY_WHEN_OK = {"text", "data"}

#: ``name`` -> file, for sites where reading the payload *is* the check (the CLI prints
#: whatever happened, including the failure, and has no fallback to skip).
ALLOWED_UNGUARDED = {"cli.py"}


def _payload_reads_without_an_ok_check() -> list[tuple[str, int, str]]:
    """Functions that read ``res.text``/``res.data`` while never reading ``res.ok``."""
    found: list[tuple[str, int, str]] = []
    for path in sorted(SRC.rglob("*.py")):
        if path.name == "llm.py" or path.name in ALLOWED_UNGUARDED:
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            results = set()
            for inner in ast.walk(node):
                if not isinstance(inner, ast.Assign) or len(inner.targets) != 1:
                    continue
                target, value = inner.targets[0], inner.value
                if not isinstance(target, ast.Name):
                    continue
                call = value.value if isinstance(value, ast.Await) else value
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) and call.func.attr == "complete":
                    results.add(target.id)
            if not results:
                continue
            reads: set[str] = set()
            payload_lines: list[int] = []
            for inner in ast.walk(node):
                if isinstance(inner, ast.Attribute) and isinstance(inner.value, ast.Name) and inner.value.id in results:
                    reads.add(inner.attr)
                    if inner.attr in TRUST_ONLY_WHEN_OK:
                        payload_lines.append(inner.lineno)
                elif (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Name)
                    and inner.func.id == "getattr"
                    and len(inner.args) >= 2
                    and isinstance(inner.args[0], ast.Name)
                    and inner.args[0].id in results
                    and isinstance(inner.args[1], ast.Constant)
                ):
                    # `getattr(res, "ok", False)` is the same check, spelled for a client that
                    # is whatever the container injected. Counting only `res.ok` would push
                    # every duck-typed call site to add a second, redundant check.
                    reads.add(str(inner.args[1].value))
            if payload_lines and "ok" not in reads:
                for line in payload_lines:
                    found.append((str(path.relative_to(SRC.parent.parent)), line, sorted(reads)[0]))
    return found


def test_a_model_payload_is_never_trusted_without_the_success_check():
    """One rule for every call site: the payload is only worth reading when ``ok`` was read too.

    The client is now honest that ``ok=True`` means "there is an answer", which makes the
    caller's half of that deal worth pinning: a site that forgets to check inherits whatever
    the failure was, and a lesson prints it.
    """
    offenders = _payload_reads_without_an_ok_check()
    assert not offenders, (
        "these read a model's text/data without checking `ok`, so a refusal, a block or a "
        f"quota miss is displayed as an answer: {offenders}"
    )


def test_the_redactor_reports_what_it_removed_and_not_only_how_much():
    safe, dropped, kinds = redact_payload(
        {"q": "email me at rowan@example.test", "display_name": "Rowan"}
    )
    assert dropped >= 1 and kinds, "a dropped identity field must say which one"
    assert "rowan@example.test" not in safe["q"]
    # An identity key is removed outright rather than blanked, and the kinds say which.
    assert "display_name" not in safe
    assert any("display_name" in kind for kind in kinds), kinds


class _StubLLM:
    """The provider's shape, returning whatever the caller's prompt expects."""

    def __init__(self, data: dict) -> None:
        self.configured = True
        self.data = data

    async def complete(self, **_: object):
        from adapt.ai.llm import LLMResult

        return LLMResult(ok=True, data=self.data, model="stub", status="ok", latency_ms=1)


async def test_teachback_commentary_survives_a_configured_model(client, services, learner):
    """The path that grades in a learner's own words, with a model actually configured.

    It was one of the five broken call sites, and it is the one where a crash is most
    visible: the learner has just written a paragraph and the reply is a 500.
    """
    from adapt.db.models import Item

    with services.scope() as s:
        rubric = s.execute(
            select(Item.rubric).where(Item.kind == "teachback", Item.rubric.is_not(None)).limit(1)
        ).scalar()
    rubric = rubric or {
        "required_concepts": [{"concept": "multiplication before addition", "keywords": ["multiplication"]}]
    }

    services.ai2.llm = _StubLLM({"verdict": "mostly right", "right_first": "You named the rule."})
    result = await services.ai2.review_teachback(
        learner["student_id"],
        {"kind": "teachback", "rubric": rubric},
        "You do the multiplication before the addition.",
    )
    assert isinstance(result, dict)
    assert "score" in result and 0.0 <= float(result["score"]) <= 1.0


async def test_a_scene_is_authored_and_narrated_with_a_model_configured(services, learner):
    """The engine's scene paths, driven with a model configured.

    Authoring a spec and narrating a frame both sit *after* a no-model early return, so the
    fallback every keyless run takes keeps these lines out of reach -- which is how three of
    them shipped unpacking two of ``redact_payload``'s three values. A stub model reaches the
    line without a key. The contract asserted is the one the learner sees: with a model that
    answers nothing useful, the lesson still plays and says why, rather than 500ing.
    """
    from adapt.ai.scene_narrator import Answer

    services.ai2.llm = _StubLLM({})
    authored = await services.ai2.author_scene(
        request="binary search over a sorted list", kind="binary_search", student_id=learner["student_id"]
    )
    # A model that answers nothing usable is not a reason to have no lesson: the built-in
    # scene is used and labelled as such.
    assert authored["ok"] is True and authored["source"] == "builtin", authored
    assert authored["draft"]["kind"] == "binary_search"

    services.ai3.llm = _StubLLM({})
    answer = await services.ai3.why(
        spec=authored["draft"],
        frame_index=0,
        question="why did we halve the range?",
        student_id=learner["student_id"],
    )
    assert isinstance(answer, Answer)
    assert answer.text.strip(), "a frame with no usable model text still has to say something"
    assert answer.source == "template" and answer.ok is False
