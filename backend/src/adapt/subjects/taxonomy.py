"""The subject skill taxonomy (round brief §6.2), as versioned code constants.

Tier 2 of the brief is "the core skills of the whole subject, valid for any branch". That is a
different measurement from the mind baseline (Tier 1: how the learner works right now) and from the
topic ladder (Tier 3: where they are in the branch they chose). The skills below are the middle
layer: what a *subject* asks of somebody, whichever topic inside it they pick.

Four things this module is strict about, because each one has a failure mode that is invisible
until a learner is misled by it:

* **Versioned.** ``TAXONOMY_VERSION`` is stored with every rating and every trial. A rating of 3
  for "argument" means nothing unless you know which list of skills it was a rating *of*, and a
  learner who was measured against last term's taxonomy must not be silently re-read against this
  one.
* **Skill ids are stable and prefixed.** ``hist.argument`` never changes meaning; a new skill gets
  a new id and a new version. Nothing here is keyed by list position.
* **Each skill has one fast game and one scorer.** §6.2's rule is "~40 seconds", so ``game.seconds``
  is capped at :data:`MAX_GAME_SECONDS` and :func:`validate_taxonomy` refuses a skill that exceeds
  it. A skill nobody can measure in 40 seconds is not a skill for this round.
* **Each skill says what it implies.** ``implies`` is the sentence the branch-readiness panel shows,
  so a measurement is never presented without the use that was made of it.

Nothing here calls a model. The taxonomy is the *allowed list* the model's ratings are validated
against (brief §2: "the AI proposes, code decides"), so it has to be code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

#: Bump this whenever the skill list, a skill's meaning, or a game's scoring changes. Ratings and
#: trials store it, so an old measurement can always be read against the taxonomy it was made with.
TAXONOMY_VERSION = "subj-taxonomy-2026.10.1"

#: §6.2's "~40 second game". A hard cap rather than a target: the whole subject round has to fit in
#: a sitting, and four skills at three items each is already most of a learner's patience.
MAX_GAME_SECONDS = 45

#: How many items one skill game asks. Three is the smallest number that can distinguish "got it"
#: from "guessed it", and it is what makes 40 seconds realistic.
ITEMS_PER_SKILL = 3

#: The checklist size the brief fixes for anything graded by a checklist: three points, never more
#: (``assessment.teachback.CHECKLIST_SIZE`` is the same rule for teach-backs).
CHECKLIST_SIZE = 3

#: Subject families. ``generic`` is not a subject: it is the fallback list §6.2 asks for, derived
#: from the same universal mechanics, used for any subject nobody has authored skills for yet.
FAMILIES: tuple[dict[str, str], ...] = (
    {
        "id": "maths",
        "title": "Maths",
        "covers": "arithmetic, algebra, calculus, discrete maths, statistics, geometry",
    },
    {
        "id": "languages",
        "title": "Languages",
        "covers": "grammar, essay writing, comprehension, Kiswahili, second languages",
    },
    {
        "id": "sciences",
        "title": "Sciences",
        "covers": "biology, chemistry, physics, agriculture, computer science as a science",
    },
    {
        "id": "history",
        "title": "History, humanities and government",
        "covers": "history, civics, government, geography, religious education, business studies",
    },
    {
        "id": "generic",
        "title": "Any other subject",
        "covers": "the universal mechanics, used when a subject has no authored skill set",
    },
)

FAMILY_IDS: tuple[str, ...] = tuple(f["id"] for f in FAMILIES)

#: The id prefix each family uses. Skill ids are short (they appear in payloads, logs and the
#: learner's own evidence rows), so "languages" becomes ``lang.`` — and the mapping lives here
#: rather than being derived, because deriving it is how ``lang.`` and ``languages`` drift apart.
FAMILY_PREFIX: dict[str, str] = {
    "maths": "maths",
    "languages": "lang",
    "sciences": "sci",
    "history": "hist",
    "generic": "gen",
}


@dataclass(frozen=True)
class Skill:
    """One subject skill: what it is, how it is measured in 40 seconds, and what it implies.

    ``game`` is a *spec*, not content: it names the kind of task, how many of them, and what the
    learner is told. The tasks themselves are generated per learner from a seed in
    :mod:`adapt.subjects.games`, so two learners get the same skill measured with different numbers
    and the same learner gets the same task back after a reload.
    """

    id: str
    family: str
    title: str
    #: One sentence: what a score on this skill is a measurement *of*. Written so a learner can read
    #: it, because §6.3 requires the learner to see why a skill was chosen.
    measures: str
    #: One sentence: what the result implies for readiness in a branch of the subject.
    implies: str
    game: Mapping[str, Any] = field(default_factory=dict)
    #: ``deterministic`` = code marks it (an exact answer). ``checklist`` = three fixed points,
    #: graded locally by the rule grader in ``assessment.teachback`` and, when a model is
    #: configured, checked against a strict ``1: yes/no`` output. Never "the model decides".
    grader: str = "deterministic"

    @property
    def kind(self) -> str:
        return str(self.game.get("kind") or "choice")

    @property
    def seconds(self) -> int:
        return int(self.game.get("seconds") or MAX_GAME_SECONDS)

    def as_dict(self, *, with_game: bool = True) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "family": self.family,
            "title": self.title,
            "measures": self.measures,
            "implies": self.implies,
            "grader": self.grader,
            "seconds": self.seconds,
            "items": int(self.game.get("items") or ITEMS_PER_SKILL),
            # A skill row without the taxonomy version cannot be read against a later list.
            "taxonomy_version": TAXONOMY_VERSION,
        }
        if with_game:
            out["game"] = dict(self.game)
        return out


def _skill(
    skill_id: str,
    family: str,
    title: str,
    measures: str,
    implies: str,
    *,
    kind: str,
    instructions: str,
    seconds: int = 40,
    items: int = ITEMS_PER_SKILL,
    grader: str = "deterministic",
    wagerable: bool = True,
    **extra: Any,
) -> Skill:
    """Build a skill, keeping the game spec's shape identical for every one of them."""
    game: dict[str, Any] = {
        "kind": kind,
        "seconds": seconds,
        "items": items,
        "instructions": instructions,
        "wagerable": wagerable,
        **extra,
    }
    return Skill(id=skill_id, family=family, title=title, measures=measures, implies=implies, game=game, grader=grader)


