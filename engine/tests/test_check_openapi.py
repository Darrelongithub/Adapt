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

from scripts.check_openapi import (
    DocError,
    check_emitted_codes,
    check_openapi,
    documented_codes,
    load_doc_text,
    main,
)

ENVELOPE_REF = "#/components/schemas/ErrorEnvelope"


def _doc() -> str:
    text, _ = load_doc_text(None)
    return text


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
    # The 200 row has no code and must not be parsed as one.
    assert "—" not in codes


def test_compliant_route_passes() -> None:
    openapi = {
        "paths": {
            "/v1/recommendations": {
                "post": _operation(
                    security=[{"ApiKeyAuth": []}],
                    responses={"200": {"description": "ok"}, "422": _envelope_response()},
                )
            }
        }
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
        "paths": {
            "/v1/learners": {
                "post": _operation(security=None, responses={"422": _envelope_response()})
            }
        }
    }
    findings = check_openapi(openapi)
    assert [f.rule for f in findings] == ["a:auth-required"]
    assert "/v1/learners" in str(findings[0])


def test_rule_b_flags_missing_error_response() -> None:
    openapi = {
        "paths": {
            "/v1/learners": {
                "post": _operation(security=[{"ApiKeyAuth": []}], responses={"200": {}})
            }
        }
    }
    findings = check_openapi(openapi)
    assert [f.rule for f in findings] == ["b:envelope-documented"]


def test_rule_b_flags_framework_validation_schema() -> None:
    """FastAPI's default 422 is HTTPValidationError, not the envelope."""
    openapi = {
        "paths": {
            "/v1/learners": {
                "post": _operation(
                    security=[{"ApiKeyAuth": []}],
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
        }
    }
    findings = check_openapi(openapi)
    assert [f.rule for f in findings] == ["b:envelope-documented"]
    assert "HTTPValidationError" in str(findings[0])


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


def test_entrypoint_reports_only_the_documented_gap() -> None:
    """At dc3195b the only missing row is the unmapped_status sentinel.

    This is a documentation gap, recorded in KNOWN_GAPS.md §5. The moment the
    row is added to §6 this test must be updated to expect 0 — the guard is
    not allowed to grow an exemption instead.
    """
    assert main([]) == 1


def test_entrypoint_passes_once_the_missing_row_is_documented(tmp_path: Path) -> None:
    """Adding the documented row is the whole fix; no code change needed."""
    doc = tmp_path / "INTEGRATION.md"
    row = (
        "| 405 | `unmapped_status` | no | the status has no documented code; "
        "a warning was logged |\n"
    )
    text = _doc()
    marker = "| 406 | `instrument_not_renderable` |"
    assert marker in text
    doc.write_text(text.replace(marker, row + marker), encoding="utf-8")
    assert main(["--doc", str(doc)]) == 0


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
