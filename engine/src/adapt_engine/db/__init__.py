"""Database access: engine factory, tenancy, roles and schema.

The connection string a process uses is never the tenant boundary; the
boundary is ``tenant_transaction`` below plus row-level security in Postgres.
"""

from adapt_engine.db.engine import check_database, create_db_engine
from adapt_engine.db.tenant import TENANT_SETTING, TenantContextError, tenant_transaction

__all__ = [
    "TENANT_SETTING",
    "TenantContextError",
    "check_database",
    "create_db_engine",
    "tenant_transaction",
]
