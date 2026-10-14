"""The scripted fake model Part E is tested with, and the red-team variants it must catch.

`scripts/live_model_check.py` takes a `model_factory` so a run can be driven without a key. This
file is that factory: a deterministic stand-in for `model(purpose, system, user, schema,
max_tokens) -> dict` that answers every purpose the pipeline and the judge use.

The content is **fake**. It is written to be check-shaped rather than to be good teaching: the
"good" variant is the smallest thing that satisfies every automated check the harness makes, so a
failure in a run that uses it is a failure of the harness, not of a model. The other variants
each break exactly one rule, and the harness's own test suite asserts that the run *fails* on
each of them -- a check that cannot fail is not a check.

Nothing here is ever used by the application, and a report produced with it says in its first
lines that a scripted fake answered every call.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Mapping

#: Topics the live run covers, with the fake's stand-in content for each. Facts are everyday
#: statements of the kind the topic itself rests on; they are the fake's, not the app's.
TOPIC_FACTS: dict[str, dict[str, Any]] = {
    "quantum chromodynamics": {
        "title": "Quantum chromodynamics",
        "family": "physics",
        "facts": [
            "Quarks carry a colour charge and are never observed on their own.",
            "Gluons carry the strong force between quarks.",
        ],
        "misconception": {
            "wrong_belief": "The strong force should fade with distance, like gravity and electricity.",
            "why_tempting": "Every other force a learner has met gets weaker further away.",
            "correction": "The strong force grows with separation, which is why a lone quark is never seen.",
        },
    },
    "how to bake bread": {
        "title": "How to bake bread",
        "family": "everyday skills",
        "facts": [
            "Yeast turns sugars in the dough into carbon dioxide.",
            "Gluten forms a stretchy network that holds that gas in.",
        ],
        "misconception": {
            "wrong_belief": "More yeast always makes a loaf rise more.",
            "why_tempting": "Yeast is what makes the gas, so more of it sounds like more rise.",
            "correction": "Rise depends on time, warmth and gluten strength as much as on the amount of yeast.",
        },
    },
    "kiswahili verbs": {
        "title": "Kiswahili verbs",
        "family": "languages",
        "facts": [
            "The verb carries its subject prefix: ni-, u-, a-.",
            "The tense marker sits between the subject prefix and the stem: ni-na-soma.",
        ],
        "misconception": {
            "wrong_belief": "A separate word is needed for the subject, as in English.",
            "why_tempting": "English always names the subject separately, so the habit carries over.",
            "correction": "The subject is built into the verb; a pronoun is added only for emphasis.",
        },
    },
    "sorting algorithms": {
        "title": "Sorting algorithms",
        "family": "computing",
        "facts": [
            "A comparison sort finds the order by comparing pairs of items.",
            "Merge sort splits the list, sorts each half, then merges the halves.",
        ],
        "misconception": {
            "wrong_belief": "The shortest program is always the fastest sort.",
            "why_tempting": "Short code looks efficient on the page.",
            "correction": "Running time grows with the number of items, and that growth decides at scale.",
        },
    },
    "supply and demand": {
        "title": "Supply and demand",
        "family": "economics",
        "facts": [
            "At a higher price, sellers offer more and buyers want less.",
            "The market price settles where those two sides meet.",
        ],
        "misconception": {
            "wrong_belief": "Raising the price always raises the money taken.",
            "why_tempting": "Each item earns more, so the total looks larger.",
            "correction": "Above the meeting point fewer items sell, so the total can fall.",
        },
    },
    "the french revolution": {
        "title": "The French Revolution",
        "family": "history",
        "facts": [
            "France in 1789 had an estate system in which the first two estates paid little tax.",
            "The king called the Estates-General in 1789 for the first time since 1614.",
        ],
        "misconception": {
            "wrong_belief": "The revolution was only about hunger.",
            "why_tempting": "Bread prices were high and hunger was visible everywhere.",
            "correction": "Hunger was the spark; the political claims of the third estate were the fuel.",
        },
    },
    "discrete maths (sets)": {
        "title": "Discrete maths (sets)",
        "family": "mathematics",
        "facts": [
            "A set is a collection of distinct objects.",
            "A is a subset of B when every member of A is a member of B.",
        ],
        "misconception": {
            "wrong_belief": "A set can contain the same object twice and that changes it.",
            "why_tempting": "Lists work that way, and a set is written like a list.",
            "correction": "Two members are either the same object or not; writing one twice adds nothing.",
        },
    },
    "derivatives": {
        "title": "Derivatives",
        "family": "mathematics",
        "facts": [
            "The derivative is the slope of the tangent line at a point.",
            "It is the limit of the average rate of change over a shrinking interval.",
        ],
        "misconception": {
            "wrong_belief": "The derivative of a product is the product of the derivatives.",
            "why_tempting": "It is true for sums, so it looks like a general rule.",
            "correction": "Products need the product rule; the sum rule does not carry over.",
        },
    },
    "constitutional law": {
        "title": "Constitutional law",
        "family": "law",
        "facts": [
            "A constitution sets the rules for how other laws are made.",
            "A written constitution usually ranks above ordinary legislation.",
        ],
        "misconception": {
            "wrong_belief": "Any law passed by a majority is constitutional.",
            "why_tempting": "A majority is how laws are made, so the step looks final.",
            "correction": "A court can strike down a law that contradicts the constitution, however popular it is.",
        },
    },
    "essay writing": {
        "title": "Essay writing",
        "family": "writing",
        "facts": [
            "A thesis sentence states the claim the essay will defend.",
            "Every paragraph should serve that claim and connect back to it.",
        ],
        "misconception": {
            "wrong_belief": "A longer essay is a better essay.",
            "why_tempting": "Length looks like effort, and effort looks like quality.",
            "correction": "Length without a claim reads as padding; the reader is scoring the argument.",
        },
    },
    "photosynthesis": {
        "title": "Photosynthesis",
        "family": "biology",
        "facts": [
            "Chlorophyll absorbs light energy inside the chloroplast.",
            "Carbon dioxide and water become glucose and oxygen.",
        ],
        "misconception": {
            "wrong_belief": "Plants get their mass from the soil.",
            "why_tempting": "They stand in soil and grow upwards out of it.",
            "correction": "Most of the mass comes from carbon dioxide in the air.",
        },
    },
    "probability": {
        "title": "Probability",
        "family": "mathematics",
        "facts": [
            "A probability is a number from 0 to 1 for how likely an event is.",
            "For equally likely outcomes it is the favourable count over the total count.",
        ],
        "misconception": {
            "wrong_belief": "After five heads, a tail is due.",
            "why_tempting": "The runs should even out, and it feels as though they must do so now.",
            "correction": "Independent tosses do not remember; the chance stays the same on every toss.",
        },
    },
}

#: Kiswahili frames for the profile that asks for Kiswahili. A frame is enough: the point of the
#: fake is the shape of the reply, and the harness checks the language of what the learner reads.
SW = {
    "idea": "Wazo kuu ni hili: somo hili linahusu {topic}, na kila hatua inafuata ile iliyotangulia.",
    "why": "Kwa nini inafanya kazi: kwa sababu kanuni moja inaeleza jinsi vitu vinavyohusiana.",
    "lead": "Fikiria mfano mmoja kwanza, kisha tazama kanuni inayoufanya ufanye kazi.",
    "check": "Swali dogo: nini wazo kuu la somo hili?",
    "text": "Hatua ndogo: soma sentensi moja, kisha sema wazo kwa maneno yako mwenyewe.",
    "explain": "Umejibu {answer}. Jibu hili linavutia kwa sababu linaonekana rahisi, lakini halifanyi kazi hapa: {correction}",
    "reteach": "Njia nyingine ya kueleza wazo hili: {idea}",
}

#: The variants `--variant` accepts. `good` must pass every check; each other name must be
#: *caught*, and `backend/tests/test_live_check_harness.py` proves exactly that.
VARIANTS = (
    "good",
    "leaky",
    "devwords",
    "placeholder",
    "bare_judge",
    "refusing",
    "key_mismatch",
    "unverified",
    "wrong_language",
    "keypaste",
)


def _entry(topic: str) -> dict[str, Any]:
    key = topic.strip().lower()
    if key in TOPIC_FACTS:
        return TOPIC_FACTS[key]
    for known, entry in TOPIC_FACTS.items():
        if known in key or key in known:
            return entry
    return {
        "title": topic.strip().title() or "The topic",
        "family": "general",
        "facts": [
            f"{topic.strip()} is set out in steps that build on one another.",
            f"Each step of {topic.strip()} can be checked against the one before it.",
        ],
        "misconception": {
            "wrong_belief": "The whole of it can be learned by reading it once.",
            "why_tempting": "Reading feels like learning while the page is in front of you.",
            "correction": "It comes together when each step is used once and checked.",
        },
    }


def _field(user: str, name: str) -> str:
    for line in user.splitlines():
        if line.lower().startswith(f"{name.lower()}:"):
            return line.split(":", 1)[1].strip()
    return ""


class ScriptedModel:
    """A fake `model(purpose, system, user, schema, max_tokens) -> dict`."""

    def __init__(self, variant: str = "good") -> None:
        if variant not in VARIANTS:
            raise ValueError(f"unknown variant {variant!r}; try one of {', '.join(VARIANTS)}")
        self.variant = variant
        self.calls: list[str] = []

    # -- the callable the contract names ---------------------------------- #

    def __call__(
        self,
        purpose: str,
        system: str,
        user: str,
        schema: Mapping[str, Any] | None = None,
        max_tokens: int = 0,
    ) -> dict[str, Any]:
        self.calls.append(purpose)
        if self.variant == "refusing":
            raise RuntimeError("scripted refusal: no reply was produced")
        handler = {
            "skeleton": self._skeleton,
            "items": self._items,
            "verify": self._verify,
            "lesson": self._lesson,
            "explain_wrong": self._explain_wrong,
            "reteach": self._reteach,
            "judge": self._judge,
        }.get(purpose)
        if handler is None:
            raise AssertionError(f"the scripted model has no reply for purpose {purpose!r}")
        return handler(user)

    # -- pipeline replies -------------------------------------------------- #

    def _skeleton(self, user: str) -> dict[str, Any]:
        topic = _field(user, "Topic")
        level = _field(user, "Learner level").split()[0] if _field(user, "Learner level") else "3"
        language = _field(user, "Language").lower()
        entry = _entry(topic)
        fact_pair = entry["facts"]
        word = entry["misconception"]
        idea = (
            SW["idea"].format(topic=entry["title"])
            if language.startswith("sw")
            else f"{entry['title']} works because {fact_pair[0][0].lower()}{fact_pair[0][1:]}"
        )
        why = (
            SW["why"]
            if language.startswith("sw")
            else f"Knowing that, the rest follows: {fact_pair[1][0].lower()}{fact_pair[1][1:]}"
        )
        return {
            "topic_title": entry["title"],
            "subject_family": entry["family"],
            "concepts": [
                {
                    "id": "c1",
                    "name": entry["title"],
                    "key_idea": idea,
                    "why_it_works": why,
                    "prerequisites": [],
                    "rung": int(level or 3),
                    "key_facts": [
                        {"id": "f11", "statement": fact_pair[0]},
                        {"id": "f12", "statement": fact_pair[1]},
                    ],
                    "misconceptions": [
                        {
                            "id": "m11",
                            "wrong_belief": word["wrong_belief"],
                            "why_tempting": word["why_tempting"],
                            "correction": word["correction"],
                        }
                    ],
                }
            ],
        }

    def _items(self, user: str) -> dict[str, Any]:
        topic = _field(user, "Topic")
        language = _field(user, "Language").lower()
        entry = _entry(topic)
        correct = entry["facts"][0].rstrip(".")
        wrong = entry["misconception"]["wrong_belief"].rstrip(".")
        third = entry["facts"][1].rstrip(".")
        stem = (
            f"Which sentence states what {entry['title'].lower()} rests on?"
            if not language.startswith("sw")
            else f"Sentensi ipi inaeleza wazo la {entry['title'].lower()}?"
        )
        if self.variant == "leaky":
            stem = f"{stem} Choose: {correct}."  # the key, printed in the question
        return {
            "items": [
                {
                    "id": "c1_i1",
                    "concept_id": "c1",
                    "kind": "mcq",
                    "stem": stem,
                    "options": [
                        {"id": "o1", "text": correct, "misconception_id": None},
                        {"id": "o2", "text": wrong, "misconception_id": "m11"},
                        {"id": "o3", "text": third, "misconception_id": None},
                    ],
                    "key": correct,
                    "pair_id": "c1_pair",
                    "hints": [
                        f"Ask what {entry['title'].lower()} would need to be true first.",
                        f"Work outwards from this: {entry['facts'][1]}",
                        f"The sentence that holds the idea is about {correct.split()[0].lower()}.",
                    ],
                    "teachback": [
                        f"Say in your own words what {entry['title'].lower()} rests on.",
                        "Give the sentence that would break if it were false.",
                        "Say where the part you agreed with gives way.",
                    ],
                    "fact_ids": ["f11"],
                }
            ]
        }

    def _verify(self, user: str) -> dict[str, Any]:
        stated = _field(user, "Stated answer")
        if self.variant == "unverified":
            return {"agrees": False, "key": "a different sentence", "reason": "reached another answer"}
        return {"agrees": True, "key": stated, "reason": "reached the same answer independently"}

    # -- what the learner reads ------------------------------------------- #

    def _lesson(self, user: str) -> dict[str, Any]:
        topic = _field(user, "Topic")
        language = _field(user, "Language").lower()
        lead = (_field(user, "Open with") or "text").lower()
        budget_line = _field(user, "At most")
        entry = _entry(topic)
        sw = language.startswith("sw") and self.variant != "wrong_language"
        item_stem = _field(user, "Item")
        item_answer = _field(user, "Item answer")

        # NB: the item's key is facts[0]. Nothing above the question may print it, so the lead
        # block and the teaching blocks talk *about* the idea without quoting that sentence.
        if sw:
            opening = SW["lead"]
            body = f"{SW['idea'].format(topic=entry['title'])} {SW['why']}"
            check_body = SW["check"]
            text_body = SW["text"]
        else:
            opening = (
                f"Start from one worked run: take {entry['title'].lower()} one case at a time and "
                f"watch which sentence decides what happens next."
            )
            body = (
                f"The idea: knowing that {entry['facts'][1][0].lower()}{entry['facts'][1][1:]} "
                f"That is what makes the rest of {entry['title'].lower()} follow."
            )
            check_body = f"Quick check: {item_stem}" if item_stem else "Quick check: what is the idea?"
            text_body = (
                f"One step at a time: read the sentence once, then say the idea in your own words. "
                f"It is worth remembering that {entry['facts'][1][0].lower()}{entry['facts'][1][1:]}"
            )
        if self.variant == "devwords":
            text_body += " This paragraph was written by Gemini and needs no API key."
        if self.variant == "placeholder":
            text_body += " The concept being explained is {topic}."
        if self.variant == "keypaste":
            # a model that pastes something credential-shaped where a learner can read it
            text_body += " Config note: api_key: AIzaSyD-FAKE-KEY-FOR-THE-CHECK-9911"

        # a check block carries the item it asks about and the answer the lesson believes;
        # `key_mismatch` is that answer disagreeing with the item's own key.
        answer = item_answer
        if self.variant == "key_mismatch":
            answer = entry["misconception"]["wrong_belief"]
        check: dict[str, Any] = {
            "type": "check",
            "body": check_body,
            "fact_ids": ["f12"],
            "why": {"evidence_ids": ["ev_1"], "n": 3},
        }
        if item_stem:
            check["item_id"] = "c1_i1"
        if answer:
            check["answer"] = answer
        _ = budget_line  # the fake writes short blocks at every profile's step size
        # Every explanation block is followed by a check: that is one of the rules the harness
        # checks in code, and the fake has to satisfy it to be a fair stand-in for Phase C.
        return {
            "concept_id": "c1",
            "blocks": [
                {
                    "type": lead if lead in ("visual", "worked_example", "text") else "text",
                    "body": opening,
                    "fact_ids": ["f11"],
                    "why": {"evidence_ids": ["ev_2"], "n": 4},
                },
                check,
                {
                    "type": "text",
                    "body": body,
                    "fact_ids": ["f12"],
                    "why": {"evidence_ids": ["ev_2"], "n": 4},
                },
                {
                    "type": "check",
                    "body": SW["check"] if sw else "Quick check: say the idea in one sentence.",
                    "fact_ids": ["f12"],
                    "why": {"evidence_ids": ["ev_1"], "n": 3},
                },
                {
                    "type": "text",
                    "body": text_body,
                    "fact_ids": ["f12"],
                    "why": {"evidence_ids": ["ev_1"], "n": 3},
                },
                {
                    "type": "check",
                    "body": SW["check"] if sw else "Quick check: where would you use this next?",
                    "fact_ids": ["f11"],
                    "why": {"evidence_ids": ["ev_1"], "n": 3},
                },
            ],
        }

    def _explain_wrong(self, user: str) -> dict[str, Any]:
        topic = _field(user, "Topic")
        answer = _field(user, "The learner answered")
        language = _field(user, "Language").lower()
        correction = _field(user, "Common misconception") or _entry(topic)["facts"][0]
        if language.startswith("sw") and self.variant != "wrong_language":
            body = SW["explain"].format(answer=answer, correction=correction)
        else:
            body = (
                f'You answered "{answer}". That answer is tempting because it matches the way the '
                f"question is usually put. It fails here: {correction} The idea that does work is "
                f"{_entry(topic)['facts'][0][0].lower()}{_entry(topic)['facts'][0][1:]}"
            )
        return {
            "blocks": [{"type": "explain", "body": body, "fact_ids": ["f11"], "why": {"evidence_ids": [], "n": 0}}],
            "method": "misconception_first",
            "analogy_id": "analogy_dough",
        }

    def _reteach(self, user: str) -> dict[str, Any]:
        topic = _field(user, "Topic")
        method = _field(user, "Method to use") or "worked_example"
        language = _field(user, "Language").lower()
        entry = _entry(topic)
        if language.startswith("sw") and self.variant != "wrong_language":
            body = SW["reteach"].format(idea=SW["idea"].format(topic=entry["title"]))
        else:
            body = (
                f"Here is the same idea by {method.replace('_', ' ')}: it rests on "
                f"{entry['facts'][0][0].lower()}{entry['facts'][0][1:]} Set against that, "
                f"{entry['misconception']['correction'][0].lower()}{entry['misconception']['correction'][1:]}"
            )
        return {
            "blocks": [{"type": "explain", "body": body, "fact_ids": ["f11"], "why": {"evidence_ids": [], "n": 0}}],
            "method": method,
            "analogy_id": f"analogy_{method}",
        }

    # -- the independent judge -------------------------------------------- #

    def _judge(self, user: str) -> dict[str, Any]:
        # Flat fields, because the judge schema the harness sends is flat: integer scores plus
        # one quote per score, which is what `_schema_for` in ai/llm.py can actually constrain.
        out: dict[str, Any] = {
            "correctness": 4,
            "explains_why": 4,
            "uses_learner_answer": 4,
            "fits_profile": 4,
            "no_leakage": 5,
            "notes": "scripted judge; these scores are the fake's, not a reading",
        }
        for index, name in enumerate(
            ("correctness", "explains_why", "uses_learner_answer", "fits_profile", "no_leakage")
        ):
            # a different line for each criterion, so the report's quotes are not one line five
            # times -- which is what a judge that is not really reading would produce
            out[f"{name}_evidence"] = "" if self.variant == "bare_judge" else _quote(user, index)
        return out


ARTEFACT_MARK = "---- ARTEFACT ----"


def _quote(user: str, offset: int = 0) -> str:
    """A sentence from inside the artefact region, so the quote really occurs in the teaching text."""
    lines = user.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == ARTEFACT_MARK), -1)
    candidates = [
        line.strip() for line in (lines[start + 1 :] if start >= 0 else lines) if len(line.strip()) >= 40 and not line.strip().endswith(":")
    ]
    if not candidates:
        return "the artefact text"
    return candidates[offset % len(candidates)][:120]


# ------------------------------------------------------------------------------- replaying --


def prompt_key(purpose: str, system: str, user: str) -> str:
    """A short digest of one request. Mirrors the harness's own helper, by design.

    A fixture identifies a reply by *what was asked*, not by when it was asked, so replaying a
    fixture does not depend on the order the pipeline happens to make its calls in -- and a
    replay model may be built fresh per call without losing its place.
    """
    import hashlib

    payload = f"{purpose}\n{system}\n{user}".encode()
    return hashlib.sha256(payload).hexdigest()[:20]


class ReplayModel:
    """Answers from a saved fixture: the same replies to the same requests, from the file.

    This is what makes saved responses usable as regression material without a key: a fixture
    written by one run can drive a later run, and the harness's checks are the assertion.
    Requests are matched by `prompt_key` when the fixture carries one (every fixture written by
    the harness does); a fixture without keys falls back to purpose, in recorded order.
    """

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._by_purpose: dict[str, list[dict[str, Any]]] = {}
        self._by_key: dict[str, list[dict[str, Any]]] = {}
        for record in responses:
            self._by_purpose.setdefault(str(record.get("purpose")), []).append(record)
            key = record.get("prompt_key")
            if key:
                self._by_key.setdefault(str(key), []).append(record)
        self._used: dict[str, int] = {}

    def __call__(
        self,
        purpose: str,
        system: str,
        user: str,
        schema: Mapping[str, Any] | None = None,
        max_tokens: int = 0,
    ) -> dict[str, Any]:
        key = prompt_key(purpose, system, user)
        options = self._by_key.get(key)
        slot = f"key:{key}"
        if not options:
            options = self._by_purpose.get(purpose)
            slot = f"purpose:{purpose}"
        if not options:
            raise RuntimeError(f"the fixture has no reply for purpose {purpose!r}")
        index = self._used.get(slot, 0)
        self._used[slot] = index + 1
        record = options[min(index, len(options) - 1)]
        if record.get("refused"):
            raise RuntimeError("the fixture recorded a refusal for this call")
        reply = record.get("reply")
        if not isinstance(reply, dict):
            raise RuntimeError(f"the fixture's reply for {purpose!r} is not an object")
        return reply


def load_fixture(path: str | Path) -> ReplayModel:
    """Read one saved fixture and hand back a model that replays it."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    responses = payload.get("responses")
    if not isinstance(responses, list):
        raise ValueError(f"{path} is not a live-check fixture: no 'responses' list")
    return ReplayModel(responses)


def make(variant: str = "good") -> Callable[..., dict[str, Any]]:
    """The `model_factory` the harness and its tests pass in."""
    return ScriptedModel(variant)
