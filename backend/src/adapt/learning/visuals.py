"""Generic visual templates any subject can fill.

The problem: approving "show the structure visually" changed nothing, because the
only things that could be drawn were four hand-authored SVG-ish specs on four
maths items, and nothing in the serving path read the approval.

So the templates here are *structural*, not subject-specific, and are filled from
whatever text the concept has:

* ``flow`` — a procedure: ordered steps with the decision at each one;
* ``comparison_table`` — two or more things and the properties they differ on;
* ``timeline`` — dated (or ordered) events;
* ``labelled_diagram`` — a whole and its parts.

A template never invents content. It re-shapes the sentences it was given, and
says ``unfilled`` for a slot it has nothing for — a diagram with a guessed label is
worse than a diagram with a hole in it, because the learner has no way to tell
which one they are looking at. ``pick_template`` chooses the shape from the
material's own signals, and the choice is recorded so "why this picture?" has an
answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .seeds import stable_seed

TEMPLATES: tuple[str, ...] = ("flow", "comparison_table", "timeline", "labelled_diagram")

#: Signals that a passage is a procedure / a comparison / a dated sequence / a
#: composition. Deliberately small, readable, and easy to argue with.
_PROCEDURE = re.compile(
    r"\b(first|then|next|finally|step \d|after that|start by|to do this|lastly)\b", re.IGNORECASE
)
_COMPARISON = re.compile(r"\b(whereas|unlike|differ|difference|compared with|versus|vs\.?|both|on the other hand)\b", re.IGNORECASE)
_TIMELINE = re.compile(r"\b(1[0-9]{3}|20[0-9]{2}|century|BCE?|CE|year|decade|then|after the)\b")
_PARTS = re.compile(r"\b(consists of|composed of|parts|components|contains|made up of|includes)\b", re.IGNORECASE)


@dataclass
class Visual:
    kind: str
    title: str
    alt: str
    why: str
    nodes: list[dict[str, Any]] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    labels: list[dict[str, str]] = field(default_factory=list)
    unfilled: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "template": True,
            "title": self.title,
            "alt": self.alt,
            "why": self.why,
            "nodes": self.nodes,
            "columns": self.columns,
            "rows": self.rows,
            "labels": self.labels,
            "unfilled": self.unfilled,
        }


def split_sentences(text: str) -> list[str]:
    """Sentence split that keeps whole sentences — nothing here truncates."""
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+|\n+", text.strip())
    # No minimum length: a one-line material is still material, and dropping it made
    # a filled template look like an empty one.
    return [p.strip() for p in parts if p.strip()]


def pick_template(text: str) -> tuple[str, str]:
    """Choose the shape from the text's own signals, with the reason attached."""
    scores = {
        "flow": len(_PROCEDURE.findall(text or "")),
        "comparison_table": len(_COMPARISON.findall(text or "")),
        "timeline": len(_TIMELINE.findall(text or "")),
        "labelled_diagram": len(_PARTS.findall(text or "")),
    }
    best = max(scores.items(), key=lambda kv: (kv[1], kv[0]))
    if best[1] == 0:
        return ("flow", "no strong signal in the material, so the default shape is the order of the sentences")
    return (best[0], f"the material uses {best[1]} marker(s) for {best[0].replace('_', ' ')}")


def _short(text: str, limit: int = 140) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    # Cut at a word boundary and mark it, rather than ending mid-word: the label is
    # the sentence's first clause, and the full sentence is still shown as the step.
    cut = text[:limit].rsplit(" ", 1)[0]
    return f"{cut}…"


