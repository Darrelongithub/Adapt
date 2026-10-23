"""The check-in estimate: tiles in, one validated estimate out.

The constructs (``docs/FRAMEWORK.md`` is the citation home):

* **Cognitive load** — Paas's single-item mental-effort rating (1–9) is the
  self-report tile; behavioural strain (time per item against the learner's
  own baseline, retries, hint use, accuracy) may raise the band one step,
  never lower what the learner said about themselves.
* **Emotions** — Pekrun's control-value achievement emotions are the tile
  vocabulary: enjoyment, pride, hope, anxiety, frustration, boredom.
* **Motivation help** — self-determination theory's three needs as one
  optional tile: a choice (autonomy), a win (competence), someone to learn
  with (relatedness). Recorded as a stated wish, never scored.

Output is always an *estimate with evidence* — the tile values and the
code-computed metrics travel with it. Nothing here diagnoses, and no field
names a trait: bands describe this sitting, not this person.
"""

from __future__ import annotations

import statistics
from typing import Any, Mapping, Sequence

#: Pekrun achievement emotions (the tile vocabulary and the output enum).
EMOTIONS: tuple[str, ...] = ("enjoyment", "pride", "hope", "anxiety", "frustration", "boredom")
BANDS: tuple[str, ...] = ("low", "medium", "high")
ACTIONS: tuple[str, ...] = ("simplify", "encourage", "challenge", "review", "break")
#: SDT "what would help" tiles: autonomy, competence, relatedness.
HELP_CHOICES: tuple[str, ...] = ("choice", "win", "together")
CONTEXTS: tuple[str, ...] = ("after_test", "after_lesson", "home", "other")

NOTE_MAX = 280
SUMMARY_MAX = 140

#: What one check-in stores, each with the consent sentence that must cover
#: it. The privacy copy test diffs these phrases against the served
#: "what we record" list, so stored-but-undeclared data cannot sneak in.
RECORDED_FIELDS: tuple[tuple[str, str], ...] = (
    ("effort", "how much effort the work felt like (1 to 9), when you check in"),
    ("emotion", "how the work felt, in your own pick of six feelings, when you check in"),
    ("help", "what you said would help next, when you check in"),
    ("note", "one short note you write at a check-in, saved so you can read it back; it is never sent anywhere or used to score you"),
    ("estimate", "an estimate of how heavy, engaging and frustrating this sitting felt, with the practice numbers behind it"),
)

_EFFORT_WORDS = {1: "very light", 2: "very light", 3: "light", 4: "steady", 5: "steady", 6: "demanding", 7: "heavy", 8: "heavy", 9: "very heavy"}

# emotion -> (frustration, engagement, motivation) starting bands, before
# behavioural adjustment. A stated feeling about this sitting, not a trait.
_EMOTION_BANDS: dict[str, tuple[str, str, str]] = {
    "enjoyment": ("low", "high", "high"),
    "pride": ("low", "high", "high"),
    "hope": ("low", "medium", "high"),
    "anxiety": ("medium", "medium", "medium"),
    "frustration": ("high", "medium", "medium"),
    "boredom": ("low", "low", "low"),
}


class CheckInError(ValueError):
    """Tiles a check-in cannot be built from. The message is learner-safe."""


def _band_up(band: str) -> str:
    return BANDS[min(BANDS.index(band) + 1, len(BANDS) - 1)]


def _load_band(effort: int) -> str:
    """Paas 1–9 in three bands: 1–3 low, 4–6 medium, 7–9 high."""
    return "low" if effort <= 3 else ("medium" if effort <= 6 else "high")


