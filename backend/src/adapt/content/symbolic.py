"""Exact symbolic checking for algebra and calculus keys (§7.3).

§7.3 asks maths and computational content to be verified *by computing*: "extend
``assessment/generated.py`` with a symbolic checker, e.g. SymPy, for calculus and algebra; keep the
existing ``safe_arithmetic`` approach for simple items."

**Why this is not SymPy.** The brief says "e.g.", and the choice is argued rather than assumed:
SymPy is a 60 MB dependency whose value here is breadth — symbolic integration, matrices, physics
units — and what the content engine needs is *depth on one class*: exact equivalence and
differentiation for polynomials and rational functions, which is what school algebra and the
differentiation half of school calculus are made of. A dependency that size has to be importable on
a phone-hosted demo and on every cold start, so a 300-line checker over exact rationals wins unless
breadth is needed. If a later phase wants integration or trigonometric identities, that is the point
to add SymPy — and this module's public surface (:func:`equivalent`, :func:`differentiate`,
:func:`solve`, :func:`evaluate`) is deliberately the shape SMT/SymPy-style callers would want, so
swapping the engine is a one-file change rather than a rewrite.

**Exactness.** Everything is :class:`fractions.Fraction` arithmetic. There is no epsilon anywhere in
this file. Binary floating point is *not* the model: a stem that says ``0.1`` means one tenth, not
the nearest double to it, so ``0.1 + 0.2`` and ``0.3`` are the same number here. That is the school
reading, and it is the reading under which a learner who types 0.3 is right. It also means this
checker will *disagree* with a naive float comparison -- which is a feature when the alternative is
marking 0.3 wrong.

**Two methods, and they must agree.** :func:`equivalent` first canonicalises both sides to a rational
function and compares exactly; when that says "not equal" it also probes the *difference* at rational
sample points, because a canonicaliser bug and a genuinely wrong key look identical from one method.
A candidate is accepted only if both methods agree — the same "second independent pass" rule §7.3
applies to non-computational content.

**What it refuses.** Anything outside the grammar raises :class:`SymbolicError` with a reason, and
callers are expected to fall back to ``unverified`` (never to accept). ``sin``, ``log``, matrices,
inequalities and non-integer powers are all refused by name, because silently treating an unsupported
function as zero is how a checker becomes a liar.
"""

from __future__ import annotations

import ast
import re
from fractions import Fraction
from typing import Any, Mapping, Sequence

__all__ = [
    "SymbolicError",
    "equivalent",
    "evaluate",
    "differentiate",
    "solve",
    "degree_of",
    "canonical_form",
    "difference_at",
    "SUPPORTED_NOTE",
]

SUPPORTED_NOTE = (
    "exact arithmetic over integers, decimals and fractions; + - * / ** with whole-number "
    "exponents; polynomials and rational functions in one or more symbols"
)

#: Names that look like functions and are deliberately *not* handled. Named explicitly so the
#: refusal message can say what to do instead, rather than reporting "unexpected call".
_KNOWN_UNSUPPORTED = {
    "sin", "cos", "tan", "asin", "acos", "atan", "log", "ln", "exp", "sqrt",
    "pow", "abs", "floor", "ceil", "round", "factorial", "min", "max",
}

_MAX_DEGREE = 24
_MAX_TERMS = 512


class SymbolicError(ValueError):
    """The input is outside the grammar, or outside what this checker will vouch for."""


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #

_POWER = re.compile(r"\^")
#: ``2x`` -> ``2*x``, ``3(x+1)`` -> ``3*(x+1)``, ``x(x+1)`` -> ``x*(x+1)``.
#: Deliberately **not** applied between two letters: ``xy`` could be one variable or two, and
#: ``sin(x)`` is how a checker that guesses becomes a checker that lies (the first version of this
#: file read it as ``s*i*n*(x)`` and reported the answer correct). Letter-letter input is refused
#: by name instead -- see :func:`normalise`.
_IMPLICIT = re.compile(r"(?<=[0-9)])\s*(?=[a-zA-Z(])")
_LETTER_LETTER = re.compile(r"(?<=[a-zA-Z])\s*(?=[a-zA-Z])")
_SYMBOL = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*$")

