#!/usr/bin/env python3
"""Generate the thin SDKs from the engine's OpenAPI document.

The clients are generated rather than hand-written for one reason: an SDK that
drifts from the contract is worse than no SDK, and the only way to know it has
not drifted is to make regenerating it a one-line, reviewable diff. `tests/test_sdk.py`
regenerates into a temporary directory and fails if the committed files differ.

The clients are deliberately **thin**: one `request()` and one method per
operation, request and response bodies as dictionaries, no models, no retries
beyond `Idempotency-Key`, no cleverness. The interesting part of the contract —
`needs.trials`, an abstention carrying no `value`, the excluded counts — is data,
and a generated client's job is to get out of its way.

    python sdk/generate.py           # rewrite the committed clients
    python sdk/generate.py --check   # fail if they are stale
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / "engine"
SDK = ROOT / "sdk"

sys.path[:0] = [str(ENGINE), str(ENGINE / "src")]

from scripts.check_openapi import build_openapi

#: Operations the SDK exposes. Anything not listed is a control-plane route a
#: host should not call with a runtime key (INTEGRATION §6A); the generator says
#: so out loud rather than quietly dropping it.
CONTROL_PLANE_PREFIXES = ("/v1/admin/",)
SKIP_PATHS = {"/v1/health", "/v1/version"}

#: Names a person would write. The generated `operationId`s are framework-shaped
#: (`decide_v1_decide_post`), and an SDK whose methods read like routes is an SDK
#: nobody uses twice.
NAMES = {
    "decide_v1_decide_post": "decide",
    "observe_v1_observe_post": "observe",
    "replay_v1_decisions__decision_id__replay_post": "replay",
    "mastery_snapshot_v1_mastery_get": "mastery",
    "mastery_for_skill_v1_mastery__skill__get": "mastery_for",
    "get_balance_v1_credits_balance_get": "credits_balance",
    "list_learners_v1_learners_get": "learners",
    "get_learner_v1_learners__learner_id__get": "learner",
    "create_learner_v1_learners_post": "create_learner",
    "whoami_v1_whoami_get": "whoami",
}

#: The engine's OpenAPI document describes **no request bodies**: the routes read
#: JSON themselves (allowlisted keys, a manual 422), so there is no schema to
#: generate from. Rather than pretend, the bodies are declared here, once, from
#: `INTEGRATION.md` §6A — and `KNOWN_GAPS` §31 records that M2 must move to
#: pydantic request models so this table can go away.
BODIES = {
    "decide": {
        "fields": [("learner_id", "str"), ("skill", "str")],
        "example": {"learner_id": "learn-0001", "skill": "skill.fractions"},
    },
    "observe": {
        "fields": [("events", "list[dict[str, Any]]")],
        "example": {
            "events": [
                {
                    "learner_id": "learn-0001",
                    "skill": "skill.fractions",
                    "item_id": "item-014",
                    "correct": True,
                    "response": "option-2",
                    "ts": "2026-11-18T12:00:00Z",
                    "seq": 0,
                }
            ]
        },
    },
    "create_learner": {
        "fields": [("learner_id", "str"), ("date_of_birth", "str")],
        "example": {"learner_id": "learn-0001", "date_of_birth": "1994-03-02"},
    },
}


def operations(document: dict[str, Any]) -> list[dict[str, Any]]:
    found = []
    for path, item in sorted(document["paths"].items()):
        if path in SKIP_PATHS or path.startswith(CONTROL_PLANE_PREFIXES):
            continue
        for method, operation in sorted(item.items()):
            if method not in {"get", "post", "patch", "delete"}:
                continue
            found.append(
                {
                    "path": path,
                    "method": method.upper(),
                    "name": NAMES.get(operation.get("operationId", ""))
                    or _name(path, method),
                    "summary": (
                        operation.get("summary") or operation.get("description") or ""
                    ).strip(),
                    "params": [
                        parameter["name"]
                        for parameter in operation.get("parameters", [])
                        if parameter.get("in") == "path"
                    ],
                    "query": [
                        parameter["name"]
                        for parameter in operation.get("parameters", [])
                        if parameter.get("in") == "query" and parameter.get("required")
                    ],
                    "body": BODIES.get(NAMES.get(operation.get("operationId", ""), "")),
                }
            )
    return found


def _name(path: str, method: str) -> str:
    parts = [
        part
        for part in path.replace("/v1/", "").split("/")
        if part and not part.startswith("{")
    ]
    return method + "_" + "_".join(p.strip("{}") for p in parts)


def _first_line(summary: str, fallback: str) -> str:
    line = (summary.splitlines() or [fallback])[0].strip()
    return line or fallback


PY_HEADER = '''"""Adapt Python SDK — generated from the engine's OpenAPI document.

Do not edit by hand: `python sdk/generate.py` rewrites this file, and
`tests/test_sdk.py` fails when the committed copy is stale.

    from adapt_sdk import Client

    client = Client("https://api.example.com", os.environ["ADAPT_KEY"])
    decision = client.decide("learn-0001", "skill.fractions")
    if decision["abstain"]:
        wait_for(decision["needs"]["trials"])
    else:
        run(decision["recommendations"][0])

