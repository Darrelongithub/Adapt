"""Scene specifications: the only thing a lesson is allowed to be made of.

A scene spec is **data**. It names a simulation kind, gives that simulation bounded
parameters, and adds a sentence of framing and some citations. It never contains markup, a
callback, an expression, or a URL to script -- ``validate`` refuses those shapes rather than
sanitising them, because the whole safety claim of the engine is that AI 2 may prepare
content while only tested application code decides what happens on screen.

Validation has three jobs, in this order:

1. **Shape** -- known kind, declared parameter names only, types and bounds honoured.
   An unknown parameter is an error, not something to drop quietly: a spec that asked for
   ``walk=False`` and had it ignored would show the learner something else.
2. **Concept rules** -- what makes *this* animation true. Binary search needs a sorted list
   of distinct values; a CPU program has to be in the machine's instruction set; a gate row
   has to be 0 or 1. These are the rules a model is most likely to get subtly wrong, so they
   live here rather than in a prompt.
3. **A dry run** -- the simulation is actually executed with the proposed parameters. If it
   raises, or produces more frames than the kind allows, the spec is rejected with the reason.
   A spec is accepted only when the animation it promises can be produced.

Rejected specs are still stored (status ``rejected``, with the errors) so a teacher or AI 2
can see what was asked for and why it failed, and so a model cannot retry silently forever.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from . import sims

TITLE_MAX = 120
INTENT_MAX = 400
FRAMING_MAX = 600
MAX_CONCEPTS = 6
MAX_CITATIONS = 6
#: anything whose name suggests "run this" rather than "show this"
CODE_SHAPED_NAMES = frozenset(
    {
        "html",
        "code",
        "script",
        "javascript",
        "src",
        "srcdoc",
        "href",
        "url",
        "uri",
        "style",
        "css",
        "onerror",
        "onload",
        "handler",
        "callback",
        "expression",
        "eval",
        "renderer",
        "component",
        "template",
        "function",
    }
)
#: a string may be plain text only -- no tags, no script syntax, no template hooks
BAD_TEXT = re.compile(r"(</?[a-zA-Z][a-zA-Z0-9]*(\s|/?>)|javascript:|data:text/html|vbscript:|\bon(click|error|load|mouseover|mouseout|focus|blur|change|input|submit|toggle|keydown|keyup)\s*=|\beval\s*\(|\bfunction\s*\(|=>|\{\{|\}\}|`[\s\S]{0,40}\()")



@dataclass
class Result:
    spec: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and self.spec is not None

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "spec": self.spec, "errors": self.errors, "warnings": self.warnings}


def spec_id(kind: str, params: dict[str, Any], title: str) -> str:
    """Content-addressed, so the same lesson cannot be seeded twice under a different id.

    The title is part of the fingerprint on purpose: re-titling a scene is a new authored
    artifact, and letting it overwrite an existing row silently would strand every LessonRun
    that refers to the old id. Text fields are whitespace-normalised first, so a stray newline
    in a form does not fork a lesson into two copies of itself.
    """
    blob = json.dumps({"kind": kind, "params": params, "title": title}, sort_keys=True, default=str)
    return f"scn_{hashlib.sha256(blob.encode()).hexdigest()[:20]}"


def _plain_text(value: Any, *, where: str, limit: int, errors: list[str]) -> str | None:
    if not isinstance(value, str):
        errors.append(f"{where} must be text, got {type(value).__name__}")
        return None
    text = " ".join(value.split())
    if len(text) > limit:
        errors.append(f"{where} is {len(text)} characters, over the limit of {limit}")
    if BAD_TEXT.search(text):
        errors.append(f"{where} contains markup or code shaped text; a scene spec is data, not something to run")
    return text


def _check_names(container: dict[str, Any], where: str, errors: list[str]) -> None:
    for key in container:
        norm = re.sub(r"[^a-z]", "", str(key).lower())
        if norm in CODE_SHAPED_NAMES or any(norm.startswith(bad) for bad in CODE_SHAPED_NAMES):
            errors.append(f"{where}.{key} is not allowed: a spec may describe content, never code to execute")


def _one(name: str, decl: dict[str, Any], raw: Any, errors: list[str]) -> Any:
    kind = decl.get("type")
    if kind == "int":
        if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
            errors.append(f"params.{name} must be a whole number")
            return None
        try:
            value = int(str(raw).strip())
        except ValueError:
            errors.append(f"params.{name} is not a number: {raw!r}")
            return None
        lo, hi = decl.get("min"), decl.get("max")
        if lo is not None and value < lo:
            errors.append(f"params.{name} must be at least {lo}")
        if hi is not None and value > hi:
            errors.append(f"params.{name} must be at most {hi}")
        return value
    if kind == "bool":
        if isinstance(raw, bool):
            return raw
        if str(raw).strip().lower() in ("1", "true", "yes", "on"):
            return True
        if str(raw).strip().lower() in ("0", "false", "no", "off", ""):
            return False
        errors.append(f"params.{name} must be yes or no")
        return None
    if kind == "choice":
        allowed = [str(x) for x in (decl.get("values") or [])]
        value = str(raw).strip()
        if allowed and value not in allowed:
            errors.append(f"params.{name} must be one of {', '.join(allowed)}")
            return None
        return value
    if kind == "ints":
        if not isinstance(raw, (list, tuple)) and not isinstance(raw, str):
            errors.append(f"params.{name} must be a list of numbers")
            return None
        if isinstance(raw, str):
            raw = [x for x in re.split(r"[,\s]+", raw.strip()) if x]
        try:
            values = [int(str(x).strip()) for x in raw]
        except ValueError:
            errors.append(f"params.{name} must hold only whole numbers")
            return None
        lo_n, hi_n = decl.get("min_items", 0), decl.get("max_items", 64)
        if not (lo_n <= len(values) <= hi_n):
            errors.append(f"params.{name} must hold between {lo_n} and {hi_n} numbers")
        lo, hi = decl.get("min"), decl.get("max")
        if lo is not None and any(v < lo for v in values):
            errors.append(f"params.{name}: every number has to be at least {lo}")
        if hi is not None and any(v > hi for v in values):
            errors.append(f"params.{name}: every number has to be at most {hi}")
        if decl.get("sorted") and any(values[i] >= values[i + 1] for i in range(len(values) - 1)):
            errors.append(
                f"params.{name} has to be strictly increasing. Here that is not a style rule: the animation "
                "shows binary search, and binary search is wrong on unsorted data."
            )
        if decl.get("unique") and len(set(values)) != len(values):
            errors.append(f"params.{name} has repeats; this scene assumes each value is distinct")
        return values
    if kind == "lines":
        if isinstance(raw, str):
            raw = [x for x in raw.splitlines() if x.strip()]
        if not isinstance(raw, (list, tuple)):
            errors.append(f"params.{name} must be a list of instruction lines")
            return None
        lo_n, hi_n = decl.get("min_items", 1), decl.get("max_items", 16)
        if not (lo_n <= len(raw) <= hi_n):
            errors.append(f"params.{name} must hold between {lo_n} and {hi_n} lines")
        lines = [str(x).strip().upper() for x in raw]
        for line in lines:
            try:
                from .sims import cpu_cycle

                cpu_cycle.parse(line)
            except ValueError as exc:
                errors.append(f"params.{name}: {exc}")
        return lines
    errors.append(f"params.{name}: the engine has no parameter type {kind!r}")
    return None


def validate(raw: dict[str, Any]) -> Result:
    """Validate one proposed scene spec. Returns the canonical spec, or the reasons."""
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(raw, dict):
        return Result(errors=["a scene spec has to be an object"])
    _check_names(raw, "spec", errors)

    kind = str(raw.get("kind") or "").strip()
    if kind not in sims.KINDS:
        return Result(errors=[f"kind must be one of {', '.join(sims.KINDS)}"], warnings=warnings)
    mod = sims.module_for(kind)

    title = _plain_text(raw.get("title") or mod.TITLE, where="title", limit=TITLE_MAX, errors=errors)
    intent = _plain_text(raw.get("intent") or "", where="intent", limit=INTENT_MAX, errors=errors) or ""
    framing = {
        "intro": _plain_text((raw.get("framing") or {}).get("intro", ""), where="framing.intro", limit=FRAMING_MAX, errors=errors) or "",
        "outro": _plain_text((raw.get("framing") or {}).get("outro", ""), where="framing.outro", limit=FRAMING_MAX, errors=errors) or "",
    }
    concepts = (raw.get("concepts") or [])[: MAX_CONCEPTS + 1]
    if len(concepts) > MAX_CONCEPTS:
        errors.append(f"concepts may hold at most {MAX_CONCEPTS} entries")
    clean_concepts = []
    for c in concepts:
        text = _plain_text(c, where="concepts[]", limit=80, errors=errors)
        if text:
            clean_concepts.append(text)

    citations = (raw.get("citations") or [])[: MAX_CITATIONS + 1]
    if len(citations) > MAX_CITATIONS:
        errors.append(f"citations may hold at most {MAX_CITATIONS} entries")
    clean_citations = []
    for c in citations:
        if not isinstance(c, dict):
            errors.append("each citation is an object with ref and claim")
            continue
        ref = _plain_text(c.get("ref"), where="citations[].ref", limit=160, errors=errors)
        claim = _plain_text(c.get("claim"), where="citations[].claim", limit=400, errors=errors)
        if ref:
            clean_citations.append({"ref": ref, "claim": claim or ""})

    declared = dict(mod.PARAMS)
    given = raw.get("params") or {}
    if not isinstance(given, dict):
        errors.append("params must be an object of the names the scene declares")
        given = {}
    _check_names(given, "params", errors)
    unknown = [k for k in given if k not in declared]
    if unknown:
        errors.append(f"params has names {kind} does not declare: {', '.join(sorted(map(str, unknown)))}")
    params: dict[str, Any] = {}
    for name, decl in declared.items():
        # an editor posts back "" or null for a field the learner cleared; that means "use the
        # scene's own default", not "the number zero" and not a validation failure
        if name in given and given[name] is not None and given[name] != "":
            value = _one(name, decl, given[name], errors)
            if value is not None:
                params[name] = value
        elif decl.get("default") is not None:
            params[name] = decl["default"]

    if errors:
        return Result(errors=errors, warnings=warnings)

    # ---- the dry run: does this actually produce a lesson? ----
    try:
        frames = sims.frames_for(kind, params)
    except ValueError as exc:
        return Result(errors=[f"the simulation refused these parameters: {exc}"], warnings=warnings)
    except Exception as exc:  # a sim that raises anything else is a bug worth surfacing, not a 500
        return Result(errors=[f"the simulation could not run this spec ({type(exc).__name__}: {exc})"], warnings=warnings)
    if not frames:
        return Result(errors=["the simulation produced no frames for these parameters"], warnings=warnings)
    if len(frames) > mod.MAX_FRAMES:
        return Result(errors=[f"{len(frames)} frames is over {kind}'s limit of {mod.MAX_FRAMES}"], warnings=warnings)

    try:
        canonical = mod.normalize(params) if hasattr(mod, "normalize") else params
    except Exception:
        canonical = params
    if hasattr(mod, "normalize"):
        # the dry run used the caller's numbers; store what the engine will actually use, so a
        # resume reproduces the same trace rather than re-deriving it from a sparser spec
        params = canonical

    prompts = [f["i"] for f in frames if f.get("prompt")]
    spec = {
        "id": spec_id(kind, params, title or mod.TITLE),
        "kind": kind,
        "title": title or mod.TITLE,
        "intent": intent,
        "framing": framing,
        "concepts": clean_concepts,
        "citations": clean_citations,
        "params": params,
        "frame_count": len(frames),
        "prediction_frames": prompts,
        "narration": [str(f.get("narration") or "") for f in frames],
        "resource_limits": {"max_frames": mod.MAX_FRAMES},
    }
    if len(prompts) == 0:
        warnings.append("this scene never asks the learner to predict anything, so it yields no evidence beyond navigation")
    if not clean_citations:
        warnings.append("no citation attached: the claim about what this teaches is unchecked")
    return Result(spec=spec, warnings=warnings)


# --------------------------------------------------------------------------- #
# the built-in catalogue -- what exists before AI 2 prepares anything
# --------------------------------------------------------------------------- #


def builtin_specs() -> list[dict[str, Any]]:
    """One validated spec per simulation the engine ships.

    These are the reference implementations of each scene kind: they are seeded into the
    database so a fresh clone has a lesson to open, and any AI 2 spec that disagrees with one
    of them in shape will fail validation the same way.
    """
    drafts = [
        {
            "kind": "binary_search",
            "title": "Binary search: one look at the middle, half the list gone",
            "intent": "predict which half survives each comparison, and say why sorted order makes that safe",
            "framing": {
                "intro": "Pause any time and change the target: the search recomputes, and you can watch whether the number of comparisons moves.",
                "outro": "If you can say, before the animation does, which half gets thrown away — you have the idea.",
            },
            "concepts": ["sorted order as a precondition", "loop invariant on the range", "logarithmic steps"],
            "citations": [{"ref": "CLRS 2.3.1", "claim": "binary search on a sorted array runs in Theta(lg n) comparisons."}],
        },
        {
            "kind": "sorting",
            "title": "Insertion sort: walking one card back into place",
            "intent": "connect the number of comparisons to how unordered the input was",
            "framing": {
                "intro": "Watch the comparisons, not the swaps: a value moves only while the card to its left is bigger.",
                "outro": "Nearly sorted input cost you almost nothing per card. That is why this sort survives in real libraries for small ranges.",
            },
            "concepts": ["sorted prefix as invariant", "stability from a strict test", "quadratic worst case"],
            "citations": [{"ref": "Cormen et al. 2.1", "claim": "insertion sort runs in Theta(n) on already sorted input and Theta(n^2) worst case."}],
        },
        {
            "kind": "logic_gates",
            "title": "Gates: two wires in, one rule out",
            "intent": "predict any row of the truth table from the rule rather than from memory",
            "framing": {
                "intro": "Answer the prompt before the output frame appears. Being wrong here is the useful part.",
                "outro": "NAND and NOR are functionally complete: you can build every other gate from one kind.",
            },
            "concepts": ["truth table as definition", "functional completeness", "inversion as composition"],
            "citations": [{"ref": "Shannon 1938", "claim": "switching circuits can be described and minimised with Boolean algebra."}],
        },
    ]
    for title, program, data, note in [
        (
            "The instruction cycle, register by register",
            ["LOAD 4", "ADD 5", "STORE 6", "HLT"],
            [7, 5, 0],
            "a straight line program: three fetch, decode, execute cycles, then a halt",
        ),
        (
            "A loop in the cycle: counting down to a jump",
            ["LOAD 5", "SUB 6", "STORE 5", "JZ 5", "JMP 0"],
            [3, 1],
            "a countdown loop: it ends only when the subtraction reaches zero and the JZ sends PC past the last instruction",
        ),
    ]:
        drafts.append(
            {
                "kind": "cpu_cycle",
                "title": title,
                "intent": "name what each phase of the cycle moves, and why PC advances before execute",
                "framing": {"intro": note, "outro": "Every phase in here is a register transfer. If you cannot say which register changed, that is the frame to replay."},
                "concepts": ["stored program", "fetch, decode, execute", "conditional jump as a PC write"],
                "citations": [{"ref": "Von Neumann et al. 1945", "claim": "storing instructions and data in one memory makes a program self modifiable and the machine general."}],
                "params": {"program": program, "data": data, "max_steps": 24 if len(program) > 4 else 12},
            }
        )

    specs = []
    for draft in drafts:
        out = validate(draft)
        if out.ok and out.spec:
            specs.append(out.spec)
    return specs
