"""The Learner State Store: one source of truth, typed, versioned, permission-checked.

Round brief §2A. The store is the only thing the three intelligences share:

* ``LearnerState`` — the typed service layer (evidence log, versioned model, content registry,
  teaching memory, plans, deliveries, session state).
* ``LearnerSnapshot`` — the **immutable** view a module builds from. A lesson is planned from one
  consistent view, and the view says which ``model_version`` it is.
* ``permissions`` — the write-permission matrix, enforced on every write.
* ``Orchestrator`` — the deterministic turn cycle (ingest → model → content → plan → deliver), with
  mid-lesson re-planning that does not restart the lesson.

Nothing in this package imports an AI module. The AIs import *this* (and the orchestrator imports
the AIs), which is the direction the brief asks for: no AI module imports another AI module.
"""

from .model import build_model
from .orchestrator import Orchestrator, Turn
from .permissions import (
    DOMAINS,
    MODULES,
    WRITE_MATRIX,
    WriteNotPermitted,
    check_write,
    matrix,
    may_read,
    may_write,
    violations,
)
from .snapshot import LearnerSnapshot, TopicMastery, freeze, thaw
from .store import (
    WEIGHT_CONTROLLED,
    WEIGHT_OBSERVATIONAL,
    EvidenceEvent,
    IngestResult,
    LearnerState,
    ModelConflict,
)

__all__ = [
    "DOMAINS",
    "MODULES",
    "WRITE_MATRIX",
    "EvidenceEvent",
    "IngestResult",
    "LearnerSnapshot",
    "LearnerState",
    "ModelConflict",
    "Orchestrator",
    "TopicMastery",
    "Turn",
    "WEIGHT_CONTROLLED",
    "WEIGHT_OBSERVATIONAL",
    "WriteNotPermitted",
    "build_model",
    "check_write",
    "freeze",
    "matrix",
    "may_read",
    "violations",
    "may_write",
    "thaw",
]
