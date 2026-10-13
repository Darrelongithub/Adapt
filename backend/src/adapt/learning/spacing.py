"""Retrieval strength and spacing: half-life regression.

Algorithm: Probabilistic Guessing Lifetime Regression (PGLR, Settles & Meeder,
KDD 2016) -- a logistic-free, closed-form model of how long a memory lasts:

    log2(half_life) = w . f(features)

Labels come from *observed* outcomes rather than a fixed ladder: after a
successful recall with lag ``L``, the half-life is estimated as ``L/2``; after a
lapse it is estimated as ``2L``. Fitting is ridge regression toward a prior mean,
which keeps a learner with three reviews from getting wild extrapolations.

Expected recall at lag ``d`` hours: ``p = 2 ** (-d / half_life)``. Reviews are
scheduled where ``p`` hits the target (default 0.85, i.e. the "desirable
difficulty" region -- not 0.99, which would mean the material is too easy).

Why not SM-2? SM-2's easiness factor has no probabilistic output; we need a
calibrated probability to compare retention *before and after* an adaptation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Sequence

import numpy as np

DEFAULT_WEIGHTS = (0.916, 0.218, 0.495, -0.257, 0.284, 0.15)  # prior mean (PGLR-ish)
FEATURE_NAMES = (
    "intercept",
    "log1p_reps",
    "log2_lag_prev",
    "difficulty",
    "recent_success",
    "self_explain",
)
MIN_HALF_LIFE_H = 0.25
MAX_HALF_LIFE_H = 24 * 400


def features(
    *,
    reps: int,
    prev_lag_hours: float,
    difficulty: float,
    recent_success: float,
    self_explain: float = 0.0,
) -> np.ndarray:
    return np.array(
        [
            1.0,
            math.log1p(max(0, reps)),
            math.log2(max(0.02, prev_lag_hours) + 1.0),
            float(np.clip(difficulty, 0.0, 1.0)),
            float(np.clip(recent_success, 0.0, 1.0)),
            float(np.clip(self_explain, 0.0, 1.0)),
        ],
        dtype=float,
    )


def expected_recall(half_life_hours: float, lag_hours: float) -> float:
    h = max(MIN_HALF_LIFE_H, float(half_life_hours))
    return float(2.0 ** (-max(0.0, lag_hours) / h))


def lag_for_target(half_life_hours: float, target: float = 0.85) -> float:
    """Hours until expected recall decays to ``target``."""
    t = min(0.999, max(0.05, target))
    return max(0.05, float(half_life_hours) * math.log2(1.0 / t))


#: One review only bounds the half-life; these are the point labels the fit uses.
#: A recall that survived ``lag`` hours means the half-life is *at least* that long,
#: and a lapse at ``lag`` means it was shorter. The multipliers place the label just
#: outside the bound (a hit at lag ~0.85 of the target implies roughly 1.5x), which is
#: the convention that keeps the ridge fit from being trained backwards -- with the
#: factors swapped, the model learns to lengthen intervals after failures.
HIT_LABEL_FACTOR = 1.5
LAPSE_LABEL_FACTOR = 0.5


def label_half_life(lag_hours: float, recalled: bool) -> float:
    """What one review tells us about the half-life of a memory."""
    lag = max(MIN_HALF_LIFE_H, float(lag_hours))
    return max(MIN_HALF_LIFE_H, (HIT_LABEL_FACTOR * lag) if recalled else (LAPSE_LABEL_FACTOR * lag))


@dataclass
class ReviewOutcome:
    reps_before: int
    lag_hours: float
    difficulty: float
    recent_success: float
    self_explain: float = 0.0
    recalled: bool = True

    def as_row(self) -> tuple[np.ndarray, float]:
        f = features(
            reps=self.reps_before,
            prev_lag_hours=self.lag_hours,
            difficulty=self.difficulty,
            recent_success=self.recent_success,
            self_explain=self.self_explain,
        )
        return f, math.log2(label_half_life(self.lag_hours, self.recalled))


@dataclass
class HalfLifeModel:
    """Ridge fit over review outcomes, shrunk toward ``DEFAULT_WEIGHTS``."""

    weights: np.ndarray = field(default_factory=lambda: np.array(DEFAULT_WEIGHTS, dtype=float))
    n: int = 0
    lam: float = 1.0
    rmse_log2: float | None = None

    def predict_hours(self, f: np.ndarray) -> float:
        return float(np.clip(2.0 ** float(np.dot(self.weights, f)), MIN_HALF_LIFE_H, MAX_HALF_LIFE_H))

    def as_dict(self) -> dict[str, object]:
        return {
            "weights": [round(float(w), 4) for w in self.weights],
            "feature_names": list(FEATURE_NAMES),
            "n": self.n,
            "rmse_log2": round(self.rmse_log2, 4) if self.rmse_log2 is not None else None,
            "lam": self.lam,
        }

    @classmethod
    def from_dict(cls, d: dict[str, object]) -> HalfLifeModel:
        w = d.get("weights")
        return cls(
            weights=np.array(w, dtype=float) if w else np.array(DEFAULT_WEIGHTS, dtype=float),
            n=int(d.get("n", 0) or 0),  # type: ignore[arg-type]
            rmse_log2=float(d["rmse_log2"]) if d.get("rmse_log2") is not None else None,  # type: ignore[index]
        )


def fit_half_life(rows: Sequence[tuple[np.ndarray, float]], *, lam: float = 1.0) -> HalfLifeModel:
    """Closed-form ridge regression toward the prior mean (Bayesian ridge)."""
    prior = np.array(DEFAULT_WEIGHTS, dtype=float)
    if len(rows) < 3:
        return HalfLifeModel(weights=prior, n=len(rows), lam=lam)
    F = np.vstack([r[0] for r in rows])
    y = np.array([r[1] for r in rows], dtype=float)
    d = y - F @ prior
    A = F.T @ F + lam * np.eye(F.shape[1])
    delta = np.linalg.solve(A, F.T @ d)
    w = prior + delta
    pred = F @ w
    rmse = float(np.sqrt(np.mean((pred - y) ** 2)))
    return HalfLifeModel(weights=w, n=len(rows), lam=lam, rmse_log2=rmse)


def hierarchical_fit(
    per_learner: dict[str, Sequence[tuple[np.ndarray, float]]],
    *,
    lam_global: float = 1.0,
    lam_local: float = 12.0,
) -> tuple[HalfLifeModel, dict[str, HalfLifeModel]]:
    """Fit one global model, then per-learner models shrunk toward it.

    Returns ``(global_model, {learner_id: model})``. With too few local
    observations the global model is returned unchanged for that learner -- an
    honest "we don't know yet" rather than a fabricated personal curve.
    """
    all_rows: list[tuple[np.ndarray, float]] = []
    for rows in per_learner.values():
        all_rows.extend(rows)
    gmodel = fit_half_life(all_rows, lam=lam_global)
    locals_: dict[str, HalfLifeModel] = {}
    for learner, rows in per_learner.items():
        if len(rows) < 5:
            locals_[learner] = HalfLifeModel(weights=gmodel.weights, n=len(rows), lam=lam_local)
        else:
            locals_[learner] = fit_half_life(rows, lam=lam_local)
    return gmodel, locals_


@dataclass
class Schedule:
    half_life_hours: float
    due_at: datetime
    expected_recall_at_due: float
    reps: int
    strength: float  # 1/half_life, normalised for display
    rationale: str

    def as_dict(self) -> dict[str, object]:
        return {
            "half_life_hours": round(self.half_life_hours, 3),
            "due_at": self.due_at.isoformat(),
            "expected_recall_at_due": round(self.expected_recall_at_due, 4),
            "reps": self.reps,
            "strength": round(self.strength, 4),
            "rationale": self.rationale,
        }


def next_schedule(
    *,
    model: HalfLifeModel,
    reps_before: int,
    prev_lag_hours: float,
    difficulty: float,
    recent_success: float,
    self_explain: float,
    recalled: bool,
    now: datetime,
    target: float = 0.85,
) -> Schedule:
    f = features(
        reps=reps_before,
        prev_lag_hours=prev_lag_hours,
        difficulty=difficulty,
        recent_success=recent_success,
        self_explain=self_explain,
    )
    h = model.predict_hours(f)
    lag = lag_for_target(h, target)
    return Schedule(
        half_life_hours=h,
        due_at=now + timedelta(hours=lag),
        expected_recall_at_due=expected_recall(h, lag),
        reps=reps_before + 1,
        strength=1.0 / max(MIN_HALF_LIFE_H, h),
        rationale=(
            f"half-life {h:.1f}h from {model.n} logged reviews; next retrieval at p\u2248{target:.2f}"
            + (" (lapse: interval shortened)" if not recalled else "")
        ),
    )
