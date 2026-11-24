"""Start a dev-only PostgreSQL for the test suite, with no Docker.

The bundled server comes from the ``dev`` extra (``pgserver``); it is never a
runtime dependency and never used by the image. It is one provider among
several: the suite itself only reads ``ADAPT_TEST_DATABASE_URL``.

    python scripts/dev_postgres.py            # start, print the URL, wait

Leave it running and point the suite at it in another shell:

    export ADAPT_TEST_DATABASE_URL='postgresql://postgres:@/postgres?host=/tmp/adapt_pg_dev'
    make test

The suite can also start this provider itself when the variable is unset
(``tests/pg.py``), which is why ``make test`` works with no setup at all. This
script exists for the cases where you want the server to outlive the test run,
e.g. to inspect the database afterwards.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-dir",
        default=os.environ.get("ADAPT_TEST_PG_DIR", "/tmp/adapt_pg_dev"),
        help="data directory for the bundled server",
    )
    args = parser.parse_args()

    try:
        # pgserver ships no type stubs; it is a dev-only convenience.
        import pgserver
    except ImportError:
        print(
            "error: pgserver is not installed. It is a dev-only dependency: "
            "pip install -e '.[dev]'",
            file=sys.stderr,
        )
        return 1

    server = pgserver.get_server(args.data_dir)  # type: ignore[attr-defined]
    url = str(server.get_uri())
    print(url, flush=True)

    print(
        "bundled PostgreSQL is running (dev only). Ctrl-C to stop.\n"
        f"export ADAPT_TEST_DATABASE_URL='{url}'",
        file=sys.stderr,
    )

    stopping = False

    def _stop(signum: int, frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    while not stopping:
        time.sleep(0.2)
    cleanup = getattr(server, "cleanup", None)
    if callable(cleanup):
        cleanup()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
