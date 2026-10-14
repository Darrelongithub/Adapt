"""The universal ladder: rungs 0-5, a validated prerequisite chain, and a staircase (§6.4).

Tier 3 of the brief. Where the skill round asks "what does this subject need of you", the ladder asks
"where are you in the branch you chose", and it answers with a placement rather than a score: a rung
per sub-area, with a count of the items behind it.

**The rungs are identical for every topic.** That is the point of the word "universal": rung 3 means
"can apply a standard case" in calculus and in civics, so a ladder position is comparable across
subjects without being a single global number (§6.1: ability in maths is not ability in languages).

**The staircase is code, and its rules are the brief's, not mine to improve on:**

* start at rung 2 in the middle of the chain — rung 2 is "recall", which is low enough that a novice
  is not humiliated and high enough that a strong learner is not bored, and mid-chain is where the
  information is: at a leaf you learn nothing about the prerequisites, at the root nothing about the
  topic;
* up on correct, down on wrong;
* a failure at rung 1 or 2 drops **down the prerequisite chain** rather than down a rung, because a
  learner who cannot recognise or recall the idea is not one rung away from it — they are a node away;
* rotate across sub-areas, so the placement is not one long interrogation of a single idea;
* stop after three reversals or nine items, whichever comes first.

Termination is a property, not a hope: :func:`advance` counts items and reversals and refuses to go
past either, and ``tests/test_subjects.py`` asserts it for every combination of right and wrong
answers a learner could produce.

**What this module does not do.** It does not compute ``p_know``, does not update BKT and does not
write to the learner model. Those are AI 1's, from evidence this ladder publishes through the store.
A ladder position is a *placement*: it says where to start teaching, and it is recorded with the
number of items behind it, which for nine items or fewer is always an early signal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from ..learning import bkt as B
from . import games as G
from .topics import TopicSpec

#: Rungs by cognitive demand (§6.4). ``scored`` marks the one rung that is not a knowledge
#: measurement: "have you met it" is a self-report, stored as stated familiarity, never as a score.
RUNGS: tuple[dict[str, Any], ...] = (
    {"rung": 0, "key": "met", "title": "Have you met it?", "demand": "familiarity, not knowledge", "scored": False},
    {"rung": 1, "key": "recognise", "title": "Recognise it", "demand": "pick an example of the idea from near-misses", "scored": True},
    {"rung": 2, "key": "recall", "title": "Recall it", "demand": "produce the key fact without a prompt", "scored": True},
    {"rung": 3, "key": "apply_standard", "title": "Apply a standard case", "demand": "use the idea on the case it is always taught with", "scored": True},
    {"rung": 4, "key": "apply_new", "title": "Apply it in a new situation", "demand": "use the idea where the surface features have changed", "scored": True},
    {"rung": 5, "key": "explain_why", "title": "Explain why", "demand": "state the reason, not just the result", "scored": True},
)

RUNG_KEYS: tuple[str, ...] = tuple(r["key"] for r in RUNGS)
MAX_RUNG = 5
MIN_RUNG = 0

#: §6.4's staircase limits.
START_RUNG = 2
MAX_REVERSALS = 3
MAX_ITEMS = 9
#: A prerequisite chain deeper than this is not a chain a learner can be placed in one sitting, and
#: §6.4 names 8 as the ceiling. ``validate`` refuses a chain over it rather than truncating one.
MAX_DEPTH = 8
#: Seconds a rung is expected to take. The ladder is untimed in the sense that nothing is scored on
#: speed, but the screen shows a budget and the run records the time actually taken per rung.
SECONDS_PER_RUNG = 45


def rung_row(rung: int) -> dict[str, Any]:
    return dict(RUNGS[max(MIN_RUNG, min(MAX_RUNG, int(rung)))])


def rung_title(rung: int) -> str:
    return str(rung_row(rung)["title"])


@dataclass(frozen=True)
class Node:
    """One node of a prerequisite chain: described, and testable at every rung.

    ``kit`` maps a rung (1-5) to an authored item in the same shape :mod:`adapt.subjects.games`
    scores, so a ladder task and a skill task are graded by the same code and reported the same way.
    Rung 0 needs no item: it is a yes/no about familiarity, generated rather than authored.
    """

    id: str
    title: str
    #: The sub-area this node belongs to. The ladder rotates across sub-areas, so a chain whose nodes
    #: all share one sub-area cannot rotate — and ``validate`` says so.
    sub_area: str
    description: str
    prerequisites: tuple[str, ...] = ()
    kit: Mapping[int, Mapping[str, Any]] = field(default_factory=dict)
    #: A code in the shipped bank, when this node *is* one of its topics. Kept so the ladder's
    #: evidence lands on a topic the rest of the app already knows.
    bank_code: str | None = None

    def probe(self, rung: int) -> Mapping[str, Any] | None:
        return self.kit.get(int(rung))

    def as_dict(self, *, with_kit: bool = False) -> dict[str, Any]:
        out = {
            "id": self.id,
            "title": self.title,
            "sub_area": self.sub_area,
            "description": self.description,
            "prerequisites": list(self.prerequisites),
            "bank_code": self.bank_code,
            "rungs_authored": sorted(int(r) for r in self.kit),
        }
        if with_kit:
            out["kit"] = {str(r): dict(v) for r, v in sorted(self.kit.items())}
        return out


class ChainError(ValueError):
    """A chain that cannot be used. Raised at build time rather than discovered mid-placement."""


class Chain:
    """A prerequisite chain: a small DAG of described, individually testable nodes.

    Built once and cached by :func:`adapt.subjects.curriculum.chain_for`, because §6.4 asks for the
    chain to be cached and because rebuilding it per request would re-run the cycle check for no
    reason.
    """

    def __init__(
        self,
        chain_id: str,
        title: str,
        nodes: Sequence[Node],
        *,
        topic_key: str | None = None,
        source: str = "authored",
        note: str = "",
    ) -> None:
        self.chain_id = chain_id
        self.title = title
        self.topic_key = topic_key
        self.source = source
        self.note = note
        self._nodes: dict[str, Node] = {}
        for n in nodes:
            if n.id in self._nodes:
                raise ChainError(f"{chain_id}: two nodes are both called {n.id!r}")
            self._nodes[n.id] = n
        if not self._nodes:
            raise ChainError(f"{chain_id}: a chain with no nodes cannot place anybody")
        # Order first (Kahn's algorithm never raises: a cycle simply leaves nodes out), then the
        # validation that reports it properly, then the depths — which recurse, and must not be asked
        # to recurse through a cycle.
        self._order = self._topological()
        problems = self.validate()
        if problems:
            raise ChainError(f"{chain_id}: " + "; ".join(problems))
        self._depth = self._compute_depth()

    # -- structure ------------------------------------------------------ #
    @property
    def nodes(self) -> tuple[Node, ...]:
        return tuple(self._nodes.values())

    @property
    def order(self) -> tuple[str, ...]:
        """Topological order: every node after all of its prerequisites."""
        return self._order

    @property
    def roots(self) -> tuple[str, ...]:
        return tuple(nid for nid in self._order if not self._nodes[nid].prerequisites)

    @property
    def leaves(self) -> tuple[str, ...]:
        used = {p for n in self._nodes.values() for p in n.prerequisites}
        return tuple(nid for nid in self._order if nid not in used)

    @property
    def depth(self) -> int:
        """The longest prerequisite path, counted in nodes."""
        return max(self._depth.values()) if self._depth else 0

    def node(self, node_id: str) -> Node:
        if node_id not in self._nodes:
            raise ChainError(f"{self.chain_id}: no node {node_id!r}")
        return self._nodes[node_id]

    def has(self, node_id: str) -> bool:
        return node_id in self._nodes

    def sub_areas(self) -> tuple[str, ...]:
        """Sub-areas in chain order, so rotation follows the subject rather than a dictionary."""
        seen: list[str] = []
        for nid in self._order:
            area = self._nodes[nid].sub_area
            if area not in seen:
                seen.append(area)
        return tuple(seen)

    def nodes_in(self, sub_area: str) -> tuple[Node, ...]:
        return tuple(self._nodes[nid] for nid in self._order if self._nodes[nid].sub_area == sub_area)

    def depth_of(self, node_id: str) -> int:
        return int(self._depth.get(node_id, 1))

    def ancestors(self, node_id: str) -> tuple[str, ...]:
        """Every node this one depends on, nearest first."""
        out: list[str] = []
        seen: set[str] = set()
        stack = list(self.node(node_id).prerequisites)
        while stack:
            nid = stack.pop(0)
            if nid in seen or nid not in self._nodes:
                continue
            seen.add(nid)
            out.append(nid)
            stack.extend(self._nodes[nid].prerequisites)
        return tuple(out)

    def path_to_root(self, node_id: str) -> tuple[str, ...]:
        """The descent path the staircase follows when a low rung fails: nearest prerequisite first,
        then *its* nearest prerequisite, and so on to a root."""
        path: list[str] = []
        current = node_id
        guard = 0
        while guard <= len(self._nodes):
            guard += 1
            prereqs = [p for p in self.node(current).prerequisites if p in self._nodes]
            if not prereqs:
                break
            # The deepest prerequisite first: descending to the one with the most structure beneath it
            # gives the learner the best chance of finding a rung they can stand on.
            nxt = max(prereqs, key=lambda p: (self.depth_of(p), p))
            path.append(nxt)
            current = nxt
        return tuple(path)

    def mid_node(self) -> str:
        """Where the staircase starts: the middle of the longest path through the chain.

        Mid-chain rather than a leaf or a root, because at a leaf the first answer tells you nothing
        about the prerequisites underneath and at a root it tells you nothing about the topic the
        learner actually named.
        """
        longest: list[str] = []
        for leaf in self.leaves or self._order:
            path = [*self.path_from_root(leaf), leaf]
            if len(path) > len(longest):
                longest = path
        if not longest:
            longest = list(self._order)
        return longest[len(longest) // 2]

    def path_from_root(self, node_id: str) -> tuple[str, ...]:
        """The chain of prerequisites behind a node, root first."""
        path: list[str] = []
        current = node_id
        guard = 0
        while guard <= len(self._nodes):
            guard += 1
            prereqs = [p for p in self.node(current).prerequisites if p in self._nodes]
            if not prereqs:
                break
            nxt = max(prereqs, key=lambda p: (self.depth_of(p), p))
            path.append(nxt)
            current = nxt
        return tuple(reversed(path))

    # -- validation ----------------------------------------------------- #
    def _compute_depth(self) -> dict[str, int]:
        depth: dict[str, int] = {}

        def walk(node_id: str, seen: set[str]) -> int:
            if node_id in depth:
                return depth[node_id]
            if node_id in seen:  # pragma: no cover - validate() runs first and refuses cycles
                raise ChainError(f"{self.chain_id}: cycle at {node_id}")
            node = self._nodes[node_id]
            prereqs = [p for p in node.prerequisites if p in self._nodes]
            value = 1 + max((walk(p, seen | {node_id}) for p in prereqs), default=0)
            depth[node_id] = value
            return value

        for nid in self._nodes:
            walk(nid, set())
        return depth

    def _topological(self) -> tuple[str, ...]:
        """Kahn's algorithm. A leftover node means a cycle, which ``validate`` reports properly."""
        indegree = {nid: len([p for p in n.prerequisites if p in self._nodes]) for nid, n in self._nodes.items()}
        ready = sorted(nid for nid, d in indegree.items() if d == 0)
        out: list[str] = []
        while ready:
            nid = ready.pop(0)
            out.append(nid)
            for other, node in sorted(self._nodes.items()):
                if nid in node.prerequisites and other not in out:
                    indegree[other] -= 1
                    if indegree[other] == 0:
                        ready.append(other)
            ready.sort()
        return tuple(out)

    def cycles(self) -> list[tuple[str, ...]]:
        """A real cycle check: depth-first with three colours, reporting the path it found.

        ``_compute_depth`` would also fail on a cycle, but with a recursion error rather than an
        answer. §6.4 requires the chain to be *validated* acyclic, and a validation that crashes
        instead of reporting is not a validation.
        """
        WHITE, GREY, BLACK = 0, 1, 2
        colour = {nid: WHITE for nid in self._nodes}
        found: list[tuple[str, ...]] = []

        def visit(node_id: str, path: list[str]) -> None:
            colour[node_id] = GREY
            path.append(node_id)
            for prereq in self._nodes[node_id].prerequisites:
                if prereq not in self._nodes:
                    continue
                if colour[prereq] == GREY:
                    start = path.index(prereq) if prereq in path else 0
                    found.append(tuple([*path[start:], prereq]))
                elif colour[prereq] == WHITE:
                    visit(prereq, path)
            path.pop()
            colour[node_id] = BLACK

        for nid in sorted(self._nodes):
            if colour[nid] == WHITE:
                visit(nid, [])
        return found

    def validate(self) -> list[str]:
        """Every structural rule §6.4 names. Returns problems (empty = usable)."""
        problems: list[str] = []
        for nid, node in self._nodes.items():
            for prereq in node.prerequisites:
                if prereq not in self._nodes:
                    problems.append(f"{nid}: prerequisite {prereq!r} is not a node in this chain")
                if prereq == nid:
                    problems.append(f"{nid}: lists itself as a prerequisite")
            if not str(node.description).strip():
                problems.append(f"{nid}: has no description, so a learner cannot be told what it is")
            if not str(node.sub_area).strip():
                problems.append(f"{nid}: has no sub-area, so the ladder cannot rotate")
            for rung in range(1, MAX_RUNG + 1):
                item = node.probe(rung)
                if not item:
                    problems.append(f"{nid}: has no task for rung {rung}, so it is not individually testable")
        for cycle in self.cycles():
            problems.append("cycle: " + " -> ".join(cycle))
        if not self.cycles() and len(self._order) == len(self._nodes):
            depth = max(self._compute_depth().values(), default=0)
            if depth > MAX_DEPTH:
                problems.append(f"depth {depth} exceeds the maximum of {MAX_DEPTH}")
        if len(self._order) != len(self._nodes):
            missing = sorted(set(self._nodes) - set(self._order))
            problems.append(f"a cycle leaves {len(missing)} node(s) out of any order: {', '.join(missing)}")
        if len(self.sub_areas()) < 2 and len(self._nodes) > 1:
            problems.append(
                f"all {len(self._nodes)} nodes are in one sub-area ({self.sub_areas()[0] if self.sub_areas() else '?'}), "
                "so the ladder cannot rotate across sub-areas"
            )
        return problems

    def depth_ok(self) -> bool:
        return self.depth <= MAX_DEPTH

    def as_dict(self, *, with_nodes: bool = True) -> dict[str, Any]:
        return {
            "chain_id": self.chain_id,
            "title": self.title,
            "topic_key": self.topic_key,
            "source": self.source,
            "note": self.note,
            "nodes": len(self._nodes),
            "depth": self.depth,
            "max_depth": MAX_DEPTH,
            "roots": list(self.roots),
            "leaves": list(self.leaves),
            "sub_areas": list(self.sub_areas()),
            "order": list(self._order),
            "start_node": self.mid_node(),
            **({"chain": [self.node(nid).as_dict() for nid in self._order]} if with_nodes else {}),
        }


