"""The six baseline games: deterministic task generation and scoring.

Round brief §5.2. Three rules shape this file:

1. **Code scores, never a model.** Every task is generated from a seed and every response is judged
   by arithmetic or an exact-match rule here. A model call in the scoring path would make a
   measurement depend on a second, unrepeatable sample.
2. **The task stream is a pure function of (seed, game, the trials already recorded).** Nothing is
   kept in memory between requests, so a learner can close the tab mid-game, come back on another
   device, and be served the same next task. It is also what makes partial completion free: the
   stream simply stops where the learner stopped.
3. **A practice round is not scored.** Every game opens with one unscored task whose only job is to
   show the shape of the game; confusion about instructions must not become a measurement.

The brief asks for specific numbers per game (forward span, falsifying-input rate, time to first
hint, recall-vs-recognition gap, …). Each is computed in :mod:`adapt.baseline.factors` from the
trials this module emits, and every trial records what it needs to be scored later — including the
latency the client measured, the wager, and whether the attempt was the practice one.
"""

from __future__ import annotations

import hashlib
import random
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

# --------------------------------------------------------------------------- #
# deterministic plumbing
# --------------------------------------------------------------------------- #


def seed_of(*parts: Any) -> int:
    """A stable seed from any parts. ``hash()`` is salted per process; this is not.

    Masked to 62 bits so it fits a SQLite ``INTEGER`` (signed 64-bit, and the value is stored on the
    run row so a learner resuming on another device gets the same codewords). A full 64-bit digest
    raised ``OverflowError`` on insert — a seed that cannot be saved is not a seed.
    """
    raw = "|".join(str(p) for p in parts)
    return int.from_bytes(hashlib.sha256(raw.encode("utf-8")).digest()[:8], "big") & ((1 << 62) - 1)


def rng(seed: int, *parts: Any) -> random.Random:
    return random.Random(seed_of(seed, *parts))


@dataclass(frozen=True)
class Trial:
    """One recorded task attempt, as the scorer sees it."""

    task_id: str
    game: str
    index: int
    phase: str  # practice | measure | show
    correct: bool | None
    latency_ms: int | None = None
    wager: int | None = None
    response: Mapping[str, Any] = field(default_factory=dict)
    task: Mapping[str, Any] = field(default_factory=dict)

    @property
    def measured(self) -> bool:
        return self.phase != "practice"


def measured(trials: Iterable[Trial]) -> list[Trial]:
    return [t for t in trials if t.measured]


# --------------------------------------------------------------------------- #
# word and number material
# --------------------------------------------------------------------------- #
#: Codename → place, neutral, non-overlapping, and short enough to read in a 45-second study
#: window. Deliberately culture-neutral (no landmarks, no brands) and phonologically distinct so
#: the pairs are not confusable with each other — the test is memory, not discrimination.
CODEWORD_PAIRS: tuple[tuple[str, str], ...] = (
    ("Vasko", "the harbour"),
    ("Neril", "a green bridge"),
    ("Domal", "the north gate"),
    ("Pirel", "a stone tower"),
    ("Sarn", "the salt market"),
    ("Tuvik", "a low hill"),
    ("Mebra", "the old well"),
    ("Quilo", "a red roof"),
    ("Harn", "the river bend"),
    ("Solva", "a quiet bay"),
    ("Drupe", "the east field"),
    ("Kendal", "a white tower"),
)

#: Language for the codewords themselves. The *pairs* are made-up words on purpose: a real word
#: would be recalled from the learner's own vocabulary and stop measuring the encoding.
CODEWORD_INSTRUCTIONS = {
    "en": "You will see eight made-up code words, each with a place. Learn them.",
    "sw": "Utaona maneno manane ya kubuni, kila moja na mahali pake. Yajifunze.",
    "mixed": "You will see eight made-up code words (maneno ya kubuni), each with a place (mahali).",
}

#: Things to hold while answering distractors ("Shopkeeper"). Concrete, one syllable of meaning,
#: and drawn from ordinary life so nothing needs explaining.
SHOP_ITEMS: tuple[str, ...] = (
    "a blue pen",
    "two batteries",
    "a loaf of bread",
    "a phone charger",
    "a packet of tea",
    "a small notebook",
    "an umbrella",
    "a bottle of water",
    "a bus ticket",
    "a pair of socks",
)