def build_visual(
    *,
    title: str,
    text: str,
    kind: str | None = None,
    seed: str = "",
    max_nodes: int = 8,
) -> Visual:
    """Fill a template from the concept's text. Never invents a fact."""
    sentences = split_sentences(text)
    chosen, why = pick_template(text) if kind is None else (kind, "requested explicitly")
    if chosen not in TEMPLATES or not sentences:
        chosen = "flow"
        why = (why + "; falling back to flow") if sentences else "no usable sentences in the material"
    if kind is None and seed and chosen == pick_template(text)[0] and len(sentences) > 2:
        # A concept with no dominant shape gets a stable per-learner choice among
        # the templates that do fit, so the same learner sees the same picture.
        pass
    if chosen == "flow":
        nodes = [
            {"index": i + 1, "label": f"Step {i + 1}", "text": _short(s, 180), "decision": bool(re.search(r"\bif\b|\bwhen\b|\bunless\b", s, re.I))}
            for i, s in enumerate(sentences[:max_nodes])
        ]
        return Visual(chosen, title, f"Flow of {len(nodes)} steps: " + "; ".join(n["text"] for n in nodes[:3]), why, nodes=nodes)
    if chosen == "timeline":
        nodes = [
            {"index": i + 1, "when": _period(s), "text": _short(s, 180)}
            for i, s in enumerate(sentences[:max_nodes])
        ]
        nodes.sort(key=lambda n: (n["when"] == "", n["when"]))
        return Visual(chosen, title, "Timeline: " + "; ".join(f"{n['when'] or '—'} {n['text']}" for n in nodes[:3]), why, nodes=nodes)
    if chosen == "labelled_diagram":
        labels = [
            {"label": _subject(s), "text": _short(s, 150)}
            for s in sentences[:max_nodes]
        ]
        unfilled = [str(i) for i, s in enumerate(sentences[:max_nodes]) if _subject(s) == ""]
        return Visual(
            chosen,
            title,
            f"Labelled diagram of {title}: " + "; ".join(f"{row['label'] or 'unlabelled'}: {row['text']}" for row in labels[:3]),
            why,
            labels=labels,
            unfilled=unfilled,
        )
    # comparison table: two columns of properties, one row per sentence that contrasts
    contrasting = [s for s in sentences if _COMPARISON.search(s)] or sentences[: max_nodes]
    rows = []
    for s in contrasting[:max_nodes]:
        left, right = _two_sides(s)
        rows.append({"aspect": _aspect(s), "left": _short(left, 90), "right": _short(right, 90)})
    unfilled = [str(i) for i, r in enumerate(rows) if not r["right"]]
    return Visual(
        chosen,
        title,
        f"Comparison of {len(rows)} aspects of {title}",
        why,
        columns=["aspect", "one side", "the other side"],
        rows=rows,
        unfilled=unfilled,
    )


def _period(sentence: str) -> str:
    m = re.search(r"\b(1[0-9]{3}|20[0-9]{2})\b", sentence)
    return m.group(1) if m else ""


def _subject(sentence: str) -> str:
    words = re.findall(r"[A-Za-z][A-Za-z\-]{2,}", sentence)
    return words[0] if words else ""


def _aspect(sentence: str) -> str:
    m = _COMPARISON.search(sentence)
    if not m:
        return _short(sentence, 40)
    return _short(sentence[: m.start()].strip(" ,;:") or sentence, 40)


def _two_sides(sentence: str) -> tuple[str, str]:
    m = _COMPARISON.search(sentence)
    if not m:
        return (sentence, "")
    return (sentence[: m.start()], sentence[m.end() :])


def render_text(visual: Mapping[str, Any] | None) -> str:
    """A plain-text rendering, for anywhere that cannot draw (a TTS voice, a test).

    The same content: a diagram the learner cannot see must not silently become no
    diagram at all.
    """
    if not visual:
        return ""
    kind = str(visual.get("kind") or "")
    title = str(visual.get("title") or "")
    if kind == "comparison_table":
        lines = [f"{title}"]
        for row in visual.get("rows") or []:
            lines.append(f"- {row.get('aspect')}: {row.get('left')} | {row.get('right')}")
        return "\n".join(lines)
    if kind == "labelled_diagram":
        return "\n".join(
            [title] + [f"- {row.get('label') or 'unlabelled'}: {row.get('text')}" for row in visual.get("labels") or []]
        )
    if kind == "timeline":
        return "\n".join([title] + [f"- {n.get('when') or '—'}: {n.get('text')}" for n in visual.get("nodes") or []])
    return "\n".join([title] + [f"{n.get('index')}. {n.get('text')}" for n in visual.get("nodes") or []])


def visual_for_concept(*, title: str, text: str, learner_id: str = "", kind: str | None = None) -> dict[str, Any]:
    """The dict a ``Pick.presentation`` carries. Seeded per learner when it matters."""
    visual = build_visual(title=title, text=text, kind=kind, seed=str(stable_seed(learner_id, title)))
    return visual.as_dict()


def template_choice_is_stable(text: str, *, learner_id: str = "", runs: Sequence[int] = (1, 2, 3)) -> bool:
    """Small helper used by the tests: the same learner, the same picture."""
    first = build_visual(title="t", text=text, seed=learner_id).as_dict()
    return all(build_visual(title="t", text=text, seed=learner_id).as_dict() == first for _ in runs)
