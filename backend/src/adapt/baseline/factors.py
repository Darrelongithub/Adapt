"""The eight mind factors, their plain-language statements, and the levers AI 3 consumes.

Round brief §5.4. Three rules, all of them about not overclaiming:

* **Every factor is a range with an n, or it is "not measured".** There is no zero, no default and
  no score out of 100. A factor with too few trials says so and says what to do to fill it.
* **The wording is about what the app will do differently**, not about the person ("you currently
  hold about 5 steps, so lessons will use smaller steps"), because the point of measuring is the
  next lesson, not a label.
* **Preferences are never a factor.** A stated preference (diagrams, tone, timers) is an
  instruction; a measured factor is evidence. They are shown side by side in the snapshot and are
  kept in different tables, so "you said you like diagrams" can never be silently read as "you
  learn better from diagrams" — which is what the comparison row in §5.4 is for.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median
from typing import Any, Iterable, Mapping, Sequence

from .games import Trial, calibration, measured, predicted_vs_actual, trial_action

#: The eight factors, in the order the snapshot shows them. Prior knowledge is filled in Phase 3
#: (the subject round); this module reports it as not measured until that evidence exists.
FACTORS: tuple[str, ...] = (
    "prior_knowledge",
    "working_memory",
    "processing_pace",
    "attention_self_control",
    "long_term_memory",
    "reasoning",
    "metacognition",
    "persistence_help_seeking",
)

FACTOR_TITLES: dict[str, str] = {
    "prior_knowledge": "What you already know",
    "working_memory": "How much you hold at once",
    "processing_pace": "How fast you work",
    "attention_self_control": "Staying on it",
    "long_term_memory": "Remembering over time",
    "reasoning": "Working out a rule",
    "metacognition": "Knowing what you know",
    "persistence_help_seeking": "Pushing through, and asking for help",
}

#: Minimum trials before a factor stops saying "not measured". Deliberately low: the brief wants a
#: range with an n, not a significance test, and a 7-minute baseline has to be able to produce one.
MIN_N: dict[str, int] = {
    "prior_knowledge": 5,
    "working_memory": 3,
    "processing_pace": 6,
    "attention_self_control": 4,
    "long_term_memory": 5,
    "reasoning": 3,
    "metacognition": 8,
    "persistence_help_seeking": 2,
}


@dataclass(frozen=True)
class Factor:
    """One measured factor: a range, an n, what it means, and what the app will do about it."""

    key: str
    title: str
    state: str  # measured | not_enough_data
    n: int = 0
    low: float | None = None
    high: float | None = None
    summary: str = ""
    statement: str = ""
    actions: tuple[str, ...] = ()
    detail: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "state": self.state,
            "n": self.n,
            "range": None if self.low is None else [round(self.low, 3), round(self.high, 3)],
            "summary": self.summary,
            "statement": self.statement,
            "actions": list(self.actions),
            "detail": dict(self.detail),
        }


def _not_measured(key: str, *, need: int, n: int, what: str) -> Factor:
    return Factor(
        key=key,
        title=FACTOR_TITLES[key],
        state="not_enough_data",
        n=n,
        summary=f"not measured yet ({n} of {need} needed)",
        statement=f"Not enough data yet — {what} would fill this in.",
        actions=(),
        detail={"needed": need, "have": n},
    )


def _band_score(*pairs: tuple[float, bool]) -> float:
    return (sum(1 for _v, ok in pairs if ok) / len(pairs)) if pairs else 0.0


# --------------------------------------------------------------------------- #
# per-factor builders
# --------------------------------------------------------------------------- #
def _working_memory(trials: Sequence[Trial]) -> Factor:
    spans = [
        int(t.task.get("length") or 0)
        for t in measured(trials)
        if t.game == "vault" and t.correct and t.task.get("length")
    ]
    failures = [t for t in measured(trials) if t.game == "vault" and t.correct is False]
    forward = max([int(t.task.get("length") or 0) for t in measured(trials) if t.game == "vault" and t.task.get("direction") == "forward" and t.correct] or [0])
    backward = max([int(t.task.get("length") or 0) for t in measured(trials) if t.game == "vault" and t.task.get("direction") == "backward" and t.correct] or [0])
    shop = [t for t in measured(trials) if t.game == "shopkeeper" and t.correct is not None]
    recall_hits = [
        int(((t.response.get("scored") or {}).get("hits") or 0)) for t in shop
    ]
    n = len(measured([t for t in trials if t.game in ("vault", "shopkeeper")]))
    if n < MIN_N["working_memory"] or not spans:
        return _not_measured("working_memory", need=MIN_N["working_memory"], n=n, what="a few more tile sequences")
    best = max(spans)
    # A span is reported as a *range*: the best sequence held, and one step either side, because a
    # span measured once in one sitting is an estimate and saying "you hold exactly 5" would be a
    # false precision the brief explicitly forbids.
    low = max(2.0, best - 1)
    high = min(9.0, best + 0.5)
    distractor_accuracy = [
        (t.response.get("scored") or {}).get("distractor_accuracy")
        for t in shop
        if (t.response.get("scored") or {}).get("distractor_accuracy") is not None
    ]
    return Factor(
        key="working_memory",
        title=FACTOR_TITLES["working_memory"],
        state="measured",
        n=n,
        low=low,
        high=high,
        summary=f"about {low:g}–{high:g} steps at once, {len(failures)} sequence(s) missed",
        statement=(
            f"You currently hold about {low:g} steps at once, so lessons will use smaller steps "
            f"and check between them."
        ),
        actions=(
            f"keep explanation blocks to about {int(low)} ideas before a check",
            "prefer a worked example split into short steps over one long paragraph",
        ),
        detail={
            "forward_span": forward,
            "backward_span": backward,
            "failures": len(failures),
            "recall_under_distraction": (round(sum(recall_hits) / (3 * len(recall_hits)), 3) if recall_hits else None),
            "distractor_accuracy": (round(sum(distractor_accuracy) / len(distractor_accuracy), 3) if distractor_accuracy else None),
        },
    )


def _pace(trials: Sequence[Trial]) -> Factor:
    quick = [t for t in measured(trials) if t.game in ("quick", "trap") and t.latency_ms]
    if len(quick) < MIN_N["processing_pace"]:
        return _not_measured("processing_pace", need=MIN_N["processing_pace"], n=len(quick), what="a few more quick taps")
    lat = sorted(int(t.latency_ms) for t in quick)
    med = median(lat)
    # The range is the interquartile spread of the learner's own trials, not a comparison with
    # anybody else: a slow careful learner and a slow struggling one have the same median time and
    # very different stories, which is why the accuracy beside it matters.
    q1 = lat[len(lat) // 4]
    q3 = lat[(3 * len(lat)) // 4]
    third = max(1, len(quick) // 3)
    early = median([int(t.latency_ms) for t in quick[:third]])
    late = median([int(t.latency_ms) for t in quick[-third:]])
    drift = (late - early) / max(1.0, early)
    accuracy = _band_score(*[(0.0, bool(t.correct)) for t in quick if t.correct is not None])
    return Factor(
        key="processing_pace",
        title=FACTOR_TITLES["processing_pace"],
        state="measured",
        n=len(quick),
        low=q1 / 1000,
        high=q3 / 1000,
        summary=f"middle {med / 1000:.1f}s per quick tap, {accuracy:.0%} right",
        statement=(
            f"Most quick answers take you about {med / 1000:.1f} seconds, so the app will pace new "
            f"material at that speed and not chase a fastest time."
        ),
        actions=(
            "size checks to about this pace rather than to a clock",
            ("shorten blocks and add a break" if drift > 0.25 else "keep block length as it is"),
        ),
        detail={
            "median_ms": int(med),
            "q1_ms": int(q1),
            "q3_ms": int(q3),
            "drift": round(drift, 3),
            "accuracy": round(accuracy, 3),
            "note": "reaction-time differences are not used as a measure: pace is, and it is reported as a range",
        },
    )


def _attention(trials: Sequence[Trial]) -> Factor:
    quick = [t for t in measured(trials) if t.game == "quick"]
    traps = [t for t in measured(trials) if t.game == "trap"]
    drift_trials = [t for t in quick if t.correct is not None]
    if len(drift_trials) + len(traps) < MIN_N["attention_self_control"]:
        return _not_measured("attention_self_control", need=MIN_N["attention_self_control"], n=len(drift_trials) + len(traps), what="a few more quick trials")
    third = max(1, len(drift_trials) // 3)
    first_acc = _band_score(*[(0.0, bool(t.correct)) for t in drift_trials[:third]])
    last_acc = _band_score(*[(0.0, bool(t.correct)) for t in drift_trials[-third:]])
    intuitive = sum(1 for t in traps if str(t.response.get("value") or "") == str(t.task.get("intuitive") or ""))
    correct_traps = sum(1 for t in traps if t.correct)
    # Pause time on a trap is the measurement of "did you think about it": the near-miss of the
    # intuitive answer is kept beside it, because the interesting case is the learner who paused,
    # saw the trap, and still chose the wrong one.
    pauses = [int(t.latency_ms) for t in traps if t.latency_ms is not None]
    pause_note = (
        f" On the two questions where the obvious answer is wrong, you paused about {median(pauses) / 1000:.1f} seconds."
        if pauses
        else " Response timing for the trap questions was not recorded."
    )
    return Factor(
        key="attention_self_control",
        title=FACTOR_TITLES["attention_self_control"],
        state="measured",
        n=len(drift_trials) + len(traps),
        low=min(first_acc, last_acc),
        high=max(first_acc, last_acc),
        summary=f"{first_acc:.0%} right early, {last_acc:.0%} right late, {correct_traps}/{len(traps)} traps spotted",
        statement=(
            "Your accuracy "
            + ("held up" if last_acc >= first_acc - 0.1 else "dropped")
            + " toward the end of the session, so "
            + ("blocks can stay long" if last_acc >= first_acc - 0.1 else "the app will shorten blocks and add a check-in")
            + ("." if not traps else pause_note)
        ),
        actions=(
            ("keep blocks long" if last_acc >= first_acc - 0.1 else "shorten blocks and add a check-in"),
            ("ask you to stop and think before answering" if intuitive and intuitive > correct_traps else "keep answers as they are"),
        ),
        detail={
            "first_third_accuracy": round(first_acc, 3),
            "last_third_accuracy": round(last_acc, 3),
            "traps_correct": correct_traps,
            "traps_intuitive": intuitive,
            "median_pause_ms": int(median(pauses)) if pauses else None,
        },
    )


def _long_term_memory(trials: Sequence[Trial]) -> Factor:
    immediate = [t for t in measured(trials) if t.game == "codewords_cued" and t.correct is not None]
    free = [t for t in measured(trials) if t.game == "codewords_delayed" and t.task.get("kind") == "free"]
    recog = [t for t in measured(trials) if t.game == "codewords_delayed" and t.task.get("kind") == "recognition"]
    nextday = [t for t in measured(trials) if t.game == "codewords_next_day"]
    n = len(immediate) + len(free) + len(recog)
    if n < MIN_N["long_term_memory"]:
        return _not_measured("long_term_memory", need=MIN_N["long_term_memory"], n=n, what="finishing the codeword round")
    immediate_score = _band_score(*[(0.0, bool(t.correct)) for t in immediate])
    free_hits = [int((t.response.get("scored") or {}).get("hits") or 0) for t in free]
    free_score = (max(free_hits) / 8.0) if free_hits else None
    recog_score = _band_score(*[(0.0, bool(t.correct)) for t in recog]) if recog else None
    gap = None
    if free_score is not None and recog_score is not None:
        gap = round(recog_score - free_score, 3)
    return Factor(
        key="long_term_memory",
        title=FACTOR_TITLES["long_term_memory"],
        state="measured",
        n=n,
        low=immediate_score - 0.1,
        high=min(1.0, immediate_score + 0.1),
        summary=f"{immediate_score:.0%} recalled straight away" + (f", {free_score:.0%} later" if free_score is not None else ""),
        statement=(
            f"You recalled {immediate_score:.0%} of the pairs straight away"
            + (f" and {free_score:.0%} about ten minutes later" if free_score is not None else "")
            + ", so review will be scheduled "
            + ("sooner than the default" if immediate_score < 0.6 else "around the default spacing")
            + "."
        ),
        actions=(
            "seed your review spacing from this, with a range",
            ("shown what you could recognise but not produce, and given another way to retrieve it" if (gap or 0) > 0.15 else "keep recall practice as it is"),
        ),
        detail={
            "immediate": round(immediate_score, 3),
            "delayed_free": None if free_score is None else round(free_score, 3),
            "recognition": None if recog_score is None else round(recog_score, 3),
            "recall_vs_recognition_gap": gap,
            "next_day": (round(_band_score(*[(0.0, bool(t.correct)) for t in nextday]), 3) if nextday else None),
        },
    )


def _reasoning(trials: Sequence[Trial]) -> Factor:
    machines = [t for t in measured(trials) if t.game == "machine"]
    guesses = [t for t in machines if trial_action(t) == "guess"]
    probes = [t for t in machines if trial_action(t) == "probe"]
    n = len(probes) + len(guesses)
    if n < MIN_N["reasoning"]:
        return _not_measured("reasoning", need=MIN_N["reasoning"], n=n, what="finishing a machine round")
    falsifying = sum(
        1 for t in probes if (t.response.get("scored") or {}).get("falsifying")
    )
    solved = [g for g in guesses if g.correct]
    tries_to_first_guess = None
    if guesses:
        first = guesses[0]
        tries_to_first_guess = len([p for p in probes if p.index < first.index])
    rate = (falsifying / len(probes)) if probes else 0.0
    return Factor(
        key="reasoning",
        title=FACTOR_TITLES["reasoning"],
        state="measured",
        n=n,
        low=rate - 0.15 if rate > 0 else 0.0,
        high=min(1.0, rate + 0.15),
        summary=f"{falsifying}/{len(probes)} tests tried to break your idea, {len(solved)}/{len(guesses)} guesses right",
        statement=(
            f"About {rate:.0%} of the inputs you tested were ones that could have proved you wrong"
            + (", which is a good habit" if rate >= 0.4 else ", so the app will prompt you to test the awkward case first")
            + "."
        ),
        actions=(
            ("keep asking you to guess first" if rate >= 0.4 else "ask you for one test that could prove you wrong before guessing"),
            "show the rule you found next to the tests that found it",
        ),
        detail={
            "probes": len(probes),
            "guesses": len(guesses),
            "falsifying_rate": round(rate, 3),
            "first_guess_correct": bool(guesses and guesses[0].correct),
            "tries_before_first_guess": tries_to_first_guess,
            "rounds_solved": len(solved),
            "noticed_the_switch": bool(
                [t for t in guesses if (t.response.get("scored") or {}).get("noticed")]
            ),
        },
    )


def _metacognition(trials: Sequence[Trial]) -> Factor:
    cal = calibration(trials)
    pred = predicted_vs_actual(trials)
    if cal["n"] < MIN_N["metacognition"]:
        return _not_measured("metacognition", need=MIN_N["metacognition"], n=cal["n"], what="a few more answers with a coin wager")
    low, high = cal["range"]
    index = round(cal["mean_confidence"] - cal["accuracy"], 3)
    return Factor(
        key="metacognition",
        title=FACTOR_TITLES["metacognition"],
        state="measured",
        n=cal["n"],
        low=low,
        high=high,
        summary=f"confident {cal['mean_confidence']:.0%} of the time, right {cal['accuracy']:.0%} of the time",
        statement=(
            ("You are a good judge of your own answers" if abs(index) <= 0.1 else
             ("You tended to feel more sure than you were" if index > 0 else "You tended to be surer of yourself than your answers suggested"))
            + f", so the app will "
            + ("keep asking for a wager" if abs(index) <= 0.1 else
               ("ask you to say how sure you are before answering" if index > 0 else "show you the evidence when you get something right"))
            + "."
        ),
        actions=(
            ("keep confidence prompts" if abs(index) <= 0.1 else
             ("ask for a confidence estimate before each answer" if index > 0 else "show evidence of success when you are right")),
            "compare your predicted score with the real one",
        ),
        detail={"calibration": cal, "predicted_vs_actual": pred, "index": index},
    )


def _persistence(trials: Sequence[Trial]) -> Factor:
    box = [t for t in trials if t.game == "locked_box"]
    attempts = [t for t in box if trial_action(t) == "attempt"]
    hints = [t for t in box if trial_action(t) == "hint"]
    quit_trial = next((t for t in box if trial_action(t) == "walk_away"), None)
    n = len(attempts) + len(hints) + (1 if quit_trial else 0)
    starter = next((t for t in box if trial_action(t) == "start"), None)
    if n < MIN_N["persistence_help_seeking"] or starter is None:
        return _not_measured("persistence_help_seeking", need=MIN_N["persistence_help_seeking"], n=n, what="trying the locked box")
    first_hint_ms = None
    if hints:
        first_hint = min(hints, key=lambda t: t.index)
        # The client times the whole puzzle, rather than restarting the clock after each hint or
        # attempt. This is time to the first request for help, not time spent reading the hint.
        first_hint_ms = int(first_hint.latency_ms or 0) or None
    wrong_attempts = [a for a in attempts if a.correct is False]
    after_wrong_retry = None
    if wrong_attempts:
        last_wrong = wrong_attempts[-1]
        after = [t for t in box if t.index > last_wrong.index and trial_action(t) == "attempt"]
        after_wrong_retry = len(after) > 0
    minutes = None
    if starter and (quit_trial or attempts or hints):
        last = max([t for t in box if t.index >= starter.index], key=lambda t: t.index)
        # As with the hint time, this is cumulative time from the puzzle screen to the final
        # action, supplied by the client; adding per-action cumulative values would double count.
        minutes = round(int(last.latency_ms or 0) / 60000, 2) if last.latency_ms is not None else None
    return Factor(
        key="persistence_help_seeking",
        title=FACTOR_TITLES["persistence_help_seeking"],
        state="measured",
        n=n,
        low=float(max(0, len(hints) - 1)),
        high=float(len(hints) + 1),
        summary=f"{len(hints)} hint(s), {len(wrong_attempts)} wrong attempt(s)" + (", walked away" if quit_trial else ", stayed with it"),
        statement=(
            "On a puzzle that was meant to be too hard, you "
            + (f"asked for help after about {round((first_hint_ms or 0) / 1000)} seconds — that is a real skill" if hints else "worked for a while before stopping")
            + (", and you left when it stopped being useful, which is also fine." if quit_trial else ", and the app will keep offering the hint shop.")
        ),
        actions=(
            ("offer a nudge earlier" if first_hint_ms and first_hint_ms < 30_000 else "let you work before offering help"),
            ("keep a walk-away button" if quit_trial else "offer a break before a hint"),
        ),
        detail={
            "hints_used": len(hints),
            "hint_cost": sum(
                int((h.response.get("scored") or {}).get("cost") or 0) for h in hints
            ),
            "wrong_attempts": len(wrong_attempts),
            "time_to_first_hint_ms": first_hint_ms,
            "walked_away": bool(quit_trial),
            "retried_after_wrong": after_wrong_retry,
            "minutes_on_puzzle": minutes,
        },
    )


def _prior_knowledge(trials: Sequence[Trial], *, model: Mapping[str, Any] | None) -> Factor:
    """Prior knowledge comes from Phase 3's subject round, not from these games.

    It is reported as not measured until it exists, and when it exists it is read from the learner
    model (the store), not recomputed here — two places computing the same number is how two screens
    end up disagreeing.
    """
    topics = list((model or {}).get("topics") or [])
    if not topics:
        return _not_measured("prior_knowledge", need=MIN_N["prior_knowledge"], n=0, what="the subject round (next step)")
    n = sum(int(t.get("n") or 0) for t in topics)
    best = max(topics, key=lambda t: float(t.get("p_know") or 0))
    worst = min(topics, key=lambda t: float(t.get("p_know") or 0))
    return Factor(
        key="prior_knowledge",
        title=FACTOR_TITLES["prior_knowledge"],
        state="measured" if n >= MIN_N["prior_knowledge"] else "not_enough_data",
        n=n,
        low=float(worst.get("p_know") or 0),
        high=float(best.get("p_know") or 0),
        summary=f"{len(topics)} topic(s) so far, {n} answer(s)",
        statement=(
            f"Your strongest topic so far is {best.get('title')} and your thinnest is {worst.get('title')}, "
            f"so practice will start where the evidence is thinnest."
        ),
        actions=("start from the thinnest topic", "re-check the strongest one later, not now"),
        detail={"topics": [{"topic_id": t.get("topic_id"), "title": t.get("title"), "p_know": t.get("p_know"), "n": t.get("n")} for t in topics]},
    )


# --------------------------------------------------------------------------- #
# the snapshot
# --------------------------------------------------------------------------- #
def build_factors(
    trials: Sequence[Trial],
    *,
    model: Mapping[str, Any] | None = None,
    preferences: Mapping[str, Any] | None = None,
) -> dict[str, Factor]:
    return {
        "prior_knowledge": _prior_knowledge(trials, model=model),
        "working_memory": _working_memory(trials),
        "processing_pace": _pace(trials),
        "attention_self_control": _attention(trials),
        "long_term_memory": _long_term_memory(trials),
        "reasoning": _reasoning(trials),
        "metacognition": _metacognition(trials),
        "persistence_help_seeking": _persistence(trials),
    }


def levers(factors: Mapping[str, Factor], *, preferences: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The eight downstream levers (brief §5.4), each traceable to a factor or a preference.

    Every lever carries a ``why``. That is the whole point: AI 3 consumes these, and the "why this
    lesson looks like this" panel has to be able to answer *which measurement* produced the step
    size rather than pointing at a model version alone.
    """
    prefs = dict(preferences or {})
    wm = factors["working_memory"]
    pace = factors["processing_pace"]
    ltm = factors["long_term_memory"]
    meta = factors["metacognition"]
    att = factors["attention_self_control"]
    reason = factors["reasoning"]
    persist = factors["persistence_help_seeking"]

    step_size = "medium"
    why_step = "no working-memory measurement yet"
    if wm.state == "measured" and wm.high is not None:
        if wm.high <= 4.5:
            step_size, why_step = "small", f"holds about {wm.high:g} steps at once (n={wm.n})"
        elif wm.high >= 6.5:
            step_size, why_step = "large", f"holds about {wm.high:g} steps at once (n={wm.n})"
        else:
            step_size, why_step = "medium", f"holds about {wm.high:g} steps at once (n={wm.n})"

    stated_format = str(prefs.get("preferred_format") or "")
    lead_format_prior = stated_format or "worked_example"
    why_lead = (
        f"you chose {stated_format.replace('_', ' ')} for a new topic (stated, not measured)"
        if stated_format
        else "no stated preference: a worked example is the default first look at a new idea"
    )

    halflife = 24.0
    why_hl = "default spacing: nothing measured yet"
    if ltm.state == "measured" and ltm.detail.get("immediate") is not None:
        immediate = float(ltm.detail["immediate"])
        halflife = 90.0 if immediate >= 0.75 else (48.0 if immediate >= 0.5 else 18.0)
        why_hl = f"recalled {immediate:.0%} of new pairs straight away (n={ltm.n})"

    hint_policy = "on request"
    why_hint = "default: hints are there when asked for"
    if persist.state == "measured":
        hints = int(persist.detail.get("hints_used") or 0)
        ttf = persist.detail.get("time_to_first_hint_ms")
        if hints == 0:
            hint_policy, why_hint = "offer after a first wrong attempt", "worked on the hard puzzle without asking for help"
        elif ttf is not None and ttf < 30_000:
            hint_policy, why_hint = "offer early", f"asked for help after {round(ttf / 1000)}s on a hard puzzle"
        else:
            hint_policy, why_hint = "on request", f"asked after {round((ttf or 0) / 1000)}s — in no hurry, so let them work"

    confidence_prompting = "off"
    why_conf = "no calibration evidence yet"
    if meta.state == "measured":
        index = float(meta.detail.get("index") or 0)
        if index > 0.1:
            confidence_prompting, why_conf = "before_each_answer", f"felt sure {index:.0%} more often than they were right (n={meta.n})"
        elif index < -0.1:
            confidence_prompting, why_conf = "evidence_of_success", f"were right {abs(index):.0%} more often than they felt (n={meta.n})"
        else:
            confidence_prompting, why_conf = "keep_asking", f"well calibrated within {abs(index):.0%} (n={meta.n})"

    tone_stated = prefs.get("feedback_style")
    language_stated = prefs.get("language")
    tone = str(tone_stated or "straight")
    language = str(language_stated or "en")
    timers_visible = bool(prefs.get("timers_help", True))
    pace_lever = "measured"
    why_pace = "no pace measurement yet"
    if pace.state == "measured":
        drift = float(pace.detail.get("drift") or 0)
        pace_lever = "steady" if drift <= 0.25 else "short_blocks"
        why_pace = f"median {pace.detail.get('median_ms')}ms per quick tap, drift {drift:+.0%} across the session (n={pace.n})"

    return {
        "step_size": {"value": step_size, "why": why_step, "source": "measured" if wm.state == "measured" else "default"},
        "lead_format_prior": {"value": lead_format_prior, "why": why_lead, "source": "stated" if stated_format else "default"},
        "pace": {"value": pace_lever, "why": why_pace, "source": "measured" if pace.state == "measured" else "default"},
        "review_halflife_prior": {"value": halflife, "why": why_hl, "source": "measured" if ltm.state == "measured" else "default"},
        "hint_policy": {"value": hint_policy, "why": why_hint, "source": "measured" if persist.state == "measured" else "default"},
        "confidence_prompting": {"value": confidence_prompting, "why": why_conf, "source": "measured" if meta.state == "measured" else "default"},
        "tone": {
            "value": tone,
            "why": "you chose how you want to hear that you got something wrong (stated)" if tone_stated else "default tone until you choose one",
            "source": "stated" if tone_stated else "default",
        },
        "language": {
            "value": language,
            "why": "you chose the language for explanations (stated)" if language_stated else "default language until you choose one",
            "source": "stated" if language_stated else "default",
        },
        "timers_visible": {
            "value": timers_visible,
            "why": (
                "you said timers " + ("help" if timers_visible else "stress you") + " (stated)"
                if "timers_help" in prefs
                else "timers are visible by default until you tell us otherwise"
            ),
            "source": "stated" if "timers_help" in prefs else "default",
        },
        "attention_note": {"value": att.summary, "why": "attention and self-control across the session", "source": "measured" if att.state == "measured" else "default"},
        "reasoning_note": {"value": reason.summary, "why": "how you tested the machine's rule", "source": "measured" if reason.state == "measured" else "default"},
    }