#: The trivial trials that give a pace estimate (brief §5.2 "quick draw, folded in").
#: Chosen so a wrong answer means a mis-tap, not a knowledge gap.
QUICK_TRIALS = (
    ("tap the bigger number", ("7", "3"), "7"),
    ("tap the bigger number", ("12", "21"), "21"),
    ("tap the smaller number", ("8", "4"), "4"),
    ("tap the bigger number", ("0.5", "0.7"), "0.7"),
    ("tap the smaller number", ("99", "100"), "99"),
    ("tap the bigger number", ("1/2", "1/4"), "1/2"),
    ("tap the longer word", ("cat", "elephant"), "elephant"),
    ("tap the heavier one", ("a feather", "a brick"), "a brick"),
)

#: Trap questions: the intuitive answer is wrong, and the point is whether the learner pauses.
#: Both are well-known, checkable, and cheap to explain, so the item is not the problem — the
#: hesitation is the measurement (pause time and intuitive-vs-correct, brief §5.2).
TRAPS: tuple[dict[str, Any], ...] = (
    {
        "id": "trap_bat_ball",
        "story": "A bat and a ball cost 1.10 together. The bat costs 1.00 more than the ball.",
        "question": "How much does the ball cost, in the currency's units?",
        "options": ["0.10", "0.05", "1.00", "0.55"],
        "intuitive": "0.10",
        "correct": "0.05",
        "explain": "If the ball were 0.10 the bat would be 1.10 and the pair 1.20. The ball is 0.05.",
    },
    {
        "id": "trap_lily",
        "story": "A patch of lily pads doubles in size every day and covers a whole lake on day 48.",
        "question": "On which day did it cover half the lake?",
        "options": ["24", "47", "36", "48"],
        "intuitive": "24",
        "correct": "47",
        "explain": "Doubling means the day before full is half. Day 47.",
    },
)

#: Rule families for "Crack the machine" (brief §5.2). Deterministic parametric families in code:
#: R1 one step, R2 two steps or a conditional, R3 the rule silently switches after the third try.
MACHINE_FAMILIES: tuple[str, ...] = ("double", "add", "times_then_add", "square", "mod", "absolute_diff")


# --------------------------------------------------------------------------- #
# Vault lights (working memory)
# --------------------------------------------------------------------------- #
VAULT_FORWARD_START = 3
VAULT_MAX_SPAN = 9
#: +1 after two successes at a length; stop after two consecutive failures (brief §5.2).
VAULT_STOP_FAILURES = 2
VAULT_SUCCESSES_TO_GROW = 2


def _vault_sequence(seed: int, direction: str, length: int, attempt: int) -> list[int]:
    """A grid sequence of ``length`` tile indices, never repeating a tile inside one sequence."""
    r = rng(seed, "vault", direction, length, attempt)
    tiles = list(range(9))
    r.shuffle(tiles)
    return tiles[:length]


def vault_next(seed: int, trials: Sequence[Trial], direction: str) -> dict[str, Any] | None:
    """The next vault task, or ``None`` when this direction is finished.

    The staircase is replayed from the trial log rather than stored: length rises after two
    successes at that length and the block ends after two consecutive failures, so the *history* is
    the state.
    """
    done = [t for t in trials if t.task.get("direction") == direction]
    # The practice trial teaches the direction; it never counts toward the staircase.
    practice_done = any(t.phase == "practice" for t in done)
    if not practice_done:
        seq = _vault_sequence(seed, direction, 3, 0)
        return {
            "id": f"vault_{direction}_practice",
            "game": "vault",
            "phase": "practice",
            "direction": direction,
            "length": 3,
            "sequence": seq,
            "answer_direction": "forward",
            "prompt": (
                "Watch the tiles light up, then tap them in the same order."
                if direction == "forward"
                else "Now tap them in the reverse of the order you saw."
            ),
            "practice": True,
            "max_latency_ms": 30000,
        }
    length = VAULT_FORWARD_START
    attempt = 0
    strengths: list[bool] = []
    fails_in_a_row = 0
    for t in measured(done):
        if t.correct:
            fails_in_a_row = 0
            strengths.append(True)
        else:
            fails_in_a_row += 1
            strengths.append(False)
        if fails_in_a_row >= VAULT_STOP_FAILURES:
            return None
        # Two successes at this length: grow.
        if sum(1 for s in strengths[-(VAULT_SUCCESSES_TO_GROW * 2) :] if s) >= VAULT_SUCCESSES_TO_GROW:
            recent = strengths[-VAULT_SUCCESSES_TO_GROW:]
            if all(recent):
                if length >= VAULT_MAX_SPAN:
                    return None
                length += 1
                attempt = 0
                strengths = []
                continue
        attempt += 1
        if attempt >= 4:
            return None
    wanted = "forward" if len(measured(done)) == 0 else "backward"
    seq = _vault_sequence(seed, direction, length, attempt)
    return {
        "id": f"vault_{direction}_{length}_{attempt}",
        "game": "vault",
        "phase": "measure",
        "direction": direction,
        "length": length,
        "sequence": seq,
        # Both directions are graded identically: the learner taps tiles, the server compares
        # against the sequence or its reverse. Recording the rule makes a mis-scored trial
        # legible after the fact.
        "answer_direction": wanted,
        "prompt": (
            f"{length} tiles. Tap them in the same order."
            if wanted == "forward"
            else f"{length} tiles. Tap them in the reverse order."
        ),
        "practice": False,
        "max_latency_ms": 30000,
    }


