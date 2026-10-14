"""Concept breakdown for a topic — step 1 of the §7 pipeline.

§7.1: "**Concept breakdown** for the chosen topic: concepts, prerequisites, common misconceptions,
learning objectives."

**Where every part of it comes from.** The temptation here is to ask a model, and a model with a key
still can (the pipeline passes a breakdown through validation like everything else). But the
deterministic path must not be a stub, because it is the path every demo, every keyless install and
every gate run takes. So this module composes the breakdown out of content a person already wrote and
that the repository already trusts:

* **concepts** — the nodes of the topic's authored prerequisite chain (``subjects.curriculum``).
  Those are exactly the topic's concepts: described, individually testable, with real prerequisites.
* **prerequisites** — the chain's own edges, resolved to concept ids *and* titles so the pack carries
  "before this, you need …" a learner can read.
* **common misconceptions** — the rung-1 kits' near-miss options. The chain's authoring rule
  (``curriculum``: "near-misses are authored, and each one is a thing a learner actually confuses the
  idea with, not an absurdity") makes these the misconceptions the material was built around. The
  authored ``why`` is carried beside each as the contrast, not rewritten into a per-option reason
  nobody wrote.
* **learning objectives** — one per rung present, quoting the authored task stem and the brief's own
  demand label from ``ladder.RUNGS``. Quoting rather than paraphrasing is the point: an objective
  the app reworded is an objective no person wrote.

**What it does when it has nothing.** A topic with no authored chain gets
``available=False`` with the reason, and the pipeline turns that into the honest "we have no breakdown
for this topic" rather than a plausible-looking invention. That is the same rule §6.4 applies to the
ladder, and it is the reason this module imports ``chain_for`` instead of maintaining a second table.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ..subjects import curriculum
from ..subjects.ladder import RUNGS, rung_row
from ..subjects.topics import TOPIC_SPECS, TopicSpec

#: Bumped when the *shape* of a breakdown changes, so a cached pack can tell "the spec moved" from
#: "the content moved". The pipeline feeds it into the spec hash (§7.6: regenerate only on spec
#: change), and a test asserts that bumping it invalidates a cached pack.
BREAKDOWN_SPEC = "breakdown/1"

#: The scaffold rungs carry items; rung 0 is a self-report and has no authored item, which is why
#: objectives start at 1. Named here so the "no objective for rung 0" decision is visible.
OBJECTIVE_RUNGS: tuple[int, ...] = (1, 2, 3, 4, 5)


@dataclass(frozen=True)
class Concept:
    """One concept: what it is, what must come first, what goes wrong, and what it aims at."""

    id: str
    title: str
    sub_area: str
    description: str
    prerequisites: tuple[str, ...] = ()
    prerequisite_titles: tuple[str, ...] = ()
    misconceptions: tuple[Mapping[str, Any], ...] = ()
    objectives: tuple[Mapping[str, Any], ...] = ()
    #: The authored kit, rung → item. The pack builder reads items from here rather than re-deriving
    #: them, so a concept's questions and the ladder's questions are the same questions.
    kit: Mapping[int, Mapping[str, Any]] = field(default_factory=dict)
    bank_code: str | None = None
    sources: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "sub_area": self.sub_area,
            "description": self.description,
            "prerequisites": list(self.prerequisites),
            "prerequisite_titles": list(self.prerequisite_titles),
            "misconceptions": [dict(m) for m in self.misconceptions],
            "objectives": [dict(o) for o in self.objectives],
            "bank_code": self.bank_code,
            "sources": list(self.sources),
        }


@dataclass(frozen=True)
class Breakdown:
    """A topic's concepts, or an honest statement that it has none authored."""

    topic_key: str
    title: str
    available: bool
    reason: str
    concepts: tuple[Concept, ...] = ()
    spec: str = BREAKDOWN_SPEC
    sources: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "topic_key": self.topic_key,
            "title": self.title,
            "available": self.available,
            "reason": self.reason,
            "spec": self.spec,
            "sources": list(self.sources),
            "concepts": [c.as_dict() for c in self.concepts],
        }

    def concept(self, concept_id: str) -> Concept | None:
        for c in self.concepts:
            if c.id == concept_id or c.title == concept_id:
                return c
        return None


def topic_spec(key: str) -> TopicSpec | None:
    """The authored topic row for a key or an alias, or ``None``.

    Matching happens in :func:`adapt.subjects.resolve.normalise` -- the repo's one normaliser --
    rather than in a private ``.lower().replace()``. The private version is what made "dy/dx" miss the
    alias "dy dx": a learner types the slash, the author wrote the space, and the two never met. The
    resolver already solved this for ``POST /topics/resolve``, and a second normaliser is a second
    thing to keep in sync.
    """
    from ..subjects.resolve import normalise

    wanted = normalise(key)
    if not wanted:
        return None
    for spec in TOPIC_SPECS:
        names = {normalise(spec.key), normalise(spec.title), *(normalise(a) for a in spec.aliases)}
        if wanted in names:
            return spec
    return None


