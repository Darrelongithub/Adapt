"""Did the adaptation actually help? Non-parametric effect estimation.

AI 1's proposals are experiments, so each one needs a pre/after comparison that a
sceptical reader would accept:

* paired or unpaired mean difference on the pre-registered metric,
* percentile bootstrap confidence interval (no normality assumption -- quiz scores
  are bounded and skewed),
* Cohen's d with a small-sample correction (Hedges' g),
* a region-of-practical-equivalence (ROPE): effects inside +/-0.1 sd are "no
  practical change", and
* an explicit ``insufficient_evidence`` verdict when n is too small, so the system
  says "we do not know yet" instead of spinning noise into a conclusion.

The retention half of the same question lives in :func:`retention_calibration`
(Brier score + calibration error of predicted vs actual recall).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

ROPE_SD = 0.10
MIN_GROUP_N = 5


def _rng(seed: int | None) -> np.random.Generator:
    return np.random.default_rng(seed if seed is not None else 20260101)


@dataclass
class Effect:
    baseline_n: int
    after_n: int
    baseline_mean: float | None
    after_mean: float | None
    mean_delta: float | None
    ci_low: float | None
    ci_high: float | None
    hedges_g: float | None
    verdict: str  # supported | refuted | inconclusive | insufficient_evidence
    explanation: str
    pooled_sd: float | None = None
    #: True when the rows behind this number came from the pre-demo pilot (see
    #: ``learning.pilot``). The number is not hidden and not softened; it is *labelled*,
    #: because a demo that quietly presents pilot data as a result is the one thing
    #: worse than a demo that says "insufficient evidence".
    pilot: bool = False

    @property
    def label(self) -> str | None:
        """The word that has to sit next to this number. ``None`` when it is not pilot data."""
        if not self.pilot:
            return None
        from .pilot import PILOT_LABEL

        return PILOT_LABEL

    def as_dict(self) -> dict[str, object]:
        def r(x: float | None, k: int = 4) -> float | None:
            return None if x is None or not math.isfinite(x) else round(x, k)

        return {
            "baseline_n": self.baseline_n,
            "after_n": self.after_n,
            "baseline_mean": r(self.baseline_mean),
            "after_mean": r(self.after_mean),
            "mean_delta": r(self.mean_delta),
            "ci_low": r(self.ci_low),
            "ci_high": r(self.ci_high),
            "hedges_g": r(self.hedges_g, 3),
            "pooled_sd": r(self.pooled_sd, 3),
            "verdict": self.verdict,
            "explanation": self.explanation,
            "pilot": self.pilot,
            "label": self.label,
        }


#: The two words an active change can be in while it is being measured. ``measured`` means a
#: verdict exists (supported / refuted / inconclusive); ``gathering_evidence`` means the window
#: is not yet full -- which is a state, not a verdict, and deliberately does not go into
#: ``AdaptationProposal.evaluation_status`` (an unfinished measurement must not be filed as one).
EFFECT_MEASURED = "measured"
EFFECT_GATHERING = "gathering_evidence"


def effect_payload(
    effect: Effect,
    *,
    n_before: int,
    n_after: int,
    verdict_words: str,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """What gets stored when a change is measured, in the one shape every reader expects.

    The numbers are the statistician's (``Effect.as_dict``); ``verdict_words`` is the sentence a
    learner reads, stored *with* the measurement so no screen has to compose its own (F14); and
    ``status`` says whether this is a verdict or a window still filling up.
    """
    payload: dict[str, Any] = {
        "effect": effect.as_dict(),
        "pilot": effect.pilot,
        "label": effect.label,
        "n_before": int(n_before),
        "n_after": int(n_after),
        "verdict_words": verdict_words,
        "status": (
            EFFECT_GATHERING if effect.verdict == "insufficient_evidence" else EFFECT_MEASURED
        ),
    }
    if extra:
        payload.update({k: v for k, v in extra.items() if k not in payload})
    return payload


def read_effect(payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The stored measurement, flattened for the screens that draw it.

    ``active()`` and the proposal history hand this to a component whose type is
    ``EffectSummary``: ``{baseline_n, after_n, baseline_mean, mean_delta, verdict, explanation…}``.
    What is *stored* is nested (``{"effect": {...}, "verdict_words": …}``) because the raw payload
    is also the audit record. Reading the nested payload with the flat type is how a measured
    change came out on screen as "0 answers before and 0 after" -- the fields were there, one
    level down. Returns ``None`` when nothing has been measured at all.
    """
    if not payload:
        return None
    inner = payload.get("effect")
    flat: dict[str, Any] = dict(inner) if isinstance(inner, Mapping) else {}
    verdict = str(flat.get("verdict") or payload.get("verdict") or "")
    # the wrapper wins for everything it carries: it is the record, the inner dict is the maths
    for key in (
        "baseline_n",
        "after_n",
        "baseline_mean",
        "after_mean",
        "mean_delta",
        "ci_low",
        "ci_high",
    ):
        if key in payload:
            flat[key] = payload[key]
    flat.setdefault("baseline_n", payload.get("n_before"))
    flat.setdefault("after_n", payload.get("n_after"))
    flat["n_before"] = payload.get("n_before", flat.get("baseline_n"))
    flat["n_after"] = payload.get("n_after", flat.get("after_n"))
    flat["verdict"] = verdict or None
    flat["verdict_words"] = payload.get("verdict_words") or payload.get("note") or None
    flat["pilot"] = bool(payload.get("pilot") or flat.get("pilot"))
    flat["label"] = payload.get("label") or flat.get("label")
    if payload.get("status"):
        flat["status"] = payload["status"]
    elif verdict == "insufficient_evidence":
        flat["status"] = EFFECT_GATHERING
    elif verdict:
        flat["status"] = EFFECT_MEASURED
    else:
        flat["status"] = "not_measured"
    return flat


