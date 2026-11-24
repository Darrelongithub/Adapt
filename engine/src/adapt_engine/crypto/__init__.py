"""Encryption at rest: envelope format, master keyring, per-tenant data keys."""

from adapt_engine.crypto.envelope import (
    CryptoError,
    DecryptionFailed,
    Envelope,
    MalformedEnvelope,
    aad_for,
    seal,
    unseal,
)
from adapt_engine.crypto.keyring import (
    DuplicateKeyId,
    Keyring,
    KeyringError,
    UnknownKeyId,
    derive_master_key,
)
from adapt_engine.crypto.store import DataKeyStore, rewrap_all

__all__ = [
    "CryptoError",
    "DataKeyStore",
    "DecryptionFailed",
    "DuplicateKeyId",
    "Envelope",
    "Keyring",
    "KeyringError",
    "MalformedEnvelope",
    "UnknownKeyId",
    "aad_for",
    "derive_master_key",
    "rewrap_all",
    "seal",
    "unseal",
]
