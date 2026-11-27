"""CI guard for the /v1 contract.

Fails the build when:

(a) a ``/v1`` route has no auth requirement (health and version are listed
    below as explicit exceptions);
(b) an operation documents a non-2xx response that is not the error envelope,
    or documents no error response at all;
(c) ``errors.py`` can emit a code that is absent from the authoritative table
    in ``docs/v2/INTEGRATION.md`` §6.

The code list is read from the document, never copied here, so the two cannot
drift. Exit codes: 0 clean, 1 violations, 2 the document could not be read.

The document is read **as written**. This guard never applies
``docs-patches/*.diff``: if it did, the implementation could write the patch
that amends the authoritative table and then pass rule (c) against its own
edit — "the doc is authoritative" would quietly become "the code is
authoritative and the doc follows". A new code is a doc change reviewed by the
document's owner. ``docs-patches/`` stays as a review artefact: the change the
code needs, expressed as a diff, applied by a human. ``--print-doc`` exists so
CI can assert that the document it fetched is the current one — it prints the
resolved text, unmodified.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ENGINE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ENGINE_ROOT.parent
DEFAULT_DOC_PATH = REPO_ROOT / "docs" / "v2" / "INTEGRATION.md"

#: Routes that carry no auth requirement, by design. Everything else under
#: /v1 must declare one.
AUTH_EXEMPT_PATHS = frozenset({"/v1/health", "/v1/version"})

#: The schema every documented error response must reference.
ENVELOPE_SCHEMA = "ErrorEnvelope"

#: An OpenAPI document, and one operation inside it. Values stay loose: this
#: is foreign JSON, not something the engine owns.
OpenAPI = dict[str, Any]
Operation = dict[str, Any]

_ROW = re.compile(r"^\|\s*(?P<status>[^|]+?)\s*\|\s*(?P<code>[^|]+?)\s*\|")


@dataclass(frozen=True)
class Finding:
    """One violation, addressed to the rule that produced it."""

    rule: str
    message: str

    def __str__(self) -> str:
        return f"[{self.rule}] {self.message}"


class DocError(Exception):
    """The authoritative document could not be located or parsed."""


#: The one section whose tables are the error vocabulary. Other sections have
#: tables too — §6A is an endpoint inventory whose second column is a *class*
#: (``operational``, ``host-facing``, ``control-plane``) and would otherwise be
#: parsed as documented codes, which is how a guard starts passing on prose.
_ERROR_HEADING = re.compile(r"^##\s*6\..*$", re.MULTILINE)
_NEXT_HEADING = re.compile(r"^##\s", re.MULTILINE)


def error_section(text: str) -> str:
    """The §6 body, or the whole text when there is no §6 heading.

    Narrowing by heading is not cosmetic: a code that appears only in another
    table must not count as documented. The fallback keeps the parser usable on
    a snippet, which is what most of its own tests pass it.
    """
    start = _ERROR_HEADING.search(text)
    if start is None:
        return text
    rest = text[start.end() :]
    end = _NEXT_HEADING.search(rest)
    return rest if end is None else rest[: end.start()]


def _table_rows(text: str) -> list[tuple[str, str]]:
    """Every (status cell, code cell) pair in the §6 table.

    Status cells are not always numeric: the sentinel row reads ``any other``,
    and the "no error" row reads ``200``. Rule (c) cares about the code cell
    of *every* row, so the two are parsed separately.
    """
    rows: list[tuple[str, str]] = []
    for line in error_section(text).splitlines():
        if not line.startswith("|"):
            continue
        match = _ROW.match(line)
        if not match:
            continue
        status_cell = match.group("status").strip()
        code_cell = match.group("code").strip().strip("`")
        if not status_cell or status_cell.lower() == "status":
            continue
        if set(status_cell) <= set("-: "):
            continue
        if not re.fullmatch(r"[a-z0-9_]+", code_cell):
            continue
        rows.append((status_cell, code_cell))
    return rows


def parse_doc_codes(text: str) -> dict[int, set[str]]:
    """Status -> code(s), for the rows whose status cell is numeric.

    A row whose status is not numeric (``any other``) contributes to
    :func:`documented_codes` but not here: it has no status to map from.
    """
    codes: dict[int, set[str]] = {}
    for status_cell, code_cell in _table_rows(text):
        for part in status_cell.split("/"):
            part = part.strip()
            if part.isdigit():
                codes.setdefault(int(part), set()).add(code_cell)
    if not codes:
        raise DocError("no status/code rows found in the error table")
    return codes


def documented_codes(text: str) -> frozenset[str]:
    """Every code in the table, from every row — numeric status or not."""
    rows = _table_rows(text)
    if not rows:
        raise DocError("no status/code rows found in the error table")
    return frozenset(code for _, code in rows)


def _operations(openapi: OpenAPI) -> list[tuple[str, str, Operation]]:
    ops: list[tuple[str, str, Operation]] = []
    for path, item in (openapi.get("paths") or {}).items():
        for method, operation in (item or {}).items():
            if method.lower() in {"get", "put", "post", "delete", "patch", "head", "options"}:
                ops.append((path, method.upper(), operation or {}))
    return ops


def _schema_ref(response: Operation) -> str | None:
    content = (response.get("content") or {}).get("application/json") or {}
    schema = content.get("schema") or {}
    ref = schema.get("$ref")
    if isinstance(ref, str):
        return ref.rsplit("/", 1)[-1]
    return None


def check_openapi(openapi: OpenAPI) -> list[Finding]:
    """Apply rules (a) and (b) to a generated OpenAPI document.

    Rule (a) has three parts: a security requirement exists, it names a scheme
    the document declares, and at least one requirement names a scope. "Auth
    required" with no scope says nothing about what the key may do.
    """
    findings: list[Finding] = []
    schemes = set(((openapi.get("components") or {}).get("securitySchemes") or {}).keys())
    for path, method, operation in _operations(openapi):
        if not path.startswith("/v1"):
            continue
        if path in AUTH_EXEMPT_PATHS:
            continue
        security = operation.get("security") or []
        if not security:
            findings.append(
                Finding(
                    "a:auth-required",
                    f"{method} {path} declares no auth requirement and is not an "
                    f"excepted route (excepted: {', '.join(sorted(AUTH_EXEMPT_PATHS))})",
                )
            )
        else:
            unknown = sorted(
                scheme
                for requirement in security
                for scheme in requirement
                if scheme not in schemes
            )
            if unknown:
                findings.append(
                    Finding(
                        "a:auth-required",
                        f"{method} {path} references undeclared security scheme(s): "
                        f"{', '.join(unknown)}",
                    )
                )
            if not any(scopes for requirement in security for scopes in requirement.values()):
                findings.append(
                    Finding(
                        "a:auth-required",
                        f"{method} {path} requires auth but names no scope",
                    )
                )
        responses: dict[str, Operation] = operation.get("responses") or {}
        error_statuses = [
            status
            for status in responses
            if isinstance(status, str) and status.isdigit() and int(status) >= 400
        ]
        if not error_statuses:
            findings.append(
                Finding(
                    "b:envelope-documented",
                    f"{method} {path} documents no error response using {ENVELOPE_SCHEMA}",
                )
            )
            continue
        for status in sorted(error_statuses, key=int):
            ref = _schema_ref(responses[status] or {})
            if ref != ENVELOPE_SCHEMA:
                findings.append(
                    Finding(
                        "b:envelope-documented",
                        f"{method} {path} documents {status} as {ref or 'no schema'}, "
                        f"expected {ENVELOPE_SCHEMA}",
                    )
                )
    return findings


def check_emitted_codes(emitted: frozenset[str], doc_codes: frozenset[str]) -> list[Finding]:
    """Rule (c): every code the engine can emit must come from the table.

    There is no exemption list here. The table is the only allowlist, so a
    code the doc has not documented fails the build even if it is our own
    sentinel for "no documented code".
    """
    findings: list[Finding] = []
    for code in sorted(emitted - doc_codes):
        findings.append(
            Finding(
                "c:code-documented",
                f"errors.py can emit {code!r}, which is absent from the INTEGRATION.md §6 table",
            )
        )
    return findings


def load_doc_text(path: str | None) -> tuple[str, str]:
    """Return (text, source description) for the contract in this checkout.

    Resolution has exactly two inputs: an explicit ``--doc`` (or
    ``ADAPT_INTEGRATION_DOC``), and the in-tree ``docs/v2/INTEGRATION.md``.
    There is no branch fallback and no ``git`` lookup at all: the tree contains
    the contract it implements, and a build that reaches for somebody's branch
    is a build that breaks the day that branch is cleaned up. Missing means missing —
    the guard exits 2 and says which path it wanted and how to point it
    elsewhere. It never guesses.
    """
    candidate = path or os.environ.get("ADAPT_INTEGRATION_DOC")
    if candidate:
        doc_path = Path(candidate)
        if not doc_path.is_file():
            raise DocError(
                f"document not found at {doc_path} (from --doc or ADAPT_INTEGRATION_DOC)"
            )
        source = str(doc_path)
    else:
        if not DEFAULT_DOC_PATH.is_file():
            raise DocError(
                f"the contract is not in this tree: no file at {DEFAULT_DOC_PATH}. "
                "The guard fails closed rather than reaching for a branch; pass "
                "--doc PATH or set ADAPT_INTEGRATION_DOC to point at it."
            )
        doc_path = DEFAULT_DOC_PATH
        source = str(DEFAULT_DOC_PATH)
    text = doc_path.read_text(encoding="utf-8")
    if not text.strip():
        raise DocError(f"the contract at {doc_path} is empty")
    return text, source


def build_openapi() -> OpenAPI:
    """Generate the app's OpenAPI document without touching any dependency."""
    if str(ENGINE_ROOT / "src") not in sys.path:
        sys.path.insert(0, str(ENGINE_ROOT / "src"))
    from pydantic import SecretStr

    from adapt_engine.config import Settings
    from adapt_engine.main import create_app

    settings = Settings(
        env="test",
        database_url="postgresql://unused:unused@example.invalid:5432/unused",
        master_key=SecretStr("schema-generation-only-not-a-real-key"),
        key_pepper=SecretStr("schema-generation-only-not-a-real-pepper"),
        redis_url="redis://example.invalid:6379/0",
        commit="schema-generation-only",
        build_time="1970-01-01T00:00:00Z",
        version="0.0.0",
    )
    app = create_app(settings, db_check=lambda: False, redis_check=lambda: False)
    return app.openapi()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--doc", help="path to INTEGRATION.md (default: docs/v2/INTEGRATION.md)")
    parser.add_argument(
        "--print-doc",
        action="store_true",
        help="print the resolved document verbatim and exit (CI checks it is current)",
    )
    args = parser.parse_args(argv)

    try:
        text, source = load_doc_text(args.doc)
    except DocError as exc:
        print(f"openapi-check: cannot read the authoritative table: {exc}", file=sys.stderr)
        return 2

    if args.print_doc:
        sys.stdout.write(text)
        print(f"openapi-check: doc read from {source}", file=sys.stderr)
        return 0

    try:
        codes = documented_codes(text)
    except DocError as exc:
        print(f"openapi-check: cannot read the authoritative table: {exc}", file=sys.stderr)
        return 2

    if str(ENGINE_ROOT / "src") not in sys.path:
        sys.path.insert(0, str(ENGINE_ROOT / "src"))
    from adapt_engine.errors import EMITTED_CODES

    findings = check_openapi(build_openapi())
    findings += check_emitted_codes(EMITTED_CODES, codes)

    print(f"openapi-check: table read from {source} ({len(codes)} codes)")
    if findings:
        for finding in findings:
            print(f"  FAIL {finding}")
        print(f"openapi-check: {len(findings)} violation(s)")
        return 1
    print("openapi-check: no violations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