# --------------------------------------------------------------------------- #
# the staircase
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class LadderStep:
    """What the learner is being asked right now."""

    step_id: str
    node_id: str
    node_title: str
    sub_area: str
    rung: int
    index: int
    task: Mapping[str, Any]
    #: The server-side copy, with the answer key. Never sent to a browser.
    task_full: Mapping[str, Any]
    why: str
    remaining: int
    done: bool = False

    def public(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "node_id": self.node_id,
            "node_title": self.node_title,
            "sub_area": self.sub_area,
            "rung": self.rung,
            "rung_title": rung_title(self.rung),
            "demand": rung_row(self.rung)["demand"],
            "index": self.index,
            "of": MAX_ITEMS,
            "remaining": self.remaining,
            "task": dict(self.task),
            "why": self.why,
        }


@dataclass(frozen=True)
class LadderState:
    """Where a learner is in the staircase. Immutable: every answer returns a new one."""

    chain_id: str
    node_id: str
    rung: int = START_RUNG
    index: int = 0
    reversals: int = 0
    direction: str = "start"  # start | up | down | rotate
    history: tuple[Mapping[str, Any], ...] = ()
    visited_nodes: tuple[str, ...] = ()
    visited_sub_areas: tuple[str, ...] = ()
    familiarity: Mapping[str, bool] = field(default_factory=dict)
    done: bool = False
    stop_reason: str = ""
    bottomed_out: bool = False
    settled: Mapping[str, int] = field(default_factory=dict)

    def replace(self, **changes: Any) -> "LadderState":
        values: dict[str, Any] = {
            "chain_id": self.chain_id,
            "node_id": self.node_id,
            "rung": self.rung,
            "index": self.index,
            "reversals": self.reversals,
            "direction": self.direction,
            "history": self.history,
            "visited_nodes": self.visited_nodes,
            "visited_sub_areas": self.visited_sub_areas,
            "familiarity": self.familiarity,
            "done": self.done,
            "stop_reason": self.stop_reason,
            "bottomed_out": self.bottomed_out,
            "settled": self.settled,
        }
        values.update(changes)
        return LadderState(**values)

    def as_dict(self) -> dict[str, Any]:
        return {
            "chain_id": self.chain_id,
            "node_id": self.node_id,
            "rung": self.rung,
            "rung_title": rung_title(self.rung),
            "index": self.index,
            "reversals": self.reversals,
            "direction": self.direction,
            "history": [dict(h) for h in self.history],
            "visited_nodes": list(self.visited_nodes),
            "visited_sub_areas": list(self.visited_sub_areas),
            "familiarity": {k: bool(v) for k, v in self.familiarity.items()},
            "done": self.done,
            "stop_reason": self.stop_reason,
            "bottomed_out": self.bottomed_out,
            "settled": {k: int(v) for k, v in self.settled.items()},
        }


