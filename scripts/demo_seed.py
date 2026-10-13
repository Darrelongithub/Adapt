#!/usr/bin/env python3
"""Create labelled pilot/demo learners with realistic synthetic histories.

Usage:
    python scripts/demo_seed.py [--count N] [--reset]

Each learner is clearly marked as synthetic in the UI (display name contains
'(demo, synthetic)'). Their histories are deterministic (seeded with 42) so
the contrast demo and evidence screens always show the same shapes.

The learners' attempts are flagged as pilot data, so every number derived
from them carries the pilot label.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Add the backend to the path
backend_src = Path(__file__).parent.parent / "backend" / "src"
sys.path.insert(0, str(backend_src))


def main():
    parser = argparse.ArgumentParser(
        description="Create labelled demo learners with synthetic histories"
    )
    parser.add_argument(
        "--count",
        type=int,
        default=3,
        help="number of demo learners to create (max 5)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="remove existing demo-seeded learners first",
    )
    args = parser.parse_args()

    from adapt.config import get_settings
    from adapt.db.engine import Database
    from adapt.db.init_db import init

    st = get_settings()
    db = Database(st)
    init(st, seed_demo=False, db=db)

    from adapt.cli import demo_seed

    class FakeSvcs:
        db = db

    result = demo_seed(FakeSvcs(), count=args.count, reset=args.reset)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