Standard library only: `urllib`, no dependencies, so "install" is one file on the
path and the first call is the host's own code, not our packaging.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
import uuid
from typing import Any


class AdaptError(Exception):
    """The contract's error envelope, raised."""

    def __init__(self, status: int, body: dict[str, Any]) -> None:
        error = body.get("error") or {}
        self.status = status
        self.code = error.get("code")
        self.message = error.get("message")
        self.request_id = error.get("request_id")
        self.retryable = bool(error.get("retryable"))
        self.details = error.get("details") or {}
        super().__init__(f"{self.status} {self.code}: {self.message} (request {self.request_id})")


class Client:
    """One method per host-facing operation, and nothing else."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 30.0,
        user_agent: str = "adapt-python-sdk/generated",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.user_agent = user_agent

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        test_traffic: bool = False,
    ) -> Any:
        """One HTTP call. Every method below is a thin wrapper around this."""
        url = self.base_url + path
        if params:
            pairs = "&".join(f"{key}={urllib.parse.quote(str(value), safe='')}" for key, value in params.items())
            url = f"{url}?{pairs}"
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Authorization", f"Bearer {self.api_key}")
        request.add_header("Content-Type", "application/json")
        request.add_header("User-Agent", self.user_agent)
        if idempotency_key is None and method != "GET":
            # Derived from the logical operation: same call, same key, so a retry
            # after a timeout consumes one credit and writes one row. Hashed
            # because a batch body is far longer than the 200-character limit.
            canonical = json.dumps(body, sort_keys=True) if body is not None else ""
            digest = hashlib.sha256(f"{method} {path} {canonical}".encode()).hexdigest()[:32]
            idempotency_key = f"adapt-{digest}"
        if idempotency_key is not None:
            request.add_header("Idempotency-Key", idempotency_key)
        if test_traffic:
            request.add_header("X-Adapt-Test", "1")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = response.read()
                return json.loads(payload) if payload else None
        except urllib.error.HTTPError as error:
            payload = error.read()
            raise AdaptError(error.code, json.loads(payload) if payload else {}) from None

    @staticmethod
    def derive_key(*parts: object) -> str:
        """A stable `Idempotency-Key` from the logical operation.

        Derived from the operation, never from the attempt: a timeout and a retry
        must consume one credit and write one row (INTEGRATION §7). Hashed so the
        key fits the contract's 8-200 characters whatever the body size.
        """
        digest = hashlib.sha256("|".join(str(part) for part in parts).encode()).hexdigest()
        return f"adapt-{digest[:32]}"
'''

TS_HEADER = """/* Adapt TypeScript SDK — generated from the engine's OpenAPI document.
 *
 * Do not edit by hand: `python sdk/generate.py` rewrites this file, and
 * `tests/test_sdk.py` fails when the committed copy is stale.
 *
 *   const client = new Client("https://api.example.com", process.env.ADAPT_KEY!);
 *   const decision = await client.decide("learn-0001", "skill.fractions");
 *
 * Uses the platform `fetch`: no dependencies, no build step beyond `tsc`.
 */

export class AdaptError extends Error {
  constructor(
    readonly status: number,
    readonly code: string | null,
    message: string,
    readonly requestId: string | null,
    readonly retryable: boolean,
    readonly details: Record<string, unknown>,
  ) {
    super(`${status} ${code ?? "error"}: ${message}`);
    this.name = "AdaptError";
  }
}

export type Json = Record<string, unknown>;

export interface RequestOptions {
  body?: Json;
  params?: Record<string, string | number | boolean | undefined>;
  idempotencyKey?: string;
  testTraffic?: boolean;
}

export class Client {
  constructor(
    readonly baseUrl: string,
    readonly apiKey: string,
    readonly userAgent = "adapt-ts-sdk/generated",
  ) {}

