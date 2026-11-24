"""Tests for the contract guard itself.

A checker nobody tests is a checker that silently stops checking. Each rule is
exercised with a document that violates it, and the end-to-end entrypoint is
run against the real INTEGRATION.md table and against a deliberately drifted
copy.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import scripts.check_openapi as guard
from scripts.check_openapi import (
    DocError,
    _table_rows,
    check_emitted_codes,
    check_openapi,
    documented_codes,
    load_doc_text,
    main,
    parse_doc_codes,
)

ENVELOPE_REF = "#/components/schemas/ErrorEnvelope"


def _doc() -> str:
    text, _ = load_doc_text(None)
    return text


SCHEMES = {"securitySchemes": {"APIKeyAuth": {"type": "http", "scheme": "bearer"}}}


def _operation(*, security: list[dict] | None, responses: dict) -> dict:
    op: dict = {"responses": responses}
    if security is not None:
        op["security"] = security
    return op


def _envelope_response() -> dict:
    return {"content": {"application/json": {"schema": {"$ref": ENVELOPE_REF}}}}


def test_parses_the_authoritative_table() -> None:
    codes = documented_codes(_doc())
    # Spot-check a few rows of the §6 table: client error, quota, server.
    assert "payload_invalid" in codes
    assert "quota_exhausted" in codes
    assert "rate_limited" in codes
    assert "internal" in codes
    assert "not_capable" in codes
    assert "gone" in codes
    # The 200 row has no code and must not be parsed as one.
    assert "—" not in codes


def test_sentinel_row_is_a_documented_code() -> None:
    """Follow-up 2: the row's status cell reads `any other`, not a number."""
    text = _doc()
    assert "unmapped_status" in documented_codes(text)
    rows = dict(_table_rows(text))
    assert rows["any other"] == "unmapped_status"
    # It contributes a code but no status mapping.
    assert "unmapped_status" not in {
        code for codes in parse_doc_codes(text).values() for code in codes
    }


def test_compliant_route_passes() -> None:
    openapi = {
        "components": SCHEMES,
        "paths": {
            "/v1/recommendations": {
                "post": _operation(
                    security=[{"APIKeyAuth": ["observe"]}],
                    responses={"200": {"description": "ok"}, "422": _envelope_response()},
                )
            }
        },
    }
    assert check_openapi(openapi) == []


def test_exempt_routes_need_no_auth_or_envelope() -> None:
    openapi = {
        "paths": {
            "/v1/health": {"get": {"responses": {"200": {"description": "ok"}}}},
            "/v1/version": {"get": {"responses": {"200": {"description": "ok"}}}},
        }
    }
    assert check_openapi(openapi) == []


def test_rule_a_flags_route_without_auth() -> None:
    openapi = {
        "components": SCHEMES,
        "paths": {
            "/v1/learners": {
                "post": _operation(security=None, responses={"422": _envelope_response()})
            }
        },
    }
    findings = check_openapi(openapi)
    assert [f.rule for f in findings] == ["a:auth-required"]
    assert "/v1/learners" in str(findings[0])


def test_rule_b_flags_missing_error_response() -> None:
    openapi = {
        "components": SCHEMES,
        "paths": {
            "/v1/learners": {
                "post": _operation(security=[{"APIKeyAuth": ["observe"]}], responses={"200": {}})
            }
        },
    }
    findings = check_openapi(openapi)
    assert [f.rule for f in findings] == ["b:envelope-documented"]


def test_rule_b_flags_framework_validation_schema() -> None:
    """FastAPI's default 422 is HTTPValidationError, not the envelope."""
    openapi = {
        "components": SCHEMES,
        "paths": {
            "/v1/learners": {
                "post": _operation(
                    security=[{"APIKeyAuth": ["observe"]}],
                    responses={
                        "422": {
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/HTTPValidationError"}
                                }
                            }
                        }
                    },
                )
            }
        },
    }
    findings = check_openapi(openapi)
    assert [f.rule for f in findings] == ["b:envelope-documented"]
    assert "HTTPValidationError" in str(findings[0])


def test_rule_a_accepts_a_requirement_list_meaning_any_of() -> None:
    """Several requirement objects are OR: "any of these scopes" is valid."""
    openapi = {
        "components": SCHEMES,
        "paths": {
            "/v1/whoami": {
                "get": _operation(
                    security=[
                        {"APIKeyAuth": ["observe"]},
                        {"APIKeyAuth": ["admin"]},
                    ],
                    responses={"422": _envelope_response()},
                )
            }
        },
    }
    assert check_openapi(openapi) == []