def preference_vs_performance(
    factors: Mapping[str, Factor], preferences: Mapping[str, Any], *, outcomes: Mapping[str, Any] | None = None
) -> dict[str, Any] | None:
    """The comparison row of §5.4: what the learner said, next to what was measured.

    It returns ``None`` rather than a sentence when there is nothing measured to compare — the brief
    forbids inventing a verdict, and "you picked diagrams and got 40% right" is not a comparison
    unless the same learner was also taught another way.
    """
    stated = preferences.get("preferred_format")
    if not stated or not outcomes:
        return None
    by_format = outcomes.get("by_format") or {}
    rows = []
    for fmt, stats in by_format.items():
        n = int(stats.get("n") or 0)
        if n < 3:
            continue
        rows.append({"format": fmt, "n": n, "accuracy": round(float(stats.get("correct") or 0) / n, 3)})
    if not rows:
        return None
    rows.sort(key=lambda r: -r["accuracy"])
    best = rows[0]
    return {
        "stated": stated,
        "measured": rows,
        "sentence": (
            f"You picked {str(stated).replace('_', ' ')}; after "
            f"{best['format'].replace('_', ' ')} you did best, {best['accuracy']:.0%} right over {best['n']} questions."
        ),
        "note": "this compares formats the app has actually shown you, not a test of learning styles",
    }


def snapshot_payload(
    factors: Mapping[str, Factor],
    *,
    levers_data: Mapping[str, Any],
    preferences: Mapping[str, Any],
    completion: Mapping[str, Any],
    preference_row: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    measured_count = sum(1 for f in factors.values() if f.state == "measured")
    return {
        "factors": [factors[k].as_dict() for k in FACTORS],
        "measured": measured_count,
        "total": len(FACTORS),
        "levers": dict(levers_data),
        "preferences": dict(preferences),
        "preference_vs_performance": dict(preference_row) if preference_row else None,
        "completion": dict(completion),
        "note": (
            "every number here is a range measured on your own answers, with the count behind it; "
            "nothing is a score out of 100 and nothing is compared with other learners"
        ),
    }


__all__ = [
    "FACTOR_TITLES",
    "FACTORS",
    "MIN_N",
    "Factor",
    "build_factors",
    "levers",
    "preference_vs_performance",
    "snapshot_payload",
]
