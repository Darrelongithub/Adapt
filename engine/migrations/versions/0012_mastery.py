"""mastery: per-skill priors, learner state, and the cohort refit log

Revision ID: 0012_mastery
Revises: 0011_observe
Create Date: 2026-11-18

Three tables, each separating something the increment could otherwise conflate:

* ``skill_priors`` — the parameters the estimator runs on, **versioned and
  stored**, one row per (tenant, skill, version). AGENT_M1 §1.3 says the priors
  are "stored and versioned, in a table": a served estimate that cannot be
  reproduced from its stored parameters is a number with no provenance. Version
  1 of every skill is the documented default; a cohort refit writes version 2,
  never an UPDATE of version 1, so an estimate served last week can still be
  explained by the row that produced it.

  Priors are **tenant-scoped**, which is a modelling decision and not a
  formality: a cohort fit on one tenant's learners must not silently change the
  numbers another tenant is served. It also keeps Rule A intact — every table
  here carries ``tenant_id`` and the tenant-isolation policy, so this migration
  adds no fourth exception to the RLS invariant.

* ``learner_mastery`` — the fold of a learner's evidence: trials, correct, and
  the filter belief ``model_p``. The estimate is *not* stored: it is a function
  of (trials, correct, prior version), and storing it would give two sources of
  truth that drift the first time a prior is refit. The counters are what the
  estimator needs and what a golden file can pin.

* ``cohort_fits`` — one row per refit attempt that changed anything, with the
  observation count and the log-likelihood it achieved. "The nightly refit ran"
  and "the nightly refit fitted something" are different claims, and only the
  second one is checkable without this table.

Counters are integers, not floats: a float accumulator that adds 0.1 forty times
gives 3.9999999999999996 and a golden file that fails once a week.
"""

from __future__ import annotations

from alembic import op

revision = "0012_mastery"
down_revision = "0011_observe"
branch_labels = None
depends_on = None

TENANT_PREDICATE = "tenant_id = nullif(current_setting('adapt.tenant_id', true), '')::uuid"

TABLES = ("skill_priors", "learner_mastery", "cohort_fits")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE skill_priors (
            tenant_id     uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
            skill         text NOT NULL,
            version       integer NOT NULL,
            p_init        double precision NOT NULL,
            p_learn       double precision NOT NULL,
            p_guess       double precision NOT NULL,
            p_slip        double precision NOT NULL,
            source        text NOT NULL,
            observations  integer NOT NULL DEFAULT 0,
            fitted_at     timestamptz,
            created_at    timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, skill, version),
            CONSTRAINT skill_priors_skill_shape CHECK (skill ~ '^[a-z][a-z0-9_.-]{0,63}$'),
            CONSTRAINT skill_priors_version_positive CHECK (version >= 1),
            CONSTRAINT skill_priors_probabilities_in_range CHECK (
                p_init  > 0 AND p_init  < 1 AND
                p_learn > 0 AND p_learn < 1 AND
                p_guess > 0 AND p_guess < 1 AND
                p_slip  > 0 AND p_slip  < 1 AND
                p_guess + p_slip < 1
            ),
            CONSTRAINT skill_priors_source_known
                CHECK (source IN ('documented_default', 'cohort_fit')),
            CONSTRAINT skill_priors_observations_non_negative CHECK (observations >= 0)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE learner_mastery (
            tenant_id       uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
            learner_id      text NOT NULL,
            skill           text NOT NULL,
            prior_version   integer NOT NULL,
            trials          integer NOT NULL DEFAULT 0,
            correct         integer NOT NULL DEFAULT 0,
            model_p         double precision NOT NULL,
            updated_at      timestamptz NOT NULL,
            created_at      timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, learner_id, skill),
            CONSTRAINT learner_mastery_learner_id_length
                CHECK (length(learner_id) BETWEEN 8 AND 64),
            CONSTRAINT learner_mastery_skill_shape
                CHECK (skill ~ '^[a-z][a-z0-9_.-]{0,63}$'),
            CONSTRAINT learner_mastery_trials_non_negative CHECK (trials >= 0),
            CONSTRAINT learner_mastery_correct_bounded
                CHECK (correct >= 0 AND correct <= trials),
            CONSTRAINT learner_mastery_model_p_in_range
                CHECK (model_p > 0 AND model_p < 1),
            CONSTRAINT learner_mastery_prior_version_positive CHECK (prior_version >= 1)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE cohort_fits (
            id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
            skill           text NOT NULL,
            band            text NOT NULL,
            prior_version   integer NOT NULL,
            learners        integer NOT NULL,
            observations    integer NOT NULL,
            log_likelihood  double precision NOT NULL,
            fitted_at       timestamptz NOT NULL,
            CONSTRAINT cohort_fits_skill_shape CHECK (skill ~ '^[a-z][a-z0-9_.-]{0,63}$'),
            CONSTRAINT cohort_fits_band_shape CHECK (band ~ '^[a-z0-9_.:-]{1,32}$'),
            CONSTRAINT cohort_fits_learners_non_negative CHECK (learners >= 0),
            CONSTRAINT cohort_fits_observations_non_negative CHECK (observations >= 0),
            CONSTRAINT cohort_fits_once_per_version UNIQUE (tenant_id, skill, band, prior_version)
        )
        """
    )

    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation ON {table}
              USING      ({TENANT_PREDICATE})
              WITH CHECK ({TENANT_PREDICATE})
            """
        )
        op.execute(f"CREATE INDEX {table}_tenant_id_idx ON {table} (tenant_id)")

    op.execute("CREATE INDEX learner_mastery_stale_idx ON learner_mastery (tenant_id, updated_at)")

    op.execute("GRANT SELECT, INSERT, UPDATE ON skill_priors TO adapt_app")
    op.execute("GRANT SELECT, INSERT, UPDATE ON learner_mastery TO adapt_app")
    op.execute("GRANT SELECT, INSERT ON cohort_fits TO adapt_app")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"REVOKE ALL ON {table} FROM adapt_app")
    op.execute("DROP TABLE cohort_fits")
    op.execute("DROP TABLE learner_mastery")
    op.execute("DROP TABLE skill_priors")
