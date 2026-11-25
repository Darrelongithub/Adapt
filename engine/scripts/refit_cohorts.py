"""Refit skill priors from each tenant's recent cohort, and refuse thin ones.

Run nightly, as the owner role:

    python -m scripts.refit_cohorts --database-url postgresql://...

For every tenant and skill with new evidence in the window, this groups the
recent events by learner (in ``ts`` order, ties broken by ``id`` so the
sequences are a function of the data), and hands the cohort to
``mastery.refit.fit``. A cohort with fewer than 20 learners or fewer than 200 new
observations is **refused**: the previous prior version keeps serving, and the
refusal is printed with its counts. A refusal is a result, not an error — the
script exits 0 when every cohort was either fitted or honestly refused, and 2
only when it could not run at all.

Two rules the SQL enforces rather than the prose:

* **Only real answers count.** Events flagged ``repeat`` are excluded: the same
  item answered twice measures the item, not the learner.
* **Nothing is fitted per learner.** The grouping is by (tenant, skill); a
  learner's sequence is an input, never a cohort of one.
"""

from __future__ import annotations

import argparse
import sys
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

from adapt_engine.mastery import refit

WINDOW_DAYS = 7


def _sequences(
    conn: Connection, *, tenant_id: uuid.UUID, skill: str, since: datetime
) -> dict[str, list[bool]]:
    rows = conn.execute(
        text(
            """
            SELECT learner_id, correct
              FROM learning_events
             WHERE tenant_id = :tenant_id
               AND skill = :skill
               AND ts >= :since
               AND NOT (flags @> ARRAY['repeat']::text[])
             ORDER BY learner_id, ts, id
            """
        ),
        {"tenant_id": tenant_id, "skill": skill, "since": since},
    ).all()
    grouped: dict[str, list[bool]] = defaultdict(list)
    for row in rows:
        grouped[str(row.learner_id)].append(bool(row.correct))
    return grouped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--window-days", type=int, default=WINDOW_DAYS)
    parser.add_argument("--tenant", default=None, help="one tenant id (default: all)")
    parser.add_argument("--now", default=None, help="ISO timestamp, for a fixed run")
    args = parser.parse_args(argv)

    from adapt_engine.config import Settings

    url = args.database_url or Settings.from_env().database_url
    engine = create_engine(url, future=True)
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(UTC)
    since = now - timedelta(days=args.window_days)

    fitted = refused = 0
    with engine.begin() as conn:
        params: dict[str, object] = {"since": since}
        where = ""
        if args.tenant:
            where = " AND tenant_id = :tenant_id"
            params["tenant_id"] = uuid.UUID(args.tenant)
        cohorts = conn.execute(
            text(
                f"""
                SELECT DISTINCT tenant_id, skill
                  FROM learning_events
                 WHERE ts >= :since{where}
                """
            ),
            params,
        ).all()

        for cohort in cohorts:
            tenant_id = uuid.UUID(str(cohort.tenant_id))
            skill = str(cohort.skill)
            grouped = _sequences(conn, tenant_id=tenant_id, skill=skill, since=since)
            result = refit.refit_cohort(
                conn,
                tenant_id=tenant_id,
                skill=skill,
                band=f"{args.window_days}d",
                sequences=list(grouped.values()),
                now=now,
            )
            if isinstance(result, refit.Refusal):
                refused += 1
                print(
                    f"refused tenant={tenant_id} skill={skill} "
                    f"reason={result.reason} learners={result.learners} "
                    f"observations={result.observations}"
                )
            else:
                fitted += 1
                print(
                    f"fitted  tenant={tenant_id} skill={skill} "
                    f"learners={result.learners} observations={result.observations} "
                    f"loglik={result.log_likelihood:.2f} "
                    f"p_init={result.params.p_init} p_guess={result.params.p_guess} "
                    f"p_slip={result.params.p_slip}"
                )
    print(f"cohorts fitted={fitted} refused={refused}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