def vault_score(task: Mapping[str, Any], response: Mapping[str, Any]) -> dict[str, Any]:
    """Grade a tap sequence. Exact match only — a near-miss is a miss, and partially right is a
    different finding from right (the trial keeps both)."""
    target = list(task.get("sequence") or [])
    if task.get("answer_direction") == "backward":
        target = list(reversed(target))
    taps = [int(x) for x in (response.get("taps") or []) if isinstance(x, (int, float, str)) and str(x).strip() != ""]
    return {
        "correct": taps == target,
        "expected": target,
        "given": taps,
        "prefix_len": _common_prefix(taps, target),
        "position_errors": sum(1 for a, b in zip(taps, target) if a != b),
    }


def _common_prefix(a: Sequence[int], b: Sequence[int]) -> int:
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def shopkeeper_next(seed: int, trials: Sequence[Trial]) -> dict[str, Any] | None:
    """The distraction task: hold three items for a short while, answering true/false in between.

    Two trials, both measured: one where the distractors are answered and the items recalled
    afterwards. `recall under distraction` is compared with the same learner's plain span, which is
    why both live in the working-memory factor.
    """
    # Filtered to this game's own trials: `next_task_for` hands every generator the whole run's
    # log, and counting all of it here meant the stage looked finished the moment anything else had
    # been measured -- so the shopkeeper never ran at all and "recall under distraction" was
    # silently missing from the working-memory factor.
    done = [t for t in trials if t.game == "shopkeeper"]
    if not done:
        return {
            "id": "vault_shop_practice",
            "game": "shopkeeper",
            "phase": "practice",
            "items": list(rng(seed, "shop", 0).sample(SHOP_ITEMS, 3)),
            "distractors": _distractors(seed, 0, 3),
            "prompt": "Hold these three items. Answer the quick questions. Then type the items back.",
            "practice": True,
            "max_latency_ms": 60000,
        }
    if len(measured(done)) >= 2:
        return None
    round_no = len(done)
    return {
        "id": f"vault_shop_{round_no}",
        "game": "shopkeeper",
        "phase": "measure",
        "items": list(rng(seed, "shop", round_no).sample(SHOP_ITEMS, 3)),
        "distractors": _distractors(seed, round_no, 3),
        "prompt": "Hold these three items. Answer the quick questions. Then type the items back.",
        "practice": False,
        "max_latency_ms": 60000,
    }


def _distractors(seed: int, round_no: int, count: int) -> list[dict[str, Any]]:
    """Three quick true/false statements about the world, half of them false."""
    r = rng(seed, "distract", round_no)
    pool = [
        ("The month after March is April.", True),
        ("A week has nine days.", False),
        ("Water freezes at 0 degrees Celsius.", True),
        ("A triangle has four sides.", False),
        ("Seven is an odd number.", True),
        ("The first month of the year is December.", False),
        ("Half of 20 is 10.", True),
        ("A kilometre is shorter than a metre.", False),
    ]
    r.shuffle(pool)
    return [{"id": f"d{round_no}_{i}", "text": text, "answer": bool(truth)} for i, (text, truth) in enumerate(pool[:count])]


def shopkeeper_score(task: Mapping[str, Any], response: Mapping[str, Any]) -> dict[str, Any]:
    recalled = [str(x).strip().lower() for x in (response.get("items") or [])]
    wanted = [str(x).strip().lower() for x in (task.get("items") or [])]
    hits = [w for w in wanted if any(w in f or f in w for f in recalled if f)]
    distractor_answers = response.get("distractors") or {}
    distractor_hits = 0
    for d in task.get("distractors") or []:
        given = distractor_answers.get(d["id"])
        if given is None:
            continue
        if bool(given) == bool(d["answer"]):
            distractor_hits += 1
    return {
        "correct": len(hits) == len(wanted),
        "recalled": recalled,
        "wanted": wanted,
        "hits": len(hits),
        "distractor_accuracy": (distractor_hits / len(task.get("distractors") or [None])) if task.get("distractors") else None,
    }


