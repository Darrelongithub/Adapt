"""Curriculum scan + syllabus approval gate (Round 9, Track N).

External sources are used for exactly one thing: finding out what a
curriculum requires. The scanner produces an ordered objective list with
an honest source label; nothing downstream (test plan, lesson skeleton,
lessons) may run for a learner-chosen subject until the learner has
approved that list — the approved list is the guideline, versioned and
kept in the encrypted store.
"""

from .scanner import CURRICULA, ScanResult, scan, simple_objectives, validate_objectives

__all__ = ["CURRICULA", "ScanResult", "scan", "simple_objectives", "validate_objectives"]
