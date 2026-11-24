"""Envelope encryption: one versioned format, one AEAD, no hand-rolled crypto.

Everything encrypted at rest in this engine is a UTF-8 string of the form::

    v1:<dkv>:<nonce>:<ciphertext>

``v1``     format version — a second version may change the layout, never reuse this one.
``dkv``    **data key version** (``dk1``), not a master key id. See below.
``nonce``  96-bit AES-GCM nonce, base64url without padding.
``ct``     AES-GCM ciphertext with its 128-bit tag, base64url without padding.

**The header does not name the master key, and that is the whole point.** An
earlier revision put the master key id in field 2 — the key that wrapped the
tenant data key at write time. Re-wrapping does not rewrite rows, so every row
ever written stayed pinned to the key it was born under: the retired key could
never be removed from the keyring, startup had to keep accepting it forever,
and a kid catalogue had to grow monotonically. What looked like versioning was
a permanent dependency. The master key id now lives only on the wrapped data
key row (``tenant_data_keys.kid``), which re-wrap *does* update; field 2 is a
data key version, which is what will matter when data-key rotation exists
(``dk1`` today; a row naming a version the tenant does not hold is refused
rather than guessed).

There are two kinds of envelope, and their AADs differ on purpose:

* **a wrapped data key** — AAD ``adapt/tenant/v1:{tenant_id}``. It belongs to a
  tenant, not to a row.
* **a value** — AAD ``adapt/v1:{tenant_id}:{learner_row_id}:{column}``. It
  belongs to exactly one cell of exactly one row. AAD of the tenant alone let a
  ciphertext be swapped between two learners of one tenant, or between two
  columns of one row, and decrypt cleanly into wrong data — corruption that
  nothing can detect afterwards, in the fields the product makes claims about.
  The row id is the internal uuid, not the host's external learner id, which
  the host may change.

The same plaintext encrypted twice produces different output: the nonce is
random, nothing is deterministic, nothing is searchable. Base64url without
padding is used so no field contains ``:`` and the envelope splits without
escaping.
"""

from __future__ import annotations

import base64
import binascii
import os
import re
import uuid
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VERSION = "v1"
NONCE_BYTES = 12
KEY_BYTES = 32
TAG_BYTES = 16
MAX_KID_LENGTH = 16

#: The only data key version that exists. A second one is what data-key
#: rotation would introduce; until then an envelope claiming ``dk2`` is a
#: ciphertext we cannot read, and saying so beats trying.
CURRENT_DATA_KEY_VERSION = "dk1"
DATA_KEY_VERSION_PATTERN = re.compile(r"^dk[0-9]{1,6}$")

#: Domain separation between the two kinds of envelope, so a wrapped data key
#: can never be replayed as a value (or the other way round): the prefixes
#: differ, and the AAD fed to AES-GCM differs with them.
DATA_KEY_AAD_PREFIX = b"adapt/tenant/v1:"
VALUE_AAD_PREFIX = b"adapt/v1:"


class CryptoError(Exception):
    """Base class: something about the ciphertext or key was wrong."""


class MalformedEnvelope(CryptoError):
    """The stored value is not a v1 envelope."""


class DecryptionFailed(CryptoError):
    """The key, the AAD binding or the bytes were wrong.

    Deliberately one error for all three. A caller that can tell "wrong
    tenant" from "wrong row" from "corrupted" has an oracle it should not have.
    """


def data_key_aad(tenant_id: uuid.UUID | str) -> bytes:
    """Associated data binding a *wrapped data key* to its tenant."""
    return DATA_KEY_AAD_PREFIX + str(tenant_id).lower().encode("ascii")


def value_aad(tenant_id: uuid.UUID | str, row_id: uuid.UUID | str, column: str) -> bytes:
    """Associated data binding a *value* to one cell of one row."""
    if ":" in column:
        raise CryptoError("a column name cannot contain ':' (the AAD separator)")
    return (
        VALUE_AAD_PREFIX
        + str(tenant_id).lower().encode("ascii")
        + b":"
        + str(row_id).lower().encode("ascii")
        + b":"
        + column.encode("ascii")
    )


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text_value: str) -> bytes:
    padding = "=" * (-len(text_value) % 4)
    try:
        return base64.urlsafe_b64decode(text_value + padding)
    except (binascii.Error, ValueError) as exc:
        raise MalformedEnvelope("value is not base64url") from exc


@dataclass(frozen=True)
class Envelope:
    """A parsed v1 envelope."""

    dkv: str
    nonce: bytes
    ciphertext: bytes

    def render(self) -> str:
        return f"{VERSION}:{self.dkv}:{_b64(self.nonce)}:{_b64(self.ciphertext)}"

    @property
    def kid(self) -> str:
        """The retired name for field 2, so an old call site fails loudly.

        Kept as a property rather than an alias: code that still believes the
        header names a master key should not compile quietly against a data
        key version.
        """
        raise AttributeError(
            "an envelope header carries a data key version (dkv), not a master kid; "
            "read the wrapped data key row for the master key id"
        )

    @classmethod
    def parse(cls, value: str) -> Envelope:
        parts = value.split(":")
        if len(parts) != 4:
            raise MalformedEnvelope("an envelope has exactly four colon-separated fields")
        version, dkv, nonce_b64, ct_b64 = parts
        if version != VERSION:
            raise MalformedEnvelope(f"unknown envelope version {version!r}")
        if not DATA_KEY_VERSION_PATTERN.match(dkv):
            raise MalformedEnvelope(
                f"field 2 must be a data key version (dk1, dk2, ...), got {dkv!r}"
            )
        nonce = _unb64(nonce_b64)
        if len(nonce) != NONCE_BYTES:
            raise MalformedEnvelope(f"nonce must be {NONCE_BYTES} bytes")
        ciphertext = _unb64(ct_b64)
        if len(ciphertext) < TAG_BYTES:
            raise MalformedEnvelope("ciphertext is shorter than its tag")
        return cls(dkv=dkv, nonce=nonce, ciphertext=ciphertext)


def seal(key: bytes, plaintext: bytes, *, aad: bytes, dkv: str = CURRENT_DATA_KEY_VERSION) -> str:
    """Encrypt to an envelope. A fresh random nonce every call, by design."""
    if len(key) != KEY_BYTES:
        raise CryptoError(f"a data key is {KEY_BYTES} bytes, got {len(key)}")
    if not DATA_KEY_VERSION_PATTERN.match(dkv):
        raise CryptoError(f"{dkv!r} is not a data key version")
    nonce = os.urandom(NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad)
    return Envelope(dkv=dkv, nonce=nonce, ciphertext=ciphertext).render()


def unseal(key: bytes, envelope: Envelope, *, aad: bytes) -> bytes:
    """Decrypt an envelope, raising :class:`DecryptionFailed` on any mismatch."""
    if len(key) != KEY_BYTES:
        raise CryptoError(f"a data key is {KEY_BYTES} bytes, got {len(key)}")
    try:
        return AESGCM(key).decrypt(envelope.nonce, envelope.ciphertext, aad)
    except InvalidTag as exc:
        raise DecryptionFailed(
            "ciphertext did not authenticate: wrong key, wrong row, wrong column, wrong "
            "tenant, or corrupted"
        ) from exc


__all__ = [
    "CURRENT_DATA_KEY_VERSION",
    "DATA_KEY_AAD_PREFIX",
    "NONCE_BYTES",
    "VALUE_AAD_PREFIX",
    "VERSION",
    "CryptoError",
    "DecryptionFailed",
    "Envelope",
    "MalformedEnvelope",
    "data_key_aad",
    "seal",
    "unseal",
    "value_aad",
]
