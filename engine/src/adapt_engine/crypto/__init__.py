"""Encryption at rest: envelope format, master keyring, per-tenant data keys."""

from adapt_engine.crypto.envelope import (
    CURRENT_DATA_KEY_VERSION,
    CryptoError,
    DecryptionFailed,
    Envelope,
    MalformedEnvelope,
    data_key_aad,
    seal,
    unseal,
    value_aad,
)
from adapt_engine.crypto.keyring import (
    DuplicateKeyId,
    Keyring,
    KeyringError,
    UnknownKeyId,
    derive_master_key,
)
from adapt_engine.crypto.provisioning import provision_data_key, provision_data_key_for_tenant
from adapt_engine.crypto.rotate import (
    RewrapResult,
    assert_retirable,
    preflight,
    referenced_kids,
    rewrap_all,
    rewrap_tenant,
)
from adapt_engine.crypto.store import DataKeyMissing, DataKeyStore

__all__ = [
    "CURRENT_DATA_KEY_VERSION",
    "CryptoError",
    "DataKeyMissing",
    "DataKeyStore",
    "DecryptionFailed",
    "DuplicateKeyId",
    "Envelope",
    "Keyring",
    "KeyringError",
    "MalformedEnvelope",
    "RewrapResult",
    "UnknownKeyId",
    "assert_retirable",
    "data_key_aad",
    "derive_master_key",
    "preflight",
    "provision_data_key",
    "provision_data_key_for_tenant",
    "referenced_kids",
    "rewrap_all",
    "rewrap_tenant",
    "seal",
    "unseal",
    "value_aad",
]