def start(chain: Chain) -> LadderState:
    """Rung 2, mid-chain (§6.4). One sub-area is already 'visited': the one they start in."""
    node_id = chain.mid_node()
    node = chain.node(node_id)
    return LadderState(
        chain_id=chain.chain_id,
        node_id=node_id,
        rung=START_RUNG,
        visited_nodes=(node_id,),
        visited_sub_areas=(node.sub_area,),
    )


def next_step(state: LadderState, chain: Chain) -> LadderStep | None:
    """The task on offer, or ``None`` when the ladder is finished."""
    if state.done:
        return None
    node = chain.node(state.node_id)
    rung = state.rung
    index = state.index
    step_id = f"{chain.chain_id}:{node.id}:{rung}:{index}"
    if rung <= MIN_RUNG:
        task_full: dict[str, Any] = {
            "id": step_id,
            "kind": "met",
            "stage": "ladder",
            "rung": 0,
            "node": node.id,
            "node_title": node.title,
            "sub_area": node.sub_area,
            "stem": f"Have you come across {node.title.lower()} before?",
            "instructions": "This is not a test. It only decides where the ladder starts climbing from.",
            "options": ["yes, I have met it", "no, this is new to me"],
            "seconds": 10,
            "wagerable": False,
            "scored": False,
        }
        why = (
            f"{node.title}: the ladder has run out of rungs below this, so it asks the one question "
            "that is not a test — whether you have met the idea at all"
        )
        return LadderStep(
            step_id=step_id,
            node_id=node.id,
            node_title=node.title,
            sub_area=node.sub_area,
            rung=0,
            index=index,
            task=G.public_task(task_full),
            task_full=task_full,
            why=why,
            remaining=max(0, MAX_ITEMS - index),
        )
    item = node.probe(rung)
    if item is None:  # pragma: no cover - validate() refuses a node without a full kit
        raise ChainError(f"{node.id}: no task for rung {rung}")
    task_full = {
        **dict(item),
        "id": step_id,
        "stage": "ladder",
        "rung": rung,
        "node": node.id,
        "node_title": node.title,
        "sub_area": node.sub_area,
        "seconds": SECONDS_PER_RUNG,
        "wagerable": True,
        "instructions": f"{rung_title(rung)} — {rung_row(rung)['demand']}",
    }
    if task_full.get("kind") == "choice" and task_full.get("options"):
        # Shuffled per (node, rung, learner seed) exactly as the skill games are, so two learners do
        # not share an answer position.
        import random

        from ..learning.seeds import stable_seed

        rng = random.Random(stable_seed("ladder", step_id, "shuffle"))
        positions = list(range(len(task_full["options"])))
        rng.shuffle(positions)
        task_full["options"] = [task_full["options"][p] for p in positions]
        task_full["answer"] = positions.index(int(task_full["answer"]))
        task_full["shuffled"] = True
    why = (
        f"{node.title}, rung {rung} ({rung_row(rung)['demand']}). "
        + _why_arrived(state, chain, node, rung)
    )
    return LadderStep(
        step_id=step_id,
        node_id=node.id,
        node_title=node.title,
        sub_area=node.sub_area,
        rung=rung,
        index=index,
        task=G.public_task(task_full),
        task_full=task_full,
        why=why,
        remaining=max(0, MAX_ITEMS - index),
    )


