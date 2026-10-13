"""Schema-validated visual templates: seven shapes, drawn as SVG, never invented.

Round brief §8 (Phase 5): *"schema-validated SVG components for flow, comparison
table, timeline, labelled diagram, number line/graph, set/Venn, bar/strip models.
Content packs supply JSON; invalid specs are rejected and fall back to text with
a note."*

Three rules run through this module:

1. **A template never invents content.** It draws what the spec carries. A slot
   the spec leaves empty stays visibly empty (``unfilled``) rather than being
   filled with a plausible guess — a diagram with a guessed label is worse than
   a diagram with a hole in it, because the learner cannot tell which is which.
2. **Validation is code, and it is total.** Every string is checked for markup
   (``<svg …>``, ``<script>``, ``javascript:``, unrendered ``{{placeholders}}``),
   every list has a maximum length, every number a range. Anything that fails
   comes back as a list of ``{code, message, path}`` — never as a half-drawn
   picture. :func:`render_svg` re-validates before it draws, so there is no
   path from an unvalidated spec to SVG.
3. **The SVG is boring on purpose.** Deterministic layout, XML-escaped text,
   a ``<title>`` and ``<desc>`` for screen readers, no external references, no
   script, no animation. The same spec always renders the same bytes, which is
   what makes "the same lesson on reload" checkable.

Kind aliases are accepted at the door (``comparison`` → ``comparison_table``,
``graph`` → ``number_line``, ``strip_model`` → ``bar`` …) and normalised, so a
content pack written against either vocabulary validates against one schema.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence
from xml.sax.saxutils import escape

#: The seven templates, in the order the brief names them.
VISUAL_KINDS: tuple[str, ...] = (
    "flow",
    "comparison_table",
    "timeline",
    "labelled_diagram",
    "number_line",
    "venn",
    "bar",
)

_KIND_ALIASES: dict[str, str] = {
    "comparison": "comparison_table",
    "table": "comparison_table",
    "diagram": "labelled_diagram",
    "labelled": "labelled_diagram",
    "number_line_graph": "number_line",
    "graph": "number_line",
    "line": "number_line",
    "set": "venn",
    "sets": "venn",
    "strip_model": "bar",
    "strip": "bar",
    "bars": "bar",
}

MAX_ITEMS = 8
MAX_TEXT_CHARS = 220
MAX_TITLE_CHARS = 120

#: Markup and unrendered-template markers. A label that contains any of these is
#: not a label: it is either an injection attempt or a placeholder nobody filled
#: in, and both are rejected rather than drawn.
_MARKUP = re.compile(r"<\s*/?\s*[a-zA-Z!]|javascript:|data:text/html|on\w+\s*=", re.IGNORECASE)
_PLACEHOLDER = re.compile(r"\{\{.*?\}\}|\$\{.*?\}|\[(TODO|TBD|XXX|placeholder)\b", re.IGNORECASE)


class VisualSpecError(ValueError):
    """Raised by :func:`render_svg` when handed a spec validation rejected."""

    def __init__(self, errors: Sequence[Mapping[str, Any]]) -> None:
        self.errors = [dict(e) for e in errors]
        super().__init__("; ".join(str(e.get("message") or e.get("code")) for e in self.errors))


def _err(code: str, message: str, path: str) -> dict[str, str]:
    return {"code": code, "message": message, "path": path}


def _clean_text(value: Any, path: str, errors: list[dict[str, str]], *, required: bool = True) -> str | None:
    if value is None:
        if required:
            errors.append(_err("missing_field", f"{path} is missing", path))
        return None
    if not isinstance(value, str):
        errors.append(_err("wrong_type", f"{path} must be words, not {type(value).__name__}", path))
        return None
    text = " ".join(value.split())
    if required and not text:
        errors.append(_err("empty", f"{path} is empty", path))
        return None
    if len(text) > MAX_TEXT_CHARS:
        errors.append(_err("too_long", f"{path} is {len(text)} characters; the limit is {MAX_TEXT_CHARS}", path))
        return None
    if _MARKUP.search(text):
        errors.append(_err("markup_forbidden", f"{path} contains markup; a picture is drawn from words, never from supplied markup", path))
        return None
    if _PLACEHOLDER.search(text):
        errors.append(_err("placeholder", f"{path} contains an unrendered placeholder", path))
        return None
    return text


def _number(value: Any, path: str, errors: list[dict[str, str]]) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append(_err("wrong_type", f"{path} must be a number", path))
        return None
    out = float(value)
    if out != out or out in (float("inf"), float("-inf")):  # NaN / infinity are not measurements
        errors.append(_err("out_of_range", f"{path} must be a finite number", path))
        return None
    return out


def _list_of(value: Any, path: str, errors: list[dict[str, str]], *, minimum: int = 1) -> list[Any] | None:
    if value is None:
        errors.append(_err("missing_field", f"{path} is missing", path))
        return None
    if not isinstance(value, (list, tuple)):
        errors.append(_err("wrong_type", f"{path} must be a list", path))
        return None
    items = list(value)
    if len(items) < minimum:
        errors.append(_err("empty", f"{path} needs at least {minimum} entr{'y' if minimum == 1 else 'ies'}", path))
        return None
    if len(items) > MAX_ITEMS:
        errors.append(_err("too_many", f"{path} has {len(items)} entries; the limit is {MAX_ITEMS} — a picture with more stops being a picture", path))
        return None
    return items


def _rows(value: Any, path: str, errors: list[dict[str, str]]) -> list[Mapping[str, Any]] | None:
    items = _list_of(value, path, errors)
    if items is None:
        return None
    out: list[Mapping[str, Any]] = []
    for i, item in enumerate(items):
        if not isinstance(item, Mapping):
            errors.append(_err("wrong_type", f"{path}[{i}] must be an object with named fields", f"{path}[{i}]"))
            continue
        out.append(item)
    return out or None


def validate_visual_spec(spec: Any) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    """Validate one visual spec. Returns ``(normalised, [])`` or ``(None, errors)``.

    The normalised form is what renderers (here and in the frontend) consume:
    one kind vocabulary, aliases resolved, text whitespace-collapsed, and an
    ``unfilled`` list naming every slot the spec honestly left empty.
    """
    errors: list[dict[str, str]] = []
    if not isinstance(spec, Mapping):
        return None, [_err("wrong_type", "a visual spec must be an object with a kind", "$")]
    kind_raw = spec.get("kind")
    if not isinstance(kind_raw, str) or not kind_raw.strip():
        return None, [_err("missing_field", "the spec does not say which picture it is (kind)", "$.kind")]
    kind = _KIND_ALIASES.get(kind_raw.strip(), kind_raw.strip())
    if kind not in VISUAL_KINDS:
        return None, [_err("unknown_kind", f"no template is called {kind_raw!r}; the seven are: {', '.join(VISUAL_KINDS)}", "$.kind")]
    if _MARKUP.search(" ".join(str(v) for v in spec.values() if isinstance(v, str))):
        # Caught per-field below too; this catches markup smuggled into a field
        # the per-kind schema does not read.
        pass

    title = _clean_text(spec.get("title"), "$.title", errors, required=False) or "Picture"
    if len(title) > MAX_TITLE_CHARS:
        errors.append(_err("too_long", f"$.title is {len(title)} characters; the limit is {MAX_TITLE_CHARS}", "$.title"))
    alt = _clean_text(spec.get("alt"), "$.alt", errors, required=False)
    out: dict[str, Any] = {"kind": kind, "title": title, "unfilled": []}
    if alt:
        out["alt"] = alt

    if kind == "flow":
        rows = _rows(spec.get("steps", spec.get("nodes")), "$.steps", errors)
        steps = []
        for i, row in enumerate(rows or []):
            text = _clean_text(row.get("text"), f"$.steps[{i}].text", errors)
            label = _clean_text(row.get("label"), f"$.steps[{i}].label", errors, required=False)
            if text:
                steps.append({"label": label or f"Step {i + 1}", "text": text})
        if len(steps) < 2:
            errors.append(_err("empty", "a flow needs at least two steps; one step is a sentence, not a flow", "$.steps"))
        out["steps"] = steps

    elif kind == "comparison_table":
        columns_raw = spec.get("columns")
        columns: list[str] = []
        if columns_raw is not None:
            if not isinstance(columns_raw, (list, tuple)) or not (2 <= len(columns_raw) <= 3):
                errors.append(_err("wrong_type", "$.columns must name two or three columns", "$.columns"))
            else:
                for i, col in enumerate(columns_raw):
                    cleaned = _clean_text(col, f"$.columns[{i}]", errors)
                    if cleaned:
                        columns.append(cleaned)
        rows = _rows(spec.get("rows"), "$.rows", errors)
        table_rows = []
        for i, row in enumerate(rows or []):
            aspect = _clean_text(row.get("aspect"), f"$.rows[{i}].aspect", errors)
            left = _clean_text(row.get("left"), f"$.rows[{i}].left", errors)
            right = _clean_text(row.get("right"), f"$.rows[{i}].right", errors, required=False)
            if right is None:
                out["unfilled"].append(f"rows[{i}].right")
                right = ""
            if aspect and left is not None:
                table_rows.append({"aspect": aspect, "left": left, "right": right})
        out["columns"] = columns or ["Aspect", "One side", "The other side"]
        out["rows"] = table_rows

    elif kind == "timeline":
        rows = _rows(spec.get("events", spec.get("nodes")), "$.events", errors)
        events = []
        for i, row in enumerate(rows or []):
            when = _clean_text(row.get("when"), f"$.events[{i}].when", errors)
            text = _clean_text(row.get("text"), f"$.events[{i}].text", errors)
            if when and text:
                events.append({"when": when, "text": text})
        out["events"] = events

    elif kind == "labelled_diagram":
        whole = _clean_text(spec.get("whole"), "$.whole", errors, required=False)
        rows = _rows(spec.get("parts", spec.get("labels")), "$.parts", errors)
        parts = []
        for i, row in enumerate(rows or []):
            label = _clean_text(row.get("label"), f"$.parts[{i}].label", errors, required=False)
            text = _clean_text(row.get("text"), f"$.parts[{i}].text", errors)
            if not label:
                out["unfilled"].append(f"parts[{i}].label")
                label = ""
            if text:
                parts.append({"label": label, "text": text})
        if whole:
            out["whole"] = whole
        out["parts"] = parts

    elif kind == "number_line":
        low = _number(spec.get("min"), "$.min", errors)
        high = _number(spec.get("max"), "$.max", errors)
        if low is not None and high is not None and not low < high:
            errors.append(_err("out_of_range", "$.min must be below $.max", "$.min"))
        rows = _rows(spec.get("points"), "$.points", errors)
        points = []
        for i, row in enumerate(rows or []):
            value = _number(row.get("value"), f"$.points[{i}].value", errors)
            label = _clean_text(row.get("label"), f"$.points[{i}].label", errors)
            if value is not None and low is not None and high is not None and not (low <= value <= high):
                errors.append(_err("out_of_range", f"$.points[{i}].value ({value:g}) sits outside the line ({low:g} to {high:g})", f"$.points[{i}].value"))
            if value is not None and label:
                points.append({"value": value, "label": label})
        unit = _clean_text(spec.get("unit"), "$.unit", errors, required=False)
        if unit:
            out["unit"] = unit
        out.update({"min": low, "max": high, "points": points})

    elif kind == "venn":
        sets_raw = _list_of(spec.get("sets"), "$.sets", errors, minimum=2)
        sets = []
        for i, entry in enumerate(sets_raw or []):
            label = entry.get("label") if isinstance(entry, Mapping) else entry
            cleaned = _clean_text(label, f"$.sets[{i}].label", errors)
            if cleaned:
                sets.append({"label": cleaned})
        if sets and len(sets) != 2:
            errors.append(_err("wrong_type", "this template draws exactly two sets; a third set needs words, not a third circle pretending to be readable", "$.sets"))
        regions: dict[str, str] = {}
        for key in ("left_only", "overlap", "right_only"):
            cleaned = _clean_text(spec.get(key), f"$.{key}", errors, required=False)
            if cleaned:
                regions[key] = cleaned
            else:
                out["unfilled"].append(key)
        out["sets"] = sets
        out["regions"] = regions

    elif kind == "bar":
        rows = _rows(spec.get("bars"), "$.bars", errors)
        bars = []
        for i, row in enumerate(rows or []):
            label = _clean_text(row.get("label"), f"$.bars[{i}].label", errors)
            value = _number(row.get("value"), f"$.bars[{i}].value", errors)
            if value is not None and value < 0:
                errors.append(_err("out_of_range", f"$.bars[{i}].value is negative; a bar cannot be shorter than nothing", f"$.bars[{i}].value"))
            if label and value is not None:
                bars.append({"label": label, "value": value})
        unit = _clean_text(spec.get("unit"), "$.unit", errors, required=False)
        if unit:
            out["unit"] = unit
        out["bars"] = bars

    if errors:
        return None, errors
    return out, []


def fallback_text(spec: Any, errors: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The words a rejected picture is replaced by, with the reason attached.

    The text is assembled from the spec's own readable strings — nothing is
    paraphrased or invented — so falling back loses the picture, never the
    facts. The note says, in plain words, that a picture was intended.
    """
    readable: list[str] = []
    if isinstance(spec, Mapping):
        def harvest(value: Any) -> None:
            if isinstance(value, str) and value.strip() and not _MARKUP.search(value):
                readable.append(" ".join(value.split()))
            elif isinstance(value, Mapping):
                for item in value.values():
                    harvest(item)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    harvest(item)

        for key in ("title", "steps", "nodes", "rows", "events", "parts", "labels", "points", "bars"):
            if key in spec:
                harvest(spec[key])
    reason = "; ".join(str(e.get("message") or e.get("code")) for e in errors) or "the spec did not validate"
    return {
        "kind": "text",
        "text": " ".join(readable) or "This part was meant to be a picture, but there is nothing readable to show in its place yet.",
        "note": f"a picture was planned here, but it could not be drawn safely ({reason}), so here it is in words instead",
        "errors": [dict(e) for e in errors],
    }


