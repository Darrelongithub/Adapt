"""Engine + session factory with encryption-at-rest policy applied.

Verified in-repo (see ``adapt doctor`` and tests/test_encryption.py):

* With SQLCipher 4 the database file header is ciphertext and opening it with
  plain ``sqlite3`` raises ``DatabaseError: file is not a database``.
* Column-level AES-256-GCM (``crypto/fields.py``) protects learner free text in
  every mode, so a fallback to plain SQLite is never "unencrypted learner text".
"""

from __future__ import annotations

import logging
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings, get_settings
from ..crypto import keys as keymod
from ..crypto.fields import FieldCipher, configure_cipher
from .models import Base

log = logging.getLogger("adapt.db")

PLAIN_SQLITE_MAGIC = b"SQLite format 3\x00"


def _import_sqlcipher():
    """Return a DBAPI2 module for SQLCipher, or None if unavailable."""
    for module_name in ("sqlcipher3", "sqlcipher3_binary", "sqlcipher3dbapi2"):
        try:
            mod = __import__(module_name, fromlist=["dbapi2"])
            return getattr(mod, "dbapi2")
        except Exception:
            continue
    return None


class DriverInfo:
    def __init__(self, name: str, file_encrypted: bool, detail: str, key_source: str) -> None:
        self.name = name
        self.file_encrypted = file_encrypted
        self.detail = detail
        self.key_source = key_source

    def as_dict(self) -> dict[str, object]:
        return {
            "driver": self.name,
            "file_encrypted": self.file_encrypted,
            "key_source": self.key_source,
            "detail": self.detail,
        }


def build_keyring(settings: Settings) -> FieldCipher | None:
    """Derive the master key + install the column cipher. Returns the cipher."""
    passphrase = os.environ.get(settings.db_key_passphrase_env) or None
    keyring = keymod.build_keyring(
        b64_key=settings.db_key,
        keyfile=settings.keyfile_path,
        passphrase=passphrase,
        kdf_salt_path=settings.keyfile_path.with_suffix(".salt"),
    )
    global _KEYRING
    _KEYRING = keyring
    return configure_cipher(keyring)


_KEYRING: keymod.Keyring | None = None


def get_keyring() -> keymod.Keyring:
    if _KEYRING is None:
        raise RuntimeError("keyring not initialised; construct Database first")
    return _KEYRING


def _sqlite_pragmas(shared: bool) -> Callable[[object, object], None]:
    def _apply(dbapi_conn, _record) -> None:
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys = ON")
        cur.execute("PRAGMA busy_timeout = 5000")
        cur.execute("PRAGMA synchronous = NORMAL")
        cur.execute("PRAGMA journal_mode = WAL" if shared else "PRAGMA journal_mode = MEMORY")
        cur.close()

    return _apply


def _on_disk_format(db_file: Path) -> str:
    """What the file actually is, from its first bytes: ``missing``/``empty``/``plaintext``/``opaque``.

    SQLCipher writes ciphertext from byte zero (``cipher_plaintext_header_size = 0``), so the first
    16 bytes answer the only question that matters before opening: was this file written by the
    SQLCipher path or the stock-sqlite3 path? Guessing wrong is not a subtle failure -- the driver
    reports ``file is not a database``, which names neither the file nor the policy that would have
    opened it. ``opaque`` is the honest word for "not a SQLite container": encrypted by us, or
    something else entirely.
    """
    try:
        if not db_file.exists() or db_file.stat().st_size == 0:
            return "missing" if not db_file.exists() else "empty"
        with open(db_file, "rb") as fh:
            head = fh.read(len(PLAIN_SQLITE_MAGIC))
    except OSError:  # unreadable for a reason we cannot fix here; let the driver report it
        return "unknown"
    return "plaintext" if head == PLAIN_SQLITE_MAGIC else "opaque"


def _refuse_mismatch(db_file: Path, *, reader: str, on_disk: str, mode: str) -> None:
    """Explain a policy/file mismatch instead of letting the driver say "not a database".

    Both directions happen in practice: a database written under the default policy and then
    opened with ``field-only`` (or ``off``), and a plaintext file from an early run opened after
    the default policy became ``auto``. Either way the operator sees a stack trace from
    SQLAlchemy, and the fix is one environment variable they have no way of guessing from it.
    """
    if reader == "sqlcipher" and on_disk == "plaintext":
        raise RuntimeError(
            f"{db_file} is an unencrypted SQLite database, but ADAPT_DB_ENCRYPTION={mode} opens "
            "databases with SQLCipher. It was most likely written by a run with "
            "ADAPT_DB_ENCRYPTION=field-only or off. Either set ADAPT_DB_ENCRYPTION to the policy "
            "that wrote it (field-only keeps the column-level encryption on learner text), or point "
            "ADAPT_DATA_DIR at a different directory to start a new database."
        )
    if reader == "sqlite" and on_disk == "opaque":
        raise RuntimeError(
            f"{db_file} does not start with the SQLite header, so the sqlite3 driver cannot read "
            f"it, but ADAPT_DB_ENCRYPTION={mode} opens databases with sqlite3 rather than SQLCipher. "
            "If ADAPT wrote this file it was under ADAPT_DB_ENCRYPTION=auto or sqlcipher. Either set "
            "ADAPT_DB_ENCRYPTION to what wrote it, or point ADAPT_DATA_DIR at a different directory "
            "to start a new database."
        )