# --------------------------------------------------------------------------- #
# maths (§6.2, first list)
# --------------------------------------------------------------------------- #

MATHS_SKILLS: tuple[Skill, ...] = (
    _skill(
        "maths.number_sense",
        "maths",
        "Number sense and estimation",
        "whether the size of a quantity is felt before it is computed",
        "if this is shaky, a branch that starts from formulas will be memorised rather than understood — start from quantities",
        kind="choice",
        instructions="Pick the closest value. Do not work it out exactly — estimate.",
        prompt_style="estimate",
    ),
    _skill(
        "maths.symbol_fluency",
        "maths",
        "Symbol fluency",
        "whether two expressions that look different read as the same statement",
        "algebra and calculus are unreadable without it: this is the gate for any symbolic branch",
        kind="choice",
        instructions="Are these two the same for every value of the letter, or not?",
        prompt_style="equal_pairs",
    ),
    _skill(
        "maths.translate_words",
        "maths",
        "Translating words and symbols",
        "whether a sentence can be turned into an expression, and an expression back into a sentence",
        "word problems and applied branches need this both ways; a one-way translation shows up as 'I can do the sums but not the questions'",
        kind="choice",
        instructions="Match the sentence to the expression that says the same thing.",
        prompt_style="word_to_symbol",
    ),
    _skill(
        "maths.logic_chains",
        "maths",
        "Logic chains",
        "whether a chain of conditions can be followed to its conclusion",
        "proof, discrete maths and any branch that says 'if… then…' depend on it",
        kind="choice",
        instructions="Follow the rules and pick what must be true.",
        prompt_style="logic_chain",
    ),
    _skill(
        "maths.pattern_structure",
        "maths",
        "Pattern and structure",
        "whether the rule behind a table or sequence can be found, not just the next term",
        "this is the same mechanic as the machine game in the mind baseline, in maths clothing: it predicts how fast a new method will be picked up",
        kind="numeric",
        instructions="Work out the rule, then type the missing value.",
        prompt_style="sequence_rule",
    ),
    _skill(
        "maths.graph_sense",
        "maths",
        "Graph sense",
        "whether a graph and the story it tells can be matched in both directions",
        "calculus, statistics and every science branch read graphs; without this the picture is decoration",
        kind="choice",
        instructions="Pick the graph that matches the description (described in words — no image needed).",
        prompt_style="graph_story",
    ),
    _skill(
        "maths.checking",
        "maths",
        "Checking somebody else's working",
        "whether a wrong step can be found in working that is not their own",
        "the strongest single predictor of independent study: a learner who can find the error can fix their own",
        kind="choice",
        instructions="One line of this working is wrong. Which one?",
        prompt_style="find_the_error",
    ),
)

