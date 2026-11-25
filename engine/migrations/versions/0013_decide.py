"""decide: the claim set, and the decisions that cite it

Revision ID: 0013_decide
Revises: 0012_mastery
Create Date: 2026-11-18

Two tables, and between them the mechanism `MEASUREMENT.md` §9 calls "record
before you serve":

* ``claims`` — the facts a recommendation may cite. Three columns carry the
  gate, not the prose: ``construct`` is a CHECK against the closed set the build
  has evidence for, ``does_not_apply_when`` is `NOT NULL` and non-empty (a claim
  without a boundary is a horoscope), and ``evidence_n >= evidence_floor`` makes
  an under-evidenced claim **refused at write time**, which is what G3 asks for
  — test the refusal, not the filter.

* ``decisions`` — one row per served or abstained decision, holding the body
  that was returned *and* the inputs it was computed from. That second column is
  the one that makes replay mean something: re-running a decision against
  today's evidence would produce a different answer for the honest reason that
  the learner has answered more items, so a replay that recomputes from live
  state would either fail constantly or prove nothing. ``inputs`` is the frozen
  snapshot, and replay folds *that*.

  ``(tenant_id, idempotency_key)`` is unique: a retried request returns the
  stored decision rather than a second one, and ``request_hash`` is what lets a
  replay with a *different* body be a `409 conflict` instead of being mistaken
  for a retry.

Both tables carry ``tenant_id`` and the tenant-isolation policy, so this
migration adds no exception to the RLS invariant. ``decisions.learner_id`` is
text, like ``learning_events.learner_id``: it is an opaque host identifier, and
the erasure path deletes these rows with it (`PRIVACY.md` §5, KNOWN_GAPS §18).
"""

from __future__ import annotations

from alembic import op

revision = "0013_decide"
down_revision = "0012_mastery"
branch_labels = None
depends_on = None

TENANT_PREDICATE = "tenant_id = nullif(current_setting('adapt.tenant_id', true), '')::uuid"

TABLES = ("claims", "decisions")


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE claims (
            tenant_id            uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
            id                   text NOT NULL,
            version              integer NOT NULL DEFAULT 1,
            construct            text NOT NULL,
            statement            text NOT NULL,
            certainty            text NOT NULL,
            applies_when         text[] NOT NULL,
            does_not_apply_when  text[] NOT NULL,
            evidence_n           integer NOT NULL,
            evidence_floor       integer NOT NULL,
            sources              jsonb NOT NULL,
            status               text NOT NULL DEFAULT 'active',
            created_at           timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, id),
            CONSTRAINT claims_id_shape CHECK (id ~ '^clm_[0-9]{4}$'),
            CONSTRAINT claims_version_positive CHECK (version >= 1),
            -- G3: a claim cannot name a construct nobody gated.
            CONSTRAINT claims_construct_gated CHECK (
                construct IN ('spacing', 'retrieval_practice', 'worked_examples',
                              'desirable_difficulty', 'interleaving')
            ),
            CONSTRAINT claims_certainty_known
                CHECK (certainty IN ('high', 'moderate', 'low', 'contested')),
            -- G3: a claim below its own evidence floor cannot be written at all.
            CONSTRAINT claims_evidence_clears_floor CHECK (evidence_n >= evidence_floor),
            CONSTRAINT claims_floor_positive CHECK (evidence_floor > 0),
            -- A boundary is mandatory, and an empty array is not a boundary.
            CONSTRAINT claims_boundary_stated CHECK (cardinality(does_not_apply_when) > 0),
            CONSTRAINT claims_applies_stated CHECK (cardinality(applies_when) > 0),
            CONSTRAINT claims_status_known CHECK (status IN ('active', 'superseded'))
        )
        """
    )
    op.execute(
        """
        CREATE TABLE decisions (
            tenant_id        uuid NOT NULL REFERENCES tenants (id) ON DELETE CASCADE,
            id               uuid PRIMARY KEY,
            connection_id    uuid REFERENCES connections (id) ON DELETE SET NULL,
            learner_id       text NOT NULL,
            skill            text NOT NULL,
            action           text NOT NULL,
            abstain          boolean NOT NULL,
            reason           text,
            request_hash     bytea NOT NULL,
            idempotency_key  text NOT NULL,
            inputs           jsonb NOT NULL,
            body             jsonb NOT NULL,
            issued_at        timestamptz NOT NULL,
            expires_at       timestamptz NOT NULL,
            reservation_id   uuid REFERENCES credit_reservations (id) ON DELETE SET NULL,
            CONSTRAINT decisions_once_per_key UNIQUE (tenant_id, idempotency_key),
            CONSTRAINT decisions_learner_id_length CHECK (length(learner_id) BETWEEN 8 AND 64),
            CONSTRAINT decisions_skill_shape CHECK (skill ~ '^[a-z][a-z0-9_.-]{0,63}$'),
            CONSTRAINT decisions_key_length CHECK (length(idempotency_key) BETWEEN 8 AND 200),
            CONSTRAINT decisions_action_known CHECK (
                action IN ('next_item_difficulty', 'spaced_review', 'worked_example', 'abstain')
            ),
            -- An abstention is an action, and it always names its reason; a
            -- served decision never does. Two states, no third.
            CONSTRAINT decisions_abstention_shape CHECK (
                (abstain AND action = 'abstain' AND reason IS NOT NULL)
                OR (NOT abstain AND action <> 'abstain' AND reason IS NULL)
            ),
            CONSTRAINT decisions_expiry_after_issue CHECK (expires_at > issued_at),
            CONSTRAINT decisions_served_is_charged CHECK (abstain OR reservation_id IS NOT NULL)
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

    op.execute(
        "CREATE INDEX decisions_learner_idx ON decisions (tenant_id, learner_id, issued_at DESC)"
    )
    op.execute("CREATE INDEX claims_construct_idx ON claims (tenant_id, construct)")

    op.execute("GRANT SELECT, INSERT, UPDATE ON claims TO adapt_app")
    op.execute("GRANT SELECT, INSERT, UPDATE ON decisions TO adapt_app")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"REVOKE ALL ON {table} FROM adapt_app")
    op.execute("DROP TABLE decisions")
    op.execute("DROP TABLE claims")
