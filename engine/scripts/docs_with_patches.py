#!/usr/bin/env python3
"""Materialise `docs/v2/INTEGRATION.md` with this repo's corrections applied.

The contract doc lives in another tree (the v1 repository) and we do not edit
it — we ship diffs in ``docs-patches/``. But the OpenAPI checker has to read the
*corrected* contract, otherwise it either fails on a code we deliberately added
or, worse, passes on a table that is missing one.

So: read the doc from a git ref or a path, apply every patch in
``docs-patches/`` in filename order, write the result, print its path.

    python scripts/docs_with_patches.py                    # prints a path
    python scripts/docs_with_patches.py --out /tmp/doc.md
    python scripts/check_openapi.py --doc "$(python scripts/docs_with_patches.py)"

Exit status is 2 when the doc or `patch` cannot be reached, matching the
checker's "cannot resolve the doc" convention.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ENGINE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ENGINE_ROOT.parent
PATCHES = ENGINE_ROOT / "docs-patches"
DEFAULT_REFS = (
    "origin/arena/cb97052d-adapt",
    "arena/cb97052d-adapt",
    "d17ae74",
)


def _read_doc(ref: str | None, path: str | None) -> str:
    if path:
        return Path(path).read_text(encoding="utf-8")
    refs = (ref,) if ref else DEFAULT_REFS
    for candidate in refs:
        proc = subprocess.run(
            ["git", "show", f"{candidate}:docs/v2/INTEGRATION.md"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return proc.stdout
    print(
        "error: could not read docs/v2/INTEGRATION.md from any of "
        f"{', '.join(r for r in refs if r)}; pass --ref or --path",
        file=sys.stderr,
    )
    raise SystemExit(2)


def apply_patches(text: str, *, patches: list[Path] | None = None) -> str:
    """Return ``text`` with every ``docs-patches/*.diff`` applied, in order.

    Raises :class:`RuntimeError` when a patch does not apply: a correction that
    silently stopped matching the doc is exactly the failure this is meant to
    prevent.
    """
    selected = patches if patches is not None else sorted(PATCHES.glob("*.diff"))
    if not selected:
        return text
    workdir = Path(tempfile.mkdtemp(prefix="adapt-doc-"))
    try:
        target = workdir / "docs" / "v2" / "INTEGRATION.md"
        target.parent.mkdir(parents=True)
        target.write_text(text, encoding="utf-8")
        for patch in selected:
            proc = subprocess.run(
                ["patch", "-p1", "--forward", "--batch", "-i", str(patch)],
                cwd=str(workdir),
                capture_output=True,
                text=True,
                check=False,
            )
            if proc.returncode != 0:
                raise RuntimeError(
                    f"{patch.name} did not apply to docs/v2/INTEGRATION.md: "
                    f"{proc.stdout.strip()}{proc.stderr.strip()}"
                )
        return target.read_text(encoding="utf-8")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ref", default=os.environ.get("ADAPT_DOCS_REF"))
    parser.add_argument("--path", default=None)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)

    text = _read_doc(args.ref, args.path)
    try:
        result = apply_patches(text)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    out = Path(args.out) if args.out else Path("/tmp") / "adapt-INTEGRATION-patched.md"
    out.write_text(result, encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