def _why_arrived(state: LadderState, chain: Chain, node: Node, rung: int) -> str:
    if state.index == 0:
        return "This is where the ladder starts for everybody: the middle of the chain, at 'recall'."
    last = state.history[-1] if state.history else {}
    if last.get("correct") is True:
        return f"The last one was right, so the ladder goes up to rung {rung}."
    if last.get("correct") is False:
        if last.get("moved_node"):
            return (
                f"The last one was wrong at rung {int(last.get('rung') or 0)}, which is low enough that "
                f"the ladder drops down the chain to what this depends on: {node.title}."
            )
        return f"The last one was wrong, so the ladder goes down to rung {rung}."
    return "The last answer was a self-report, so the ladder moved on."


def score_step(step: LadderStep, response: Mapping[str, Any]) -> dict[str, Any]:
    """Grade one ladder answer. Rung 0 is a self-report and is never scored as knowledge."""
    if step.rung <= MIN_RUNG:
        given = response.get("choice", response.get("met"))
        if given is None or given == "":
            return {"correct": None, "status": "skipped", "met": None, "points": 0, "max_points": 0,
                    "why": "nothing was answered, so this is recorded as not measured"}
        try:
            met = bool(int(given) == 0)
        except (TypeError, ValueError):
            met = str(given).strip().lower() in ("yes", "true", "met")
        return {
            "correct": None,
            "status": "familiarity",
            "met": met,
            "points": 0,
            "max_points": 0,
            "scored": False,
            "why": (
                "you have met this idea, so the ladder will keep climbing here"
                if met
                else "this is new to you, and that is an answer, not a failure: the ladder records it as familiarity and moves to another part of the topic"
            ),
        }
    return G.score_task(step.task_full, response)


