"""Safe, deterministic scene-template library used by lesson plans.

A lesson writer may name a visual and provide JSON parameters.  It may not provide SVG, HTML,
JavaScript, a URL, a callback or an executable expression.  This module is the code-owned side of
that boundary: schemas are closed, values are bounded, and every accepted spec is dry-run into
plain JSON frames before it reaches a browser.
"""
from __future__ import annotations

import math
import re
from typing import Any

TEMPLATE_SCHEMAS: dict[str, dict[str, dict[str, Any]]] = {
    "angle-sum-of-triangle": {"angles": {"type": "numbers", "min_items": 3, "max_items": 3, "min": 1, "max": 178}},
    "pythagoras": {"a": {"type": "number", "min": 1, "max": 100}, "b": {"type": "number", "min": 1, "max": 100}},
    "similar-triangles": {"scale": {"type": "number", "min": 0.2, "max": 8}},
    "number-line-operations": {"start": {"type": "number", "min": -100, "max": 100}, "steps": {"type": "numbers", "min_items": 1, "max_items": 8, "min": -50, "max": 50}},
    "fraction-bars": {"fractions": {"type": "fractions", "min_items": 1, "max_items": 6}},
    "function-plot": {"kind": {"type": "choice", "values": ["linear", "quadratic"]}, "a": {"type": "number", "min": -5, "max": 5}, "b": {"type": "number", "min": -20, "max": 20}},
    "vector-force": {"vectors": {"type": "vectors", "min_items": 1, "max_items": 4}},
    "car-motion": {"speed": {"type": "number", "min": 0, "max": 120}, "seconds": {"type": "number", "min": 1, "max": 60}},
    "probability-tree": {"branches": {"type": "probabilities", "min_items": 2, "max_items": 6}},
    "venn-sets": {"left": {"type": "strings", "min_items": 0, "max_items": 8}, "right": {"type": "strings", "min_items": 0, "max_items": 8}},
    "graph-traversal": {"nodes": {"type": "strings", "min_items": 2, "max_items": 12}, "edges": {"type": "edges", "max_items": 24}, "algorithm": {"type": "choice", "values": ["BFS", "DFS"]}},
    "binary-tree": {"values": {"type": "numbers", "min_items": 1, "max_items": 15, "min": -100, "max": 100}},
    "recursion-stack": {"label": {"type": "text"}, "depth": {"type": "int", "min": 1, "max": 8}},
    "sorting": {"values": {"type": "numbers", "min_items": 3, "max_items": 12, "min": -100, "max": 100}},
    "binary-search": {"values": {"type": "numbers", "min_items": 3, "max_items": 16, "min": -100, "max": 100}, "target": {"type": "number", "min": -100, "max": 100}},
    "logic-gates": {"gate": {"type": "choice", "values": ["AND", "OR", "XOR", "NOT", "NAND", "NOR"]}},
    "cpu-cycle": {"instructions": {"type": "strings", "min_items": 1, "max_items": 8}},
    "cycle-diagram": {"nodes": {"type": "strings", "min_items": 2, "max_items": 8}},
    "timeline": {"events": {"type": "timeline", "min_items": 2, "max_items": 10}},
    "flowchart": {"steps": {"type": "strings", "min_items": 2, "max_items": 10}},
    "cause-effect-chain": {"causes": {"type": "strings", "min_items": 2, "max_items": 8}},
    "grammar-sentence-diagram": {"words": {"type": "strings", "min_items": 2, "max_items": 12}},
}

TEMPLATE_IDS = tuple(TEMPLATE_SCHEMAS)
_BAD = re.compile(r"</?[a-z]|javascript:|data:|https?://|\bon\w+\s*=|=>|\b(eval|function|callback)\b|[{}]", re.I)


def _default_params(template: str) -> dict[str, Any]:
    defaults: dict[str, dict[str, Any]] = {
        "angle-sum-of-triangle": {"angles": [60, 60, 60]}, "pythagoras": {"a": 3, "b": 4},
        "similar-triangles": {"scale": 2}, "number-line-operations": {"start": 0, "steps": [3, -1]},
        "fraction-bars": {"fractions": [[1, 2], [3, 4]]}, "function-plot": {"kind": "linear", "a": 1, "b": 0},
        "vector-force": {"vectors": [[2, 1], [-1, 2]]}, "car-motion": {"speed": 20, "seconds": 5},
        "probability-tree": {"branches": [0.5, 0.5]}, "venn-sets": {"left": ["a", "b"], "right": ["b", "c"]},
        "graph-traversal": {"nodes": ["A", "B", "C"], "edges": [["A", "B"], ["B", "C"]], "algorithm": "BFS"},
        "binary-tree": {"values": [4, 2, 6]}, "recursion-stack": {"label": "count", "depth": 3},
        "sorting": {"values": [3, 1, 2]}, "binary-search": {"values": [1, 3, 5, 7], "target": 5},
        "logic-gates": {"gate": "AND"}, "cpu-cycle": {"instructions": ["LOAD", "ADD", "STORE"]},
        "cycle-diagram": {"nodes": ["sunlight", "plant", "oxygen"]}, "timeline": {"events": [["1900", "start"], ["2000", "change"]]},
        "flowchart": {"steps": ["start", "choose", "finish"]}, "cause-effect-chain": {"causes": ["rain", "river", "harvest"]},
        "grammar-sentence-diagram": {"words": ["Learners", "solve", "problems"]},
    }
    return dict(defaults.get(template, {}))