def create_db_engine(settings: Settings, *, db_file: Path | None = None) -> tuple[Engine, DriverInfo]:
    """Build the engine, applying the encryption policy from settings."""
    db_file = Path(db_file or settings.db_file)
    db_file.parent.mkdir(parents=True, exist_ok=True)

    cipher = build_keyring(settings)
    assert cipher is not None
    keyring = get_keyring()
    mode = settings.db_encryption
    sqlcipher = _import_sqlcipher() if mode in ("auto", "sqlcipher") else None
    is_file = ":memory:" not in str(db_file)

    if is_file:
        # Fail before the engine is built: the driver's own complaint about a mismatched file is a
        # traceback through SQLAlchemy's pool, and it never mentions the policy.
        _refuse_mismatch(
            db_file,
            reader="sqlcipher" if sqlcipher is not None else "sqlite",
            on_disk=_on_disk_format(db_file),
            mode=mode,
        )

    if sqlcipher is not None and is_file:
        engine = create_engine(
            f"sqlite:///{db_file}",
            module=sqlcipher,
            future=True,
            connect_args={"check_same_thread": False},
        )
        key_hex = keyring.subkey("sqlcipher").hex()
        memory_kib = max(128, settings.sqlcipher_memory // 1024)

        @event.listens_for(engine, "connect")
        def _sqlcipher_pragmas(dbapi_conn, _record):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute(f'PRAGMA key = "x\'{key_hex}\'"')
            cur.execute(f"PRAGMA kdf_iter = {settings.sqlcipher_kdf_iter}")
            cur.execute("PRAGMA cipher_compatibility = 4")
            cur.execute(f"PRAGMA cipher_memory_capacity = {memory_kib}")
            cur.execute("PRAGMA cipher_plaintext_header_size = 0")
            cur.execute("PRAGMA foreign_keys = ON")
            cur.execute("PRAGMA busy_timeout = 5000")
            cur.execute("PRAGMA journal_mode = WAL")
            cur.execute("PRAGMA synchronous = NORMAL")
            cur.close()

        info = DriverInfo(
            "sqlcipher",
            True,
            "SQLCipher 4, AES-256-CBC + HMAC-SHA512, "
            f"{settings.sqlcipher_kdf_iter} KDF iterations, cipher_plaintext_header_size=0",
            keyring.source,
        )
        return engine, info

    if mode == "sqlcipher":
        raise RuntimeError(
            "ADAPT_DB_ENCRYPTION=sqlcipher but the sqlcipher3 driver is unavailable. "
            "Install it with: pip install 'sqlcipher3-binary'"
        )

    engine = create_engine(
        f"sqlite:///{db_file}",
        future=True,
        connect_args={"check_same_thread": False} if is_file else {},
    )
    event.listen(engine, "connect", _sqlite_pragmas(is_file))

    if mode == "off":
        # Operator explicitly opted out: drop column encryption as well, so the
        # intent is unambiguous and `doctor` flags the whole file as unprotected.
        from ..crypto.fields import set_cipher

        set_cipher(None)
        info = DriverInfo(
            "sqlite", False, "encryption disabled by ADAPT_DB_ENCRYPTION=off (not recommended)", keyring.source
        )
        log.warning("ENCRYPTION DISABLED by configuration -- learner data is plaintext")
    else:
        info = DriverInfo(
            "sqlite",
            False,
            "SQLCipher driver unavailable -> column-level AES-256-GCM only",
            keyring.source,
        )
        if mode != "field-only":
            log.warning("falling back to column-level encryption: %s", info.detail)
    return engine, info


class Database:
    @property
    def db_file(self) -> Path:
        """The file this engine opened -- which is not always ``settings.db_file``."""
        return self._db_file

    def __init__(self, settings: Settings | None = None, *, db_file: Path | None = None) -> None:
        self.settings = settings or get_settings()
        self.engine, self.driver = create_db_engine(self.settings, db_file=db_file)
        self._db_file = Path(db_file or self.settings.db_file)
        self.sessionmaker = sessionmaker(bind=self.engine, expire_on_commit=False, future=True)

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)

    def drop_all(self) -> None:
        Base.metadata.drop_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        s = self.sessionmaker()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def dispose(self) -> None:
        self.engine.dispose()

    @property
    def url(self) -> str:
        return str(self.engine.url)

    def header_is_plaintext(self) -> bool:
        """True if the DB file is a readable (unencrypted) SQLite container."""
        path = self._db_file
        if not path.exists():
            return False
        with open(path, "rb") as fh:
            return fh.read(16) == PLAIN_SQLITE_MAGIC

    def file_encrypted(self) -> bool:
        """Verified claim: the file itself is ciphertext (or nothing exists yet)."""
        if self.driver.name != "sqlcipher":
            return False
        return not self.header_is_plaintext()

    def student_count(self) -> int:
        with self.session() as s:
            return int(s.execute(text("SELECT COUNT(*) FROM students")).scalar() or 0)


_shared: Database | None = None
_lock = threading.Lock()


def get_db() -> Database:
    global _shared
    if _shared is None:
        with _lock:
            if _shared is None:
                _shared = Database()
                log.info(
                    "database ready: %s driver=%s file_encrypted=%s key=%s",
                    _shared.url,
                    _shared.driver.name,
                    _shared.file_encrypted(),
                    _shared.driver.key_source,
                )
    return _shared


def set_db(db: Database | None) -> None:
    """Override the singleton (tests, CLI)."""
    global _shared
    if _shared is not None and _shared is not db:
        try:
            _shared.dispose()
        except Exception:  # pragma: no cover
            pass
    _shared = db


@contextmanager
def session_scope() -> Iterator[Session]:
    yield from get_db().session()
