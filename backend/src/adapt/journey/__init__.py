"""The learner's path through the app, as *state* rather than as a set of screens.

Round 2, Part A of the brief replaced "six separate destinations" with one path:
account → preferences → mind games → subject → subject round → snapshot → first
lesson → practice. This package owns that path's stage list and its persisted
state; the screens that do the work at each stage stay where they already are.

Two decisions are worth stating here, because they are the whole reason this is a
package and not a column on ``students``:

* **The order is code, and there is one copy of it.** :data:`~adapt.journey.stages.STAGES`
  is the sequence, the progress bar and the resume rule. A learner's place is
  derived from it, never stored as a cursor -- the same rule ``baseline/flow.py``
  follows, for the same reason: a stored cursor can disagree with the data.
* **A skip is a first-class answer.** Every stage can be skipped, and a skipped
  stage is stored as ``skipped`` and reported as *not measured* by everything
  downstream (which is already how the baseline, the subject round and the
  snapshot phrase an absent measurement). Nothing here invents a value for a rule
  that was waved through.

What the package does *not* own: the games, the questions, the lessons, the
snapshot. Those live in ``baseline/``, ``subjects/``, ``ai/`` and the routes that
serve them. This package only ever reads their rows and points at their screens.
"""

from __future__ import annotations

from .models import JourneyStage
from .stages import STAGE_BY_ID, STAGE_IDS, STAGES
from .state import advance, payload, statuses

__all__ = [
    "STAGES",
    "STAGE_BY_ID",
    "STAGE_IDS",
    "JourneyStage",
    "advance",
    "payload",
    "statuses",
]
