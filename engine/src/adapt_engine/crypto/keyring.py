"""Master keys, in a keyring, with an id on every one of them.

The master key never encrypts data. It wraps per-tenant data keys, and each
data key does the bulk work (see :mod:`adapt_engine.crypto.store`). That single
level of indirection is what makes rotation cheap: re-wrap N data keys, do not
re-encrypt rows.

A keyring holds the current key plus the previous ones (``N-1``): after a
rotation, ciphertext written under an older key must still open. Dropping a key
that is still referenced is the one failure this module cannot paper over, so
it raises — and the application refuses to start (see ``create_app``).

Ids are short, lowercase, and carry no secret: they appear in every ciphertext
header, so they are not sensitive. The id says *which master key*, not what it
is.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import SecretStr

from adapt_engine.crypto.envelope import KEY_BYTES, CryptoError

#: Ids appear in a colon-delimited header; keep them boring.
KID_PATTERN = re.compile(r"^[a-z0-9]{1,16}$")

#: A master secret is stretched, not used raw: operators paste passphrases of
#: wildly different shapes and lengths, and a hash gives every key exactly 32
#: bytes of entropy-independent material. The label is domain separation from
#: any other use of the same secret.
_DERIVATION_LABEL = b"adapt/master-key/v1:"


class KeyringError(CryptoError):
    """The keyring is malformed, or a needed key is missing."""


class UnknownKeyId(KeyringError):
    """A kid is in use but has no key in this keyring — refuse, do not guess."""


class DuplicateKeyId(KeyringError):
    """The same kid was supplied twice; one of them would win silently."""


def derive_master_key(secret: str) -> bytes:
    """Stretch an operator-supplied secret to a 32-byte AES key."""
    if len(secret.encode("utf-8")) < 32:
        raise KeyringError("a master key secret must be at least 32 bytes")
    return hashlib.sha256(_DERIVATION_LABEL + secret.encode("utf-8")).digest()


class KeyedSettings(Protocol):
    """The slice of `Settings` a keyring needs, so this module imports none of it."""

    master_key: SecretStr
    master_key_id: str
    master_key_previous: SecretStr | None
    master_key_previous_id: str | None


def _check_kid(kid: str) -> str:
    if not KID_PATTERN.match(kid):
        raise KeyringError(f"master key id {kid!r} must match {KID_PATTERN.pattern}")
    return kid


@dataclass(frozen=True)
class Keyring:
    """Current master key plus the previous ones."""

    current: str
    keys: Mapping[str, bytes] = field(repr=False)

    def __post_init__(self) -> None:
        if not self.keys:
            raise KeyringError("a keyring needs at least one key")
        if self.current not in self.keys:
            raise KeyringError(f"current kid {self.current!r} has no key")
        for kid, key in self.keys.items():
            _check_kid(kid)
            if len(key) != KEY_BYTES:
                raise KeyringError(f"master key {kid!r} is not {KEY_BYTES} bytes")

    @classmethod
    def from_secrets(cls, entries: Sequence[tuple[str, str]]) -> Keyring:
        """Build from ``[(kid, secret), ...]``; the first entry is current."""
        if not entries:
            raise KeyringError("at least one master key is required")
        keys: dict[str, bytes] = {}
        for kid, secret in entries:
            _check_kid(kid)
            if kid in keys:
                raise DuplicateKeyId(f"master key id {kid!r} supplied twice")
            keys[kid] = derive_master_key(secret)
        return cls(current=entries[0][0], keys=keys)

    @classmethod
    def from_settings(cls, settings: KeyedSettings) -> Keyring:
        """Build from :class:`adapt_engine.config.Settings`.

        ``master_key``/``master_key_id`` are the current key;
        ``master_key_previous``/``master_key_previous_id`` (optional) is the one
        it replaced. Rotation therefore needs no downtime: start with both,
        re-wrap, then drop the old pair.
        """
        entries: list[tuple[str, str]] = []
        current_id = str(getattr(settings, "master_key_id", "k1"))
        entries.append((current_id, settings.master_key.get_secret_value()))
        previous = getattr(settings, "master_key_previous", None)
        if previous is not None:
            previous_id = getattr(settings, "master_key_previous_id", None)
            if not previous_id:
                raise KeyringError(
                    "ADAPT_MASTER_KEY_PREVIOUS_ID is required with ADAPT_MASTER_KEY_PREVIOUS"
                )
            entries.append((str(previous_id), previous.get_secret_value()))
        return cls.from_secrets(entries)

    def key(self, kid: str) -> bytes:
        try:
            return self.keys[kid]
        except KeyError as exc:
            raise UnknownKeyId(
                f"no master key for id {kid!r}; available: {sorted(self.keys)}"
            ) from exc

    def __contains__(self, kid: object) -> bool:
        return kid in self.keys

    def check_covers(self, used: Iterable[str]) -> None:
        """Raise unless every kid in use has a key here."""
        missing = sorted({kid for kid in used if kid not in self.keys})
        if missing:
            raise UnknownKeyId(
                "ciphertext or a wrapped data key references master key id(s) "
                f"{missing}, which this keyring does not hold "
                f"(it holds {sorted(self.keys)}); add the key or restore a backup"
            )


__all__ = [
    "KID_PATTERN",
    "DuplicateKeyId",
    "Keyring",
    "KeyringError",
    "UnknownKeyId",
    "derive_master_key",
]
