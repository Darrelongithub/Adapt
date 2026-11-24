"""per-tenant data keys, and encrypted learner columns

Revision ID: 0007_encryption
Revises: 0006_credits_identity
Create Date: 2026-11-17

Two changes, one idea: the database holds what only the application can read.

``tenant_data_keys`` stores one wrapped AES-256 data key per tenant. The
wrapper is a ``v1`` envelope (``v1:<kid>:<nonce>:<ct>``) whose key is the
master key named by ``kid`` and whose AAD is the tenant id, so a wrapped key
moved to another tenant's row does not unwrap.

``learners.profile`` and ``learners.date_of_birth`` become ciphertext:

* ``profile`` keeps its type (``text``) but loses the two-literal CHECK. A
  constraint cannot read a value it must not see, so the allowlist moves to
  ``db/schema.PROFILES``, checked in Python before encryption. The CHECK that
  remains asserts the *shape*: the column holds a v1 envelope.
* ``date_of_birth`` changes from ``date`` to ``text`` for the same reason: an
  ISO date is a personal datum and a typed column is plaintext by definition.
  The value is validated as a date before encryption and parsed back after
  decryption.

Point-in-time honesty: at this revision no deployment holds learner rows
(the table was created in 0001 and is only written by the API added in
increment 2), so the type change discards nothing. A deployment that *did*
hold rows would need a backfill migration instead, and this migration's
downgrade would not be able to restore ``date`` values from ciphertext — which
is why the downgrade drops the column rather than pretending.
"""

from __future__ import annotations

from alembic import op

revision = "0007_encryption"
down_revision = "0006_credits_identity"
branch_labels = None
depends_on = None

#: What a stored envelope looks like: version, kid, 12-byte nonce, ciphertext.
ENVELOPE = r"^v1:[a-z0-9]{1,16}:[A-Za-z0-9_-]{16}:[A-Za-z0-9_-]{16,}$"

TENANT_PREDICATE = "tenant_id = nullif(current_setting('adapt.tenant_id', true), '')::uuid"


def upgrade() -> None:
    _data_keys()
    _encrypted_learner_columns()
    _data_key_id_catalogue()


def _data_keys() -> None:
    op.execute(
        """
        CREATE TABLE tenant_data_keys (
            tenant_id   uuid PRIMARY KEY REFERENCES tenants (id) ON DELETE CASCADE,
            kid         text NOT NULL,
            wrapped     text NOT NULL,
            created_at  timestamptz NOT NULL DEFAULT now(),
            rotated_at  timestamptz,
            CONSTRAINT tenant_data_keys_kid_format CHECK (kid ~ '^[a-z0-9]{1,16}$'),
            CONSTRAINT tenant_data_keys_wrapped_is_envelope CHECK (wrapped ~ '^v1:')
        )
        """
    )
    op.execute("ALTER TABLE tenant_data_keys ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenant_data_keys FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON tenant_data_keys
            AS PERMISSIVE
            FOR ALL
            TO adapt_app
            USING ({TENANT_PREDICATE})
            WITH CHECK ({TENANT_PREDICATE})
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON tenant_data_keys TO adapt_app")



def _data_key_id_catalogue() -> None:
    """Every master key id that has ever wrapped a data key.

    Startup asks one question — "does my keyring cover the kids in use?" — and
    the answer has to span tenants. It cannot be read from `tenant_data_keys`:
    that table is FORCEd RLS, so the owner sees zero rows without a tenant
    context, and a SECURITY DEFINER function with `row_security = off` is
    refused for exactly the same reason. Rather than weaken the isolation of a
    table that holds key material, the ids are catalogued here.

    This table has no tenant column because it holds no tenant data: a kid is
    already printed in the header of every ciphertext it wrote, so it is the
    one part of the scheme that is not a secret. It is append-only by grant
    (``adapt_app`` gets SELECT and INSERT, never UPDATE or DELETE): a id that
    was used once stays in the catalogue forever, which is what makes "drop
    k1 after rotation" a startup failure rather than a data-loss event on the
    first read of an old row.
    """
    op.execute(
        """
        CREATE TABLE data_key_ids (
            kid         text PRIMARY KEY,
            first_seen  timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT data_key_ids_kid_format CHECK (kid ~ '^[a-z0-9]{1,16}$')
        )
        """
    )
    op.execute("GRANT SELECT, INSERT ON data_key_ids TO adapt_app")


def _encrypted_learner_columns() -> None:
    op.execute("ALTER TABLE learners DROP CONSTRAINT IF EXISTS learners_profile_check")
    op.execute(
        f"""
        ALTER TABLE learners
            ADD CONSTRAINT learners_profile_is_envelope CHECK (profile ~ '{ENVELOPE}')
        """
    )
    # date -> text. USING NULL is deliberate and documented above: there is no
    # plaintext date that could be preserved as ciphertext here, and inventing
    # one would need an application key inside a migration.
    op.execute(
        "ALTER TABLE learners ALTER COLUMN date_of_birth TYPE text USING NULL::text"
    )
    op.execute(
        f"""
        ALTER TABLE learners
            ADD CONSTRAINT learners_date_of_birth_is_envelope
            CHECK (date_of_birth IS NULL OR date_of_birth ~ '{ENVELOPE}')
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE learners DROP CONSTRAINT IF EXISTS learners_date_of_birth_is_envelope")
    op.execute(
        "ALTER TABLE learners ALTER COLUMN date_of_birth TYPE date USING NULL::date"
    )
    op.execute("ALTER TABLE learners DROP CONSTRAINT IF EXISTS learners_profile_is_envelope")
    op.execute(
        """
        ALTER TABLE learners
            ADD CONSTRAINT learners_profile_check
            CHECK (profile IN ('adult_general', 'cognitive_support'))
        """
    )
    op.execute("DROP TABLE IF EXISTS data_key_ids")
    op.execute("DROP TABLE IF EXISTS tenant_data_keys")