def bootstrap_mean_diff(
    a: Sequence[float], b: Sequence[float], *, iters: int = 4000, alpha: float = 0.05, seed: int | None = None
) -> tuple[float, float]:
    """Percentile CI for mean(b) - mean(a). Unpaired by default."""
    rng = _rng(seed)
    A = np.asarray(list(a), dtype=float)
    B = np.asarray(list(b), dtype=float)
    if A.size == 0 or B.size == 0:
        return (math.nan, math.nan)
    idx_a = rng.integers(0, A.size, size=(iters, A.size))
    idx_b = rng.integers(0, B.size, size=(iters, B.size))
    diffs = B[idx_b].mean(axis=1) - A[idx_a].mean(axis=1)
    return (float(np.quantile(diffs, alpha / 2)), float(np.quantile(diffs, 1 - alpha / 2)))


def hedges_g(a: Sequence[float], b: Sequence[float]) -> tuple[float, float]:
    A = np.asarray(list(a), dtype=float)
    B = np.asarray(list(b), dtype=float)
    na, nb = A.size, B.size
    if na < 2 or nb < 2:
        return (math.nan, math.nan)
    va, vb = A.var(ddof=1), B.var(ddof=1)
    pooled = math.sqrt(((na - 1) * va + (nb - 1) * vb) / (na + nb - 2))
    if pooled < 1e-9:
        return (0.0, pooled)
    d = (B.mean() - A.mean()) / pooled
    j = 1.0 - 3.0 / (4.0 * (na + nb) - 9.0)
    return (d * j, pooled)


def evaluate_change(
    baseline: Sequence[float],
    after: Sequence[float],
    *,
    metric: str,
    rope_sd: float = ROPE_SD,
    min_n: int = MIN_GROUP_N,
    seed: int | None = None,
    pilot: bool = False,
) -> Effect:
    """Compare a pre-registered metric before/after an approved adaptation.

    ``min_n`` is *not* lowered for a pilot. A pilot does not make four answers
    conclusive; it makes them four answers with a label on them, which is what
    ``pilot=True`` adds to every shape this returns.
    """
    A = [float(x) for x in baseline if x is not None and math.isfinite(float(x))]
    B = [float(x) for x in after if x is not None and math.isfinite(float(x))]
    bm = float(np.mean(A)) if A else None
    am = float(np.mean(B)) if B else None
    if len(A) < min_n or len(B) < min_n:
        return Effect(
            baseline_n=len(A),
            after_n=len(B),
            baseline_mean=bm,
            after_mean=am,
            mean_delta=(am - bm) if (bm is not None and am is not None) else None,
            ci_low=None,
            ci_high=None,
            hedges_g=None,
            verdict="insufficient_evidence",
            explanation=(
                f"{metric}: need >= {min_n} scored attempts on each side of the change; "
                f"have {len(A)} baseline and {len(B)} after. Keeping the change under "
                "observation rather than drawing a conclusion."
            ),
            pilot=pilot,
        )
    delta = float(np.mean(B) - np.mean(A))
    lo, hi = bootstrap_mean_diff(A, B, seed=seed)
    g, pooled = hedges_g(A, B)
    rope = rope_sd * (pooled if math.isfinite(pooled) and pooled > 0 else 1.0)
    if math.isfinite(lo) and lo > rope:
        verdict, why = "supported", "the whole confidence interval sits above the no-practical-change band"
    elif math.isfinite(hi) and hi < -rope:
        verdict, why = "refuted", "the change made this metric worse"
    elif math.isfinite(lo) and lo <= rope <= hi:
        verdict, why = (
            "inconclusive",
            "the interval crosses both zero and the practical-equivalence band",
        )
    else:
        verdict, why = "inconclusive", "point estimate positive but the interval includes no real change"
    return Effect(
        baseline_n=len(A),
        after_n=len(B),
        baseline_mean=bm,
        after_mean=am,
        mean_delta=delta,
        ci_low=lo,
        ci_high=hi,
        hedges_g=g if math.isfinite(g) else None,
        verdict=verdict,
        explanation=(
            f"{metric}: {bm:.2f} -> {am:.2f} over {len(A)}+{len(B)} scored attempts "
            f"(delta {delta:+.3f}, 95% CI [{lo:+.3f}, {hi:+.3f}], g {g:+.2f}); "
            f"{why}."
        ),
        pooled_sd=pooled if math.isfinite(pooled) else None,
        pilot=pilot,
    )