# --------------------------------------------------------------------------- #
# Spy codewords (long-term memory)
# --------------------------------------------------------------------------- #
CODEWORD_STUDY_MS = 45_000


def codewords_study(seed: int, trials: Sequence[Trial]) -> dict[str, Any] | None:
    """The study screen itself. Not a trial to be scored — it is what is being learned."""
    if any(t.game == "codewords_study" for t in trials):
        return None
    pairs = rng(seed, "codewords").sample(CODEWORD_PAIRS, 8)
    return {
        "id": "codewords_study",
        "game": "codewords_study",
        "phase": "show",
        "pairs": [{"code": c, "place": p} for c, p in pairs],
        "study_ms": CODEWORD_STUDY_MS,
        "prompt": "Learn these eight pairs. You will be asked for them again later.",
        "max_latency_ms": CODEWORD_STUDY_MS + 30_000,
    }


def codewords_pairs(seed: int) -> list[dict[str, str]]:
    """The exact set this learner was shown — derived from the seed, so it cannot drift."""
    return [{"code": c, "place": p} for c, p in rng(seed, "codewords").sample(CODEWORD_PAIRS, 8)]


def codewords_cued(seed: int, trials: Sequence[Trial]) -> dict[str, Any] | None:
    """Immediate cued recall: the code word is given, the place is asked (or the reverse)."""
    done = [t for t in trials if t.game == "codewords_cued"]
    pairs = codewords_pairs(seed)
    if not done:
        first = pairs[0]
        return {
            "id": "codewords_cued_practice",
            "game": "codewords_cued",
            "phase": "practice",
            "prompt": f"Which place went with {first['code']}?",
            "cue": first["code"],
            "answer": first["place"],
            "practice": True,
            "max_latency_ms": 30000,
        }
    index = len(measured(done))
    if index >= len(pairs):
        return None
    pair = pairs[index]
    r = rng(seed, "cue_dir", index)
    forward = r.random() < 0.5
    return {
        "id": f"codewords_cued_{index}",
        "game": "codewords_cued",
        "phase": "measure",
        "prompt": (
            f"Which place went with {pair['code']}?" if forward else f"Which code word went with {pair['place']}?"
        ),
        "cue": pair["code"] if forward else pair["place"],
        "answer": pair["place"] if forward else pair["code"],
        "practice": False,
        "max_latency_ms": 30000,
    }


def codewords_delayed(seed: int, trials: Sequence[Trial], *, kind: str) -> dict[str, Any] | None:
    """Delayed free recall, then recognition (brief §5.2).

    The gap between free recall and recognition is itself the measurement — a learner who recognises
    what they could not produce has a retrieval problem, not an encoding one, and that changes what
    the app should do next.
    """
    done = [t for t in trials if t.game == "codewords_delayed" and t.phase == "measure"]
    pairs = codewords_pairs(seed)
    if kind == "free":
        if done:
            return None
        return {
            "id": "codewords_delayed_free",
            "game": "codewords_delayed",
            "kind": "free",
            "phase": "measure",
            "prompt": "Type as many of the eight code words as you can remember, separated by commas.",
            "max_latency_ms": 90_000,
        }
    if len(done) < 1:
        return None
    index = len(done) - 1
    if index >= len(pairs) * 2:
        return None
    if index >= len(pairs):
        return None
    pair = pairs[index]
    r = rng(seed, "recog", index)
    options = [pair["place"]]
    for other in pairs:
        if other["place"] != pair["place"]:
            options.append(other["place"])
    r.shuffle(options)
    options = options[:4]
    if pair["place"] not in options:  # pragma: no cover - defensive, the shuffle cannot drop it
        options[0] = pair["place"]
    return {
        "id": f"codewords_recog_{index}",
        "game": "codewords_delayed",
        "kind": "recognition",
        "phase": "measure",
        "prompt": f"Recognise: which place went with {pair['code']}?",
        "cue": pair["code"],
        "options": options,
        "answer": pair["place"],
        "max_latency_ms": 30000,
    }