def normalise_tiles(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the learner's tiles. Unknown values are refused, not guessed.

    The note is kept to one line: whitespace-collapsed and length-capped.
    It is stored encrypted for the learner to read back and is never used
    as an instruction anywhere (see :func:`adapt.ai.monitor.service.record`).
    """
    effort = raw.get("effort")
    if isinstance(effort, bool) or not isinstance(effort, int) or not 1 <= effort <= 9:
        raise CheckInError("tap how much effort that took, from 1 (very light) to 9 (very heavy)")
    emotion = raw.get("emotion")
    if emotion is not None and emotion not in EMOTIONS:
        raise CheckInError("that feeling is not one of the six choices")
    help_choice = raw.get("help")
    if help_choice is not None and help_choice not in HELP_CHOICES:
        raise CheckInError("that help choice is not one of the three choices")
    context = raw.get("context") or "other"
    if context not in CONTEXTS:
        context = "other"
    note = " ".join(str(raw.get("note") or "").split())[:NOTE_MAX]
    return {
        "effort": effort,
        "emotion": emotion,
        "help": help_choice,
        "context": context,
        "note": note,
        "session_id": str(raw.get("session_id") or "")[:120] or None,
        "subject_id": str(raw.get("subject_id") or "")[:120] or None,
    }


def behavioural_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Code-computed signals from real evidence rows — never self-reported.

    ``rows`` are normalised evidence dicts (``correct``, ``ms``,
    ``hints_used``, ``retries``), newest first. The recent window is the
    last 12; the learner's own baseline is the 30 before that, so "slower
    than usual" means slower than *their* usual. With too little history
    the ratio is ``None`` — an honest unknown, not a 1.0.
    """
    recent = list(rows)[:12]
    earlier = list(rows)[12:42]
    scored = [r for r in recent if isinstance(r.get("correct"), bool)]
    accuracy = round(sum(1 for r in scored if r["correct"]) / len(scored), 3) if scored else None
    times = [float(r["ms"]) for r in recent if isinstance(r.get("ms"), (int, float)) and float(r["ms"]) > 0]
    base_times = [float(r["ms"]) for r in earlier if isinstance(r.get("ms"), (int, float)) and float(r["ms"]) > 0]
    median_ms = round(statistics.median(times)) if times else None
    baseline_ms = round(statistics.median(base_times)) if len(base_times) >= 5 else None
    time_ratio = round(median_ms / baseline_ms, 2) if median_ms and baseline_ms else None
    hints = sum(int(r.get("hints_used") or 0) for r in recent)
    retries = sum(int(r.get("retries") or 0) for r in recent)
    strain = 0
    if time_ratio is not None and time_ratio >= 1.5:
        strain += 1
    if accuracy is not None and len(scored) >= 3 and accuracy < 0.5:
        strain += 1
    if hints + retries >= 4:
        strain += 1
    return {
        "n_recent": len(recent),
        "accuracy": accuracy,
        "median_ms": median_ms,
        "baseline_ms": baseline_ms,
        "time_ratio": time_ratio,
        "hints": hints,
        "retries": retries,
        "strain": strain,  # 0..3 corroborating strain signals
    }


def recommended_action(estimate: Mapping[str, Any], metrics: Mapping[str, Any], tiles: Mapping[str, Any]) -> str:
    """The code rule for what to suggest next. A model never decides this."""
    load = str(estimate.get("cognitive_load") or "medium")
    frustration = str(estimate.get("frustration") or "low")
    engagement = str(estimate.get("engagement") or "medium")
    emotion = str(estimate.get("primary_emotion") or tiles.get("emotion") or "")
    effort = int(tiles.get("effort") or 5)
    accuracy = metrics.get("accuracy")
    if effort >= 9 or (frustration == "high" and load == "high"):
        return "break"
    if frustration == "high" or load == "high":
        return "simplify"
    if emotion == "boredom" and load == "low":
        return "challenge"
    if isinstance(accuracy, (int, float)) and int(metrics.get("n_recent") or 0) >= 3 and float(accuracy) < 0.5:
        return "review"
    if engagement == "high" and load == "low" and emotion in ("enjoyment", "pride"):
        return "challenge"
    return "encourage"


def simple_estimate(tiles: Mapping[str, Any], metrics: Mapping[str, Any]) -> dict[str, Any]:
    """The deterministic estimate: the same tiles+metrics, the same output.

    This is simple mode's whole check-in (no key configured), and the
    per-field replacement when a model's output fails validation.
    """
    effort = int(tiles["effort"])
    emotion = tiles.get("emotion") or ("frustration" if effort >= 8 else "boredom" if effort <= 2 else "hope")
    load = _load_band(effort)
    if int(metrics.get("strain") or 0) >= 2 and load != "high":
        load = _band_up(load)  # corroborated strain may raise, never lower, the self-report
    frustration, engagement, motivation = _EMOTION_BANDS[str(emotion)]
    accuracy = metrics.get("accuracy")
    if isinstance(accuracy, (int, float)) and int(metrics.get("n_recent") or 0) >= 3:
        if float(accuracy) < 0.4 and frustration != "high":
            frustration = _band_up(frustration)
        if float(accuracy) >= 0.85 and engagement == "medium":
            engagement = "high"
    intensity = round(0.2 + 0.8 * (effort - 1) / 8, 2) if emotion in ("frustration", "anxiety", "enjoyment", "pride") else round(0.15 + 0.6 * (effort - 1) / 8, 2)
    estimate = {
        "primary_emotion": emotion,
        "intensity": intensity,
        "cognitive_load": load,
        "engagement": engagement,
        "frustration": frustration,
        "motivation": motivation,
        "recommended_action": "encourage",
        "summary": "",
    }
    estimate["recommended_action"] = recommended_action(estimate, metrics, tiles)
    slow = " Recent answers were slower than your usual pace." if (metrics.get("time_ratio") or 0) >= 1.5 else ""
    estimate["summary"] = f"You said that felt {_EFFORT_WORDS[effort]} (effort {effort} of 9), mostly {emotion}.{slow}"[:SUMMARY_MAX]
    return estimate


def validate_model_estimate(raw: Any, *, fallback: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Validate a model's check-in JSON field by field against the enums.

    Unknown enums, out-of-range numbers and oversized text are *replaced*
    with the deterministic estimate's values (never clamped into looking
    valid, never repaired silently — the replacements are returned so the
    caller can record what was refused). Extra keys — including any
    behavioural numbers the model invents — are dropped. ``recommended_action``
    is not the model's to give: the caller re-derives it with
    :func:`recommended_action`.
    """
    replaced: list[str] = []
    if not isinstance(raw, Mapping):
        return dict(fallback), ["whole estimate (not an object)"]
    out = dict(fallback)

    emotion = raw.get("primary_emotion")
    if emotion in EMOTIONS:
        out["primary_emotion"] = emotion
    elif "primary_emotion" in raw:
        replaced.append("primary_emotion")

    intensity = raw.get("intensity")
    if isinstance(intensity, (int, float)) and not isinstance(intensity, bool) and 0.0 <= float(intensity) <= 1.0:
        out["intensity"] = round(float(intensity), 2)
    elif "intensity" in raw:
        replaced.append("intensity")

    for field in ("cognitive_load", "engagement", "frustration", "motivation"):
        value = raw.get(field)
        if value in BANDS:
            out[field] = value
        elif field in raw:
            replaced.append(field)

    summary = raw.get("summary")
    if isinstance(summary, str) and summary.strip() and len(summary.strip()) <= SUMMARY_MAX:
        out["summary"] = " ".join(summary.split())
    elif "summary" in raw:
        replaced.append("summary")
    return out, replaced