def test_rule_a_flags_a_scope_less_requirement() -> None:
    openapi = {
        "components": SCHEMES,
        "paths": {
            "/v1/learners": {
                "post": _operation(
                    security=[{"APIKeyAuth": []}], responses={"422": _envelope_response()}
                )
            }
        },
    }
    findings = check_openapi(openapi)
    assert [f.rule for f in findings] == ["a:auth-required"]
    assert "names no scope" in str(findings[0])


def test_rule_a_flags_an_undeclared_scheme() -> None:
    openapi = {
        "components": SCHEMES,
        "paths": {
            "/v1/learners": {
                "post": _operation(
                    security=[{"Nope": ["observe"]}], responses={"422": _envelope_response()}
                )
            }
        },
    }
    findings = check_openapi(openapi)
    assert [f.rule for f in findings] == ["a:auth-required"]
    assert "undeclared security scheme" in str(findings[0])


def test_rule_c_flags_undocumented_code() -> None:
    findings = check_emitted_codes(
        frozenset({"internal", "invented_code"}), frozenset({"internal"})
    )
    assert [f.rule for f in findings] == ["c:code-documented"]
    assert "invented_code" in str(findings[0])


def test_rule_c_has_no_exemptions() -> None:
    """The doc is the only allowlist — including for our own sentinel."""
    findings = check_emitted_codes(
        frozenset({"internal", "unmapped_status"}), frozenset({"internal"})
    )
    assert [f.rule for f in findings] == ["c:code-documented"]
    assert "unmapped_status" in str(findings[0])


def test_entrypoint_passes_against_the_current_doc() -> None:
    """The sentinel and the age-gate code are documented, so rule (c) is green."""
    assert main([]) == 0


def test_the_guard_never_patches_the_contract() -> None:
    """The document is authoritative as written — AGENT_M0 §6.

    A guard that applied ``docs-patches/*.diff`` before comparing could be made
    to pass by the implementation it is supposed to police: write the patch,
    patch the table, rule (c) satisfied. So there is no patch step at all, and
    the tooling that materialised one is gone.
    """
    source = Path(guard.__file__).read_text(encoding="utf-8")
    assert "apply_patches" not in source
    assert not hasattr(guard, "_apply_patches")
    scripts_dir = Path(guard.__file__).resolve().parent
    assert not (scripts_dir / "docs_with_patches.py").exists()
    assert "minor_not_supported" not in source


def test_entrypoint_fails_when_the_age_gate_code_leaves_the_doc(tmp_path: Path) -> None:
    """Red for the rule above: drop the row and the guard refuses to pass.

    This is the code we added in increment 6. It is green only because the
    document's owner documented it; delete the row from a copy and the guard
    exits 1, which is what would have happened if we had shipped it alone.
    """
    text = _doc()
    row = next(
        line
        for line in text.splitlines()
        if line.startswith("| 422 |") and "minor_not_supported" in line
    )
    doc = tmp_path / "INTEGRATION.md"
    doc.write_text(text.replace(row + "\n", ""), encoding="utf-8")
    assert main(["--doc", str(doc)]) == 1


def test_entrypoint_fails_when_the_sentinel_row_is_removed(tmp_path: Path) -> None:
    """The rule is absolute: delete the row and the guard fails again.

    This is the follow-up-2 proof — no whitelist, no exemption, only the doc.
    """
    text = _doc()
    sentinel_line = next(line for line in text.splitlines() if "`unmapped_status`" in line)
    doc = tmp_path / "INTEGRATION.md"
    doc.write_text(text.replace(sentinel_line + "\n", ""), encoding="utf-8")
    assert main(["--doc", str(doc)]) == 1


def test_entrypoint_fails_when_table_and_code_disagree(tmp_path: Path) -> None:
    """Drop a code from a copy of the table: the guard must exit 1."""
    doc = tmp_path / "INTEGRATION.md"
    doc.write_text(
        _doc().replace("| 429 | `rate_limited` | yes", "| 429 | `rate_limited_old` | yes"),
        encoding="utf-8",
    )
    assert main(["--doc", str(doc)]) == 1


def test_entrypoint_errors_when_doc_missing(tmp_path: Path) -> None:
    assert main(["--doc", str(tmp_path / "absent.md")]) == 2


def test_missing_doc_raises_doc_error(tmp_path: Path) -> None:
    with pytest.raises(DocError):
        load_doc_text(str(tmp_path / "absent.md"))
