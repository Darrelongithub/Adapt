"""tenant creation is provisioning, not runtime

Revision ID: 0002_tenant_provisioning
Revises: 0001_tenancy
Create Date: 2026-11-17

Follow-up from increment 2: ``adapt_app`` served requests and could also
create and rewrite tenants. Tenant creation belongs to the operator or control
plane acting as ``adapt_owner``; a request-serving role that can mint tenants
can mint itself a tenant, and that is a privilege-escalation path rather than
a convenience.

This is a separate revision rather than an edit to 0001 because 0001 has been
reviewed. The down migration restores the grants 0001 left behind.
"""

from __future__ import annotations

from alembic import op

revision = "0002_tenant_provisioning"
down_revision = "0001_tenancy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Read-only for the runtime role. No INSERT, no UPDATE, no DELETE.
    op.execute("REVOKE INSERT, UPDATE ON tenants FROM adapt_app")
    op.execute("GRANT SELECT ON tenants TO adapt_app")


def downgrade() -> None:
    op.execute("GRANT INSERT, UPDATE ON tenants TO adapt_app")