#: A "symbol" is what a *variable* looks like. Single letters and short names, because `xy` in a
#: stem almost always means "two symbols" and only sometimes means "a variable called xy"; a name
#: longer than one letter is assumed to be a word, and words are refused (they are usually units).
_LONG_SYMBOL_OK = {"dx", "dy", "dt"}


def _symbolise(name: str) -> str:
    if len(name) == 1 or name in _LONG_SYMBOL_OK or name[0] == "_":
        return name
    raise SymbolicError(
        f"{name!r} looks like a word, not a variable; rename it to a single letter before checking "
        f"(this checker verifies algebra, and a name that reads like prose usually hides a unit)"
    )


def normalise(text: str) -> str:
    """``^`` to ``**`` and implicit multiplication to explicit, so ``2(x+3)`` parses.

    ``2x`` becoming ``2*x`` is the single most common way a school-algebra expression fails to
    parse, and refusing it would make the checker useless on the material it exists for. The
    transformation is deliberately conservative: it only inserts ``*`` where a number or a closing
    bracket is directly followed by a letter or an opening bracket.
    """
    source = _POWER.sub("**", str(text or "").strip())
    named = re.match(r"^\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", source)
    if named and named.group(1) in _KNOWN_UNSUPPORTED:
        raise SymbolicError(
            f"{named.group(1)}() is outside what this checker verifies ({SUPPORTED_NOTE}); verify "
            "that item another way rather than assuming the key"
        )
    if _LETTER_LETTER.search(source):
        raise SymbolicError(
            f"{text!r} has two letters side by side, which is ambiguous (one variable called "
            '"xy", or x times y?); write the multiplication as "x*y" and it can be checked'
        )
    previous = None
    out = source
    while previous != out:
        previous = out
        out = _IMPLICIT.sub("*", out)
    return out


def _number(text: str) -> Fraction:
    try:
        return Fraction(text)
    except (ValueError, ZeroDivisionError) as exc:
        raise SymbolicError(f"{text!r} is not a number: {exc}") from exc


def _fraction(value: Any) -> Fraction:
    if isinstance(value, bool):
        raise SymbolicError("a boolean is not a number")
    if isinstance(value, Fraction):
        return value
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise SymbolicError(f"{value!r} is not a finite number")
        return Fraction(str(value)).limit_denominator(10**9)
    try:
        return Fraction(str(value).strip())
    except (ValueError, ZeroDivisionError) as exc:
        raise SymbolicError(f"{value!r} is not a number this checker can hold exactly") from exc


# --------------------------------------------------------------------------- #
# the algebra
# --------------------------------------------------------------------------- #

#: A polynomial is a dict from exponent tuple to coefficient. One symbol gives keys like ``(2,)``,
#: two give ``(1, 1)``. Sparse-by-construction, so ``x**20`` costs one term.
Poly = dict[tuple[int, ...], Fraction]
#: A rational function is a pair of polynomials: ``(numerator, denominator)``.
Rat = tuple[Poly, Poly]


def _var_index(symbols: Sequence[str], name: str) -> int:
    try:
        return list(symbols).index(name)
    except ValueError as exc:  # pragma: no cover - callers build symbols from the same parse
        raise SymbolicError(f"{name!r} is not one of the symbols {list(symbols)}") from exc


def _zero(n: int = 1) -> Poly:
    return {}


def _constant(value: Fraction, n: int = 1) -> Poly:
    return {} if value == 0 else {(0,) * n: value}


def _poly_add(a: Poly, b: Poly) -> Poly:
    out = dict(a)
    for key, value in b.items():
        total = out.get(key, Fraction(0)) + value
        if total == 0:
            out.pop(key, None)
        else:
            out[key] = total
    return out


def _poly_scale(a: Poly, factor: Fraction) -> Poly:
    if factor == 0:
        return {}
    return {k: v * factor for k, v in a.items() if v * factor != 0}


