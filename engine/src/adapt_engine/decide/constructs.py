"""The closed vocabularies `decide` is allowed to speak.

Three sets, each closed on purpose, because every one of them is a promise:

* :data:`CONSTRUCTS` — the constructs this build has evidence claims for. A
  claim about anything else is not "a claim we don't cite yet", it is a claim
  nobody gated, and `claims.construct` refuses it at write time (G3).
* :data:`ABSTENTION_REASONS` — the six codes from `METRICS.md` §1.3. An
  abstention names exactly one primary reason, and it may name contributing
  ones. A reason outside this set is a 500 in review, not a new word at runtime.
* :data:`SERVED_REASONS` — why a *served* decision was served. Not in the
  metrics doc, because the doc enumerates abstentions; a served decision still
  has to say which rule fired, or a customer cannot audit the advice.

Nothing here is a model. They are the words, and the words are the contract.
"""

from __future__ import annotations

from typing import Final

#: Constructs with a gated claim set at M1 (L1). Mirrors the CHECK on
#: `claims.construct`; `tests/test_decide.py` asserts the two agree by trying to
#: write a claim outside this set and watching the database refuse it.
CONSTRUCTS: Final = (
    "spacing",
    "retrieval_practice",
    "worked_examples",
    "desirable_difficulty",
    "interleaving",
)

#: `METRICS.md` §1.3, in the doc's order. Five are reachable in `decide` v1;
#: `consent_withheld` needs a consent store, which does not exist yet.
ABSTENTION_REASONS: Final = (
    "insufficient_evidence",
    "low_confidence",
    "conflicting_signals",
    "consent_withheld",
    "data_quality",
    "unsupported_request",
)

#: The reason codes a served decision can carry.
SERVED_REASONS: Final = (
    "consecutive_errors",
    "high_mastery",
    "mastery_band_mid",
    "low_mastery",
)

#: The four actions of `AGENT_M1.md` §4 — the closed action set at L1.
ACTIONS: Final = (
    "next_item_difficulty",
    "spaced_review",
    "worked_example",
    "abstain",
)

#: Above this interval width we know too little to choose between actions, even
#: though the evidence floor is met. 0.45 is wide enough to hold a real
#: mid-mastery learner at twenty trials and tight enough that a
#: `next_item_difficulty` choice is defensible.
MAX_INTERVAL_WIDTH: Final = 0.45

#: Share of the learner's recent trials carrying validity flags above which the
#: evidence is a problem with the host's instrumentation, not with the learner.
MAX_FLAGGED_SHARE: Final = 0.30

#: The success rate the difficulty rule aims at (`AGENT_M1.md` §4: 70-85%).
TARGET_SUCCESS: Final = 0.78

#: How long a decision's advice stays valid. Fifteen minutes for item-level
#: advice: long enough for a host to render it, short enough that a stale plan
#: cannot be served after the learner has done something else.
DECISION_TTL_SECONDS: Final = 900

#: The default spacing interval when mastery is high. There is no retention model
#: at L1 — that is L2 — so this is a documented constant cited to the spacing
#: claim, not a prediction dressed up as one.
DEFAULT_REVIEW_DAYS: Final = 7