# --------------------------------------------------------------------------- #
# languages (§6.2, second list)
# --------------------------------------------------------------------------- #

LANGUAGES_SKILLS: tuple[Skill, ...] = (
    _skill(
        "lang.vocabulary_context",
        "languages",
        "Vocabulary in context",
        "whether the meaning of an unfamiliar word can be inferred from the sentence around it",
        "a made-up word is used on purpose: real vocabulary measures what they have read, not how they read",
        kind="choice",
        instructions="The bold word is made up. Pick the meaning the sentence forces on it.",
        prompt_style="invented_word",
    ),
    _skill(
        "lang.grammar_sense",
        "languages",
        "Grammar sense",
        "whether a broken sentence can be fixed and the fix named",
        "essay-heavy branches need this to be automatic; if it is not, writing effort goes into sentences instead of ideas",
        kind="choice",
        instructions="Pick the version that is grammatically correct.",
        prompt_style="fix_the_sentence",
    ),
    _skill(
        "lang.comprehension",
        "languages",
        "Comprehension: literal and inference",
        "whether both what a passage says and what it implies can be picked out",
        "source work in history and question stems in sciences are both reading tasks; this is the floor under them",
        kind="choice",
        instructions="Read the short passage, then answer: one question is stated, one has to be inferred.",
        prompt_style="passage",
        items=2,
        seconds=45,
    ),
    _skill(
        "lang.flexibility",
        "languages",
        "Saying it differently",
        "whether one idea can be restated without losing its meaning",
        "explaining, revising and answering 'in your own words' all need it; it also predicts how well a second teaching format will land",
        kind="text",
        instructions="Say the same thing in your own words. Do not reuse the sentence's own key phrase.",
        prompt_style="restate",
        grader="deterministic",
        seconds=45,
    ),
    _skill(
        "lang.production",
        "languages",
        "Writing two sentences that do the job",
        "whether a short piece of writing hits the three points it was asked for",
        "marked against a fixed three-point checklist, never against style: this is what an essay branch runs on",
        kind="text",
        instructions="Write two sentences. Three points are marked, and they are listed for you.",
        prompt_style="two_sentences",
        rubric_hint="the two sentences must do the job they were given: subject, action, and a reason it matters",
        grader="checklist",
        seconds=45,
        wagerable=False,
    ),
)

# --------------------------------------------------------------------------- #
# sciences (§6.2, third list)
# --------------------------------------------------------------------------- #