def advance(
    state: LadderState,
    chain: Chain,
    *,
    step: LadderStep,
    scored: Mapping[str, Any],
    latency_ms: int | None = None,
    wager: int | None = None,
) -> LadderState:
    """One answer in, the next position out. The staircase rules of §6.4, in one place.

    Returns a new :class:`LadderState`; the old one is untouched, so a caller can always show the
    learner the path they took rather than a summary of it.
    """
    correct = scored.get("correct")
    node = chain.node(state.node_id)
    entry: dict[str, Any] = {
        "index": state.index,
        "node_id": node.id,
        "node_title": node.title,
        "sub_area": node.sub_area,
        "rung": step.rung,
        "correct": correct,
        "points": scored.get("points"),
        "max_points": scored.get("max_points"),
        "status": scored.get("status"),
        "latency_ms": latency_ms,
        "wager": wager,
        "step_id": step.step_id,
        "moved_node": False,
    }
    history = (*state.history, entry)
    index = state.index + 1
    familiarity = dict(state.familiarity)

    # --- rung 0: a self-report ends this sub-area's climb ---------------- #
    if step.rung <= MIN_RUNG:
        familiarity[node.id] = bool(scored.get("met"))
        settled = dict(state.settled)
        settled[node.sub_area] = MIN_RUNG
        new = state.replace(
            index=index,
            history=history,
            familiarity=familiarity,
            settled=settled,
            bottomed_out=True,
            direction="rotate",
        )
        return _rotate_or_stop(new, chain)

    if correct is None:
        # A blank is not a miss and not a pass: it does not move the ladder. §6.3's "skipped is never
        # zero" applies one item at a time as well as one skill at a time.
        new = state.replace(index=index, history=history)
        return _stop_if_over(new, chain, reason="an item was left blank; the ladder does not move on a blank")

    if correct:
        if step.rung >= MAX_RUNG:
            settled = dict(state.settled)
            settled[node.sub_area] = MAX_RUNG
            new = state.replace(
                index=index,
                history=history,
                settled=settled,
                direction="rotate",
                reversals=state.reversals,
            )
            return _rotate_or_stop(new, chain)
        direction = "up"
        reversals = state.reversals + (1 if state.direction == "down" else 0)
        new = state.replace(index=index, history=history, rung=step.rung + 1, direction=direction, reversals=reversals)
        return _stop_if_over(new, chain)

    # --- wrong ----------------------------------------------------------- #
    if step.rung > 2:
        direction = "down"
        reversals = state.reversals + (1 if state.direction == "up" else 0)
        new = state.replace(index=index, history=history, rung=step.rung - 1, direction=direction, reversals=reversals)
        return _stop_if_over(new, chain)

    # A failure at rung 1 or 2 is not one rung away from being fixed: the ladder drops down the
    # prerequisite chain (§6.4). At a root there is nothing below, so it asks about familiarity.
    descent = [nid for nid in chain.path_to_root(node.id) if nid not in state.visited_nodes]
    if not descent:
        new = state.replace(index=index, history=history, rung=MIN_RUNG, direction="down")
        return _stop_if_over(new, chain, reason="the chain has nothing below this node")
    target = descent[0]
    entry_moved = dict(history[-1])
    entry_moved["moved_node"] = True
    entry_moved["moved_to"] = target
    history = (*history[:-1], entry_moved)
    area = chain.node(target).sub_area
    visited_areas = state.visited_sub_areas if area in state.visited_sub_areas else (*state.visited_sub_areas, area)
    new = state.replace(
        index=index,
        history=history,
        node_id=target,
        # A rung-2 failure drops to "recall" at the prerequisite; a rung-1 failure means even
        # recognition is not there, so the climb restarts one rung lower.
        rung=START_RUNG if step.rung == 2 else 1,
        visited_nodes=(*state.visited_nodes, target),
        visited_sub_areas=visited_areas,
        direction="down",
        reversals=state.reversals + (1 if state.direction == "up" else 0),
    )
    return _stop_if_over(new, chain)


