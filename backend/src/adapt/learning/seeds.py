"""Stable seeds: one place, so two features cannot disagree about "the same draw".

``hash()`` on a ``str`` is salted per interpreter (PYTHONHASHSEED), so anything
seeded with it produced a different answer on every restart: the diagnostic was
"randomised per learner, reproducible for the same learner" and was not
reproducible at all. BLAKE2b is stable across processes, platforms and versions,
which is the only property needed here; it is not used for anything secret.
"""

from __future__ import annotations

import hashlib


def stable_seed(*parts: object) -> int:
    """An int derived from strings/numbers, identical in every process."""
    digest = hashlib.blake2b(
        "\x1f".join("" if p is None else str(p) for p in parts).encode("utf-8"),
        digest_size=8,
    ).digest()
    return int.from_bytes(digest, "big")