def codewords_next_day(seed: int, trials: Sequence[Trial]) -> dict[str, Any] | None:
    """The next-day check: three cues from the same set, no new study.

    ``done`` is filtered to this game's own trials. Counting every measured trial in the run would
    make the check vanish the moment the learner had answered three questions of anything — which is
    exactly what it did until a test with a stop-early baseline caught it.
    """
    done = [t for t in trials if t.game == "codewords_next_day" and t.phase == "measure"]
    if len(done) >= 3:
        return None
    pairs = codewords_pairs(seed)
    pair = pairs[len(done) % len(pairs)]
    return {
        "id": f"codewords_next_day_{len(done)}",
        "game": "codewords_next_day",
        "phase": "measure",
        "prompt": f"Next-day check: which place went with {pair['code']}?",
        "cue": pair["code"],
        "answer": pair["place"],
        "max_latency_ms": 30000,
    }


def recall_score(task: Mapping[str, Any], response: Mapping[str, Any]) -> dict[str, Any]:
    """Free or cued recall: a normalised comparison, tolerant of case and articles.

    "the harbour" and "harbour" are the same memory; marking them apart would measure typing.
    """
    given = str(response.get("text") or response.get("value") or "")
    wanted = str(task.get("answer") or "")
    if task.get("kind") == "free":
        tokens = {_norm(x) for x in re.split(r"[,;/\n]+", given) if _norm(x)}
        hits = sum(1 for pair in codewords_pairs(int(response.get("seed") or 0)) if _norm(pair["code"]) in tokens)
        # Free recall is scored against the code words (the produce-able half of the pair); the
        # place is what recognition asks about.
        return {"correct": hits >= 5, "hits": hits, "given_tokens": sorted(tokens)}
    ok = _norm(given) == _norm(wanted) or _norm(wanted) in _norm(given)
    return {"correct": bool(ok), "expected": wanted, "given": given}


def _norm(text: str) -> str:
    t = re.sub(r"[^a-z0-9 ]+", " ", str(text).lower())
    t = re.sub(r"\b(the|a|an)\b", " ", t)
    return " ".join(t.split())


# --------------------------------------------------------------------------- #
# Crack the machine (reasoning)
# --------------------------------------------------------------------------- #
MACHINE_ROUNDS = 3
MACHINE_MAX_WRONG_GUESSES = 2
MACHINE_TEST_INPUTS = 5


@dataclass(frozen=True)
class MachineRule:
    """One hidden rule. ``apply`` is the only thing that knows what it is."""

    name: str
    family: str
    params: dict[str, Any]

    def apply(self, x: float) -> float:
        p = self.params
        if self.family == "double":
            return 2 * x
        if self.family == "add":
            return x + p["k"]
        if self.family == "times_then_add":
            return x * p["m"] + p["k"]
        if self.family == "square":
            return x * x
        if self.family == "mod":
            return float(int(x) % p["k"])
        if self.family == "absolute_diff":
            return abs(x - p["k"])
        raise ValueError(f"unknown family {self.family!r}")  # pragma: no cover


def machine_rule(seed: int, round_no: int) -> tuple[MachineRule, MachineRule | None, int]:
    """The rule(s) for a round, plus the switch point for round 3.

    Round 1: one step. Round 2: two steps or a conditional. Round 3: the rule silently switches
    after the third probe. The second rule in round 3 is always a *different family*, because a
    switch between two neighbouring parameters is not the thing being measured — noticing a real
    change of behaviour is (brief §5.2).
    """
    r = rng(seed, "machine", round_no)
    if round_no == 1:
        family = r.choice(("double", "add"))
        rule = MachineRule(
            name={"double": "double the number", "add": f"add {r.choice([3, 5, 7])}"}[family],
            family=family,
            params={"m": 2, "k": r.choice([3, 5, 7])},
        )
        return rule, None, 0
    if round_no == 2:
        family = r.choice(("times_then_add", "square"))
        if family == "times_then_add":
            m, k = r.choice([(3, 2), (2, 5), (4, 1)])
            return MachineRule(name=f"multiply by {m} then add {k}", family=family, params={"m": m, "k": k}), None, 0
        return MachineRule(name="square the number", family=family, params={}), None, 0
    first = MachineRule(name="add 4", family="add", params={"k": 4})
    second = MachineRule(name="the number of a different kind", family="mod", params={"k": r.choice([3, 5, 7])})
    return first, second, 3