def _rotate_or_stop(state: LadderState, chain: Chain) -> LadderState:
    """Move to a sub-area the ladder has not measured yet, at rung 2 (or 1 after a struggle)."""
    if _over(state):
        return _stopped(state, chain)
    areas = chain.sub_areas()
    remaining = [a for a in areas if a not in state.visited_sub_areas]
    if not remaining:
        return _stopped(state, chain, reason="every sub-area in this chain has been measured")
    area = remaining[0]
    # Start at the deepest node of the new sub-area that has not been visited, so the rotation walks
    # the chain rather than jumping to its root.
    candidates = [n for n in chain.nodes_in(area) if n.id not in state.visited_nodes] or list(chain.nodes_in(area))
    target = max(candidates, key=lambda n: (chain.depth_of(n.id), n.id))
    recent = [h for h in state.history[-2:] if h.get("correct") is not None]
    struggled = bool(recent) and not all(h.get("correct") for h in recent)
    return state.replace(
        node_id=target.id,
        rung=1 if struggled else START_RUNG,
        visited_nodes=(*state.visited_nodes, target.id),
        visited_sub_areas=(*state.visited_sub_areas, area),
        direction="rotate",
    )


def _over(state: LadderState) -> bool:
    return state.index >= MAX_ITEMS or state.reversals >= MAX_REVERSALS or state.done


def _stop_if_over(state: LadderState, chain: Chain, *, reason: str = "") -> LadderState:
    if not _over(state):
        return state
    return _stopped(state, chain, reason=reason)


