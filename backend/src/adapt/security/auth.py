"""AuthN: password hashing, opaque session tokens, CSRF, guest/demo access.

Threat model for the demo (docs/SECURITY.md has the full version):
the app is reachable by judges over a public tunnel, so *learner records* must not
be world-readable just because someone guessed the URL. Therefore:

* every learner-touching route requires a session cookie;
* a guest session (the demo path, no password to type in front of a judge) is only
  minted when ``ADAPT_DEMO_PASSPHRASE`` is unset **or** the caller presents it;
* tokens are opaque random values, stored only as SHA-256 hashes, rotated on use,
  HttpOnly + SameSite=Lax + Secure-when-HTTPS;
* mutating requests must echo the CSRF token held in the session record;
* passwords use Argon2id with parameters above the OWASP minimum.
"""

from __future__ import annotations

import re

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db.models import AuthSession, Student, utcnow

_hasher: PasswordHasher | None = None


def _hasher_instance() -> PasswordHasher:
    global _hasher
    if _hasher is None:
        s = get_settings()
        _hasher = PasswordHasher(
            time_cost=s.argon2_time_cost,
            memory_cost=s.argon2_memory_kib,
            parallelism=2,
            hash_len=32,
            salt_len=16,
        )
    return _hasher


def hash_password(password: str) -> str:
    if len(password) < 10:
        raise ValueError("password too short: use at least 10 characters")
    return _hasher_instance().hash(password)


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        return _hasher_instance().verify(stored, password)
    except (VerifyMismatchError, InvalidHashError, Exception):
        return False


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def hash_ip(ip: str | None) -> str | None:
    """IPs are stored only as keyed digests: enough for abuse control, no log of
    where a minor was sitting."""
    if not ip:
        return None
    s = get_settings()
    key = (s.db_key or str(s.keyfile_path)).encode()
    return hmac.new(key, ip.encode(), hashlib.sha256).hexdigest()[:32]


@dataclass
class Issued:
    session: AuthSession
    token: str
    csrf: str


def create_session(
    db: Session,
    student_id: str,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
    hours: int | None = None,
) -> Issued:
    s = get_settings()
    ttl_hours = max(1, int(hours)) if hours is not None else s.session_ttl_hours
    token = new_token()
    csrf = new_token(24)
    row = AuthSession(
        student_id=student_id,
        token_hash=hash_token(token),
        csrf_token=csrf,
        expires_at=utcnow() + timedelta(hours=ttl_hours),
        ip_hash=hash_ip(ip),
        user_agent_hash=hashlib.sha256((user_agent or "")[:200].encode()).hexdigest()[:32]
        if user_agent
        else None,
    )
    db.add(row)
    db.flush()
    return Issued(session=row, token=token, csrf=csrf)


# "last seen" is a 1-minute-resolution presence marker, not an audit field. It is
# written and committed immediately, because an uncommitted UPDATE inside an
# authentication helper would hold SQLite's single write lock for the rest of the
# request -- and every later write in that request would block behind a read path.
LAST_SEEN_THROTTLE_S = 60.0


def lookup_session(db: Session, token: str | None) -> AuthSession | None:
    if not token:
        return None
    row = db.execute(select(AuthSession).where(AuthSession.token_hash == hash_token(token))).scalar_one_or_none()
    if row is None or row.revoked_at is not None:
        return None
    now = utcnow()
    if row.expires_at < now:
        row.revoked_at = now
        db.commit()  # an expired token must stop working now, not at request end
        return None
    if row.last_seen_at is None or (now - row.last_seen_at).total_seconds() >= LAST_SEEN_THROTTLE_S:
        row.last_seen_at = now
        db.commit()
    return row


def revoke(db: Session, row: AuthSession) -> None:
    """Logout is durable immediately: a token that should not work must not stay
    valid for the length of a response."""
    row.revoked_at = utcnow()
    row.expires_at = utcnow()
    db.commit()


def check_csrf(row: AuthSession, provided: str | None) -> bool:
    return bool(provided) and hmac.compare_digest(row.csrf_token, provided)