def _near_misses(item: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """The rung-1 kit's near-miss options, with the item's authored why kept as the contrast.

    The ``answer`` index points at the example, so everything else is a near-miss. No per-option
    reason is invented: the authored ``why`` explains why the example is the example, and that is
    what the entry records.
    """
    options = [str(o) for o in (item.get("options") or [])]
    answer = item.get("answer")
    if not options or not isinstance(answer, int) or not 0 <= answer < len(options):
        return []
    return [
        {
            "claim": text,
            "contrast": str(item.get("why") or ""),
            "source": "rung 1 near-miss authored for this concept",
        }
        for i, text in enumerate(options)
        if i != answer and text.strip()
    ]


def _objectives(node: Any) -> list[Mapping[str, Any]]:
    """One objective per authored rung task, quoting the task and the brief's demand label."""
    out: list[Mapping[str, Any]] = []
    for rung in OBJECTIVE_RUNGS:
        item = (getattr(node, "kit", {}) or {}).get(rung)
        if not isinstance(item, Mapping):
            continue
        row = rung_row(rung)
        out.append(
            {
                "rung": rung,
                "key": row["key"],
                "demand": row["demand"],
                "task_stem": str(item.get("stem") or ""),
            }
        )
    return out


def concepts_for(topic_key: str) -> tuple[Concept, ...]:
    """The authored concepts of a topic, in prerequisite order.

    Order is the chain's own: ``Chain.order()`` (or, failing that, a topological pass here) puts a
    node after everything it depends on, which is the order a lesson should teach them in and the
    order a pack builder wants so a concept's prerequisites are already built when it is reached.
    """
    chain = curriculum.chain_for_key(topic_key)
    if chain is None:
        return ()
    nodes = list(getattr(chain, "nodes", ()) or ())
    ordered = _in_prerequisite_order(nodes)
    titles = {n.id: getattr(n, "title", n.id) for n in nodes}
    out: list[Concept] = []
    for node in ordered:
        kit = dict(getattr(node, "kit", {}) or {})
        prereqs = tuple(str(p) for p in (getattr(node, "prerequisites", ()) or ()))
        out.append(
            Concept(
                id=str(node.id),
                title=str(getattr(node, "title", node.id)),
                sub_area=str(getattr(node, "sub_area", "")),
                description=str(getattr(node, "description", "")),
                prerequisites=prereqs,
                prerequisite_titles=tuple(titles.get(p, p) for p in prereqs),
                misconceptions=tuple(_near_misses(kit.get(1) or {})),
                objectives=tuple(_objectives(node)),
                kit={int(r): dict(v) for r, v in kit.items() if isinstance(v, Mapping)},
                bank_code=getattr(node, "bank_code", None),
                sources=(
                    "concept and description: the authored prerequisite chain (subjects/curriculum.py)",
                    "misconceptions: the rung-1 kit's authored near-misses",
                    "objectives: the authored rung tasks, quoted",
                ),
            )
        )
    return tuple(out)


def _in_prerequisite_order(nodes: Sequence[Any]) -> list[Any]:
    """Topological order by prerequisites, stable for nodes that share a depth.

    The chain validates itself acyclic at build time, so the cycle branch here is unreachable in
    practice; it exists so a malformed chain produces *some* usable order rather than hanging, and
    the leftovers are appended in authored order with their prerequisites unmet (which the pack
    validator then reports as a missing prerequisite).
    """
    by_id = {str(getattr(n, "id", i)): n for i, n in enumerate(nodes)}
    ordered: list[Any] = []
    seen: set[str] = set()
    for node in nodes:  # authored order is the tie-break
        stack = [(str(getattr(node, "id", "")), False)]
        while stack:
            node_id, expanded = stack.pop()
            if node_id in seen or node_id not in by_id:
                continue
            current = by_id[node_id]
            if expanded:
                seen.add(node_id)
                ordered.append(current)
                continue
            stack.append((node_id, True))
            for prereq in reversed([str(p) for p in (getattr(current, "prerequisites", ()) or ())]):
                if prereq not in seen:
                    stack.append((prereq, False))
    for node in nodes:
        if str(getattr(node, "id", "")) not in seen:
            ordered.append(node)
    return ordered


def breakdown_for(topic_key: str, *, level: str | None = None) -> Breakdown:
    """The breakdown for one topic, or ``available=False`` with the reason it has none."""
    spec = topic_spec(topic_key)
    key = spec.key if spec is not None else str(topic_key or "").strip().lower()
    title = spec.title if spec is not None else key
    concepts = concepts_for(key)
    if not concepts:
        chain = curriculum.chain_for_key(key)
        reason = (
            f"{title!r} has no authored prerequisite chain, so there is no concept breakdown for it; "
            "the app says so rather than inventing concepts (the same rule the placement ladder "
            "follows with its 'no chain for this topic' answer)"
            if chain is None
            else f"the chain for {title!r} has no nodes"
        )
        return Breakdown(topic_key=key, title=title, available=False, reason=reason)
    return Breakdown(
        topic_key=key,
        title=title,
        available=True,
        reason=f"{len(concepts)} concepts from the authored chain for {title}",
        concepts=concepts,
        sources=(
            "subjects/curriculum.py (authored chain: concepts, prerequisites, descriptions)",
            "subjects/games.py (authored rung kits: near-misses, tasks)",
            f"ladder.RUNGS (the brief's rung labels, {len(RUNGS)} rungs)",
        ),
    )