def _poly_mul(a: Poly, b: Poly) -> Poly:
    out: Poly = {}
    for ka, va in a.items():
        for kb, vb in b.items():
            key = tuple(x + y for x, y in zip(ka, kb))
            if sum(key) > _MAX_DEGREE:
                raise SymbolicError(f"the product exceeds degree {_MAX_DEGREE}; refusing to guess")
            total = out.get(key, Fraction(0)) + va * vb
            if total == 0:
                out.pop(key, None)
            else:
                out[key] = total
            if len(out) > _MAX_TERMS:
                raise SymbolicError(f"the product exceeds {_MAX_TERMS} terms")
    return out


def _poly_pow(a: Poly, exponent: int, n: int) -> Poly:
    if exponent < 0:
        raise SymbolicError("a negative power of a polynomial is not a polynomial")
    if exponent > _MAX_DEGREE:
        raise SymbolicError(f"an exponent of {exponent} exceeds the supported degree {_MAX_DEGREE}")
    out = _constant(Fraction(1), n)
    for _ in range(exponent):
        out = _poly_mul(out, a)
    return out


def _rat_add(a: Rat, b: Rat) -> Rat:
    num = _poly_add(_poly_mul(a[0], b[1]), _poly_mul(b[0], a[1]))
    den = _poly_mul(a[1], b[1])
    return (num, den)


def _rat_mul(a: Rat, b: Rat) -> Rat:
    return (_poly_mul(a[0], b[0]), _poly_mul(a[1], b[1]))


def _rat_scale(a: Rat, factor: Fraction) -> Rat:
    return (_poly_scale(a[0], factor), a[1])


def _rat_pow(a: Rat, exponent: int, n: int) -> Rat:
    if exponent < 0:
        positive = _rat_pow(a, -exponent, n)
        return (positive[1], positive[0])
    out: Rat = (_constant(Fraction(1), n), _constant(Fraction(1), n))
    for _ in range(exponent):
        out = _rat_mul(out, a)
    return out


def _parse(text: str, symbols: Sequence[str]) -> Rat:
    """Parse an expression into a rational function over ``symbols``."""
    source = normalise(text)
    if not source:
        raise SymbolicError("empty expression")
    try:
        tree = ast.parse(source, mode="eval")
    except SyntaxError as exc:
        raise SymbolicError(f"{text!r} does not parse as an expression: {exc.msg}") from exc

    def walk(node: ast.AST, depth: int = 0) -> Rat:
        if depth > 60:
            raise SymbolicError("the expression nests more than 60 levels deep")
        if isinstance(node, ast.Expression):
            return walk(node.body, depth + 1)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise SymbolicError(f"{node.value!r} is not a number or a symbol")
            return (_constant(_fraction(node.value), len(symbols)), _constant(Fraction(1), len(symbols)))
        if isinstance(node, ast.Name):
            if node.id in _KNOWN_UNSUPPORTED:
                raise SymbolicError(
                    f"{node.id}() is outside what this checker verifies ({SUPPORTED_NOTE}); "
                    "an item using it must be verified another way rather than assumed"
                )
            _symbolise(node.id)
            index = _var_index(symbols, node.id)
            return (
                {tuple(1 if i == index else 0 for i in range(len(symbols))): Fraction(1)},
                _constant(Fraction(1), len(symbols)),
            )
        if isinstance(node, ast.UnaryOp):
            inner = walk(node.operand, depth + 1)
            if isinstance(node.op, ast.USub):
                return _rat_scale(inner, Fraction(-1))
            if isinstance(node.op, ast.UAdd):
                return inner
            raise SymbolicError("only unary + and - are supported")
        if isinstance(node, ast.BinOp):
            left = walk(node.left, depth + 1)
            right = walk(node.right, depth + 1)
            if isinstance(node.op, ast.Add):
                return _rat_add(left, right)
            if isinstance(node.op, ast.Sub):
                return _rat_add(left, _rat_scale(right, Fraction(-1)))
            if isinstance(node.op, ast.Mult):
                return _rat_mul(left, right)
            if isinstance(node.op, ast.Div):
                if not right[0]:
                    raise SymbolicError("division by zero")
                return _rat_mul(left, (right[1], right[0]))
            if isinstance(node.op, ast.Pow):
                exponent = _exponent_of(node.right)
                if exponent is None:
                    raise SymbolicError(
                        "the exponent must be a whole number known at check time; a symbolic "
                        "exponent is outside what this checker verifies"
                    )
                return _rat_pow(left, exponent, len(symbols))
            raise SymbolicError(f"{type(node.op).__name__} is not supported")
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", "")
            if name in _KNOWN_UNSUPPORTED:
                raise SymbolicError(
                    f"{name}() is outside what this checker verifies ({SUPPORTED_NOTE})"
                )
            raise SymbolicError(f"{name or 'that call'}() is not a supported function")
        raise SymbolicError(f"{type(node).__name__} is not part of an expression")

    return walk(tree)