def machine_next(seed: int, trials: Sequence[Trial], round_no: int) -> dict[str, Any] | None:
    """The next state of a machine round: probing, guessing, or finished.

    A round ends when the learner has used five test inputs, made two wrong guesses, or guessed
    right. All three endings are recorded, because "solved in one probe" and "solved after five"
    are different findings.
    """
    done = [t for t in trials if t.task.get("round") == round_no]
    if not done:
        rule, second, switch_at = machine_rule(seed, round_no)
        return {
            "id": f"machine_r{round_no}_practice",
            "game": "machine",
            "round": round_no,
            "phase": "practice",
            "prompt": "Type a number, see what the machine does with it. Then guess the rule.",
            "max_probes": MACHINE_TEST_INPUTS,
            "max_wrong_guesses": MACHINE_MAX_WRONG_GUESSES,
            "switches": bool(second),
            "practice": True,
            "warmup_inputs": [1, 2, 5],
            "max_latency_ms": 120000,
        }
    probes = [t for t in done if t.task.get("action") == "probe"]
    guesses = [t for t in done if t.task.get("action") == "guess"]
    wrong = [g for g in guesses if not g.correct]
    solved = any(g.correct for g in guesses)
    if solved or len(wrong) >= MACHINE_MAX_WRONG_GUESSES or len(probes) >= MACHINE_TEST_INPUTS:
        return None
    return {
        "id": f"machine_r{round_no}_step{len(done)}",
        "game": "machine",
        "round": round_no,
        "phase": "measure",
        "prompt": "Test another input, or guess the rule.",
        "max_probes": MACHINE_TEST_INPUTS,
        "max_wrong_guesses": MACHINE_MAX_WRONG_GUESSES,
        "probes_used": len(probes),
        "wrong_guesses": len(wrong),
        "max_latency_ms": 120000,
    }


def machine_respond(seed: int, round_no: int, task: Mapping[str, Any], response: Mapping[str, Any], trials: Sequence[Trial]) -> dict[str, Any]:
    """Score a probe or a guess.

    A *probe* is scored only for what it reveals about the learner's search: the falsifying-input
    rate asks whether they tested a boundary (0, a negative, a large number, a decimal) or only
    inputs that would confirm what they already believed (brief §5.2). A *guess* is compared with
    the rule the code is holding.
    """
    rule_one, rule_two, switch_at = machine_rule(seed, round_no)
    probes_done = len([t for t in trials if t.task.get("round") == round_no and t.task.get("action") == "probe"])
    rule = rule_two if (rule_two is not None and probes_done >= switch_at + 1) else rule_one
    action = str(response.get("action") or "probe")
    if action == "guess":
        text = str(response.get("text") or "")
        ok = _guess_matches(text, rule)
        return {"correct": bool(ok), "action": "guess", "expected": rule.name, "given": text, "rule": rule.name}
    raw = response.get("value")
    try:
        x = float(raw)
    except (TypeError, ValueError):
        return {"correct": None, "action": "probe", "error": "not a number", "value": raw}
    return {
        "correct": None,  # a probe is not right or wrong; it is informative or not
        "action": "probe",
        "value": x,
        "output": rule.apply(x),
        "falsifying": is_falsifying(x, probes_done),
        "rule_name": rule.name,
    }


def is_falsifying(x: float, probes_done: int) -> bool:
    """Did this probe *test* the hypothesis, or only fit it?

    The rule: 0, a negative, a value past 100, or a decimal all force the machine to behave
    differently from the "+1 at a time" family a learner tends to assume. Probing the same shape
    five times is the behaviour this measures.
    """
    return bool(x == 0 or x < 0 or abs(x) > 100 or (x != int(x)))


def _guess_matches(text: str, rule: MachineRule) -> bool:
    """Whether a typed guess names the rule. Deliberately literal, and generous about *form*.

    ``x*3+2``, ``times 3 add 2``, ``multiply by 3 then add 2`` are the same rule, and a learner who
    can say it should not lose the point for phrasing. What it will not accept is a vague answer
    ("it doubles" for a rule that does not double): a wrong guess has to count, or the round ends
    on the first try.
    """
    t = re.sub(r"[^a-z0-9+*/x ]+", " ", str(text).lower())
    t = t.replace("x", " ").replace("×", " ")
    p = rule.params
    if rule.family == "double":
        return any(k in t for k in ("double", "2 ", "*2", "2*", "times 2", "twice"))
    if rule.family == "add":
        return any(k in t for k in (f"+{p['k']}", f"add {p['k']}", f"plus {p['k']}", f"{p['k']} more"))
    if rule.family == "times_then_add":
        return (
            any(k in t for k in (f"*{p['m']}", f"times {p['m']}", f"multiply by {p['m']}"))
            and any(k in t for k in (f"+{p['k']}", f"add {p['k']}", f"plus {p['k']}"))
        )
    if rule.family == "square":
        return any(k in t for k in ("square", "* itself", "times itself", "**2"))
    if rule.family == "mod":
        return any(k in t for k in (f"remainder", f"mod", f"left over")) and str(p["k"]) in t
    if rule.family == "absolute_diff":
        return "difference" in t and str(p["k"]) in t
    return False  # pragma: no cover