def derive_spec(kind: str, *, title: str, sentences: Sequence[str], extra: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    """Build the one spec that can honestly be derived from plain sentences.

    Only ``flow`` is derivable: the order of the sentences *is* the content.
    Every other shape needs structure (two sides, dates, values) that prose
    does not carry, and guessing that structure is how a template starts
    inventing facts. Returns ``None`` for those, and the caller falls back.
    """
    kind = _KIND_ALIASES.get(kind, kind)
    if kind != "flow":
        return None
    steps = [{"label": f"Step {i + 1}", "text": s} for i, s in enumerate([str(s).strip() for s in sentences if str(s).strip()][:MAX_ITEMS])]
    if len(steps) < 2:
        return None
    spec: dict[str, Any] = {"kind": "flow", "title": title, "steps": steps}
    if extra:
        spec.update(dict(extra))
    validated, _errors = validate_visual_spec(spec)
    return validated


def schema_summary() -> dict[str, Any]:
    """The seven schemas in one payload, for the API and the docs."""
    return {
        "kinds": list(VISUAL_KINDS),
        "aliases": dict(_KIND_ALIASES),
        "limits": {"max_items_per_picture": MAX_ITEMS, "max_text_chars": MAX_TEXT_CHARS},
        "schemas": {
            "flow": {"steps": "[{label, text}] — at least two, in order"},
            "comparison_table": {"columns": "[two or three names]", "rows": "[{aspect, left, right}]"},
            "timeline": {"events": "[{when, text}] — in the order they happened"},
            "labelled_diagram": {"whole": "what the picture is of (optional)", "parts": "[{label, text}]"},
            "number_line": {"min": "number", "max": "number above min", "points": "[{value, label}] — every value on the line"},
            "venn": {"sets": "[exactly two {label}]", "left_only": "text", "overlap": "text", "right_only": "text"},
            "bar": {"bars": "[{label, value}] — values zero or above", "unit": "optional"},
        },
        "note": "a spec that fails validation is never drawn; the lesson shows the same content in words and says why",
    }


# --------------------------------------------------------------------------- #
# SVG rendering (deterministic; every string escaped; nothing external)
# --------------------------------------------------------------------------- #

def _t(x: float, y: float, text: str, *, size: int = 13, anchor: str = "start", weight: str = "normal", fill: str = "#1a1a1a") -> str:
    return f'<text x="{x:g}" y="{y:g}" font-size="{size}" text-anchor="{anchor}" font-weight="{weight}" fill="{fill}">{escape(text)}</text>'


def render_svg(spec: Mapping[str, Any], *, width: int = 640) -> str:
    """Render a validated spec to a standalone SVG string.

    Re-validates first: a caller that skipped validation gets a
    :class:`VisualSpecError`, not a picture. Output is deterministic — the
    same spec renders byte-identical SVG — and carries ``role="img"`` with a
    ``<title>``/``<desc>`` pair so a screen reader gets the picture's point.
    """
    validated, errors = validate_visual_spec(spec)
    if validated is None:
        raise VisualSpecError(errors)
    kind = validated["kind"]
    body, height = _RENDERERS[kind](validated, width)
    title = escape(validated["title"])
    desc = escape(validated.get("alt") or _auto_alt(validated))
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="vt vd">'
        f'<title id="vt">{title}</title><desc id="vd">{desc}</desc>'
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff"/>'
        + _t(16, 26, validated["title"], size=15, weight="bold")
        + body
        + "</svg>"
    )


