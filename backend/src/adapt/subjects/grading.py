"""Server-held scoring for interactive subject tests.

A plan needs two different representations of each item: rendering parameters for
the browser and the complete item for scoring.  The complete item travels only
inside an AES-GCM token bound to the learner, run and item.  The browser therefore
never receives a plaintext answer key, while a refresh can still resume without a
second plaintext answer store.
"""

from __future__ import annotations

import base64
import json
from typing import Any, Mapping

from ..crypto.fields import get_cipher

TOKEN_CONTEXT = "subject-test-grade-v1"
#: Fields which identify or explain the correct response. Prompt data (including
#: the number to place on a number line and the words to memorise) remains visible.
GRADING_FIELDS = frozenset(
    {"answer", "answer_index", "answer_indices", "answer_region", "pairs", "target_cells", "why"}
)


def _parts(value: Any) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def _option_answer(parameters: Mapping[str, Any], *, option_rows: bool = False) -> str | None:
    raw = str(parameters.get("options") or "")
    options = raw.split("|") if option_rows else _parts(raw)
    try:
        index = int(parameters.get("answer_index"))
    except (TypeError, ValueError):
        return None
    if not 0 <= index < len(options):
        return None
    if option_rows:
        return ",".join(_parts(options[index]))
    return str(options[index]).strip()


def score_answer(item: Mapping[str, Any], answer: str | None) -> bool:
    """Grade the wire-format answer produced by the corresponding renderer."""
    item_type = str(item.get("item_type") or "")
    parameters = item.get("parameters")
    if not isinstance(parameters, Mapping):
        return False
    candidate = str(answer or "").strip()

    if item_type in {"pattern_pick", "odd_one_out", "geometry_angle", "fraction_bar"}:
        expected = _option_answer(parameters)
        return expected is not None and candidate == expected
    if item_type == "geometry_congruent":
        expected = _option_answer(parameters, option_rows=True)
        return expected is not None and _parts(candidate) == _parts(expected)
    if item_type == "cloze_word_bank":
        raw = dict(parameters)
        raw["options"] = raw.get("bank")
        expected = _option_answer(raw)
        return expected is not None and candidate == expected
    if item_type == "drag_order":
        expected_order = _parts(parameters.get("answer"))
        actual_order = [part.strip() for part in candidate.split(">") if part.strip()]
        return bool(expected_order) and actual_order == expected_order
    if item_type == "drag_match":
        expected: dict[str, str] = {}
        for pair in _parts(parameters.get("pairs")):
            left, separator, right = pair.partition(":")
            if separator and left.strip() and right.strip():
                expected[left.strip()] = right.strip()
        actual: dict[str, str] = {}
        for pair in _parts(candidate):
            left, separator, right = pair.partition("=")
            if separator and left.strip():
                actual[left.strip()] = right.strip()
        return bool(expected) and actual == expected
    if item_type == "truth_table":
        expected_cells = _parts(parameters.get("target_cells"))
        return bool(expected_cells) and _parts(candidate) == expected_cells
    if item_type == "venn_region":
        expected_region = str(parameters.get("answer_region") or "")
        return bool(expected_region) and candidate == expected_region
    if item_type == "number_line":
        try:
            value = float(parameters.get("value"))
            marked = float(candidate)
            tolerance = float(parameters.get("tolerance", 8))
        except (TypeError, ValueError):
            return False
        return abs(marked - value) <= tolerance
    if item_type == "quick_recall":
        choices = _parts(parameters.get("choices"))
        try:
            indices = [int(value) for value in _parts(parameters.get("answer_indices"))]
        except ValueError:
            return False
        if not indices or any(not 0 <= index < len(choices) for index in indices):
            return False
        return set(_parts(candidate)) == {choices[index] for index in indices}
    return False


def issue_grading_token(
    item: Mapping[str, Any], *, student_id: str, run_id: str, item_id: str, subject_id: str | None = None
) -> str:
    cipher = get_cipher()
    if cipher is None:
        raise RuntimeError("test grading requires the configured field cipher")
    payload = {
        "student_id": student_id,
        "run_id": run_id,
        "item_id": item_id,
        "subject_id": subject_id,
        "item": dict(item),
    }
    plaintext = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    encrypted = cipher.encrypt(plaintext, context=TOKEN_CONTEXT)
    return base64.urlsafe_b64encode(encrypted).decode("ascii").rstrip("=")


def read_grading_token(
    token: str, *, student_id: str, run_id: str, item_id: str
) -> dict[str, Any]:
    """Authenticate and unwrap one token, rejecting cross-learner/run reuse."""
    cipher = get_cipher()
    if cipher is None:
        raise ValueError("test grading is unavailable")
    try:
        padded = token + "=" * (-len(token) % 4)
        encrypted = base64.b64decode(padded.encode("ascii"), altchars=b"-_", validate=True)
        payload = json.loads(cipher.decrypt(encrypted, context=TOKEN_CONTEXT))
    except Exception as exc:
        raise ValueError("invalid grading token") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("item"), dict):
        raise ValueError("invalid grading token")
    if (payload.get("student_id"), payload.get("run_id"), payload.get("item_id")) != (student_id, run_id, item_id):
        raise ValueError("grading token does not belong to this attempt")
    return payload


def public_item(
    item: Mapping[str, Any], *, student_id: str, run_id: str, item_id: str, subject_id: str | None = None
) -> dict[str, Any]:
    parameters = item.get("parameters")
    visible = {
        key: value
        for key, value in dict(parameters if isinstance(parameters, Mapping) else {}).items()
        if key not in GRADING_FIELDS
    }
    return {
        "id": item_id,
        "run_id": run_id,
        "item_type": item.get("item_type"),
        "skill": item.get("skill"),
        "difficulty": item.get("difficulty"),
        "parameters": visible,
        "grading_seal": issue_grading_token(
            item, student_id=student_id, run_id=run_id, item_id=item_id, subject_id=subject_id
        ),
    }


__all__ = [
    "GRADING_FIELDS",
    "issue_grading_token",
    "public_item",
    "read_grading_token",
    "score_answer",
]