def machine_switched(seed: int, round_no: int, trials: Sequence[Trial]) -> dict[str, Any]:
    """When the round-3 switch happened, and whether it was noticed.

    "Noticed" means a probe after the switch, or a guess after it that matches the *new* rule. A
    learner who keeps probing and works it out later counts; one who never changes strategy does
    not, and the trial log says which it was.
    """
    rule_one, rule_two, switch_at = machine_rule(seed, round_no)
    if rule_two is None:
        return {"switched": False}
    probes = [t for t in trials if t.task.get("round") == round_no and t.task.get("action") == "probe"]
    after = probes[switch_at:]
    # "Noticed" = a probe after the switch whose *output* matched the new rule. That is the moment
    # the learner's model of the machine had to change; a guess that happens to name the new rule
    # counts too. Reading it from the graded output (rather than from a typed sentence) means it is
    # true of probes as well as guesses — which is where noticing usually shows up first.
    noticed_at: int | None = None
    for t in after:
        scored = t.response.get("scored") or {}
        value, output = scored.get("value"), scored.get("output")
        if value is None or output is None:
            continue
        try:
            if abs(float(rule_two.apply(float(value))) - float(output)) < 1e-9:
                noticed_at = t.index
                break
        except (TypeError, ValueError):
            continue
    guessed_new = [
        t for t in trials
        if t.task.get("round") == round_no and t.task.get("action") == "guess" and t.correct and str(t.response.get("rule") or "") == rule_two.name
    ]
    return {
        "switched": True,
        "switch_after_probe": switch_at,
        "probes_after_switch": len(after),
        "noticed": bool(noticed_at is not None or guessed_new),
        "probes_before_noticing": (noticed_at - switch_at) if noticed_at is not None else None,
    }


# --------------------------------------------------------------------------- #
# The locked box (persistence and help-seeking)
# --------------------------------------------------------------------------- #
LOCKED_BOX_HINTS: tuple[dict[str, Any], ...] = (
    {
        "id": "hint_nudge",
        "cost": 1,
        "text": "Write down what changes and what does not. One of them is the key.",
        "level": "nudge",
    },
    {
        "id": "hint_method",
        "cost": 2,
        "text": "Try the smallest case first, then the next one up, and look at the difference between them.",
        "level": "method",
    },
    {
        "id": "hint_near",
        "cost": 3,
        "text": "The answer for the first case is one of the numbers you have already written down. Check it.",
        "level": "near answer",
    },
)
LOCKED_BOX_HINT_SHOP = len(LOCKED_BOX_HINTS)


def locked_box_task(seed: int) -> dict[str, Any]:
    """A hard puzzle generated in code, with a hint shop and a walk-away button.

    It is stated to be hard on purpose and it is *not* scored on correctness: the measurement is
    when help is asked for, how much is taken, and what happens after a wrong attempt (brief §5.2).
    """
    r = rng(seed, "lockedbox")
    start = r.choice([1, 2, 3])
    step = r.choice([3, 5, 7])
    def shape(n: int) -> int:
        return start + step * (n - 1) * n // 2
    cases = [(1, shape(1)), (2, shape(2)), (3, shape(3)), (4, shape(4))]
    return {
        "id": "locked_box",
        "game": "locked_box",
        "phase": "measure",
        "prompt": (
            "A lock opens on a sequence of numbers. The first four are below. "
            "Work out the fifth. This one is hard on purpose — you can buy a hint, try again, "
            "or walk away and nothing bad happens."
        ),
        "cases": [{"n": n, "value": v} for n, v in cases],
        "answer": shape(5),
        "hint_shop": [dict(h) for h in LOCKED_BOX_HINTS],
        "max_latency_ms": 300_000,
    }


def locked_box_score(task: Mapping[str, Any], response: Mapping[str, Any]) -> dict[str, Any]:
    try:
        given = float(response.get("value"))
    except (TypeError, ValueError):
        return {"correct": False, "given": response.get("value"), "expected": task.get("answer")}
    expected = float(task.get("answer") or 0)
    return {"correct": abs(given - expected) < 1e-6, "given": given, "expected": expected}


