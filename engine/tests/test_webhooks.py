"""Standard Webhooks: sign, verify, and every way verification must refuse.

The rules under test are the ones that make a signature worth having, and each
has a failure mode that still "works" if you remove it:

* freshness — a captured delivery replays forever;
* the HMAC — anything you like is a valid webhook;
* the version tag — a future scheme can be downgraded into an old parser;
* dedupe — one message becomes N events at the receiver.

The duplicate case is asserted as an **outcome**, not an exception: the same
message arriving twice is not an error, it is one event delivered twice, and the
receiver's answer is "already have it".
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime

import pytest

from adapt_engine.connections import service as connections
from adapt_engine.webhooks import dispatch, signing

SECRET = "whsec_" + base64.b64encode(b"0123456789abcdefghijklmn").decode()
OTHER_SECRET = "whsec_" + base64.b64encode(b"klmnopqrstuvwxyz012345").decode()
NOW = 1_780_000_000


def _body(**overrides: object) -> bytes:
    payload = {"id": "msg_" + "a" * 32, "type": "decision.created", "data": {"id": "dcn_1"}}
    payload.update(overrides)
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


def _msg_id(n: int = 0) -> str:
    return "msg_" + f"{n:032x}"


def _verify(
    body: bytes, headers: dict[str, str], *, now: int = NOW, secret: str = SECRET, seen=None
):
    return signing.verify(
        secret,
        headers,
        body,
        now=now,
        seen=seen if seen is not None else signing.MemorySeenIds(),
    )


def test_a_signed_delivery_verifies_and_is_deliverable() -> None:
    body = _body()
    headers = signing.headers_for(SECRET, msg_id=_msg_id(), timestamp=NOW, body=body)

    verified = _verify(body, headers)

    assert verified.outcome is signing.Outcome.DELIVER
    assert verified.msg_id == _msg_id()
    # The header set is exactly the documented triple plus nothing secret.
    assert set(headers) == {"webhook-id", "webhook-timestamp", "webhook-signature"}
    assert headers["webhook-signature"].startswith("v1,")


def test_a_forged_signature_is_refused() -> None:
    """Change one byte of the body and the HMAC no longer matches."""
    body = _body()
    headers = signing.headers_for(SECRET, msg_id=_msg_id(), timestamp=NOW, body=body)
    tampered = body.replace(b"dcn_1", b"dcn_2")

    with pytest.raises(signing.WebhookRejected) as excinfo:
        _verify(tampered, headers)
    assert excinfo.value.reason == "signature_mismatch"


def test_a_signature_from_another_secret_is_refused() -> None:
    """A receiver that accepts any well-formed signature accepts any forgery."""
    body = _body()
    headers = signing.headers_for(OTHER_SECRET, msg_id=_msg_id(), timestamp=NOW, body=body)

    with pytest.raises(signing.WebhookRejected) as excinfo:
        _verify(body, headers)
    assert excinfo.value.reason == "signature_mismatch"


def test_a_replay_outside_the_window_is_refused() -> None:
    """Five minutes is the window; a day-old capture is not a delivery."""
    body = _body()
    headers = signing.headers_for(SECRET, msg_id=_msg_id(), timestamp=NOW, body=body)

    with pytest.raises(signing.WebhookRejected) as excinfo:
        _verify(body, headers, now=NOW + signing.TOLERANCE_SECONDS + 1)
    assert excinfo.value.reason == "timestamp_out_of_window"

    # A timestamp far in the future is the same problem from the other side:
    # a receiver that only checks "not too old" accepts a message signed with a
    # clock it cannot account for.
    with pytest.raises(signing.WebhookRejected) as future:
        _verify(body, headers, now=NOW - signing.TOLERANCE_SECONDS - 1)
    assert future.value.reason == "timestamp_out_of_window"


def test_a_delivery_inside_the_window_is_accepted_at_the_edges() -> None:
    body = _body()
    headers = signing.headers_for(SECRET, msg_id=_msg_id(), timestamp=NOW, body=body)
    assert _verify(body, headers, now=NOW + signing.TOLERANCE_SECONDS).outcome is (
        signing.Outcome.DELIVER
    )


def test_a_duplicate_msg_id_is_a_no_op_not_a_second_delivery() -> None:
    """The same message twice is one event: deliver, then report duplicate."""
    body = _body()
    seen = signing.MemorySeenIds()
    headers = signing.headers_for(SECRET, msg_id=_msg_id(7), timestamp=NOW, body=body)

    first = _verify(body, headers, seen=seen)
    second = _verify(body, headers, seen=seen)

    assert first.outcome is signing.Outcome.DELIVER
    assert second.outcome is signing.Outcome.DUPLICATE
    # A different message with a valid signature is still delivered.
    other = signing.headers_for(SECRET, msg_id=_msg_id(8), timestamp=NOW, body=body)
    assert _verify(body, other, seen=seen).outcome is signing.Outcome.DELIVER


def test_a_forged_message_cannot_poison_the_seen_set() -> None:
    """Dedupe is the only check that writes, so it runs last.

    Otherwise an attacker who can reach the endpoint fills the set with ids and
    the legitimate delivery of that id is dropped as a duplicate.
    """
    body = _body()
    seen = signing.MemorySeenIds()
    forged = signing.headers_for(OTHER_SECRET, msg_id=_msg_id(9), timestamp=NOW, body=body)

    with pytest.raises(signing.WebhookRejected):
        _verify(body, forged, seen=seen)

    good = signing.headers_for(SECRET, msg_id=_msg_id(9), timestamp=NOW, body=body)
    assert _verify(body, good, seen=seen).outcome is signing.Outcome.DELIVER


def test_an_unsupported_version_is_refused() -> None:
    body = _body()
    headers = signing.headers_for(SECRET, msg_id=_msg_id(), timestamp=NOW, body=body)
    headers["webhook-signature"] = headers["webhook-signature"].replace("v1,", "v2,", 1)

    with pytest.raises(signing.WebhookRejected) as excinfo:
        _verify(body, headers)
    assert excinfo.value.reason == "signature_version_unsupported"


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda h: h.pop("webhook-id"), "msg_id_malformed"),
        (lambda h: h.__setitem__("webhook-id", "not-a-msg-id"), "msg_id_malformed"),
        (lambda h: h.pop("webhook-timestamp"), "timestamp_missing"),
        (lambda h: h.__setitem__("webhook-timestamp", "yesterday"), "timestamp_malformed"),
        (lambda h: h.pop("webhook-signature"), "signature_missing"),
        (lambda h: h.__setitem__("webhook-signature", "v1,"), "signature_version_unsupported"),
    ],
)
def test_malformed_headers_are_refused_with_a_reason(mutate, reason: str) -> None:
    """Every refusal names what was wrong; none of them logs the body."""
    body = _body()
    headers = signing.headers_for(SECRET, msg_id=_msg_id(), timestamp=NOW, body=body)
    mutate(headers)

    with pytest.raises(signing.WebhookRejected) as excinfo:
        _verify(body, headers)
    assert excinfo.value.reason == reason


def test_a_malformed_secret_is_refused_not_guessed() -> None:
    body = _body()
    with pytest.raises(signing.WebhookRejected) as excinfo:
        signing.sign("not-a-whsec-secret", msg_id=_msg_id(), timestamp=NOW, body=body)
    assert excinfo.value.reason == "secret_malformed"


def test_the_seen_set_is_bounded() -> None:
    """A memory of every id ever seen is a leak with a security story."""
    seen = signing.MemorySeenIds(capacity=8, now=NOW)
    for n in range(32):
        seen.seen_before(_msg_id(n))
    assert len(seen._entries) <= 8


def test_the_notification_set_is_exactly_the_documented_five() -> None:
    """§8 lists five types; a type the doc names must be enqueueable."""
    assert {
        "decision.created",
        "consent.changed",
        "quota.warning",
        "learner.erased",
        "instrument.not_renderable",
    } == connections.EVENT_TYPES


def test_jitter_is_reproducible_for_one_delivery() -> None:
    """Same delivery, same attempt, same second time — a replay is reproducible."""
    delivery_id = uuid.UUID("2f8f6c1e-0000-4000-8000-000000000001")
    now = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
    first = dispatch._next_attempt_at(delivery_id, 1, now)
    second = dispatch._next_attempt_at(delivery_id, 1, now)
    assert first == second
    assert 0.8 <= (first - now).total_seconds() <= 1.2


def test_two_deliveries_do_not_retry_at_the_same_instant() -> None:
    """The point of jitter: a hundred receivers coming back at once fan out."""
    now = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
    scheduled = {dispatch._next_attempt_at(uuid.uuid4(), 4, now) for _ in range(20)}
    assert len(scheduled) > 1
    for at in scheduled:
        assert at is not None
        assert 12.8 <= (at - now).total_seconds() <= 76.8


def test_the_fifth_attempt_has_no_successor() -> None:
    now = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
    assert dispatch._next_attempt_at(uuid.uuid4(), dispatch.MAX_ATTEMPTS, now) is None
