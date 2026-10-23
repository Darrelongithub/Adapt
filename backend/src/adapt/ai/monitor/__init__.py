"""AI 1's monitor: the 10-second check-in (Round 9, Track M).

A structured learner-state estimate built from published learning-science
constructs implemented as code-owned scales (see ``docs/FRAMEWORK.md``):
Paas's single-item mental-effort rating for cognitive load, Pekrun's
achievement emotions for the feeling vocabulary, and behavioural signals
computed from real evidence rows. A model, when configured, only phrases
the summary and nudges enum fields — every field is re-validated by code,
the recommended action is re-derived by a code rule, and the learner's
free-text note is never sent to a model at all.
"""

from .checkin import (
    ACTIONS,
    BANDS,
    CONTEXTS,
    EMOTIONS,
    HELP_CHOICES,
    RECORDED_FIELDS,
    CheckInError,
    behavioural_metrics,
    normalise_tiles,
    recommended_action,
    simple_estimate,
    validate_model_estimate,
)

__all__ = [
    "ACTIONS",
    "BANDS",
    "CONTEXTS",
    "EMOTIONS",
    "HELP_CHOICES",
    "RECORDED_FIELDS",
    "CheckInError",
    "behavioural_metrics",
    "normalise_tiles",
    "recommended_action",
    "simple_estimate",
    "validate_model_estimate",
]
