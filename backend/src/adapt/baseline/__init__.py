"""The universal mind baseline (round brief section 5).

Six games, scored entirely in code, that produce an eight-factor snapshot with ranges and sample
sizes — or honest "not measured" states — plus the levers AI 3 consumes.

* :mod:`adapt.baseline.games` — deterministic task generation and scoring for every game.
* :mod:`adapt.baseline.flow` — the fixed stage order, and what "next" means.
* :mod:`adapt.baseline.factors` — the eight factors, their plain-language statements, the levers.
* :mod:`adapt.baseline.service` — the run lifecycle the API calls.

Nothing here imports an AI module, and nothing here calls a model: the brief's rule for this phase
is that the scoring path is deterministic, and a measurement that changed between two samples would
not be a measurement.
"""

from .factors import FACTORS, FACTOR_TITLES, MIN_N, Factor, build_factors, levers, snapshot_payload
from .flow import STAGES, STAGE_KEYS, Step, next_step, stage_report
from .games import Trial, seed_of
from .service import FORMAT_OPTIONS, QUESTIONS, Baseline, BaselineError

__all__ = [
    "FACTORS",
    "FACTOR_TITLES",
    "FORMAT_OPTIONS",
    "MIN_N",
    "QUESTIONS",
    "STAGES",
    "STAGE_KEYS",
    "Baseline",
    "BaselineError",
    "Factor",
    "Step",
    "Trial",
    "build_factors",
    "levers",
    "next_step",
    "seed_of",
    "snapshot_payload",
    "stage_report",
]
