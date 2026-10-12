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
    from adapt.ai.ai3 import Answer

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
