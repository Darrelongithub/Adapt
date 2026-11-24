"""Envelope encryption: one versioned format, one AEAD, no hand-rolled crypto.

Everything encrypted at rest in this engine is a UTF-8 string of the form::

    v1:<kid>:<nonce>:<ciphertext>

``v1``     format version — a second version may change the layout, never reuse this one.
``kid``    master key id that can unwrap the tenant data key used here.
``nonce``  96-bit AES-GCM nonce, base64url without padding.
``ct``     AES-GCM ciphertext with its 128-bit tag, base64url without padding.

The associated data is the tenant id (see :func:`aad_for`). Two consequences
are worth stating because they are the point of the design:

* A ciphertext copied into another tenant's row fails to decrypt. It does not
  decrypt to "garbage that we then store": GCM's tag check raises.
* The same plaintext encrypted twice produces different output, because the
  nonce is random. Nothing here is deterministic and nothing is searchable.

Base64url without padding is used so the payload contains no ``:`` and the
envelope can be split on the separator without escaping.
"""

from __future__ import annotations

import base64
import binascii
import os
import uuid
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

VERSION = "v1"
NONCE_BYTES = 12
KEY_BYTES = 32
TAG_BYTES = 16
MAX_KID_LENGTH = 16

#: Domain separation: the AAD is the tenant id *under this prefix*, so a value
#: authenticated for `learners.profile` cannot be replayed as AAD for another
#: purpose that also hashes a uuid into it.
AAD_PREFIX = b"adapt/tenant/v1:"


class CryptoError(Exception):
    """Base class: something about the ciphertext or key was wrong."""


class MalformedEnvelope(CryptoError):
    """The stored value is not a v1 envelope."""


class DecryptionFailed(CryptoError):
    """The key, the tenant binding or the bytes were wrong.

    Deliberately one error for all three. A caller that can tell "wrong tenant"
    from "wrong key" from "corrupted" has an oracle it should not have.
    """


def aad_for(tenant_id: uuid.UUID | str) -> bytes:
    """Associated data binding a ciphertext to exactly one tenant."""
    return AAD_PREFIX + str(tenant_id).lower().encode("ascii")


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    try:
        return base64.urlsafe_b64decode(text + padding)
    except (binascii.Error, ValueError) as exc:
        raise MalformedEnvelope("value is not base64url") from exc


@dataclass(frozen=True)
class Envelope:
    """A parsed v1 envelope."""

    kid: str
    nonce: bytes
    ciphertext: bytes

    def render(self) -> str:
        return f"{VERSION}:{self.kid}:{_b64(self.nonce)}:{_b64(self.ciphertext)}"

    @classmethod
    def parse(cls, value: str) -> Envelope:
        parts = value.split(":")
        if len(parts) != 4:
            raise MalformedEnvelope("an envelope has exactly four colon-separated fields")
        version, kid, nonce_b64, ct_b64 = parts
        if version != VERSION:
            raise MalformedEnvelope(f"unknown envelope version {version!r}")
        if not 1 <= len(kid) <= MAX_KID_LENGTH:
            raise MalformedEnvelope("master key id has an implausible length")
        nonce = _unb64(nonce_b64)
        if len(nonce) != NONCE_BYTES:
            raise MalformedEnvelope(f"nonce must be {NONCE_BYTES} bytes")
        ciphertext = _unb64(ct_b64)
        if len(ciphertext) < TAG_BYTES:
            raise MalformedEnvelope("ciphertext is shorter than its tag")
        return cls(kid=kid, nonce=nonce, ciphertext=ciphertext)


def seal(key: bytes, plaintext: bytes, *, aad: bytes, kid: str) -> str:
    """Encrypt to an envelope. A fresh random nonce every call, by design."""
    if len(key) != KEY_BYTES:
        raise CryptoError(f"a data key is {KEY_BYTES} bytes, got {len(key)}")
    nonce = os.urandom(NONCE_BYTES)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad)
    return Envelope(kid=kid, nonce=nonce, ciphertext=ciphertext).render()


def unseal(key: bytes, envelope: Envelope, *, aad: bytes) -> bytes:
    """Decrypt an envelope, raising :class:`DecryptionFailed` on any mismatch."""
    if len(key) != KEY_BYTES:
        raise CryptoError(f"a data key is {KEY_BYTES} bytes, got {len(key)}")
    try:
        return AESGCM(key).decrypt(envelope.nonce, envelope.ciphertext, aad)
    except InvalidTag as exc:
        raise DecryptionFailed(
            "ciphertext did not authenticate: wrong key, wrong tenant, or corrupted"
        ) from exc


__all__ = [
    "AAD_PREFIX",
    "NONCE_BYTES",
    "VERSION",
    "CryptoError",
    "DecryptionFailed",
    "Envelope",
    "MalformedEnvelope",
    "aad_for",
    "seal",
    "unseal",
]
