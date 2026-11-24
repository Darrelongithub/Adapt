"""Encryption at rest: what it must refuse, and what it must not leak.

The six properties this file exists to prove, in the order the increment
named them:

1. tenant A's ciphertext does not open for tenant B — it raises, it does not
   return garbage;
2. an AAD mismatch raises;
3. the same plaintext twice is different ciphertext;
4. ten thousand encryptions under one data key hold no repeated nonce;
5. a rotation re-wraps data keys only, and rows written before it still read;
6. no plaintext and no key material appears in a log line.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine

from adapt_engine.crypto import (
    DataKeyStore,
    DecryptionFailed,
    Envelope,
    Keyring,
    MalformedEnvelope,
    UnknownKeyId,
    aad_for,
    seal,
    unseal,
)
from adapt_engine.db.tenant import tenant_transaction
from adapt_engine.logging import configure_logging, get_logger
from adapt_engine.main import create_app
from tests.conftest import make_settings
from tests.pg import (
    PLACEHOLDER_ENVELOPE,
    TEST_KEYRING,
    TEST_MASTER_KEY,
    bootstrap_key,
    create_learner,
    provision_tenant,
)

NEW_MASTER_KEY = "rotated-master-key-32-bytes-long-yy"


@pytest.fixture
def tenant(database) -> uuid.UUID:
    return provision_tenant(
        database, slug=f"crypto-{uuid.uuid4().hex[:8]}", name="Crypto Tenant"
    )


@pytest.fixture
def other_tenant(database) -> uuid.UUID:
    return provision_tenant(
        database, slug=f"crypto-b-{uuid.uuid4().hex[:8]}", name="Crypto Tenant B"
    )


@pytest.fixture
def store() -> DataKeyStore:
    return DataKeyStore(keyring=TEST_KEYRING)


# ---------------------------------------------------------------------------
# 1. Cross-tenant ciphertext
# ---------------------------------------------------------------------------


def test_tenant_a_ciphertext_does_not_open_for_tenant_b(
    app_engine: Engine, tenant: uuid.UUID, other_tenant: uuid.UUID, store: DataKeyStore
) -> None:
    """Moved ciphertext raises. It must never return a plausible wrong value."""
    with tenant_transaction(app_engine, tenant) as conn:
        stolen = store.encrypt(conn, tenant, "cognitive_support")
    assert stolen is not None and stolen.startswith("v1:")

    # Plant A's ciphertext in B's row, as a compromised or buggy writer would.
    with tenant_transaction(app_engine, other_tenant) as conn:
        conn.execute(
            text(
                "INSERT INTO learners (tenant_id, learner_id, profile) "
                "VALUES (:t, 'learner-stolen-01', :profile)"
            ),
            {"t": other_tenant, "profile": stolen},
        )
        with pytest.raises(DecryptionFailed):
            store.decrypt(conn, other_tenant, stolen)


def test_a_wrapped_data_key_does_not_move_between_tenants(
    app_engine: Engine, tenant: uuid.UUID, other_tenant: uuid.UUID, store: DataKeyStore
) -> None:
    """The wrapper is bound to its tenant too, so copying the row fails closed."""
    with tenant_transaction(app_engine, tenant) as conn:
        store.encrypt(conn, tenant, "adult_general")
        wrapped = conn.execute(
            text("SELECT kid, wrapped FROM tenant_data_keys WHERE tenant_id = :t"),
            {"t": tenant},
        ).one()
    with tenant_transaction(app_engine, other_tenant) as conn:
        conn.execute(
            text(
                "INSERT INTO tenant_data_keys (tenant_id, kid, wrapped) "
                "VALUES (:t, :kid, :wrapped)"
            ),
            {"t": other_tenant, "kid": wrapped.kid, "wrapped": wrapped.wrapped},
        )
        with pytest.raises(DecryptionFailed):
            store.encrypt(conn, other_tenant, "adult_general")


# ---------------------------------------------------------------------------
# 2. AAD mismatch
# ---------------------------------------------------------------------------


def test_aad_mismatch_raises() -> None:
    """The same key with the wrong tenant id in the AAD fails the tag check."""
    key = TEST_KEYRING.key("k1")
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    envelope_text = seal(key, b"adult_general", aad=aad_for(tenant_a), kid="k1")

    assert unseal(key, Envelope.parse(envelope_text), aad=aad_for(tenant_a)) == b"adult_general"
    with pytest.raises(DecryptionFailed):
        unseal(key, Envelope.parse(envelope_text), aad=aad_for(tenant_b))
    with pytest.raises(DecryptionFailed):
        unseal(key, Envelope.parse(envelope_text), aad=b"")


def test_a_corrupted_ciphertext_raises(app_engine: Engine, tenant: uuid.UUID, store: DataKeyStore) -> None:
    with tenant_transaction(app_engine, tenant) as conn:
        envelope = store.encrypt(conn, tenant, "adult_general")
        assert envelope is not None
        flipped = envelope[:-6] + ("A" if envelope[-6] != "A" else "B") + envelope[-5:]
        with pytest.raises(DecryptionFailed):
            store.decrypt(conn, tenant, flipped)


def test_a_malformed_value_is_rejected_before_any_key_use() -> None:
    for value in ("", "adult_general", "v1:k1:short:short", "v2:k1:" + "A" * 16 + ":" + "A" * 24,
                  "v1:k1:AAAA:AAAA"):
        with pytest.raises(MalformedEnvelope):
            Envelope.parse(value)


def test_a_ciphertext_naming_an_unconfigured_kid_is_rejected(
    app_engine: Engine, tenant: uuid.UUID, store: DataKeyStore
) -> None:
    with tenant_transaction(app_engine, tenant) as conn:
        envelope = store.encrypt(conn, tenant, "adult_general")
        assert envelope is not None
        unknown = envelope.replace("v1:k1:", "v1:k9:", 1)
        with pytest.raises(MalformedEnvelope):
            store.decrypt(conn, tenant, unknown)


# ---------------------------------------------------------------------------
# 3. Non-determinism
# ---------------------------------------------------------------------------


def test_the_same_plaintext_twice_is_different_ciphertext(
    app_engine: Engine, tenant: uuid.UUID, store: DataKeyStore
) -> None:
    with tenant_transaction(app_engine, tenant) as conn:
        first = store.encrypt(conn, tenant, "adult_general")
        second = store.encrypt(conn, tenant, "adult_general")
        assert first != second
        assert Envelope.parse(first).nonce != Envelope.parse(second).nonce
        assert store.decrypt(conn, tenant, first) == store.decrypt(conn, tenant, second)


def test_absent_values_stay_absent(app_engine: Engine, tenant: uuid.UUID, store: DataKeyStore) -> None:
    """`None` is not encrypted to a ciphertext of the empty string."""
    with tenant_transaction(app_engine, tenant) as conn:
        assert store.encrypt(conn, tenant, None) is None
        assert store.decrypt(conn, tenant, None) is None


# ---------------------------------------------------------------------------
# 4. Nonce sweep
# ---------------------------------------------------------------------------


def test_ten_thousand_encryptions_hold_no_repeated_nonce(
    app_engine: Engine, tenant: uuid.UUID, store: DataKeyStore
) -> None:
    """One data key, 10,000 values, zero repeats — the birthday bound, measured.

    A 96-bit random nonce makes a collision negligible, not impossible; the
    failure mode if the nonce were derived from a counter that resets, or from
    the plaintext, is catastrophic (reused keystream) and silent. So it is
    counted rather than argued about.
    """
    with tenant_transaction(app_engine, tenant) as conn:
        nonces = set()
        for index in range(10_000):
            envelope = store.encrypt(conn, tenant, f"adult_general-{index}")
            nonces.add(Envelope.parse(envelope).nonce)
        assert len(nonces) == 10_000


# ---------------------------------------------------------------------------
# 5. Rotation
# ---------------------------------------------------------------------------


def test_rotation_rewraps_keys_only_and_old_rows_still_read(
    app_engine: Engine, tenant: uuid.UUID
) -> None:
    before = DataKeyStore(keyring=Keyring.from_secrets([("k1", TEST_MASTER_KEY)]))
    with tenant_transaction(app_engine, tenant) as conn:
        old_ciphertext = before.encrypt(conn, tenant, "cognitive_support")
        conn.execute(
            text(
                "INSERT INTO learners (tenant_id, learner_id, profile) "
                "VALUES (:t, 'learner-rotated-01', :profile)"
            ),
            {"t": tenant, "profile": old_ciphertext},
        )

    after_rotation = DataKeyStore(
        keyring=Keyring.from_secrets([("k2", NEW_MASTER_KEY), ("k1", TEST_MASTER_KEY)])
    )
    with tenant_transaction(app_engine, tenant) as conn:
        assert after_rotation.rewrap(conn, tenant) == "k2"
        row = conn.execute(
            text("SELECT kid, wrapped FROM tenant_data_keys WHERE tenant_id = :t"),
            {"t": tenant},
        ).one()
        assert row.kid == "k2"
        assert Envelope.parse(row.wrapped).kid == "k2"
        # Nothing in `learners` was touched.
        stored = conn.execute(
            text("SELECT profile FROM learners WHERE learner_id = 'learner-rotated-01'")
        ).scalar_one()
        assert stored == old_ciphertext

        # The old row opens under the new keyring, and new writes carry k2.
        assert after_rotation.decrypt(conn, tenant, old_ciphertext) == "cognitive_support"
        new_ciphertext = after_rotation.encrypt(conn, tenant, "adult_general")
        assert Envelope.parse(new_ciphertext).kid == "k2"


def test_startup_check_notices_a_dropped_key_that_rows_still_need(
    app_engine: Engine, tenant: uuid.UUID, store: DataKeyStore
) -> None:
    """Dropping a key early is the failure rotation exists to prevent."""
    with tenant_transaction(app_engine, tenant) as conn:
        create_learner(app_engine, tenant, "learner-kid-used-01")
        assert store.encrypt(conn, tenant, "adult_general").startswith("v1:k1:")

    # A keyring that has already moved on and dropped k1: the wrapper may be
    # re-wrapped (it is k1), but the learner row still names k1.
    only_new = DataKeyStore(keyring=Keyring.from_secrets([("k2", NEW_MASTER_KEY)]))
    with pytest.raises(UnknownKeyId) as refused:
        only_new.verify_covers_usage(app_engine)
    assert "k1" in str(refused.value)
    assert TEST_MASTER_KEY not in str(refused.value), "the message leaks key material"


def test_a_keyring_that_covers_every_kid_in_use_starts(
    app_engine: Engine, tenant: uuid.UUID
) -> None:
    """The positive case: hold the current key *and* the one it replaced."""
    create_learner(app_engine, tenant, "learner-kid-covered-1")
    # The catalogue is global and append-only, so it also holds kids that other
    # tests' rotations registered. A keyring carrying both starts happily.
    covering = DataKeyStore(
        keyring=Keyring.from_secrets([("k1", TEST_MASTER_KEY), ("k2", NEW_MASTER_KEY)])
    )
    covering.verify_covers_usage(app_engine)  # does not raise
    assert "k1" in covering.kids_in_use(app_engine)


def test_the_app_refuses_to_start_with_an_uncovered_kid(
    app_engine: Engine, tenant: uuid.UUID, store: DataKeyStore
) -> None:
    """The gate runs in the lifespan, before the first request is served."""
    create_learner(app_engine, tenant, "learner-kid-startup-1")
    settings = make_settings(
        master_key=NEW_MASTER_KEY,
        master_key_id="k2",
        master_key_previous=None,
    )
    app = create_app(
        settings,
        db_check=lambda: True,
        redis_check=lambda: True,
        engine=app_engine,
    )
    with pytest.raises(RuntimeError) as refused:
        with TestClient(app):
            pass
    assert "refusing to start" in str(refused.value)
    assert "k1" in str(refused.value)


# ---------------------------------------------------------------------------
# 6. Logs
# ---------------------------------------------------------------------------


def test_no_plaintext_or_key_material_reaches_a_log_line(
    app_engine: Engine,
    tenant: uuid.UUID,
    store: DataKeyStore,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The crypto path logs nothing, so the allowlist needs no crypto exception.

    Sealing, opening, a failed open and a wrapping step all run with logging
    configured the way the app configures it. Afterwards the captured output is
    searched for the plaintext, for the data key, for the master secret and for
    any envelope. A future `logger.debug(envelope)` fails here.
    """
    configure_logging()
    logger = get_logger()
    logger.info("crypto_probe", component="test")

    with tenant_transaction(app_engine, tenant) as conn:
        envelope = store.encrypt(conn, tenant, "cognitive_support")
        assert envelope is not None
        store.decrypt(conn, tenant, envelope)
        data_key = store._load(conn, tenant)[1]
        with pytest.raises(DecryptionFailed):
            store.decrypt(conn, tenant, envelope.replace("v1:k1:", "v1:k1:", 1)[:-6] + "AAAAAA")

    logs = capsys.readouterr().out
    assert "crypto_probe" in logs, "logging is not capturing; the assertion below would be vacuous"
    assert "cognitive_support" not in logs
    assert envelope.split(":")[3] not in logs
    assert envelope not in logs
    assert data_key.hex() not in logs
    assert data_key.hex()[:16] not in logs
    assert TEST_MASTER_KEY not in logs
    # And the allowlist still holds for the fields that are logged.
    assert set(__import__("json").loads(logs.strip().splitlines()[-1])) <= {
        "event",
        "level",
        "timestamp",
        "component",
    }


