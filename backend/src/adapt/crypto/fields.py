"""Column-level encryption for learner data.

This layer is always on (unless ``ADAPT_DB_ENCRYPTION=off``, which the app warns
about and refuses to use when a learner record exists), *in addition to*
SQLCipher file encryption. Rationale: the blueprint says "SQLite does not
provide encryption by default, so an actual encryption implementation must be
selected" -- we select both, because SQLCipher needs a native driver that may be
absent on the demo machine, while AES-GCM column encryption is pure
``cryptography`` and keeps learner free text (answers, transcripts, profile
notes) confidential in every mode.

Format: ``b"AGC1" || nonce(12) || ciphertext||tag`` with the column name as
additional authenticated data, so a ciphertext cannot be moved between columns.
"""

from __future__ import annotations

import json
import os
from typing import Any

from sqlalchemy.dialects.sqlite import BLOB
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

from .keys import KeyMaterialError, Keyring

MAGIC = b"AGC1"
NONCE_BYTES = 12
_ENCRYPTOR: "FieldCipher | None" = None


class FieldCipher:
    def __init__(self, keyring: Keyring, *, prefix: str = "column") -> None:
        self._keyring = keyring
        self._prefix = prefix

    @property
    def keyring(self) -> Keyring:
        return self._keyring

    def encrypt(self, plaintext: str, *, context: str) -> bytes:
        aesgcm = self._keyring.aesgcm("fields")
        nonce = os.urandom(NONCE_BYTES)
        aad = f"{self._prefix}:{context}".encode()
        return MAGIC + nonce + aesgcm.encrypt(nonce, plaintext.encode(), aad)

    def decrypt(self, blob: bytes, *, context: str) -> str:
        if not blob or blob[:4] != MAGIC:
            # Legacy/plaintext row (e.g. imported fixture): tolerate but do not
            # re-encrypt silently. Callers migrate on next write.
            return blob.decode() if isinstance(blob, (bytes, bytearray)) else str(blob)
        aesgcm = self._keyring.aesgcm("fields")
        nonce = blob[4 : 4 + NONCE_BYTES]
        aad = f"{self._prefix}:{context}".encode()
        return aesgcm.decrypt(nonce, blob[4 + NONCE_BYTES :], aad).decode()

    def blind_index(self, value: str, *, column: str | None = None) -> str:
        """Deterministic, keyed digest for equality lookups on encrypted columns."""
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.hmac import HMAC

        label = f"{self._prefix}:{column}" if column else self._prefix
        h = HMAC(self._keyring.subkey("blind_index"), hashes.SHA256())
        h.update(f"{label}|{value.strip().lower()}".encode())
        # cryptography's HMAC.finalize() hands back raw bytes (unlike hashlib's
        # update()/hexdigest() idiom); 128 bits of it is plenty for an equality
        # index over a single-tenant table.
        return h.finalize().hex()[:32]


def configure_cipher(keyring: Keyring, *, force: bool = False) -> FieldCipher:
    """Install the process-wide cipher, refusing a silent key swap.

    Every ``EncryptedText`` column reads through this one object. If a second
    ``Database`` were built from a different key file while rows already exist,
    those rows would become undecryptable and the failure would surface much later,
    as ``InvalidTag`` inside an unrelated request. Refusing here turns that into an
    startup error that names the key file. ``force`` (and ``set_cipher``) remain the
    escape hatches for tests and for a deliberate re-key.
    """
    global _ENCRYPTOR
    if _ENCRYPTOR is not None and not force:
        current = _ENCRYPTOR.keyring.master_fingerprint
        if current != keyring.master_fingerprint:
            raise KeyMaterialError(
                "a different master key is already in use "
                f"(installed {current}, requested {keyring.master_fingerprint} from {keyring.source}). "
                "Learner rows written with the first key would stop decrypting. "
                "Point ADAPT_KEYFILE / ADAPT_DB_KEY at the original key, or pass force=True."
            )
        return _ENCRYPTOR
    _ENCRYPTOR = FieldCipher(keyring)
    return _ENCRYPTOR


def get_cipher() -> FieldCipher | None:
    return _ENCRYPTOR


def set_cipher(cipher: FieldCipher | None) -> None:
    """Install/clear the process-wide cipher (used by tests and by the
    explicit ``ADAPT_DB_ENCRYPTION=off`` escape hatch)."""
    global _ENCRYPTOR
    _ENCRYPTOR = cipher