def demo_passphrase_required() -> bool:
    """True when a public tunnel is live and the operator set a passphrase.

    That combination is the one where a guessed URL could otherwise read learner
    data, so guest sessions require the phrase the presenter whispers to judges.
    """
    return get_settings().mode == "tunnel" and bool(_demo_passphrase())


def _demo_passphrase() -> str | None:
    import os

    return os.environ.get("ADAPT_DEMO_PASSPHRASE") or None


def check_demo_passphrase(provided: str | None) -> bool:
    expected = _demo_passphrase()
    if not expected:
        return True
    return bool(provided) and hmac.compare_digest(expected, provided or "")


def purge_expired(db: Session) -> int:
    stmt = select(AuthSession).where(AuthSession.expires_at < utcnow(), AuthSession.revoked_at.is_(None))
    n = 0
    for row in db.execute(stmt).scalars().all():
        row.revoked_at = utcnow()
        n += 1
    return n


def email_index_for(email: str | None) -> str | None:
    """The keyed digest that makes an encrypted column searchable by equality.

    Creation and lookup must call *this* function: the digest is bound to the column
    label, so a caller that inlines its own ``blind_index(...)`` with a different
    label produces an index that never matches, and login fails only for real users.
    """
    from ..crypto.fields import blind_index

    if not email:
        return None
    return blind_index(email, column="student.email")


USERNAME_MIN, USERNAME_MAX = 3, 40
_USERNAME_OK = re.compile(r"^[a-z0-9][a-z0-9._]*$")


def normalise_username(username: str | None) -> str:
    """The canonical form a username is stored and looked up as.

    Lowercased and trimmed for the same reason an email is: ``Rowan`` and ``rowan`` are one
    person, and letting them be two accounts means the second one silently takes over the
    first one's name on screen.
    """
    return (username or "").strip().lower()


def username_problem(username: str | None) -> str | None:
    """Why this username cannot be used, in the words a learner would read. ``None`` = fine.

    The character set is deliberately small (letters, digits, dot, underscore): a username
    is an identity key that is encrypted and blind indexed, not a display name, and a
    narrow set is the difference between "we can find your row" and "which of these six
    lookalike strings did you mean".
    """
    value = normalise_username(username)
    if not value:
        return "a username is required"
    if len(value) < USERNAME_MIN:
        return f"a username needs at least {USERNAME_MIN} characters"
    if len(value) > USERNAME_MAX:
        return f"a username can be at most {USERNAME_MAX} characters"
    if not _USERNAME_OK.match(value):
        return "letters, numbers, dots and underscores only, starting with a letter or a number"
    return None


def username_index_for(username: str | None) -> str | None:
    """The keyed digest that makes the encrypted username searchable by equality.

    Creation and lookup must call *this* function: the digest is bound to the column
    label, so a caller that inlines its own ``blind_index(...)`` with a different label
    produces an index that never matches, and sign-in fails only for real users.
    """
    from ..crypto.fields import blind_index

    value = normalise_username(username)
    if not value:
        return None
    return blind_index(value, column="student.username")


def student_by_username(db: Session, username: str | None) -> Student | None:
    value = normalise_username(username)
    if not value:
        return None
    idx = username_index_for(value)
    if not idx:
        # Cipher disabled (ADAPT_DB_ENCRYPTION=off): compare the plaintext column, exactly as
        # the email lookup does, and never fall back to "some student with no index".
        return db.execute(
            select(Student).where(func.lower(Student.username) == value, Student.deleted_at.is_(None))
        ).scalars().first()
    return db.execute(
        select(Student).where(Student.username_index == idx, Student.deleted_at.is_(None))
    ).scalars().first()


def student_by_email(db: Session, email: str) -> Student | None:
    email = (email or "").strip()
    if not email:
        return None
    idx = email_index_for(email)
    if not idx:
        # Cipher disabled (ADAPT_DB_ENCRYPTION=off): the blind index is not usable, so
        # compare the plaintext column. The fallback must never be "some student whose
        # index is null" -- that would answer a lookup for one person with another
        # person's row, which is both a login bug and an account-recovery bug.
        return db.execute(select(Student).where(func.lower(Student.email) == email.lower())).scalars().first()
    return db.execute(select(Student).where(Student.email_index == idx)).scalar_one_or_none()
