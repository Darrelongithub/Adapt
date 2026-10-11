"""Key material for encryption at rest.

Design (blueprint section 9, "protection of encryption keys"):

* One 256-bit master key per deployment.
* Resolution order: ``ADAPT_DB_KEY`` (base64) -> key file -> Argon2id KDF from
  ``ADAPT_DB_PASSPHRASE``. If nothing exists, a key file is generated with 0600
  permissions so a fresh clone is still encrypted at rest by default.
* Subkeys are derived with HKDF-SHA256 per purpose, so the SQLCipher file key and
  the column-encryption key are never the same bytes.
* Keys are never logged, never returned by an API, never committed. ``.gitignore``
  excludes ``var/``.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import hmac
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

KEY_BYTES = 32
PURPOSES = ("sqlcipher", "fields", "blind_index", "tokens", "csrf")


class KeyMaterialError(RuntimeError):
    pass


def _argon2_kdf(secret: str, salt: bytes, length: int = KEY_BYTES) -> bytes:
    try:  # preferred: memory-hard
        from argon2.low_level import Type, hash_secret_raw

        return hash_secret_raw(
            secret=secret.encode(),
            salt=salt,
            time_cost=3,
            memory_cost=64 * 1024,
            parallelism=2,
            hash_len=length,
            type=Type.ID,
        )
    except Exception:  # pragma: no cover - argon2-cffi is a hard dep in practice
        return PBKDF2HMAC(hashes.SHA256(), length=length, salt=salt, iterations=600_000).derive(
            secret.encode()
        )


@dataclass(frozen=True)
class Keyring:
    """Derived per-purpose keys. Values are secrets: never ``repr`` them."""

    master_fingerprint: str
    source: str
    _subkeys: dict[str, bytes]

    def subkey(self, purpose: str) -> bytes:
        try:
            return self._subkeys[purpose]
        except KeyError as exc:  # pragma: no cover
            raise KeyMaterialError(f"no subkey for purpose {purpose!r}") from exc

    def aesgcm(self, purpose: str) -> AESGCM:
        return AESGCM(self.subkey(purpose))

    def mac(self, purpose: str) -> hmac.HMAC:
        return hmac.HMAC(self.subkey(purpose), hashes.SHA256())

    def derive(self, label: str, length: int = 32) -> bytes:
        """Arbitrary context-specific subkey (e.g. one HMAC key per table)."""
        base = self.subkey("fields")
        return HKDF(
            algorithm=hashes.SHA256(),
            length=length,
            salt=None,
            info=f"adapt/v1/{label}".encode(),
        ).derive(base)

    def __repr__(self) -> str:  # keep secrets out of logs/tracebacks
        return f"Keyring(source={self.source!r}, fp={self.master_fingerprint!r})"


def _validate_key(raw: bytes, *, source: str) -> bytes:
    if len(raw) != KEY_BYTES:
        raise KeyMaterialError(
            f"{source} must contain exactly {KEY_BYTES} bytes (got {len(raw)}). "
            "Generate one with: adapt keygen"
        )
    return raw


def _read_keyfile(path: Path) -> bytes:
    try:
        text = path.read_text().strip()
    except FileNotFoundError as exc:
        raise KeyMaterialError(f"key file not found: {path}") from exc
    if not text:
        raise KeyMaterialError(f"key file is empty: {path}")
    try:
        raw = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise KeyMaterialError(f"key file is not base64: {path}") from exc
    _validate_key(raw, source=f"key file {path}")
    _warn_if_world_readable(path)
    return raw


def _warn_if_world_readable(path: Path) -> None:
    try:
        mode = path.stat().st_mode
    except OSError:  # pragma: no cover
        return
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        # Loud, but do not refuse to start: a demo machine with a loose umask
        # should still run. `adapt doctor` reports this as a finding.
        import logging

        logging.getLogger("adapt.crypto").warning(
            "key file %s is readable by group/other (mode %o); run: chmod 600 %s",
            path,
            stat.S_IMODE(mode),
            path,
        )


def write_keyfile(path: Path, raw: bytes | None = None) -> Path:
    """Create a key file with 0600 permissions. Refuses to overwrite silently."""
    path = Path(path)
    if path.exists() and path.stat().st_size > 0:
        raise KeyMaterialError(f"refusing to overwrite existing key file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = raw or os.urandom(KEY_BYTES)
    _validate_key(raw, source="generated key")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(base64.b64encode(raw).decode() + "\n")
    os.chmod(path, 0o600)
    return path


def load_master_key(
    *,
    b64_key: str | None = None,
    keyfile: Path | None = None,
    passphrase: str | None = None,
    kdf_salt_path: Path | None = None,
) -> tuple[bytes, str]:
    """Return ``(key, source)`` using the documented resolution order."""
    if b64_key:
        raw = base64.b64decode(b64_key.strip(), validate=True)
        return _validate_key(raw, source="ADAPT_DB_KEY"), "env:ADAPT_DB_KEY"

    if keyfile is not None and Path(keyfile).exists():
        return _read_keyfile(Path(keyfile)), f"keyfile:{keyfile}"

    if passphrase:
        # Deterministic across restarts, salt stored next to the key file so a
        # passphrase change (or new install) yields a new key.
        salt_file = kdf_salt_path or (Path(keyfile).with_suffix(".salt") if keyfile else None)
        if salt_file is not None:
            salt_file = Path(salt_file)
            if salt_file.exists():
                salt = bytes.fromhex(salt_file.read_text().strip())
            else:
                salt = os.urandom(16)
                salt_file.parent.mkdir(parents=True, exist_ok=True)
                salt_file.write_text(salt.hex(), encoding="ascii")
                os.chmod(salt_file, 0o600)
        else:
            salt = b"adapt-static-salt!"
        return _argon2_kdf(passphrase, salt[:16]), "kdf:passphrase"

    if keyfile is not None:
        path = Path(keyfile)
        raw = os.urandom(KEY_BYTES)
        write_keyfile(path, raw)
        return raw, f"keyfile(generated):{path}"

    raise KeyMaterialError(
        "no key material: set ADAPT_DB_KEY, provide a key file, or set "
        "ADAPT_DB_PASSPHRASE (see `adapt keygen`)"
    )


def build_keyring(**kwargs: object) -> Keyring:
    master, source = load_master_key(**kwargs)  # type: ignore[arg-type]
    subkeys = {
        purpose: HKDF(
            algorithm=hashes.SHA256(),
            length=KEY_BYTES,
            salt=b"adapt/v1",
            info=f"adapt/v1/{purpose}".encode(),
        ).derive(master)
        for purpose in PURPOSES
    }
    return Keyring(
        master_fingerprint=hashlib.sha256(master).hexdigest()[:12],
        source=source,
        _subkeys=subkeys,
    )