class EncryptedText(TypeDecorator):
    """Transparent AES-256-GCM ``Text`` column."""

    impl = BLOB
    cache_ok = True

    def __init__(self, *, context: str = "generic", **kw: Any) -> None:
        super().__init__(**kw)
        self.context = context

    def _ctx(self) -> str:
        return self.context

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        cipher = get_cipher()
        text = value if isinstance(value, str) else json.dumps(value, separators=(",", ":"))
        if cipher is None:
            return text.encode()  # unencrypted mode (tests / explicit opt-out)
        return cipher.encrypt(text, context=self._ctx())

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        cipher = get_cipher()
        if cipher is None:
            return bytes(value).decode() if isinstance(value, (bytes, bytearray)) else str(value)
        return cipher.decrypt(bytes(value), context=self._ctx())


class EncryptedJSON(EncryptedText):
    """AES-GCM encrypted JSON blob.

    ``cache_ok`` is safe here: the type carries no per-instance state beyond the
    context string, which is part of the column definition, so two columns with the
    same context always encode identically.
    """

    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if value is None:
            return None
        return super().process_bind_param(json.dumps(value, separators=(",", ":")), dialect)

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        raw = super().process_result_value(value, dialect)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:  # pragma: no cover - defensive
            return {"_unparseable": raw[:200]}


class BlindIndexedEmail(EncryptedText):
    """Encrypted email; the *index* is a keyed digest so login works with no plaintext.

    Subclassing (rather than delegating to a freshly built ``EncryptedText``) keeps
    the authenticated-data context tied to the column it protects: two copies of the
    string "student.email" is how a future rename quietly makes every row unreadable.
    """

    cache_ok = True

    def __init__(self) -> None:
        super().__init__(context="student.email")


def blind_index(value: str, *, column: str = "student.email") -> str | None:
    cipher = get_cipher()
    if cipher is None or not value:
        return None
    return cipher.blind_index(value, column=column)


def blind_index_for(column: str, value: str) -> str | None:
    """Stable index used by UNIQUE constraints on encrypted identifiers."""
    return blind_index(value, column=column)


def encrypted_columns() -> list[tuple[str, str]]:
    """``(table, column)`` for every column this module protects."""
    from ..db.models import Base

    out: list[tuple[str, str]] = []
    for table in Base.metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, EncryptedText) or col.name.endswith("_cipher"):
                out.append((table.name, col.name))
    return out


def verify_stored_data(engine: Any, *, per_column: int = 4) -> dict[str, Any]:
    """Can the key that is loaded *right now* open what is already in the database?

    Losing ``keys``/``adapt.key`` (or pointing ``ADAPT_DATA_DIR`` at a different
    directory than the key that wrote it) does not fail loudly in the usual sense: every
    encrypted column simply refuses to open, and the first learner to sign in is served a
    500 whose only clue is ``InvalidTag``. Checking at startup turns that into one
    sentence, and it is deliberately a *read* of the same code path the columns use -- so a
    healthy instance can never be blocked by a false alarm about a context string.

    Returns counts, never plaintext: the values it touches are learner emails and answers.
    """
    from sqlalchemy import text

    report: dict[str, Any] = {"columns": 0, "checked": 0, "unreadable": 0, "columns_unreadable": [], "note": None}
    for table_name, column_name in encrypted_columns():
        try:
            with engine.connect() as cx:
                rows = cx.execute(
                    text(f'SELECT "{column_name}" FROM "{table_name}" WHERE "{column_name}" IS NOT NULL LIMIT :n'),
                    {"n": per_column},
                ).fetchall()
        except Exception:  # a table this build does not have yet is not a key problem
            continue
        if not rows:
            continue
        col = None
        from ..db.models import Base

        try:
            col = Base.metadata.tables[table_name].columns[column_name]
        except KeyError:  # pragma: no cover - defensive
            continue
        report["columns"] += 1
        for (blob,) in rows:
            if blob is None:
                continue
            report["checked"] += 1
            try:
                # The column type's own result processor: same context, same cipher, no
                # second implementation that could disagree with the first.
                col.type.process_result_value(blob, None)
            except Exception as exc:
                report["unreadable"] += 1
                report["note"] = type(exc).__name__
                if table_name not in report["columns_unreadable"]:
                    report["columns_unreadable"].append(table_name)
    return report