def _stopped(state: LadderState, chain: Chain, *, reason: str = "") -> LadderState:
    if state.done:
        return state
    if not reason:
        if state.index >= MAX_ITEMS:
            reason = f"the ladder stops after {MAX_ITEMS} items"
        elif state.reversals >= MAX_REVERSALS:
            reason = f"the ladder stops after {MAX_REVERSALS} reversals (up, then down, then up)"
        else:
            reason = "the ladder has measured what it set out to measure"
    return state.replace(done=True, stop_reason=reason)


# --------------------------------------------------------------------------- #
# the placement the ladder produces
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SubAreaPosition:
    """Where one sub-area settled, and how much evidence is behind it.

    ``steady_rung`` is the classic staircase estimate — the rung at which the climb turned around —
    and where no turn happened in a sub-area it is the highest rung answered correctly there. It is
    ``None`` when the ladder knows only that the learner is *below* a rung it never tested, which
    happens when a rung-2 failure sends the ladder down the prerequisite chain instead of down a
    rung. Reporting that as rung 1 would claim a recognition the app never measured, and reporting it
    as rung 0 would claim a beginner the app never met; ``placed=False`` with the floor named is the
    only honest option.
    """

    sub_area: str
    node_ids: tuple[str, ...]
    node_titles: tuple[str, ...]
    steady_rung: int | None
    rung_title: str
    #: Whether the ladder actually found a rung in this sub-area.
    placed: bool
    items: int
    correct: int
    n: int
    #: ``early signal`` whenever n is under the app's own threshold. Nine items is the ladder's
    #: ceiling, so a ladder position is always an early signal — and says so.
    evidence: str
    reversals: int
    familiarity: bool | None
    #: The rung the learner was found to be below, when the ladder never measured where they stand.
    below_rung: int | None = None
    seconds_per_rung: dict[int, float] = field(default_factory=dict)
    wagers: tuple[int, ...] = ()
    rung_path: tuple[int, ...] = ()
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "sub_area": self.sub_area,
            "nodes": [{"id": i, "title": t} for i, t in zip(self.node_ids, self.node_titles)],
            "steady_rung": self.steady_rung,
            "rung_title": self.rung_title,
            "demand": rung_row(self.steady_rung if self.steady_rung is not None else 0)["demand"],
            "placed": self.placed,
            "below_rung": self.below_rung,
            "items": self.items,
            "correct": self.correct,
            "n": self.n,
            "observations": self.n,
            "evidence": self.evidence,
            "reversals": self.reversals,
            "familiarity": self.familiarity,
            "seconds_per_rung": {str(k): round(v, 1) for k, v in sorted(self.seconds_per_rung.items())},
            "wagers": list(self.wagers),
            "rung_path": list(self.rung_path),
            "note": self.note,
            # Never a score out of anything: §5.4's rule ("no scores out of 100") applies here too.
            "score": None,
        }


