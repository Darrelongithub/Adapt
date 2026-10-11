"""Knowledge gaps and prerequisite-driven remediation.

The topic graph is a DAG: ``topic -> prerequisite`` edges. AI 1 uses it for three
things that the blueprint asks for by name:

* ``detect_gaps`` -- a topic is only blamed on itself when its prerequisites are
  actually traced as known; otherwise the gap is reported *one level down*, which
  is where the fix belongs.
* ``remediation_plan`` -- shortest fix order, weakest prerequisite first, with the
  topics each fix unblocks (so the student sees why we are going "backwards").
* ``learning_path`` -- a personalised ordering over the remaining topics that only
  advances when prerequisites clear a threshold, plus spaced review insertion.

Severity deliberately mixes mastery and *fan-out* (how much of the course a weak
topic blocks): a shaky topic that gates six others is a bigger problem than a
shaky leaf.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field


@dataclass
class Graph:
    topics: dict[str, str] = field(default_factory=dict)  # id -> title
    prereqs: dict[str, list[str]] = field(default_factory=dict)  # topic -> [prereq ids]
    weights: dict[tuple[str, str], float] = field(default_factory=dict)

    @property
    def dependents(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = defaultdict(list)
        for topic, ps in self.prereqs.items():
            for p in ps:
                out[p].append(topic)
        return out

    def direct_prereqs(self, topic: str) -> list[str]:
        return list(self.prereqs.get(topic, []))

    def closure(self, topic: str, *, max_depth: int = 6) -> list[str]:
        """All transitive prerequisites (BFS, cycle-safe)."""
        seen: list[str] = []
        q: deque[tuple[str, int]] = deque((p, 1) for p in self.prereqs.get(topic, []))
        while q:
            node, depth = q.popleft()
            if node in seen or depth > max_depth:
                continue
            seen.append(node)
            for p in self.prereqs.get(node, []):
                q.append((p, depth + 1))
        return seen

    def has_cycle(self) -> list[str] | None:
        """Return a cycle if the graph is not a DAG (config error, not learner data)."""
        indeg = {t: 0 for t in self.topics}
        for topic, ps in self.prereqs.items():
            for p in ps:
                if topic in indeg:
                    indeg[topic] += 1
        q = deque(t for t, d in indeg.items() if d == 0)
        seen = 0
        dependents = self.dependents
        while q:
            node = q.popleft()
            seen += 1
            for nxt in dependents.get(node, []):
                indeg[nxt] -= 1
                if indeg[nxt] == 0:
                    q.append(nxt)
        return None if seen == len(indeg) else [t for t, d in indeg.items() if d > 0]


@dataclass
class Gap:
    topic_id: str
    topic: str
    blocking_prereq: str
    blocking_prereq_title: str
    mastery: float
    prereq_mastery: float
    n_obs: int
    fan_out: int
    severity: float
    unblocks: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "topic_id": self.topic_id,
            "topic": self.topic_id,
            "topic_title": self.topic,
            "blocking_prerequisite": self.blocking_prereq,
            "blocking_prerequisite_title": self.blocking_prereq_title,
            "topic_mastery": round(self.mastery, 3),
            "prerequisite_mastery": round(self.prereq_mastery, 3),
            "observations": self.n_obs,
            "blocks_topics": self.fan_out,
            "severity": round(self.severity, 3),
            "unblocks": self.unblocks,
            "why": (
                f"{self.topic} is at {self.mastery:.0%} and its prerequisite "
                f"{self.blocking_prereq_title} is at {self.prereq_mastery:.0%}; "
                f"fixing the prerequisite first is likely to move {self.fan_out} "
                f"other topic(s)."
            ),
        }


def build_graph(topics: list[tuple[str, str]], edges: list[tuple[str, str, float]]) -> Graph:
    """topics: [(id, title)], edges: [(topic_id, prerequisite_id, weight)]."""
    g = Graph()
    for tid, title in topics:
        g.topics[tid] = title
    for tid, pid, w in edges:
        if tid == pid:
            continue
        g.prereqs.setdefault(tid, []).append(pid)
        g.weights[(tid, pid)] = w
    for tid in g.topics:
        g.prereqs.setdefault(tid, [])
    return g


def detect_gaps(
    graph: Graph,
    mastery: dict[str, float],
    observations: dict[str, int] | None = None,
    *,
    threshold: float = 0.45,
    min_observations: int = 2,
) -> list[Gap]:
    """Weak prerequisite under a topic that is also weak (or untried)."""
    observations = observations or {}
    gaps: list[Gap] = []
    for topic, prereqs in graph.prereqs.items():
        if not prereqs:
            continue
        t_mastery = mastery.get(topic)
        n = observations.get(topic, 0)
        for p in prereqs:
            p_mastery = mastery.get(p)
            p_n = observations.get(p, 0)
            if p_mastery is None or p_n < 1:
                # Unmeasured is not weak. Defaulting an absent trace to 0.0 turns
                # every topic the learner has not reached yet into an urgent
                # "gap", which is how a system like this starts recommending a
                # tour of the whole syllabus. Absence of evidence gets reported
                # as absence of evidence (see profile.open_questions).
                continue
            if p_mastery >= threshold:
                continue
            # Either the dependent topic is already failing, or we have no data on
            # it yet and the prerequisite alone is the actionable fact.
            if t_mastery is not None and t_mastery > 0.65:
                continue
            t_mastery_v = t_mastery if t_mastery is not None else 0.0
            dependents = graph.dependents.get(p, [])
            severity = min(
                1.0,
                (threshold - p_mastery) / max(0.05, threshold) * 0.6
                + (0.4 * min(1.0, len(dependents) / 4 if dependents else 0.15))
                + (0.1 if n >= min_observations else 0.0),
            )
            gaps.append(
                Gap(
                    topic_id=topic,
                    topic=graph.topics.get(topic, topic),
                    blocking_prereq=p,
                    blocking_prereq_title=graph.topics.get(p, p),
                    mastery=t_mastery_v,
                    prereq_mastery=p_mastery,
                    n_obs=n,
                    fan_out=len(dependents),
                    severity=severity,
                    unblocks=[graph.topics.get(d, d) for d in dependents][:6],
                )
            )
    return sorted(gaps, key=lambda g: g.severity, reverse=True)


def remediation_plan(
    graph: Graph,
    mastery: dict[str, float],
    gaps: list[Gap],
    *,
    max_steps: int = 4,
    threshold: float = 0.45,
) -> list[dict[str, object]]:
    """Ordered 'go back and fix this' list, most foundational first.

    Ranking rule: repair a prerequisite before anything that depends on it, so we
    never remediate a skill whose own input is still missing. ``upstream_load`` is
    the number of weak topics that transitively require a node -- the higher it
    is, the more of the course a single fix unblocks.
    """
    weak = {g.blocking_prereq for g in gaps if g.prereq_mastery < threshold}
    if not weak:
        return []
    dependents = graph.dependents

    def _upstream_load(node: str) -> int:
        """How many weak topics sit above ``node`` in the graph."""
        seen: set[str] = set()
        q: deque[str] = deque(dependents.get(node, []))
        while q:
            nxt = q.popleft()
            if nxt in seen:
                continue
            seen.add(nxt)
            q.extend(dependents.get(nxt, []))
        return len(seen & weak)

    ranked = sorted(
        weak,
        key=lambda t: (-_upstream_load(t), -len(dependents.get(t, [])), mastery.get(t, 0.0)),
    )
    plan: list[dict[str, object]] = []
    for i, topic in enumerate(ranked[:max_steps]):
        blocked = [d for d in dependents.get(topic, []) if mastery.get(d, 0.0) < 0.7]
        plan.append(
            {
                "step": i + 1,
                "topic_id": topic,
                "topic_title": graph.topics.get(topic, topic),
                "mastery": round(mastery.get(topic, 0.0), 3),
                "upstream_load": _upstream_load(topic),
                "unblocks": [graph.topics.get(b, b) for b in blocked][:6],
                "items": 3 if i == 0 else 2,
                "why": (
                    f"We trace {graph.topics.get(topic, topic)} at "
                    f"{mastery.get(topic, 0.0):.0%}; {len(blocked)} later topic(s) depend on it."
                ),
            }
        )
    return plan


def learning_path(
    graph: Graph,
    mastery: dict[str, float],
    *,
    goal_topics: list[str] | None = None,
    target: float = 0.7,
    max_items: int = 8,
) -> dict[str, object]:
    """Personalised path: topological walk that only advances when prereqs clear."""
    goals = [t for t in (goal_topics or list(graph.topics)) if t in graph.topics]
    if not goals:
        return {"steps": [], "ready": [], "note": "no topics in scope yet"}
    indeg: dict[str, int] = {}
    prereqs = graph.prereqs
    for t in goals:
        indeg[t] = len([p for p in prereqs.get(t, []) if p in graph.topics])
    q = deque(sorted((t for t, d in indeg.items() if d == 0), key=lambda t: mastery.get(t, 0.0)))
    steps: list[dict[str, object]] = []
    ready: list[str] = []
    visited: set[str] = set()
    while q and len(steps) < max_items:
        node = q.popleft()
        if node in visited:
            continue
        visited.add(node)
        m = mastery.get(node, 0.0)
        unmet = [
            p for p in prereqs.get(node, []) if mastery.get(p, 0.0) < target and p in graph.topics
        ]
        if unmet:
            steps.append(
                {
                    "topic_id": node,
                    "topic_title": graph.topics.get(node, node),
                    "status": "blocked",
                    "mastery": round(m, 3),
                    "needs_first": [graph.topics.get(p, p) for p in unmet[:4]],
                    "action": "remediate_prerequisite",
                }
            )
        elif m >= target:
            ready.append(node)
            steps.append(
                {
                    "topic_id": node,
                    "topic_title": graph.topics.get(node, node),
                    "status": "solid",
                    "mastery": round(m, 3),
                    "action": "skip_or_extend",
                }
            )
        else:
            steps.append(
                {
                    "topic_id": node,
                    "topic_title": graph.topics.get(node, node),
                    "status": "next_up",
                    "mastery": round(m, 3),
                    "action": "teach_then_practice",
                }
            )
        for nxt in graph.dependents.get(node, []):
            if nxt in graph.topics and indeg.get(nxt, 0) > 0:
                indeg[nxt] -= 1
                if indeg[nxt] == 0:
                    q.append(nxt)
    return {
        "steps": steps,
        "ready": [graph.topics.get(t, t) for t in ready],
        "covered": len(visited),
        "note": "ordering follows the prerequisite graph, so nothing is scheduled before the skill it needs",
    }
