"""data key versions, row-bound values, and a catalogue the owner maintains

Revision ID: 0008_key_lifecycle
Revises: 0007_encryption
Create Date: 2026-11-17

Three corrections to increment 5, all of them about *who owns a key's
lifetime*.

**1. The value header stops naming the master key.** 0007 wrote
``v1:<kid>:<nonce>:<ct>`` where ``kid`` was the master key that wrapped the
tenant data key at write time. Re-wrapping does not rewrite rows, so every row
stayed pinned to the key it was born under: the retired key could never be
removed from the keyring, and startup had to keep accepting it forever. Field 2
now carries a **data key version** (``dk1``), and the master kid lives only on
``tenant_data_keys.kid`` — which re-wrap updates. Startup therefore validates
key ids against *data key rows* (through ``data_key_ids``), never against value
headers.

**2. ``data_key_ids`` becomes maintainable.** It was append-only by grant, so it
grew monotonically and made retirement impossible by construction. It is now
written by provisioning and by re-wrap, both of which run as the owner, and a
kid is deleted when its last referring data key row moves away. The runtime role
keeps ``SELECT`` and loses ``INSERT``: a role that serves requests must not be
able to register a kid that the startup check then trusts.

**3. The runtime loses the writes it should never have had.**
``adapt_app`` held ``INSERT, UPDATE`` on ``tenant_data_keys`` so it could create
a key lazily on first write. Lazy provisioning is the same trust problem as the
catalogue, one table over, so creation moves to provisioning and the runtime
keeps ``SELECT`` only.

The owner gains two things it needs to do any of this: ``SELECT`` on
``tenant_data_keys`` across tenants (wrapped keys, key ids and versions — no
learner data), and tenant-scoped writes so provisioning and re-wrap are
possible at all. Without the cross-tenant read a rotation could not enumerate
tenants or answer "is this kid still referenced?", because every table carrying
a tenant is FORCEd RLS and the owner sees nothing without a tenant context.

``data_key_ids`` keeps no row-level security, for the same reason it had none
before: it has no tenant column and holds key ids, which are already printed in
the header of everything they encrypted. It is the second documented exception
(KNOWN_GAPS §15), narrower than ``api_keys``: it holds no row that belongs to
anyone.
"""

from __future__ import annotations

from alembic import op

revision = "0008_key_lifecycle"
down_revision = "0007_encryption"
branch_labels = None
depends_on = None

#: The header of a value: version, data key version, nonce, ciphertext.
VALUE_ENVELOPE = r"^v1:dk[0-9]{1,6}:[A-Za-z0-9_-]{16}:[A-Za-z0-9_-]{16,}$"

TENANT_PREDICATE = "tenant_id = nullif(current_setting('adapt.tenant_id', true), '')::uuid"


def upgrade() -> None:
    _data_key_version()
    _owner_can_provision_and_rotate()
    _runtime_is_read_only()
    _values_are_row_bound()


def _data_key_version() -> None:
    op.execute("ALTER TABLE tenant_data_keys ADD COLUMN dkv text NOT NULL DEFAULT 'dk1'")
    op.execute(
        """
        ALTER TABLE tenant_data_keys
            ADD CONSTRAINT tenant_data_keys_dkv_format CHECK (dkv ~ '^dk[0-9]{1,6}$')
        """
    )


def _owner_can_provision_and_rotate() -> None:
    # Writes stay tenant-scoped: provisioning and re-wrap run inside a tenant
    # context, like everything else. The read policy is the one cross-tenant
    # grant in the schema, and it reaches wrapped keys only.
    op.execute(
        f"""
        CREATE POLICY data_key_owner_write ON tenant_data_keys
            AS PERMISSIVE
            FOR ALL
            TO adapt_owner
            USING ({TENANT_PREDICATE})
            WITH CHECK ({TENANT_PREDICATE})
        """
    )
    op.execute(
        """
        CREATE POLICY data_key_owner_read ON tenant_data_keys
            AS PERMISSIVE
            FOR SELECT
            TO adapt_owner
            USING (true)
        """
    )


def _runtime_is_read_only() -> None:
    op.execute("REVOKE INSERT, UPDATE ON tenant_data_keys FROM adapt_app")
    op.execute("REVOKE INSERT, UPDATE ON data_key_ids FROM adapt_app")
    op.execute("GRANT SELECT ON tenant_data_keys TO adapt_app")
    op.execute("GRANT SELECT ON data_key_ids TO adapt_app")


def _values_are_row_bound() -> None:
    """Constraint shape only: the AAD binding is enforced where the AAD exists.

    A CHECK cannot see associated data. What it can do is keep the header
    honest — four fields, a data key version, no master kid — so a row written
    in the old layout is refused at write time rather than discovered at read
    time.
    """
    op.execute("ALTER TABLE learners DROP CONSTRAINT IF EXISTS learners_profile_is_envelope")
    op.execute("ALTER TABLE learners DROP CONSTRAINT IF EXISTS learners_date_of_birth_is_envelope")
    op.execute(
        f"""
        ALTER TABLE learners
            ADD CONSTRAINT learners_profile_is_envelope CHECK (profile ~ '{VALUE_ENVELOPE}')
        """
    )
    op.execute(
        f"""
        ALTER TABLE learners
            ADD CONSTRAINT learners_date_of_birth_is_envelope
            CHECK (date_of_birth IS NULL OR date_of_birth ~ '{VALUE_ENVELOPE}')
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE learners DROP CONSTRAINT IF EXISTS learners_date_of_birth_is_envelope")
    op.execute("ALTER TABLE learners DROP CONSTRAINT IF EXISTS learners_profile_is_envelope")
    op.execute(
        """
        ALTER TABLE learners
            ADD CONSTRAINT learners_profile_is_envelope
            CHECK (profile ~ '^v1:[a-z0-9]{1,16}:[A-Za-z0-9_-]{16}:[A-Za-z0-9_-]{16,}$')
        """
    )
    op.execute(
        """
        ALTER TABLE learners
            ADD CONSTRAINT learners_date_of_birth_is_envelope
            CHECK (date_of_birth IS NULL
                   OR date_of_birth ~ '^v1:[a-z0-9]{1,16}:[A-Za-z0-9_-]{16}:[A-Za-z0-9_-]{16,}$')
        """
    )
    op.execute("GRANT INSERT, UPDATE ON data_key_ids TO adapt_app")
    op.execute("GRANT INSERT, UPDATE ON tenant_data_keys TO adapt_app")
    op.execute("DROP POLICY IF EXISTS data_key_owner_read ON tenant_data_keys")
    op.execute("DROP POLICY IF EXISTS data_key_owner_write ON tenant_data_keys")
    op.execute("ALTER TABLE tenant_data_keys DROP CONSTRAINT IF EXISTS tenant_data_keys_dkv_format")
    op.execute("ALTER TABLE tenant_data_keys DROP COLUMN IF EXISTS dkv")