  async request(method: string, path: string, options: RequestOptions = {}): Promise<any> {
    const url = new URL(this.baseUrl.replace(/\\/$/, "") + path);
    for (const [key, value] of Object.entries(options.params ?? {})) {
      if (value !== undefined) url.searchParams.set(key, String(value));
    }
    const headers: Record<string, string> = {
      Authorization: `Bearer ${this.apiKey}`,
      "Content-Type": "application/json",
      "User-Agent": this.userAgent,
    };
    const key = options.idempotencyKey ?? Client.deriveKey(method, path, JSON.stringify(options.body ?? null));
    headers["Idempotency-Key"] = key;
    if (options.testTraffic) headers["X-Adapt-Test"] = "1";
    const response = await fetch(url, {
      method,
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body),
    });
    const text = await response.text();
    const payload = text ? JSON.parse(text) : null;
    if (!response.ok) {
      const error = payload?.error ?? {};
      throw new AdaptError(
        response.status,
        error.code ?? null,
        error.message ?? response.statusText,
        error.request_id ?? null,
        Boolean(error.retryable),
        error.details ?? {},
      );
    }
    return payload;
  }

  /** A stable idempotency key from the logical operation, not the attempt.
   *
   * FNV-1a rather than a cryptographic hash because this has to be synchronous
   * and dependency-free: the key's job is to be *stable*, not secret. Pass your
   * own when you need a coarser logical operation than one request.
   */
  static deriveKey(...parts: Array<string | number>): string {
    let hash = 0x811c9dc5;
    for (const character of parts.map(String).join("|")) {
      hash ^= character.codePointAt(0)!;
      hash = Math.imul(hash, 0x01000193) >>> 0;
    }
    return `adapt-${hash.toString(16).padStart(8, "0")}${parts.length}`;
  }
}
"""


def python_method(operation: dict[str, Any]) -> str:
    name = _snake(operation["name"])
    path = operation["path"]
    interp = path
    for parameter in operation["params"]:
        interp = interp.replace("{" + parameter + "}", "{" + _snake(parameter) + "}")

    arguments = ["self"]
    arguments += [f"{_snake(parameter)}: str" for parameter in operation["params"]]
    arguments += [f"{_snake(parameter)}: str" for parameter in operation["query"]]
    if operation["body"]:
        arguments.append("body: dict[str, Any]")
    if operation["method"] != "GET":
        arguments.append("idempotency_key: str | None = None")

    doc = [_first_line(operation["summary"], operation["method"] + " " + path)]
    if operation["body"]:
        doc += [
            "",
            "Body (INTEGRATION §6A; the OpenAPI document declares no requestBody):",
            json.dumps(operation["body"]["example"]),
        ]

    lines = [
        f"    def {name}(",
        *(f"        {argument}," for argument in arguments),
        "    ) -> Any:",
    ]
    if len(doc) == 1:
        lines.append(f'        """{doc[0]}"""')
    else:
        lines += [
            '        """' + doc[0],
            *("        " + line if line else "" for line in doc[1:]),
            '        """',
        ]
    lines += [
        "        return self.request(",
        f'            "{operation["method"]}",',
        f'            {"f" if "{" in interp else ""}"{interp}",',
    ]
    if operation["query"]:
        params = ", ".join(
            f'"{parameter}": {_snake(parameter)}' for parameter in operation["query"]
        )
        lines.append(f"            params={{{params}}},")
    if operation["body"]:
        lines.append("            body=body,")
    if operation["method"] != "GET":
        lines.append("            idempotency_key=idempotency_key,")
    lines.append("        )")
    return "\n".join(lines)


def ts_method(operation: dict[str, Any]) -> str:
    name = _camel(operation["name"])
    path = operation["path"]
    args = [_snake(parameter) + ": string" for parameter in operation["params"]]
    args += [_snake(parameter) + ": string" for parameter in operation["query"]]
    if operation["body"]:
        args.append("body: Json")
    args.append('options: Omit<RequestOptions, "body" | "params"> = {}')

    interp = path
    for parameter in operation["params"]:
        interp = interp.replace("{" + parameter + "}", "${" + _snake(parameter) + "}")
    params = ", ".join(
        f"{parameter}: {_snake(parameter)}" for parameter in operation["query"]
    )
    body = [
        f'    return this.request("{operation["method"]}", `{interp}`, {{',
        f"      params: {{ {params} }}," if operation["query"] else "",
        "      body," if operation["body"] else "",
        "      ...options,",
        "    });",
    ]
    signature = ",\n    ".join(args)
    return "\n".join(
        [
            f"  /** {_first_line(operation['summary'], operation['method'] + ' ' + path)} */",
            f"  async {name}(",
            f"    {signature},",
            "  ): Promise<any> {",
            *[line for line in body if line],
            "  }",
        ]
    )


def _snake(name: str) -> str:
    out = []
    for index, character in enumerate(name):
        if character.isupper() and index:
            out.append("_")
        out.append(character.lower())
    return "".join(out).replace("-", "_")


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part.title() for part in rest)


def render_python(ops: list[dict[str, Any]]) -> str:
    body = "\n\n".join(python_method(operation) for operation in ops)
    return PY_HEADER + "\n" + body + "\n"


def render_typescript(ops: list[dict[str, Any]]) -> str:
    body = "\n\n".join(ts_method(operation) for operation in ops)
    return TS_HEADER + "\n" + body + "\n"


def write(path: Path, content: str, *, check: bool) -> bool:
    current = path.read_text() if path.exists() else None
    if current == content:
        return True
    if check:
        print(f"stale: {path}", file=sys.stderr)
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    print(f"wrote {path.relative_to(ROOT)}")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    ops = operations(build_openapi())
    ok = write(
        SDK / "python" / "adapt_sdk" / "client.py", render_python(ops), check=args.check
    )
    ok &= write(
        SDK / "typescript" / "src" / "client.ts",
        render_typescript(ops),
        check=args.check,
    )
    print(
        json.dumps({"operations": [operation["name"] for operation in ops]}, indent=2)
    )
    if not ok:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