SCIENCES_SKILLS: tuple[Skill, ...] = (
    _skill(
        "sci.prediction_models",
        "sciences",
        "Prediction and models",
        "whether a learner commits to an outcome before it is revealed",
        "predicting first is what turns a demonstration into evidence; a branch that teaches models needs it",
        kind="choice",
        instructions="Commit to what happens, then see whether you were right.",
        prompt_style="predict_reveal",
    ),
    _skill(
        "sci.cause_chains",
        "sciences",
        "Cause chains",
        "whether a process can be put in the right order",
        "cycles, reactions and mechanisms are all ordered chains: this is the skill that reads them",
        kind="order",
        instructions="Put the steps of the process in order, first to last.",
        prompt_style="order_process",
    ),
    _skill(
        "sci.data_sense",
        "sciences",
        "Data sense",
        "whether a table or graph yields both the fact and the trend",
        "practical and exam work in every science branch is half reading data; a fact without a trend is half a measurement",
        kind="choice",
        instructions="Read the data, then answer one question about the value and one about the trend.",
        prompt_style="read_the_data",
        items=2,
        seconds=45,
    ),
    _skill(
        "sci.experimental_thinking",
        "sciences",
        "Experimental thinking",
        "whether the flaw in an unfair test can be spotted",
        "this is the difference between recalling a method and being able to design one",
        kind="choice",
        instructions="This test is not fair. Pick the reason why.",
        prompt_style="spot_the_flaw",
    ),
    _skill(
        "sci.scale_sense",
        "sciences",
        "Scale sense",
        "whether orders of magnitude can be placed",
        "chemistry, physics and biology all fail quietly without it: the working is right and the size is absurd",
        kind="choice",
        instructions="Pick the size that is closest to the real thing.",
        prompt_style="orders_of_magnitude",
    ),
)

# --------------------------------------------------------------------------- #
# history / humanities / government (§6.2, fourth list)
# --------------------------------------------------------------------------- #

HISTORY_SKILLS: tuple[Skill, ...] = (
    _skill(
        "hist.chronology",
        "history",
        "Chronology",
        "whether events can be placed in time relative to each other",
        "a timeline is the scaffolding every history branch hangs its causes on; without it, causes get invented backwards",
        kind="order",
        instructions="Put these in order, earliest first.",
        prompt_style="timeline",
    ),
    _skill(
        "hist.cause_consequence",
        "history",
        "Cause and consequence",
        "whether the main cause can be picked out and defended against a plausible rival",
        "this is the mark scheme in every humanities branch: naming a cause scores little, ranking causes scores the marks",
        kind="choice",
        instructions="Pick the cause that mattered most, then say why in one sentence.",
        prompt_style="main_cause",
        items=2,
        seconds=45,
    ),
    _skill(
        "hist.source_analysis",
        "history",
        "Source analysis",
        "whether who wrote something, and who they were persuading, can be read off a source",
        "government and history branches both turn on this: a source read as a fact is a source that has persuaded you",
        kind="choice",
        instructions="Read the source note, then pick what it was written to do.",
        prompt_style="source_purpose",
    ),
    _skill(
        "hist.argument",
        "history",
        "Supporting a claim with evidence",
        "whether a claim is backed by a specific piece of evidence rather than by agreement",
        "the single skill an essay or a civics branch cannot do without",
        kind="text",
        instructions="Make the claim true for a reader who disagrees: one claim, one specific piece of evidence.",
        prompt_style="claim_evidence",
        rubric_hint="a claim, a specific piece of evidence for it, and the link between the two",
        grader="checklist",
        seconds=45,
        wagerable=False,
    ),
    _skill(
        "hist.perspective",
        "history",
        "Seeing the other side",
        "whether the same event can be described from a position that is not their own",
        "perspective-taking is what separates a summary from an account; it is also what a debate branch needs first",
        kind="text",
        instructions="Write the other side's own reason, in their words, as they would say it.",
        prompt_style="other_side",
        rubric_hint="the other side named, their own reason stated, and no caricature of them",
        grader="checklist",
        seconds=45,
        wagerable=False,
    ),
)

# --------------------------------------------------------------------------- #
# the generic fallback (§6.2, fifth list)
# --------------------------------------------------------------------------- #