# --------------------------------------------------------------------------- #
# Quick trials and traps (speed, attention, self-control)
# --------------------------------------------------------------------------- #
def quick_trial(index: int) -> dict[str, Any]:
    label, pair, answer = QUICK_TRIALS[index % len(QUICK_TRIALS)]
    return {
        "id": f"quick_{index}",
        "game": "quick",
        "phase": "measure",
        "prompt": label,
        "options": list(pair),
        "answer": answer,
        "max_latency_ms": 15000,
    }


def trap_task(index: int) -> dict[str, Any]:
    t = TRAPS[index % len(TRAPS)]
    return {
        "id": t["id"],
        "game": "trap",
        "phase": "measure",
        "story": t["story"],
        "prompt": t["question"],
        "options": list(t["options"]),
        "answer": t["correct"],
        "intuitive": t["intuitive"],
        "explain": t["explain"],
        "max_latency_ms": 60000,
    }


def choice_score(task: Mapping[str, Any], response: Mapping[str, Any]) -> dict[str, Any]:
    given = str(response.get("value") or response.get("text") or "").strip()
    expected = str(task.get("answer") or "").strip()
    return {"correct": given.lower() == expected.lower(), "expected": expected, "given": given}


# --------------------------------------------------------------------------- #
# Bet your coins (metacognition)
# --------------------------------------------------------------------------- #
MAX_WAGER = 3


def wager_allowed(task: Mapping[str, Any]) -> bool:
    """A wager needs an answer that can be right or wrong, and not a practice trial."""
    return task.get("phase") == "measure" and task.get("game") in ("codewords_cued", "codewords_delayed", "trap", "quick")


def calibration(trials: Sequence[Trial]) -> dict[str, Any]:
    """Confidence vs correctness, with a minimum-n rule and a range (brief §5.2).

    ``index`` — how much more confident the learner is than they are right — is only reported with
    at least eight wagers; below that the honest answer is "not enough data yet", which is a
    first-class state in this app rather than a zero.
    """
    wagered = [t for t in trials if t.wager is not None and t.correct is not None and t.phase == "measure"]
    n = len(wagered)
    if n == 0:
        return {"n": 0, "state": "not_enough_data", "note": "no wagers yet"}
    mean_confidence = sum(float(t.wager) for t in wagered) / n / MAX_WAGER
    accuracy = sum(1 for t in wagered if t.correct) / n
    out: dict[str, Any] = {
        "n": n,
        "accuracy": round(accuracy, 3),
        "mean_confidence": round(mean_confidence, 3),
        "state": "measured" if n >= 8 else "not_enough_data",
        "range": [round(max(0.0, accuracy - 1.96 * (accuracy * (1 - accuracy) / n) ** 0.5), 3),
                  round(min(1.0, accuracy + 1.96 * (accuracy * (1 - accuracy) / n) ** 0.5), 3)],
    }
    if n >= 8:
        over = [t for t in wagered if (t.wager or 0) >= 2 and not t.correct]
        under = [t for t in wagered if (t.wager or 0) <= 1 and t.correct]
        out["overconfidence_rate"] = round(len(over) / max(1, len([t for t in wagered if (t.wager or 0) >= 2])), 3)
        out["underconfidence_rate"] = round(len(under) / max(1, len([t for t in wagered if t.correct])), 3)
    return out


def predicted_vs_actual(trials: Sequence[Trial]) -> dict[str, Any] | None:
    predicted = [t for t in trials if t.task.get("action") == "predict"]
    if not predicted:
        return None
    p = predicted[0]
    try:
        guess = float(p.response.get("value"))
    except (TypeError, ValueError):
        return None
    scored = [t for t in measured(trials) if t.correct is not None]
    if not scored:
        return None
    actual = sum(1 for t in scored if t.correct) / len(scored)
    return {"predicted": guess, "actual": round(actual, 3), "n": len(scored), "gap": round(guess - actual, 3)}


__all__ = [
    "CODEWORD_INSTRUCTIONS",
    "CODEWORD_PAIRS",
    "LOCKED_BOX_HINTS",
    "MAX_WAGER",
    "Trial",
    "calibration",
    "choice_score",
    "codewords_cued",
    "codewords_delayed",
    "codewords_next_day",
    "codewords_pairs",
    "codewords_study",
    "is_falsifying",
    "locked_box_score",
    "locked_box_task",
    "machine_next",
    "machine_respond",
    "machine_rule",
    "machine_switched",
    "measured",
    "predicted_vs_actual",
    "quick_trial",
    "recall_score",
    "rng",
    "seed_of",
    "shopkeeper_next",
    "shopkeeper_score",
    "trap_task",
    "vault_next",
    "vault_score",
    "wager_allowed",
]