def _exponent_of(node: ast.AST) -> int | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        value = _fraction(node.value)
        return int(value) if value.denominator == 1 else None
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _exponent_of(node.operand)
        return None if inner is None else -inner
    return None


def symbols_in(*expressions: str) -> list[str]:
    """Every symbol used, in first-appearance order. Lets callers check without declaring vars."""
    found: list[str] = []
    for text in expressions:
        try:
            tree = ast.parse(normalise(text), mode="eval")
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id not in _KNOWN_UNSUPPORTED:
                if len(node.id) == 1 or node.id in _LONG_SYMBOL_OK:
                    if node.id not in found:
                        found.append(node.id)
    return found


# --------------------------------------------------------------------------- #
# public surface
# --------------------------------------------------------------------------- #


def canonical_form(text: str, *, symbols: Sequence[str] | None = None) -> str:
    """The exact canonical string for an expression — equal expressions share a string.

    The denominator is cleared by cross-multiplication rather than by polynomial gcd, so
    ``(x+1)/(x+1)`` and ``1`` do **not** canonicalise to the same string here. That is deliberate:
    they differ at ``x = -1``, and a checker that said they were the same would be wrong about the
    one point anyone would think to ask about. :func:`equivalent` compares cross-multiplied forms,
    which is the question actually being asked ("are these the same expression where both are
    defined?").
    """
    names = list(symbols) if symbols is not None else symbols_in(text)
    if not names:
        names = ["x"]  # a bare number still has to have somewhere to live
    num, den = _parse(text, names)
    return f"({_poly_text(num, names)})/({_poly_text(den, names)})"


def _poly_text(poly: Poly, symbols: Sequence[str]) -> str:
    if not poly:
        return "0"
    parts: list[str] = []
    for key in sorted(poly, key=lambda k: (-sum(k), k)):
        coeff = poly[key]
        factors = []
        for name, power in zip(symbols, key):
            if power == 1:
                factors.append(name)
            elif power > 1:
                factors.append(f"{name}^{power}")
        body = "*".join(factors)
        if not body:
            parts.append(str(coeff))
        elif coeff == 1:
            parts.append(body)
        elif coeff == -1:
            parts.append(f"-{body}")
        else:
            parts.append(f"{coeff}*{body}" if coeff.denominator == 1 else f"({coeff})*{body}")
    return " + ".join(parts).replace("+ -", "- ")


def difference_at(left: str, right: str, values: Mapping[str, Any], *, symbols: Sequence[str] | None = None) -> Fraction:
    """``left - right`` at one exact point — the probe :func:`equivalent` uses as a second method."""
    names = list(symbols) if symbols is not None else symbols_in(left, right)
    point = {name: _fraction(values.get(name, 0)) for name in names}
    return _value(left, names, point) - _value(right, names, point)