def _text(value: Any, where: str, errors: list[str], max_len: int = 120) -> str:
    if not isinstance(value, str):
        errors.append(f"{where} must be text")
        return ""
    value = " ".join(value.split())
    if len(value) > max_len:
        errors.append(f"{where} is over the limit of {max_len} characters")
    if _BAD.search(value):
        errors.append(f"{where} contains markup, a URL or executable text")
    return value[:max_len]


def _number(value: Any, decl: dict[str, Any], where: str, errors: list[str]) -> float:
    if isinstance(value, bool):
        errors.append(f"{where} must be a number")
        return 0
    try:
        number = float(value)
    except (TypeError, ValueError):
        errors.append(f"{where} must be a number")
        return 0
    if not math.isfinite(number) or number < decl.get("min", -math.inf) or number > decl.get("max", math.inf):
        errors.append(f"{where} is outside its safe range")
    return int(number) if number.is_integer() else number


def _items(value: Any, decl: dict[str, Any], where: str, errors: list[str]) -> list[Any]:
    if not isinstance(value, (list, tuple)):
        errors.append(f"{where} must be a list")
        return []
    lo, hi = decl.get("min_items", 0), decl.get("max_items", 32)
    if not lo <= len(value) <= hi:
        errors.append(f"{where} must contain between {lo} and {hi} items")
    return list(value)[:hi]


def validate_template(raw: Any) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    if not isinstance(raw, dict):
        return None, ["scene spec must be an object"]
    unknown_top = set(raw) - {"template", "params", "captions", "source"}
    errors.extend(f"{key} is not allowed in a scene spec" for key in sorted(unknown_top))
    template = str(raw.get("template") or "").strip()
    if template not in TEMPLATE_SCHEMAS:
        return None, [f"template must be one of {', '.join(TEMPLATE_IDS)}"]
    params = raw.get("params") or {}
    if not isinstance(params, dict):
        return None, ["params must be an object"]
    allowed = TEMPLATE_SCHEMAS[template]
    unknown = set(params) - set(allowed)
    errors.extend(f"params.{key} is not declared by this template" for key in sorted(unknown))
    clean: dict[str, Any] = {}
    for name, decl in allowed.items():
        value = params.get(name, _default_params(template).get(name))
        typ = decl["type"]
        where = f"params.{name}"
        if typ in {"number", "int"}:
            out = _number(value, decl, where, errors)
            if typ == "int": out = int(out)
            clean[name] = out
        elif typ == "choice":
            if value not in decl["values"]: errors.append(f"{where} is not an allowed choice")
            clean[name] = str(value)
        elif typ == "text":
            clean[name] = _text(value, where, errors)
        else:
            vals = _items(value, decl, where, errors)
            if typ == "numbers": clean[name] = [_number(v, decl, f"{where}[]", errors) for v in vals]
            elif typ == "probabilities":
                clean[name] = [_number(v, {"min": 0, "max": 1}, f"{where}[]", errors) for v in vals]
            elif typ == "fractions":
                out: list[list[int]] = []
                for pair in vals:
                    if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                        errors.append(f"{where} must contain numerator/denominator pairs")
                        continue
                    try:
                        numerator, denominator = int(pair[0]), int(pair[1])
                    except (TypeError, ValueError):
                        errors.append(f"{where} must contain whole-number numerator/denominator pairs")
                        continue
                    if denominator == 0:
                        errors.append(f"{where} cannot contain a zero denominator")
                        continue
                    if abs(numerator) > 100 or abs(denominator) > 100:
                        errors.append(f"{where} values must stay between -100 and 100")
                        continue
                    out.append([numerator, denominator])
                clean[name] = out
            elif typ == "vectors":
                out = []
                for pair in vals:
                    if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                        errors.append(f"{where} must contain two-number vectors")
                    else: out.append([_number(pair[0], {"min": -100, "max": 100}, f"{where}[]", errors), _number(pair[1], {"min": -100, "max": 100}, f"{where}[]", errors)])
                clean[name] = out
            elif typ == "edges":
                out = []
                for pair in vals:
                    if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                        errors.append(f"{where} must contain pairs of node names")
                    else:
                        out.append([_text(pair[0], f"{where}[]", errors, 80), _text(pair[1], f"{where}[]", errors, 80)])
                clean[name] = out
            elif typ == "strings":
                clean[name] = [_text(item, f"{where}[]", errors) for item in vals]
            elif typ == "timeline":
                out = []
                for item in vals:
                    if not isinstance(item, (list, tuple)) or len(item) != 2:
                        errors.append(f"{where} must contain date and event pairs")
                    else:
                        out.append([_text(item[0], f"{where}[]", errors), _text(item[1], f"{where}[]", errors)])
                clean[name] = out
            else:
                clean[name] = vals
    captions = raw.get("captions") or []
    if not isinstance(captions, list) or len(captions) > 24:
        errors.append("captions must be a list of at most 24 plain sentences")
        captions = []
    clean_captions: list[str] = []
    for index, caption in enumerate(captions):
        if not isinstance(caption, str):
            errors.append(f"captions[{index}] must be plain text")
            continue
        clean_captions.append(_text(caption, f"captions[{index}]", errors, 240))
    source = str(raw.get("source") or "simple")
    if source not in {"model", "simple"}: errors.append("source must be model or simple")
    if errors: return None, errors
    return {"template": template, "params": clean, "captions": clean_captions, "source": source}, []


