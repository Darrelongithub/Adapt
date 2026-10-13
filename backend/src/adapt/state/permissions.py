"""Who is allowed to write what — the matrix from round brief §2A, enforced in code.

The brief states it as a table:

| module | writes | reads |
| --- | --- | --- |
| code (scoring, ingestion) | evidence log, scores | everything |
| AI 1 | learner model versions, hypotheses, proposals, test selection | evidence, preferences, content |
| AI 2 | content registry | learner model snapshot, teaching memory |
| AI 3 | lesson plans, deliveries, teaching memory | learner model snapshot, content, preferences |

A table in a document is a table in a document. This module is the same matrix as data, and
:func:`check_write` is called by the store before every write, so a module that reaches for a
domain it does not own gets a :class:`WriteNotPermitted` naming the caller rather than a row it
should not have.

Two decisions worth stating plainly:

* The domains are **named after the kind of fact**, not after the SQL table. A table name can be
  renamed or split; "the content registry" is a promise about ownership, and ownership is what the
  matrix is about.
* ``code`` includes the ingestion path, scoring, and the store's own bookkeeping. It is the only
  writer that may touch the evidence log, because the evidence log is the record of what happened —
  nothing that *interprets* the record may also edit it.
"""

from __future__ import annotations

from typing import Iterable, Mapping

#: Every module that writes to the store. ``code`` is not an AI: it is the deterministic layer
#: (scoring, ingestion, the store itself).
MODULES: tuple[str, ...] = ("code", "ai1", "ai2", "ai3")

#: The domains of the store.
DOMAINS: tuple[str, ...] = (
    "evidence",  # the append-only log: events, attempts, game results, method assignments
    "scores",  # the graded outcome attached to an attempt
    "learner_model",  # versioned derived model (BKT, factors, subject maps, ladder positions, posteriors)
    "hypotheses",
    "proposals",
    "approvals",  # the learner's own decision on a proposal
    "content",  # the content registry
    "plans",  # lesson plans
    "deliveries",  # what was actually shown, and why
    "teaching_memory",
    "preferences",  # stated, not measured — the learner's, so only the learner (via code) writes
    "session_state",
)

#: The matrix. Read access is total by design (every module builds from one consistent snapshot);
#: write access is not.
#:
#: ``code`` includes ``content`` for exactly one operation: attaching or withdrawing the validation
#: verdict on a pack. The brief's rule is "the AI proposes, code decides", and content is the one
#: domain where a proposal can be *rejected outright* — so the verdict cannot live in AI 2's own
#: hands. The narrowing is enforced in the store, not by convention: ``put_content`` refuses
#: ``code`` (the code layer cannot author content) and ``set_validation`` accepts nothing else (an
#: AI cannot bless its own content). Both directions are tested.
WRITE_MATRIX: Mapping[str, frozenset[str]] = {
    "code": frozenset({"evidence", "scores", "approvals", "preferences", "session_state", "content"}),
    "ai1": frozenset({"learner_model", "hypotheses", "proposals"}),
    # AI 2 owns the content registry and nothing else: it is the module that teaches, and a
    # teacher that can edit the record of what happened is not a record.
    "ai2": frozenset({"content"}),
    "ai3": frozenset({"plans", "deliveries", "teaching_memory"}),
}


class WriteNotPermitted(PermissionError):
    """Raised when a module tries to write a domain the matrix does not give it."""

    def __init__(self, module: str, domain: str) -> None:
        allowed = ", ".join(sorted(WRITE_MATRIX.get(module, frozenset()))) or "nothing"
        super().__init__(
            f"module {module!r} may not write {domain!r}; the write-permission matrix gives it: {allowed}"
        )
        self.module = module
        self.domain = domain


def may_write(module: str, domain: str) -> bool:
    return domain in WRITE_MATRIX.get(module, frozenset())


def may_read(module: str, domain: str) -> bool:  # noqa: ARG001 - read access is total, by design
    return domain in DOMAINS


def check_write(module: str, domain: str) -> None:
    """Raise unless ``module`` owns ``domain``. Called by every store write."""
    if module not in WRITE_MATRIX:
        raise WriteNotPermitted(module, domain)
    if not may_write(module, domain):
        raise WriteNotPermitted(module, domain)


def matrix() -> dict[str, dict[str, list[str]]]:
    """The matrix as data, for docs and for the console (`adapt status --matrix`)."""
    return {
        module: {
            "writes": sorted(WRITE_MATRIX.get(module, frozenset())),
            "reads": sorted(DOMAINS),
        }
        for module in MODULES
    }


def violations(calls: Iterable[tuple[str, str]]) -> list[tuple[str, str]]:
    """Which of these (module, domain) write attempts the matrix refuses — a test helper."""
    return [(m, d) for m, d in calls if not may_write(m, d)]


__all__ = [
    "DOMAINS",
    "MODULES",
    "WRITE_MATRIX",
    "WriteNotPermitted",
    "check_write",
    "matrix",
    "may_read",
    "may_write",
    "violations",
]