def proportion_ci(successes: int, n: int, *, alpha: float = 0.05) -> tuple[float, float]:
    """Wilson score interval -- safe at n=0..5, which happens constantly in a demo."""
    if n <= 0:
        return (0.0, 1.0)
    z = 1.959963985 if abs(alpha - 0.05) < 1e-9 else 1.0
    p = successes / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, centre - half), min(1.0, centre + half))


def retention_calibration(
    predictions: Sequence[tuple[float, bool]], *, buckets: int = 5
) -> dict[str, object]:
    """How well does predicted recall track actual recall?

    ``predictions`` = (expected_recall, did_recall). Returns Brier score,
    reliability buckets and expected calibration error.
    """
    if not predictions:
        return {"n": 0, "brier": None, "ece": None, "buckets": []}
    p = np.array([max(0.0, min(1.0, x[0])) for x in predictions], dtype=float)
    y = np.array([1.0 if x[1] else 0.0 for x in predictions], dtype=float)
    brier = float(np.mean((p - y) ** 2))
    edges = np.linspace(0, 1, buckets + 1)
    rows: list[dict[str, object]] = []
    ece = 0.0
    for i in range(buckets):
        mask = (p >= edges[i]) & (p <= edges[i + 1] if i == buckets - 1 else p < edges[i + 1])
        n_i = int(mask.sum())
        if n_i == 0:
            continue
        pp, yy = float(p[mask].mean()), float(y[mask].mean())
        ece += abs(pp - yy) * n_i / p.size
        rows.append(
            {"range": [round(float(edges[i]), 2), round(float(edges[i + 1]), 2)], "n": n_i, "predicted": round(pp, 3), "observed": round(yy, 3)}
        )
    return {
        "n": int(p.size),
        "brier": round(brier, 4),
        "brier_skill": round(1 - brier / max(1e-9, float(np.mean((y - y.mean()) ** 2) + 1e-9)), 4),
        "ece": round(float(ece), 4),
        "buckets": rows,
        "mean_predicted": round(float(p.mean()), 3),
        "mean_observed": round(float(y.mean()), 3),
    }


def trend(values: Sequence[float], *, window: int = 5) -> dict[str, object]:
    """Simple OLS slope over the last ``window`` points + noise-aware verdict."""
    v = [float(x) for x in values if x is not None and math.isfinite(float(x))]
    if len(v) < 3:
        return {"n": len(v), "slope": None, "direction": "insufficient_data"}
    arr = np.asarray(v[-window:], dtype=float)
    x = np.arange(arr.size, dtype=float)
    slope = float(np.polyfit(x, arr, 1)[0])
    resid = arr - np.polyval(np.polyfit(x, arr, 1), x)
    sd = float(arr.std()) or 1.0
    direction = "rising" if slope > 0.05 * sd else "falling" if slope < -0.05 * sd else "flat"
    return {
        "n": arr.size,
        "slope": round(slope, 4),
        "direction": direction,
        "residual_sd": round(float(resid.std()), 4),
    }


def describe_window(window: dict[str, object] | None) -> str:
    """Plain words for the measurement window a proposal promises to watch.

    The window itself stays machine-readable (it is what ``evaluate_change``
    consumes); this exists because a learner should never have to read a dict to
    understand what they are agreeing to.
    """
    if not window:
        return "your next few answers"
    kind = str(window.get("kind") or "next_items")
    lo = window.get("min_items")
    hi = window.get("max_items")
    if kind == "next_items" and lo is not None and hi is not None:
        return f"your next {lo}-{hi} answers"
    if kind == "next_items":
        return f"your next {lo or hi or 'few'} answers"
    if kind == "days":
        return f"the next {window.get('days', lo or 7)} days"
    if kind == "minutes":
        return f"the next {window.get('minutes', lo or 20)} minutes of work"
    if kind == "sessions":
        return f"your next {window.get('sessions', lo or 3)} sessions"
    if kind == "retention":
        return "the next scheduled review of that topic"
    return f"{kind.replace('_', ' ')}"