def _value(text: str, names: Sequence[str], point: Mapping[str, Fraction]) -> Fraction:
    num, den = _parse(text, names)

    def poly_value(poly: Poly) -> Fraction:
        total = Fraction(0)
        for key, coeff in poly.items():
            term = coeff
            for name, power in zip(names, key):
                term *= point[name] ** power
            total += term
        return total

    den_value = poly_value(den)
    if den_value == 0:
        raise SymbolicError(f"the expression is undefined at {dict(point)} (the denominator is 0)")
    return poly_value(num) / den_value


def evaluate(text: str, values: Mapping[str, Any] | None = None) -> Fraction:
    """The exact value of an expression at a point. Raises rather than approximating."""
    names = symbols_in(text)
    point = {name: _fraction((values or {}).get(name, 0)) for name in names}
    missing = [n for n in names if n not in (values or {})]
    if missing:
        raise SymbolicError(f"no value given for {', '.join(missing)}")
    return _value(text, names, point)


def probe_points(degree: int, *, offset: int = 3) -> list[Fraction]:
    """Enough distinct rational points to expose a difference of this degree.

    A degree-``d`` difference vanishes at at most ``d`` points, so ``d + 2`` distinct samples make a
    false "equal" impossible *if the difference really is a polynomial of that degree* — which is
    exactly the assumption this second method is not allowed to make, so the count is generous and
    the points include 0, negatives and fractions (the values a learner would pick).
    """
    n = max(4, min(2 * max(degree, 1) + 6, 24))
    out: list[Fraction] = []
    for i in range(n):
        out.append(Fraction(i - n // 2) * (Fraction(1, 2) if i % 3 == 0 else Fraction(1)))
    return out


def _degree(poly: Poly) -> int:
    return max((sum(k) for k in poly), default=-1)


def degree_of(text: str, *, symbols: Sequence[str] | None = None) -> int:
    names = list(symbols) if symbols is not None else symbols_in(text)
    if not names:
        names = ["x"]
    num, den = _parse(text, names)
    if den and any(sum(k) > 0 for k in den):
        raise SymbolicError("that is a rational function, not a polynomial")
    return _degree(num)


def equivalent(left: str, right: str, *, symbols: Sequence[str] | None = None) -> tuple[bool, str]:
    """Are these two expressions the same? Returns ``(verdict, how it was decided)``.

    Both methods run. "Equal" requires the canonical forms to match **and** every probe to vanish;
    "not equal" is reported with the first probe that showed a difference, so a rejection carries
    the counterexample a human can check by hand (``x = 3/2`` gives -1).

    The verdict is identity *where both sides are defined*, which is the question a key asks. So
    ``(x^2-1)/(x-1)`` equals ``x+1`` here even though the first is undefined at ``x = 1``; the
    verdict string says so when the two sides disagree on their domain.
    """
    names = list(symbols) if symbols is not None else symbols_in(left, right)
    if not names:
        names = ["x"]
    left_num, left_den = _parse(left, names)
    right_num, right_den = _parse(right, names)
    same = _poly_add(_poly_mul(left_num, right_den), _poly_scale(_poly_mul(right_num, left_den), Fraction(-1))) == {}
    degree = max(_degree(left_num) + _degree(right_den), _degree(right_num) + _degree(left_den))
    if not same:
        for point in probe_points(degree):
            try:
                difference = difference_at(left, right, {names[0]: point}, symbols=names)
            except SymbolicError:
                continue
            if difference != 0:
                return (False, f"they differ at {names[0]} = {point} (difference {difference})")
        return (False, "the canonical forms are different but no sampled point separated them")
    for point in probe_points(degree):
        try:
            difference = difference_at(left, right, {names[0]: point}, symbols=names)
        except SymbolicError:
            continue
        if difference != 0:
            return (False, f"the canonical forms agreed but {names[0]} = {point} does not (difference {difference})")
    domain = (
        " (the two sides agree where both are defined, but one of them divides by something and "
        "the other does not, so they are not defined in the same places)"
        if _one_side_divides(left_den, right_den)
        else ""
    )
    return (
        True,
        f"exact rational-function comparison over {', '.join(names)}, agreeing at "
        f"{len(probe_points(degree))} sampled points{domain}",
    )


def _one_side_divides(a_den: Poly, b_den: Poly) -> bool:
    """True when exactly one side divides by a non-constant polynomial.

    This is the only domain difference the checker states as fact, because it is the only one it can
    prove from the parsed forms: a side whose denominator is a constant is defined everywhere, a side
    whose denominator is not has somewhere it is undefined. Two sides that *both* divide by something
    may still have different domains (``1/x`` and ``1/x^2``) and this deliberately says nothing about
    them rather than guessing: comparing zero sets needs a squarefree factorisation, and a wrong
    claim about a domain is worse than a silent one.
    """
    a_constant = not a_den or all(sum(k) == 0 for k in a_den)
    b_constant = not b_den or all(sum(k) == 0 for k in b_den)
    return a_constant != b_constant


def differentiate(text: str, *, var: str = "x") -> str:
    """The exact derivative of a polynomial or rational function, as a string.

    Only the power rule, the sum rule and the quotient rule are implemented — which between them
    cover school differentiation. The result is returned in the same "one symbol, tidy string"
    form the bank authors write answers in, not a canonical dump.
    """
    names = symbols_in(text) or [var]
    if var not in names:
        names = [*names, var]
    num, den = _parse(text, names)
    index = _var_index(names, var)

    def derivative(poly: Poly) -> Poly:
        out: Poly = {}
        for key, coeff in poly.items():
            if key[index] == 0:
                continue
            new_key = list(key)
            new_key[index] -= 1
            out[tuple(new_key)] = coeff * key[index]
        return {k: v for k, v in out.items() if v != 0}

    d_num, d_den = derivative(num), derivative(den)
    if not den or all(sum(k) == 0 for k in den):
        return _poly_text(d_num, names)
    # quotient rule: (n'd - nd') / d^2
    top = _poly_add(_poly_mul(d_num, den), _poly_scale(_poly_mul(num, d_den), Fraction(-1)))
    bottom = _poly_mul(den, den)
    if bottom == _constant(Fraction(1), len(names)):
        return _poly_text(top, names)
    return f"({_poly_text(top, names)})/({_poly_text(bottom, names)})"


def solve(equation: str, *, var: str = "x") -> tuple[list[Fraction], str]:
    """Exact roots of ``lhs = rhs`` (or of an expression set to zero) for one symbol.

    Linear and quadratic equations only, exactly: a quadratic whose discriminant is not a perfect
    square of a rational has **no rational root**, and rather than returning ``±1.4142...`` this
    returns an empty list and says so in the status. A key that needs an irrational answer is a key
    that should be asked as a decimal with the tolerance declared, not silently rounded here.
    """
    text = str(equation or "")
    if "=" in text:
        left, _, right = text.partition("=")
        remainder = f"({left}) - ({right})"
    else:
        remainder = text
    names = symbols_in(remainder) or [var]
    if var not in names:
        names = [*names, var]
    index = _var_index(names, var)
    num, den = _parse(remainder, names)
    if den and any(sum(k) > 0 for k in den):
        raise SymbolicError("solving a rational equation is outside what this checker verifies")
    coeffs: dict[int, Fraction] = {}
    for key, coeff in num.items():
        power = key[index]
        others = sum(p for i, p in enumerate(key) if i != index)
        if others:
            raise SymbolicError("the equation has more than one unknown; solving needs one symbol")
        coeffs[power] = coeffs.get(power, Fraction(0)) + coeff
    degree = max((p for p, c in coeffs.items() if c != 0), default=-1)
    if degree < 0:
        return ([], "every value is a solution (the two sides are the same expression)")
    if degree == 0:
        return ([], "no solution: the equation reduces to a non-zero constant = 0")
    if degree == 1:
        a = coeffs.get(1, Fraction(0))
        b = coeffs.get(0, Fraction(0))
        root = -b / a
        return ([root], f"linear: {var} = {root}")
    if degree == 2:
        a = coeffs.get(2, Fraction(0))
        b = coeffs.get(1, Fraction(0))
        c = coeffs.get(0, Fraction(0))
        discriminant = b * b - 4 * a * c
        if discriminant < 0:
            return ([], "no real solution: the discriminant is negative")
        root = _exact_sqrt(discriminant)
        if root is None:
            return ([], "no rational solution: the discriminant is not a perfect square")
        return (
            [(-b + root) / (2 * a), (-b - root) / (2 * a)],
            f"quadratic with discriminant {discriminant}",
        )
    raise SymbolicError(
        f"degree {degree} is outside what this checker solves (linear and quadratic only)"
    )


def _exact_sqrt(value: Fraction) -> Fraction | None:
    import math

    if value < 0:
        return None
    num = math.isqrt(value.numerator)
    den = math.isqrt(value.denominator)
    if num * num != value.numerator or den * den != value.denominator:
        return None
    return Fraction(num, den)


def check_answer(
    *,
    expression: str,
    answer: Any,
    variables: Mapping[str, Any] | None = None,
    equals: str | None = None,
) -> tuple[str, str]:
    """The one entry point ``assessment.generated`` uses.

    Three shapes, tried in order:

    * ``equals`` given: "these two expressions are the same for every value" — equivalence.
    * ``variables`` given: the expression's exact value at that point, compared to ``answer``.
    * neither: the expression is treated as an equation equal to zero and its roots are compared
      with ``answer`` when the answer looks like a root.

    Returns ``("symbolic", how)`` when verified, ``("symbolic_mismatch", detail)`` when the checker
    *disagrees*, and ``("symbolic_error", reason)`` when it cannot be applied at all — the caller
    turns that last one into ``unverified`` rather than a rejection, because "we could not check
    this" and "this is wrong" are different things and only one of them should fail a build.
    """
    try:
        if "=" in str(expression) and equals is None:
            # An equation is a different question from an expression: the key is a root, not a value.
            # Sending it down the expression path made every equation come back "could not be
            # checked", which is true of a parser and useless as a verdict on a key.
            roots, status = solve(str(expression))
            want = answer if isinstance(answer, (list, tuple, set)) else [answer]
            wanted = {_fraction(v) for v in want if str(v).strip() != ""}
            if wanted and wanted == set(roots):
                return ("symbolic", status)
            return (
                "symbolic_mismatch",
                f"the key says {sorted(str(w) for w in wanted)}, solving gives {sorted(str(r) for r in roots)}",
            )
        if equals is not None:
            verdict, how = equivalent(str(expression), str(equals))
            return ("symbolic", how) if verdict else ("symbolic_mismatch", how)
        if variables is None and not symbols_in(str(expression)) and equals is None:
            # No symbols at all: this is arithmetic wearing an algebra hat, and the exact value is
            # the whole check. Falling through to the "solve" branch would report "no solution" for
            # 7*8 -- true of an equation, useless as a verdict on a key.
            got = evaluate(str(expression), {})
            want = _fraction(answer)
            if got == want:
                return ("symbolic", f"the expression is exactly {got}")
            return (
                "symbolic_mismatch",
                f"the expression is exactly {got}, the key says {want}",
            )
        if variables is not None:
            got = evaluate(str(expression), variables)
            want = _fraction(answer)
            if got == want:
                return ("symbolic", f"the expression is exactly {got} at the declared values")
            return (
                "symbolic_mismatch",
                f"the expression gives {got} at the declared values, the key says {want}",
            )
        roots, status = solve(str(expression))
        want = answer if isinstance(answer, (list, tuple, set)) else [answer]
        wanted = {_fraction(v) for v in want if str(v).strip() != ""}
        if not roots:
            return ("symbolic_error", status)
        if wanted and wanted == set(roots):
            return ("symbolic", status)
        if wanted & set(roots):
            return ("symbolic", status)
        return ("symbolic_mismatch", f"the key says {sorted(str(w) for w in wanted)}, solving gives {sorted(str(r) for r in roots)}")
    except SymbolicError as exc:
        return ("symbolic_error", str(exc))