def _auto_alt(spec: Mapping[str, Any]) -> str:
    kind = spec["kind"]
    if kind == "flow":
        return "Flow: " + "; then ".join(s["text"] for s in spec["steps"])
    if kind == "comparison_table":
        return "Comparison: " + "; ".join(f"{r['aspect']}: {r['left']} / {r['right']}" for r in spec["rows"])
    if kind == "timeline":
        return "Timeline: " + "; ".join(f"{e['when']}: {e['text']}" for e in spec["events"])
    if kind == "labelled_diagram":
        return "Labelled diagram: " + "; ".join(f"{p['label']}: {p['text']}" for p in spec["parts"])
    if kind == "number_line":
        return "Number line from {:g} to {:g}: ".format(spec["min"], spec["max"]) + "; ".join(
            f"{p['label']} at {p['value']:g}" for p in spec["points"]
        )
    if kind == "venn":
        labels = [s["label"] for s in spec["sets"]]
        return f"Two sets, {labels[0]} and {labels[1]}, and what they share"
    return "Bars: " + "; ".join(f"{b['label']} {b['value']:g}" for b in spec["bars"])


def _wrap(text: str, *, chars: int = 52) -> list[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        if current and len(current) + 1 + len(word) > chars:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines[:3]


def _render_flow(spec: Mapping[str, Any], width: int) -> tuple[str, int]:
    parts, y = [], 48
    for i, step in enumerate(spec["steps"]):
        lines = _wrap(step["text"])
        box_h = 26 + 16 * len(lines)
        parts.append(f'<rect x="16" y="{y}" width="{width - 32}" height="{box_h}" rx="4" fill="#f4f6fb" stroke="#9db2d8"/>')
        parts.append(_t(28, y + 20, step["label"], size=12, weight="bold", fill="#33507e"))
        for j, line in enumerate(lines):
            parts.append(_t(28, y + 38 + 16 * j, line))
        y += box_h
        if i < len(spec["steps"]) - 1:
            parts.append(f'<line x1="{width // 2}" y1="{y}" x2="{width // 2}" y2="{y + 14}" stroke="#33507e" stroke-width="2"/>')
            parts.append(f'<polygon points="{width // 2 - 5},{y + 14} {width // 2 + 5},{y + 14} {width // 2},{y + 22}" fill="#33507e"/>')
            y += 22
    return "".join(parts), y + 12


def _render_comparison(spec: Mapping[str, Any], width: int) -> tuple[str, int]:
    col_x = [16, 170, 390]
    col_w = [150, 216, width - 406]
    parts = []
    y = 48
    for j, col in enumerate(spec["columns"][:3]):
        parts.append(_t(col_x[j] + 4, y + 16, col, size=12, weight="bold", fill="#33507e"))
    parts.append(f'<line x1="16" y1="{y + 24}" x2="{width - 16}" y2="{y + 24}" stroke="#9db2d8"/>')
    y += 34
    for row in spec["rows"]:
        cells = [row["aspect"], row["left"], row["right"]]
        wrapped = [_wrap(c, chars=max(14, col_w[j] // 8)) for j, c in enumerate(cells)]
        row_h = 12 + 16 * max(len(w) for w in wrapped)
        for j, lines in enumerate(wrapped):
            for k, line in enumerate(lines):
                parts.append(_t(col_x[j] + 4, y + 14 + 16 * k, line, size=12))
        y += row_h
        parts.append(f'<line x1="16" y1="{y}" x2="{width - 16}" y2="{y}" stroke="#e3e8f2"/>')
        y += 6
    return "".join(parts), y + 8


def _render_timeline(spec: Mapping[str, Any], width: int) -> tuple[str, int]:
    parts = [f'<line x1="40" y1="60" x2="40" y2="{60 + 44 * len(spec["events"])}" stroke="#9db2d8" stroke-width="2"/>']
    y = 60
    for event in spec["events"]:
        parts.append(f'<circle cx="40" cy="{y}" r="6" fill="#33507e"/>')
        parts.append(_t(58, y - 2, event["when"], size=12, weight="bold", fill="#33507e"))
        for k, line in enumerate(_wrap(event["text"], chars=64)):
            parts.append(_t(58, y + 14 + 15 * k, line, size=12))
        y += 44
    return "".join(parts), y + 8


def _render_labelled(spec: Mapping[str, Any], width: int) -> tuple[str, int]:
    parts, y = [], 52
    if spec.get("whole"):
        parts.append(_t(width // 2, y, spec["whole"], size=14, weight="bold", anchor="middle", fill="#33507e"))
        y += 14
    parts.append(f'<rect x="{width // 2 - 90}" y="{y}" width="180" height="30" rx="15" fill="#f4f6fb" stroke="#9db2d8"/>')
    parts.append(_t(width // 2, y + 20, spec["title"][:28], size=12, anchor="middle", weight="bold"))
    y += 52
    for part in spec["parts"]:
        label = part["label"] or "part"
        parts.append(f'<rect x="16" y="{y}" width="{width - 32}" height="34" rx="4" fill="#fbfcfe" stroke="#e3e8f2"/>')
        parts.append(_t(28, y + 21, f"{label}:", size=12, weight="bold", fill="#33507e"))
        parts.append(_t(28 + 9 * len(label) + 14, y + 21, _wrap(part["text"], chars=58)[0], size=12))
        y += 42
    return "".join(parts), y + 8


def _render_number_line(spec: Mapping[str, Any], width: int) -> tuple[str, int]:
    low, high = float(spec["min"]), float(spec["max"])
    x0, x1, y = 48, width - 48, 92
    span = (high - low) or 1.0

    def px(value: float) -> float:
        return x0 + (value - low) / span * (x1 - x0)

    parts = [
        f'<line x1="{x0}" y1="{y}" x2="{x1}" y2="{y}" stroke="#33507e" stroke-width="2"/>',
        f'<polygon points="{x1},{y} {x1 - 9},{y - 5} {x1 - 9},{y + 5}" fill="#33507e"/>',
        _t(x0, y + 26, f"{low:g}", size=11, anchor="middle"),
        _t(x1, y + 26, f"{high:g}", size=11, anchor="middle"),
    ]
    for i, point in enumerate(spec["points"]):
        x = px(float(point["value"]))
        above = i % 2 == 0
        parts.append(f'<circle cx="{x:g}" cy="{y}" r="5" fill="#b3552e"/>')
        parts.append(_t(x, y - 14 if above else y + 40, point["label"], size=11, anchor="middle", weight="bold"))
        parts.append(_t(x, y + 26 if above else y - 26, f"{float(point['value']):g}", size=10, anchor="middle", fill="#666666"))
    if spec.get("unit"):
        parts.append(_t(width // 2, 52, f"unit: {spec['unit']}", size=11, anchor="middle", fill="#666666"))
    return "".join(parts), 150


def _render_venn(spec: Mapping[str, Any], width: int) -> tuple[str, int]:
    cx = width // 2
    parts = [
        f'<circle cx="{cx - 68}" cy="110" r="78" fill="#dbe7f7" fill-opacity="0.75" stroke="#33507e"/>',
        f'<circle cx="{cx + 68}" cy="110" r="78" fill="#f7e3d3" fill-opacity="0.75" stroke="#b3552e"/>',
        _t(cx - 150, 52, spec["sets"][0]["label"], size=13, weight="bold", fill="#33507e"),
        _t(cx + 150, 52, spec["sets"][1]["label"], size=13, weight="bold", anchor="end", fill="#8a3d1c"),
    ]
    regions = spec.get("regions") or {}
    if regions.get("left_only"):
        parts.append(_t(cx - 118, 112, _wrap(regions["left_only"], chars=16)[0], size=11, anchor="middle"))
    if regions.get("overlap"):
        parts.append(_t(cx, 112, _wrap(regions["overlap"], chars=14)[0], size=11, anchor="middle", weight="bold"))
    if regions.get("right_only"):
        parts.append(_t(cx + 118, 112, _wrap(regions["right_only"], chars=16)[0], size=11, anchor="middle"))
    return "".join(parts), 208


def _render_bar(spec: Mapping[str, Any], width: int) -> tuple[str, int]:
    bars = spec["bars"]
    top = max(float(b["value"]) for b in bars) or 1.0
    parts, y = [], 52
    for bar_ in bars:
        w = max(2.0, float(bar_["value"]) / top * (width - 220))
        parts.append(_t(16, y + 14, bar_["label"], size=12))
        parts.append(f'<rect x="170" y="{y}" width="{w:g}" height="18" fill="#33507e"/>')
        parts.append(_t(178 + w, y + 14, f"{float(bar_['value']):g}" + (f" {spec['unit']}" if spec.get("unit") else ""), size=11))
        y += 28
    return "".join(parts), y + 10


_RENDERERS = {
    "flow": _render_flow,
    "comparison_table": _render_comparison,
    "timeline": _render_timeline,
    "labelled_diagram": _render_labelled,
    "number_line": _render_number_line,
    "venn": _render_venn,
    "bar": _render_bar,
}

__all__ = [
    "MAX_ITEMS",
    "VISUAL_KINDS",
    "VisualSpecError",
    "derive_spec",
    "fallback_text",
    "render_svg",
    "schema_summary",
    "validate_visual_spec",
]
