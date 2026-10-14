"""The content engine (§7): concept breakdown → variants → questions → validation → cache.

Phase 4's job, in the brief's words, is to "replace 'retrieve a paragraph and slice it' with a
pipeline that works for any subject". The pieces:

* :mod:`~adapt.content.symbolic` — exact algebra/calculus checking, so a maths key is verified by
  computing (§7.3).
* :mod:`~adapt.content.breakdown` — concepts, prerequisites, misconceptions and objectives for a
  topic, taken from authored material (§7.1).
* :mod:`~adapt.content.pack` — one concept's three teaching variants, its matched question pair, its
  graded hints and its three-point checklist (§7.2).
* :mod:`~adapt.content.validation` — the mandatory code check before any learner sees an item (§7.3).
* :mod:`~adapt.content.pipeline` — generate, validate, cache by content hash, log every rejection
  (§7.6, and §7.3's "log every rejection with a reason").

The gates that keep this honest live in ``scripts/teaching_quality_eval.py`` and
``backend/tests/test_content_engine.py``: both drive the same pipeline, so a content regression fails
the build and the report rather than only one of them.
"""

from __future__ import annotations

from .breakdown import BREAKDOWN_SPEC, Breakdown, Concept, breakdown_for, concepts_for, topic_spec
from .pack import PACK_SPEC, build_concept_pack, build_pack
from .pipeline import PIPELINE_SPEC, Generated, build_and_store, generate
from .symbolic import SymbolicError, check_answer, differentiate, equivalent, evaluate, solve
from .validation import GATE_CODES, validate_item, validate_pack

__all__ = [
    "BREAKDOWN_SPEC",
    "Breakdown",
    "Concept",
    "Generated",
    "GATE_CODES",
    "PACK_SPEC",
    "PIPELINE_SPEC",
    "SymbolicError",
    "breakdown_for",
    "build_and_store",
    "build_concept_pack",
    "build_pack",
    "check_answer",
    "concepts_for",
    "differentiate",
    "equivalent",
    "evaluate",
    "generate",
    "solve",
    "topic_spec",
    "validate_item",
    "validate_pack",
]