def positions(state: LadderState, chain: Chain) -> list[SubAreaPosition]:
    """The placement: one row per sub-area the chain has, measured or not."""
    out: list[SubAreaPosition] = []
    for area in chain.sub_areas():
        rows = [h for h in state.history if h.get("sub_area") == area]
        scored_rows = [h for h in rows if h.get("correct") is not None]
        nodes = [nid for nid in dict.fromkeys(h.get("node_id") for h in rows if h.get("node_id"))]
        titles = tuple(chain.node(nid).title for nid in nodes if chain.has(nid))
        reversals = 0
        direction = ""
        for h in rows:
            if h.get("correct") is True:
                step_dir = "up"
            elif h.get("correct") is False:
                step_dir = "down"
            else:
                continue
            if direction and step_dir != direction:
                reversals += 1
            direction = step_dir

        familiarity = None
        for nid in nodes:
            if nid in state.familiarity:
                familiarity = bool(state.familiarity[nid])

        latencies: dict[int, list[float]] = {}
        for h in scored_rows:
            if h.get("latency_ms") is not None:
                latencies.setdefault(int(h.get("rung") or 0), []).append(float(h["latency_ms"]) / 1000.0)

        turn = _reversal_rung(rows)
        correct_rungs = [int(h.get("rung") or 0) for h in scored_rows if h.get("correct")]
        failed_rungs = [int(h.get("rung") or 0) for h in scored_rows if h.get("correct") is False]

        if not rows:
            out.append(
                SubAreaPosition(
                    sub_area=area,
                    node_ids=tuple(n.id for n in chain.nodes_in(area)),
                    node_titles=tuple(n.title for n in chain.nodes_in(area)),
                    steady_rung=None,
                    rung_title="not reached",
                    placed=False,
                    items=0,
                    correct=0,
                    n=0,
                    evidence="not measured",
                    reversals=0,
                    familiarity=None,
                    note=(
                        "the ladder did not reach this sub-area, so it is not measured — which is not "
                        "the same as being at rung 0"
                    ),
                )
            )
            continue

        if turn is not None:
            steady: int | None = turn
            placed = True
            note = f"the climb turned here at rung {turn}, which is what a staircase reports"
        elif correct_rungs:
            steady = max(correct_rungs)
            placed = True
            note = "no turn in this sub-area, so the position is the highest rung answered correctly"
        elif familiarity is not None:
            # The learner bottomed out: the ladder ran out of chain below and asked the one question
            # that is not a test. Rung 0 is a real placement outcome (§6.4's true beginner).
            steady = MIN_RUNG
            placed = True
            note = (
                "the ladder reached the bottom of the chain here and asked whether you had met the idea; "
                "rung 0 is where teaching starts, not a grade"
            )
        else:
            steady = None
            placed = False
            note = (
                f"every item here was wrong (the lowest rung asked was {min(failed_rungs) if failed_rungs else '?'}), "
                "and the ladder went down the chain instead of testing a lower rung in this sub-area, "
                "so it is below that rung but not placed"
            )

        n = len(scored_rows)
        if n and n < B.EARLY_SIGNAL_N:
            note = f"{note}. Early signal: {n} item(s) is under the {B.EARLY_SIGNAL_N} the app asks for before it calls an estimate measured"
        out.append(
            SubAreaPosition(
                sub_area=area,
                node_ids=tuple(nodes),
                node_titles=titles,
                steady_rung=steady,
                rung_title=rung_title(steady) if steady is not None else "not placed",
                placed=placed,
                items=len(rows),
                correct=len(correct_rungs),
                n=n,
                evidence="early signal" if 0 < n < B.EARLY_SIGNAL_N else ("measured" if n >= B.EARLY_SIGNAL_N else "not measured"),
                reversals=reversals,
                familiarity=familiarity,
                below_rung=(min(failed_rungs) if steady is None and failed_rungs else None),
                seconds_per_rung={r: sum(v) / len(v) for r, v in latencies.items()},
                wagers=tuple(int(h["wager"]) for h in scored_rows if h.get("wager") is not None),
                rung_path=tuple(int(h.get("rung") or 0) for h in rows),
                note=note,
            )
        )
    return out


def _reversal_rung(rows: Sequence[Mapping[str, Any]]) -> int | None:
    """The rung at which the climb turned from up to down — the staircase's own estimate."""
    direction = ""
    for h in rows:
        if h.get("correct") is True:
            step_dir = "up"
        elif h.get("correct") is False:
            step_dir = "down"
        else:
            continue
        if direction == "up" and step_dir == "down":
            return max(MIN_RUNG, int(h.get("rung") or 0) - 1)
        direction = step_dir
    return None


def placement_payload(state: LadderState, chain: Chain) -> dict[str, Any]:
    """The whole placement, as the snapshot screen and the API want it."""
    rows = positions(state, chain)
    measured = [r for r in rows if r.placed and r.n > 0]
    highest = max((r.steady_rung for r in measured if r.steady_rung is not None), default=MIN_RUNG)
    return {
        "available": True,
        "chain": chain.as_dict(with_nodes=False),
        "state": state.as_dict(),
        "positions": [r.as_dict() for r in rows],
        "measured_sub_areas": len(measured),
        "placed_sub_areas": len(measured),
        "sub_areas": len(rows),
        "highest_steady_rung": highest,
        "highest_rung_title": rung_title(highest),
        "items": len(state.history),
        "max_items": MAX_ITEMS,
        "reversals": state.reversals,
        "max_reversals": MAX_REVERSALS,
        "bottomed_out": state.bottomed_out,
        "stop_reason": state.stop_reason,
        "rungs": [dict(r) for r in RUNGS],
        "note": (
            "A ladder position says where to start teaching. It is not a score, and it is not the "
            "learner model: mastery estimates come from the evidence log, with their own n and range."
            if measured
            else "the ladder did not reach a rung it could measure, so nothing here is a placement"
        ),
    }


def chain_from_spec(spec: TopicSpec | None) -> Chain | None:
    """Convenience for callers that only have a topic spec. ``curriculum.chain_for`` is the real entry."""
    if spec is None or not spec.chain:
        return None
    from .curriculum import chain_for_key

    return chain_for_key(spec.chain)


__all__ = [
    "MAX_DEPTH",
    "MAX_ITEMS",
    "MAX_REVERSALS",
    "MAX_RUNG",
    "MIN_RUNG",
    "RUNGS",
    "RUNG_KEYS",
    "SECONDS_PER_RUNG",
    "START_RUNG",
    "Chain",
    "ChainError",
    "LadderState",
    "LadderStep",
    "Node",
    "SubAreaPosition",
    "advance",
    "chain_from_spec",
    "next_step",
    "placement_payload",
    "positions",
    "rung_row",
    "rung_title",
    "score_step",
    "start",
]