GENERIC_SKILLS: tuple[Skill, ...] = (
    _skill(
        "gen.recognise",
        "generic",
        "Recognising the idea",
        "whether an example of the subject's central idea can be picked out from near-misses",
        "the entry rung of the ladder for any subject: if this fails, teaching should start below the topic",
        kind="choice",
        instructions="Pick the one that is an example of the idea.",
        prompt_style="recognise",
    ),
    _skill(
        "gen.recall",
        "generic",
        "Recalling the key fact",
        "whether the one fact the subject keeps coming back to can be produced without a prompt",
        "recall, not recognition: a branch built on a fact that has to be looked up every time is a branch that will not hold",
        kind="text",
        instructions="Write the key fact from memory. One line is enough.",
        prompt_style="recall",
        seconds=45,
    ),
    _skill(
        "gen.apply",
        "generic",
        "Applying it to a standard case",
        "whether the idea can be used on the case the subject always uses first",
        "this is the difference between a lesson that landed and a lesson that was enjoyed",
        kind="choice",
        instructions="Use the idea on this ordinary case.",
        prompt_style="apply",
    ),
    _skill(
        "gen.explain",
        "generic",
        "Explaining why",
        "whether the reason behind the idea can be stated, not just the idea",
        "the top rung of the ladder in every subject, and the one that tells a teacher the rest is real",
        kind="text",
        instructions="Explain why it works that way. Three points are marked.",
        prompt_style="explain",
        rubric_hint="the mechanism, why the alternative fails, and what would show it is true",
        grader="checklist",
        seconds=45,
        wagerable=False,
    ),
    _skill(
        "gen.spot_error",
        "generic",
        "Spotting the error",
        "whether a plausible but wrong use of the idea can be identified",
        "the most transferable study skill there is: it is what a learner does alone at night with no teacher",
        kind="choice",
        instructions="One of these uses the idea wrongly. Which?",
        prompt_style="spot_error",
    ),
    _skill(
        "gen.predict",
        "generic",
        "Predicting what comes next",
        "whether the idea can be run forwards to a case nobody showed them",
        "prediction is how a subject becomes usable: a learner who can predict has a model, not a memory",
        kind="choice",
        instructions="Commit to an answer before you are told.",
        prompt_style="predict",
    ),
)

#: Every skill, in family order. This tuple *is* the taxonomy; the model's ratings are validated
#: against exactly these ids and nothing else.
SKILLS: tuple[Skill, ...] = MATHS_SKILLS + LANGUAGES_SKILLS + SCIENCES_SKILLS + HISTORY_SKILLS + GENERIC_SKILLS

SKILL_IDS: tuple[str, ...] = tuple(s.id for s in SKILLS)

_BY_ID: dict[str, Skill] = {s.id: s for s in SKILLS}

#: The skills a family is measured with. ``generic`` is deliberately *not* merged into the others:
#: a family's own set replaces the generic list rather than adding to it, so a round stays four or
#: five skills long whatever the subject.
FAMILY_SKILLS: dict[str, tuple[str, ...]] = {
    "maths": tuple(s.id for s in MATHS_SKILLS),
    "languages": tuple(s.id for s in LANGUAGES_SKILLS),
    "sciences": tuple(s.id for s in SCIENCES_SKILLS),
    "history": tuple(s.id for s in HISTORY_SKILLS),
    "generic": tuple(s.id for s in GENERIC_SKILLS),
}

#: The generic profile §6.2 asks for: every generic skill matters a little, none dominates. Used
#: whenever a topic has no authored ratings and no model answered — and always *labelled* as the
#: fallback, because "we could not tell" and "these four matter equally" are different claims.
GENERIC_PROFILE: Mapping[str, int] = {sid: 2 for sid in FAMILY_SKILLS["generic"]}


def skill(skill_id: str) -> Skill | None:
    return _BY_ID.get(skill_id)


def require_skill(skill_id: str) -> Skill:
    """Like :func:`skill`, but a wrong id is an error rather than a ``None`` to trip over later."""
    found = _BY_ID.get(skill_id)
    if found is None:
        raise KeyError(f"{skill_id!r} is not a skill in taxonomy {TAXONOMY_VERSION}")
    return found


def skills_for(family: str) -> tuple[Skill, ...]:
    """The skill set for one subject family (``generic`` when the family is unknown)."""
    ids = FAMILY_SKILLS.get(family) or FAMILY_SKILLS["generic"]
    return tuple(_BY_ID[i] for i in ids)