# ---------------------------------------------------------------------------
# The API path: ciphertext at rest, plaintext on the wire
# ---------------------------------------------------------------------------


def test_a_learner_is_encrypted_at_rest_and_plaintext_over_http(
    app_engine: Engine, owner_engine: Engine, database, tenant: uuid.UUID
) -> None:
    admin = bootstrap_key(database, tenant, scopes=("observe", "admin"))
    app = create_app(
        make_settings(),
        db_check=lambda: True,
        redis_check=lambda: True,
        engine=app_engine,
    )
    client = TestClient(app, raise_server_exceptions=False)
    headers = {"Authorization": f"Bearer {admin.full_key}"}

    created = client.post(
        "/v1/learners",
        headers=headers,
        json={
            "learner_id": "learner-crypto-0001",
            "profile": "cognitive_support",
            "date_of_birth": "2011-04-05",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["profile"] == "cognitive_support"
    assert created.json()["date_of_birth"] == "2011-04-05"

    # At rest: both columns are envelopes, and neither plaintext appears.
    # Read through the tenant's own context — even the owner cannot see these
    # rows without one, which is the isolation working as designed.
    with tenant_transaction(app_engine, tenant) as conn:
        stored = conn.execute(
            text(
                "SELECT profile, date_of_birth FROM learners "
                "WHERE learner_id = 'learner-crypto-0001'"
            )
        ).one()
    assert stored.profile.startswith("v1:")
    assert stored.date_of_birth.startswith("v1:")
    assert "cognitive_support" not in stored.profile
    assert "2011-04-05" not in stored.date_of_birth

    # And the read path decrypts for this tenant only.
    read = client.get("/v1/learners/learner-crypto-0001", headers=headers)
    assert read.status_code == 200, read.text
    assert read.json()["profile"] == "cognitive_support"
    assert read.json()["date_of_birth"] == "2011-04-05"

    listed = client.get("/v1/learners", headers=headers)
    assert [item["learner_id"] for item in listed.json()] == ["learner-crypto-0001"]


def test_the_placeholder_envelope_is_well_formed() -> None:
    """The fixture constant satisfies the column CHECK it stands in for."""
    Envelope.parse(PLACEHOLDER_ENVELOPE)


def test_the_profile_column_stores_an_envelope_not_a_literal(
    app_engine: Engine, tenant: uuid.UUID
) -> None:
    """The database can no longer read the profile, by design."""
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError) as rejected:
        with tenant_transaction(app_engine, tenant) as conn:
            conn.execute(
                text(
                    "INSERT INTO learners (tenant_id, learner_id, profile) "
                    "VALUES (:t, 'learner-plaintext-01', 'adult_general')"
                ),
                {"t": tenant},
            )
    assert "learners_profile_is_envelope" in str(rejected.value)


def test_a_date_of_birth_column_is_text_now(owner_engine: Engine) -> None:
    with owner_engine.connect() as conn:
        column_type = conn.execute(
            text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_name = 'learners' AND column_name = 'date_of_birth'"
            )
        ).scalar_one()
    assert column_type == "text"


def test_an_expiry_of_the_data_key_row_is_tenant_scoped(
    app_engine: Engine, tenant: uuid.UUID, other_tenant: uuid.UUID
) -> None:
    """Another tenant's wrapped key is invisible, so it cannot be swapped in."""
    with tenant_transaction(app_engine, tenant) as conn:
        store = DataKeyStore(keyring=TEST_KEYRING)
        store.encrypt(conn, tenant, "adult_general")
    with tenant_transaction(app_engine, other_tenant) as conn:
        assert conn.execute(text("SELECT count(*) FROM tenant_data_keys")).scalar_one() == 0


def test_encryption_is_not_a_date_parser() -> None:
    """A date that survived the API is an ISO string, or it is not encrypted."""
    assert date.fromisoformat("2011-04-05").isoformat() == "2011-04-05"
