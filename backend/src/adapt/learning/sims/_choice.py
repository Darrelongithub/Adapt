"""Ordering the options a learner is offered.

A prompt whose correct answer is always first is not a question, it is a habit. The order has
to be *deterministic* though -- the same spec replayed after a restart must show the same
buttons, or a resumed lesson disagrees with the one the learner left, and the trace stops being
reproducible for the tests. So: hash the kind, the frame and the option text, sort by that.
"""

from __future__ import annotations

import hashlib
from typing import Iterable


def order_choices(kind: str, frame_index: int, choices: Iterable[object]) -> list[str]:
    seen = {str(c) for c in choices if c is not None and str(c).strip()}
    return sorted(seen, key=lambda c: hashlib.sha256(f"{kind}|{frame_index}|{c}".encode()).hexdigest())