def _frame(template: str, params: dict[str, Any], caption: str, step: int = 0) -> dict[str, Any]:
    """A compact renderer-neutral frame made only of primitives."""
    objects: list[dict[str, Any]] = [{"type": "text", "x": 12, "y": 18, "text": caption}]
    if template in {"angle-sum-of-triangle", "pythagoras", "similar-triangles"}:
        objects += [{"type": "triangle", "x": 20, "y": 28, "size": 70, "labels": ["A", "B", "C"]}]
    elif template in {"number-line-operations", "function-plot", "car-motion", "timeline"}:
        objects += [{"type": "line", "x1": 10, "y1": 60, "x2": 230, "y2": 60, "arrow": True}]
    elif template in {"venn-sets"}:
        objects += [{"type": "circle", "cx": 80, "cy": 55, "r": 30}, {"type": "circle", "cx": 125, "cy": 55, "r": 30}]
    elif template in {"graph-traversal", "binary-tree", "probability-tree", "cause-effect-chain", "cycle-diagram"}:
        objects += [{"type": "node", "x": 30 + i * 45, "y": 55, "label": str(value)} for i, value in enumerate(list(params.values())[0][:5])]
    elif template in {"fraction-bars", "sorting", "binary-search", "flowchart", "grammar-sentence-diagram"}:
        objects += [{"type": "rectangle", "x": 12 + i * 34, "y": 40, "width": 28, "height": 24, "label": str(value)} for i, value in enumerate(list(params.values())[0][:6])]
    else:
        objects += [{"type": "circle", "cx": 80, "cy": 55, "r": 24, "label": template.replace("-", " ")}]
    return {"step": step, "caption": caption, "objects": objects, "actions": [{"type": "appear", "target": "all", "easing": "ease-out", "duration_ms": 450}]}


def dry_run(spec: dict[str, Any]) -> list[dict[str, Any]]:
    template = spec["template"]
    params = spec["params"]
    captions = spec.get("captions") or [f"{template.replace('-', ' ').capitalize()}.", "Pause and predict the next step."]
    return [_frame(template, params, caption, i) for i, caption in enumerate(captions[:8])]


def suggest_template(concept: str, family: str = "") -> str:
    text = f"{concept} {family}".lower()
    rules = (("triangle", "angle-sum-of-triangle"), ("pythag", "pythagoras"), ("fraction", "fraction-bars"), ("probab", "probability-tree"), ("venn", "venn-sets"), ("graph", "graph-traversal"), ("tree", "binary-tree"), ("recurs", "recursion-stack"), ("sort", "sorting"), ("search", "binary-search"), ("gate", "logic-gates"), ("cpu", "cpu-cycle"), ("cycle", "cycle-diagram"), ("timeline", "timeline"), ("grammar", "grammar-sentence-diagram"), ("force", "vector-force"), ("car", "car-motion"), ("function", "function-plot"), ("number line", "number-line-operations"), ("cause", "cause-effect-chain"), ("flow", "flowchart"))
    return next((template for needle, template in rules if needle in text), "flowchart")


def validate_and_dry_run(raw: Any) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[str]]:
    spec, errors = validate_template(raw)
    if errors or spec is None: return None, [], errors
    try: return spec, dry_run(spec), []
    except Exception as exc: return None, [], [f"dry run failed: {type(exc).__name__}"]
