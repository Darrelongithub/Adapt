#!/usr/bin/env python3
"""Rotate the master key. Re-wrap data keys; retire the old key when nothing needs it.

The lifecycle, in the order an operator runs it:

    # 0. what does the cluster actually reference today?
    python scripts/rotate_master_key.py status

    # 1. start the service with both keys: the new one current, the old one kept
    export ADAPT_MASTER_KEY=<new>          ADAPT_MASTER_KEY_ID=k2
    export ADAPT_MASTER_KEY_PREVIOUS=<old> ADAPT_MASTER_KEY_PREVIOUS_ID=k1

    # 2. re-wrap every tenant's data key under k2 (idempotent, touches no row)
    python scripts/rotate_master_key.py rewrap

    # 3. is k1 still referenced? (this is the query a change review asks for)
    python scripts/rotate_master_key.py preflight k1

    # 4. only when that answers 0: drop ADAPT_MASTER_KEY_PREVIOUS and restart

It runs as the **owner**, on the same credential migrations use
(`ADAPT_MIGRATION_DATABASE_URL`), because the runtime role cannot write a data
key row — that is the point of migration 0008: a role that serves requests must
not be able to register a master key id the startup check then trusts.

Nothing here rewrites learner data. `rewrap` updates one column on one row per
tenant; the ciphertext of every value stays exactly as written.
"""

from __future__ import annotations

import argparse
import os
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from adapt_engine.config import Settings
from adapt_engine.crypto.keyring import Keyring, UnknownKeyId
from adapt_engine.crypto.rotate import assert_retirable, preflight, referenced_kids, rewrap_all
from adapt_engine.errors import ConfigError


def _owner_engine() -> Engine:
    url = os.environ.get("ADAPT_MIGRATION_DATABASE_URL")
    if not url:
        print(
            "error: ADAPT_MIGRATION_DATABASE_URL is not set; rotation runs as the owner",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return create_engine(url, future=True)


def _keyring() -> Keyring:
    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        print(f"error: {exc.variable}: {exc.detail}", file=sys.stderr)
        raise SystemExit(1) from exc
    return Keyring.from_settings(settings)


def _status(owner: Engine) -> int:
    with owner.connect() as conn:
        referenced = referenced_kids(conn)
        catalogue = conn.execute(text("SELECT kid FROM data_key_ids ORDER BY kid")).scalars().all()
    print("kid | data key rows referencing it")
    print("-" * 40)
    for kid, count in referenced.items():
        print(f"{kid} | {count}")
    stale = [kid for kid in catalogue if kid not in referenced]
    print(f"\ncatalogue: {', '.join(catalogue) if catalogue else '(empty)'}")
    if stale:
        print(f"unreferenced entries: {', '.join(stale)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Master-key rotation for the Adapt engine")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="what each kid is still referenced by")
    sub.add_parser("rewrap", help="re-wrap every tenant data key under the current key")
    pre = sub.add_parser("preflight", help="how many data key rows still name a kid")
    pre.add_argument("kid")
    retire = sub.add_parser("retire", help="assert a kid is unreferenced, then say what to do")
    retire.add_argument("kid")
    args = parser.parse_args(argv)

    owner = _owner_engine()
    try:
        if args.command == "status":
            return _status(owner)

        if args.command == "rewrap":
            keyring = _keyring()
            results = rewrap_all(owner, keyring)
            changed = [result for result in results if result.changed]
            print(
                f"re-wrapped {len(changed)} of {len(results)} tenant data key(s) "
                f"to {keyring.current!r}"
            )
            for result in results:
                if not result.changed:
                    continue
                print(f"  {result.tenant_id}: {result.old_kid} -> {result.new_kid}")
            print("\nnext: python scripts/rotate_master_key.py preflight <old kid>")
            return 0

        if args.command == "preflight":
            count = preflight(owner, args.kid)
            print(f"{args.kid}: {count} data key row(s) still reference it")
            if count:
                print("do not remove this key yet: re-wrap first (`rewrap`)")
                return 1
            print("safe to remove from the keyring: nothing references it")
            return 0

        # retire
        keyring = _keyring()
        try:
            assert_retirable(owner, args.kid, keyring)
        except UnknownKeyId as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 1
        print(
            f"{args.kid} is unreferenced. Remove ADAPT_MASTER_KEY_PREVIOUS* and restart; "
            "every value written under it still reads, because the value headers carry a "
            "data key version, not a master key id."
        )
        return 0
    finally:
        owner.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