def known_skill_ids(family: str | None = None) -> tuple[str, ...]:
    """The ids a rating is allowed to name — the validation list for a model's answer."""
    if family and family in FAMILY_SKILLS:
        return FAMILY_SKILLS[family]
    return SKILL_IDS


def family_title(family: str) -> str:
    return next((f["title"] for f in FAMILIES if f["id"] == family), "Any other subject")


def taxonomy_payload(*, with_games: bool = False) -> dict[str, Any]:
    """The taxonomy as data, for the console, the API and the snapshot's "what could be measured"."""
    return {
        "taxonomy_version": TAXONOMY_VERSION,
        "max_game_seconds": MAX_GAME_SECONDS,
        "items_per_skill": ITEMS_PER_SKILL,
        "families": [dict(f) for f in FAMILIES],
        "skills": [s.as_dict(with_game=with_games) for s in SKILLS],
        "counts": {fam: len(ids) for fam, ids in FAMILY_SKILLS.items()},
    }


def validate_taxonomy() -> list[str]:
    """Structural checks on the constant itself. Returns a list of problems (empty = valid).

    Run at import time by the test suite rather than at app startup: a broken taxonomy is a build
    fault, and failing a test says so more clearly than a startup log line nobody reads.
    """
    problems: list[str] = []
    seen: set[str] = set()
    for s in SKILLS:
        if s.id in seen:
            problems.append(f"{s.id}: duplicate skill id")
        seen.add(s.id)
        if not s.id.startswith(f"{FAMILY_PREFIX.get(s.family, s.family)}."):
            problems.append(f"{s.id}: id does not belong to family {s.family!r}")
        if s.family not in FAMILY_IDS:
            problems.append(f"{s.id}: unknown family {s.family!r}")
        for field_name in ("title", "measures", "implies"):
            if not str(getattr(s, field_name)).strip():
                problems.append(f"{s.id}: {field_name} is empty")
        if s.seconds > MAX_GAME_SECONDS:
            problems.append(f"{s.id}: game is {s.seconds}s, over the {MAX_GAME_SECONDS}s cap")
        if s.grader not in ("deterministic", "checklist"):
            problems.append(f"{s.id}: grader {s.grader!r} is not one a scorer can honour")
        kind = s.kind
        if kind not in ("choice", "order", "numeric", "text", "match"):
            problems.append(f"{s.id}: game kind {kind!r} has no renderer or scorer")
        if kind == "text" and s.grader == "checklist" and not s.game.get("rubric_hint"):
            problems.append(f"{s.id}: a checklist game must name what its three points are about")
    for fam, ids in FAMILY_SKILLS.items():
        missing = [i for i in ids if i not in seen]
        if missing:
            problems.append(f"{fam}: lists skills that are not defined: {', '.join(missing)}")
        if not ids:
            problems.append(f"{fam}: has no skills")
    return problems


def skills_payload(skills: Sequence[Skill | Mapping[str, Any]], *, extra: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Skill rows for a response, with any per-skill facts (a rating, a reason) merged in."""
    extra = dict(extra or {})
    rows: list[dict[str, Any]] = []
    for s in skills:
        row = s.as_dict() if isinstance(s, Skill) else dict(s)
        for key, values in extra.items():
            if isinstance(values, Mapping) and row["id"] in values:
                row[key] = values[row["id"]]
        rows.append(row)
    return rows


__all__ = [
    "CHECKLIST_SIZE",
    "FAMILIES",
    "FAMILY_IDS",
    "FAMILY_SKILLS",
    "GENERIC_PROFILE",
    "GENERIC_SKILLS",
    "HISTORY_SKILLS",
    "ITEMS_PER_SKILL",
    "LANGUAGES_SKILLS",
    "MATHS_SKILLS",
    "MAX_GAME_SECONDS",
    "SCIENCES_SKILLS",
    "SKILL_IDS",
    "SKILLS",
    "Skill",
    "TAXONOMY_VERSION",
    "family_title",
    "known_skill_ids",
    "require_skill",
    "skill",
    "skills_for",
    "skills_payload",
    "taxonomy_payload",
    "validate_taxonomy",
]
