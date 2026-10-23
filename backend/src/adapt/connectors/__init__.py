"""Optional external connectors (Round 9, Track P).

Every connector here is **off by default** and exists for exactly one job:
finding out what a curriculum requires (Oak, Wikimedia outlines) or offering
general wellbeing reading after a hard check-in (Psychologie et Sérénité).
None of them writes lessons, scores learners, or receives learner data —
queries carry a subject or a theme, never a name, an answer, or a check-in
note. Verification notes (what was tested live, when, and what was skipped)
live in ``docs/CONNECTORS.md``.
"""

from .registry import SPECS, ConnectorSpec, connector_status, health_check

__all__ = ["SPECS", "ConnectorSpec", "connector_status", "health_check"]
