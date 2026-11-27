"""Standard Webhooks signing and verification — the sender and the receiver rules.

The triple is the whole protocol::

    webhook-id:        msg_<32 hex>          # the message, stable across retries
    webhook-timestamp: <unix seconds>        # when this attempt was signed
    webhook-signature: v1,<base64 HMAC-SHA256>

and the signed content is ``"{id}.{timestamp}.{body}"``, keyed with the secret's
base64 part. Two rules make it worth something:

* **The timestamp is fresh.** A signature that verified a week ago still
  verifies today unless the receiver bounds the window; we use five minutes,
  which is the published recommendation, and the check is on the *receiver's*
  clock, never on the timestamp's word about itself.
* **The message id is remembered.** Within the window a replay is possible, so
  the receiver keeps ids it has already accepted. A duplicate id is a **no-op**
  — the same message arriving twice is not two things happening — while a fresh
  id with a valid signature is delivered.

Both halves live here because a sender that cannot verify what it sends has no
idea whether its receiver will accept it, and the tests exercise ours against
ours: sign a payload, verify it, then break each rule in turn and watch it
refuse.

**What the secret is.** ``whsec_`` + base64 of 24 random bytes. It is stored
encrypted under the tenant data key (the value envelope, AAD
``(tenant, connection row, 'webhook_secret')``), never logged, and returned
exactly once when the connection is created — the same once-only rule as an API
key.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

#: Five minutes. Replays arrive with a stale signature, and a receiver that
#: does not check this accepts them forever.
TOLERANCE_SECONDS = 300

#: ``v1`` is the only scheme. Versioning the scheme is what lets the format
#: change without a flag day; accepting an unknown version would defeat it.
SIGNATURE_VERSION = "v1"

MSG_ID_PATTERN = re.compile(r"^msg_[0-9a-f]{32}$")
SECRET_PREFIX = "whsec_"

HEADER_ID = "webhook-id"
HEADER_TIMESTAMP = "webhook-timestamp"
HEADER_SIGNATURE = "webhook-signature"


class WebhookRejected(Exception):
    """Verification failed. The reason is a machine-readable token, not prose."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class Outcome(StrEnum):
    """What the receiver should do with a verified message."""

    DELIVER = "deliver"
    DUPLICATE = "duplicate"


class SeenIds(Protocol):
    """Ids already accepted, with a bounded memory."""

    def seen_before(self, msg_id: str) -> bool:
        """True if this id was accepted before; records it if not."""


class MemorySeenIds:
    """A bounded, time-aware set of accepted ids.

    Bounded because an unbounded set is a memory leak with a security story,
    and time-aware because an id older than the window can never be replayed
    successfully anyway. Entries are dropped oldest-first.
    """

    def __init__(self, *, capacity: int = 4096, now: float | None = None) -> None:
        self._capacity = capacity
        self._entries: dict[str, float] = {}
        self._now = now

    def seen_before(self, msg_id: str) -> bool:
        current = self._now if self._now is not None else time.time()
        self._evict(current)
        if msg_id in self._entries:
            return True
        self._entries[msg_id] = current
        return False

    def _evict(self, now: float) -> None:
        expired = [key for key, at in self._entries.items() if now - at > TOLERANCE_SECONDS]
        for key in expired:
            del self._entries[key]
        while len(self._entries) >= self._capacity:
            oldest = min(self._entries, key=lambda key: self._entries[key])
            del self._entries[oldest]


def new_msg_id() -> str:
    """A message id: ``msg_`` + 32 hex characters, the same shape Standard uses."""
    return "msg_" + secrets.token_hex(16)


def _key(secret: str) -> bytes:
    if not secret.startswith(SECRET_PREFIX):
        raise WebhookRejected("secret_malformed")
    try:
        return base64.b64decode(secret[len(SECRET_PREFIX) :], validate=True)
    except Exception as exc:
        raise WebhookRejected("secret_malformed") from exc


def sign(secret: str, *, msg_id: str, timestamp: int, body: bytes) -> str:
    """The ``webhook-signature`` header value for one attempt."""
    if not MSG_ID_PATTERN.match(msg_id):
        raise WebhookRejected("msg_id_malformed")
    signed = f"{msg_id}.{timestamp}.{body.decode('utf-8')}".encode()
    digest = hmac.new(_key(secret), signed, hashlib.sha256).digest()
    return f"{SIGNATURE_VERSION},{base64.b64encode(digest).decode('ascii')}"


def headers_for(secret: str, *, msg_id: str, timestamp: int, body: bytes) -> dict[str, str]:
    """The three headers, ready to send."""
    return {
        HEADER_ID: msg_id,
        HEADER_TIMESTAMP: str(timestamp),
        HEADER_SIGNATURE: sign(secret, msg_id=msg_id, timestamp=timestamp, body=body),
    }


@dataclass(frozen=True)
class Verified:
    """A message that passed every check."""

    msg_id: str
    timestamp: int
    outcome: Outcome


def verify(
    secret: str,
    headers: dict[str, str],
    body: bytes,
    *,
    now: int,
    seen: SeenIds,
    tolerance_seconds: int = TOLERANCE_SECONDS,
) -> Verified:
    """Verify a delivery, or raise :class:`WebhookRejected`.

    Order matters and is fixed: shape, then freshness, then signature, then
    dedupe. Freshness before the HMAC is deliberate — an attacker replaying an
    old message gets a cheap rejection instead of making us compute a
    comparison, and the timestamp field is attacker-controlled either way, so
    nothing about the order leaks the secret.

    Dedupe is last because it is the only check that *writes*: a forged message
    must not be able to fill the seen-set.
    """
    lowered = {key.lower(): value for key, value in headers.items()}
    msg_id = lowered.get(HEADER_ID)
    raw_timestamp = lowered.get(HEADER_TIMESTAMP)
    signature = lowered.get(HEADER_SIGNATURE)

    if not msg_id or not MSG_ID_PATTERN.match(msg_id):
        raise WebhookRejected("msg_id_malformed")
    if not raw_timestamp:
        raise WebhookRejected("timestamp_missing")
    try:
        timestamp = int(raw_timestamp)
    except ValueError as exc:
        raise WebhookRejected("timestamp_malformed") from exc

    skew = abs(now - timestamp)
    if skew > tolerance_seconds:
        raise WebhookRejected("timestamp_out_of_window")

    if not signature:
        raise WebhookRejected("signature_missing")
    version, _, provided = signature.partition(",")
    if version != SIGNATURE_VERSION or not provided:
        raise WebhookRejected("signature_version_unsupported")

    expected = sign(secret, msg_id=msg_id, timestamp=timestamp, body=body).partition(",")[2]
    # compare_digest on the base64 text: the strings are the same length only
    # when the scheme matches, and the comparison must not early-exit.
    if not hmac.compare_digest(expected, provided):
        raise WebhookRejected("signature_mismatch")

    if seen.seen_before(msg_id):
        return Verified(msg_id=msg_id, timestamp=timestamp, outcome=Outcome.DUPLICATE)
    return Verified(msg_id=msg_id, timestamp=timestamp, outcome=Outcome.DELIVER)
