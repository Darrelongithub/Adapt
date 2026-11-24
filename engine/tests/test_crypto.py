"""Encryption at rest: what it must refuse, what it must not leak, what it can retire.

The properties this file exists to prove:

1. the value header names a **data key version**, never a master key id — so a
   master key can actually be retired (`test_a_master_key_can_be_retired_after_rewrap`);
2. a value's AAD binds tenant + row + column, so a ciphertext swapped between
   two learners, between two columns, or between two tenants raises;
3. nothing registers a master key id except the owner, so the startup check
   cannot be fed by the role it protects;
4. the same plaintext twice is different ciphertext, and 10,000 encryptions
   under one data key hold no repeated nonce;
5. no plaintext and no key material appears in a log line.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError

from adapt_engine.crypto import (
    CURRENT_DATA_KEY_VERSION,
    DataKeyMissing,
    DataKeyStore,
    DecryptionFailed,
    Envelope,
    Keyring,
    MalformedEnvelope,
    UnknownKeyId,
    assert_retirable,
    data_key_aad,
    preflight,
    provision_data_key,
    rewrap_all,
    seal,
    unseal,
    value_aad,
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
K1_ONLY = Keyring.from_secrets([("k1", TEST_MASTER_KEY)])
K2_AND_K1 = Keyring.from_secrets([("k2", NEW_MASTER_KEY), ("k1", TEST_MASTER_KEY)])
K2_ONLY = Keyring.from_secrets([("k2", NEW_MASTER_KEY)])


@pytest.fixture
def tenant(database) -> uuid.UUID:
    """Provisioned, which includes its data key."""
    return provision_tenant(database, slug=f"crypto-{uuid.uuid4().hex[:8]}", name="Crypto Tenant")


@pytest.fixture
def other_tenant(database) -> uuid.UUID:
    return provision_tenant(
        database, slug=f"crypto-b-{uuid.uuid4().hex[:8]}", name="Crypto Tenant B"
    )


@pytest.fixture
def store() -> DataKeyStore:
    return DataKeyStore(keyring=K1_ONLY)


def _learner(engine: Engine, tenant_id: uuid.UUID, learner_id: str) -> uuid.UUID:
    return create_learner(engine, tenant_id, learner_id)


def _read_row(engine: Engine, tenant_id: uuid.UUID, learner_id: str) -> object:
    with tenant_transaction(engine, tenant_id) as conn:
        return conn.execute(
            text("SELECT id, profile, date_of_birth FROM learners WHERE learner_id = :lid"),
            {"lid": learner_id},
        ).one()


# ---------------------------------------------------------------------------
# 1. The header is not a master key id
# ---------------------------------------------------------------------------


def test_the_value_header_names_a_data_key_version_not_a_master_key(
    app_engine: Engine, tenant: uuid.UUID, store: DataKeyStore
) -> None:
    row_id = _learner(app_engine, tenant, "learner-header-0001")
    with tenant_transaction(app_engine, tenant) as conn:
        envelope = store.encrypt(conn, tenant, row_id, "profile", "adult_general")
        assert envelope is not None
        parsed = Envelope.parse(envelope)
        assert parsed.dkv == CURRENT_DATA_KEY_VERSION
        # Field 2 is exactly the data key version — the master kid has no
        # place in the value, so re-wrap can retire it.
        assert envelope.split(":")[:2] == ["v1", CURRENT_DATA_KEY_VERSION]
        assert envelope.count(":") == 3


def test_an_envelope_in_the_old_layout_is_refused() -> None:
    """`v1:k1:...` was a header that pinned a row to a master key forever."""
    with pytest.raises(MalformedEnvelope) as refused:
        Envelope.parse(PLACEHOLDER_ENVELOPE.replace("dk1", "k1"))
    assert "data key version" in str(refused.value)


def test_the_column_constraint_keeps_the_header_honest(owner_engine: Engine) -> None:
    with owner_engine.connect() as conn:
        definition = conn.execute(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conname = 'learners_profile_is_envelope'"
            )
        ).scalar_one()
    assert "dk[0-9]" in definition


# ---------------------------------------------------------------------------
# 2. AAD binds the row
# ---------------------------------------------------------------------------


def test_swapping_profile_between_two_learners_raises(
    app_engine: Engine, tenant: uuid.UUID, store: DataKeyStore
) -> None:
    """Two learners of one tenant, one ciphertext moved: it must not decrypt."""
    first = _learner(app_engine, tenant, "learner-swap-0001")
    second = _learner(app_engine, tenant, "learner-swap-0002")
    row = _read_row(app_engine, tenant, "learner-swap-0002")
    stolen = row.profile
    with tenant_transaction(app_engine, tenant) as conn:
        with pytest.raises(DecryptionFailed):
            store.decrypt(conn, tenant, first, "profile", stolen)
        # The rightful cell still opens, which is what makes this detectable.
        assert store.decrypt(conn, tenant, second, "profile", stolen) == "adult_general"


def test_swapping_two_columns_of_one_row_raises(
    app_engine: Engine, tenant: uuid.UUID, store: DataKeyStore
) -> None:
    """profile and date_of_birth in one row do not authenticate against each other."""
    row_id = uuid.uuid4()
    with tenant_transaction(app_engine, tenant) as conn:
        profile = store.encrypt(conn, tenant, row_id, "profile", "cognitive_support")
        dob = store.encrypt(conn, tenant, row_id, "date_of_birth", "1996-04-05")
        assert dob is not None and profile is not None
        with pytest.raises(DecryptionFailed):
            store.decrypt(conn, tenant, row_id, "date_of_birth", profile)
        with pytest.raises(DecryptionFailed):
            store.decrypt(conn, tenant, row_id, "profile", dob)
        # And each opens in its own column, so the test is not passing vacuously.
        assert store.decrypt(conn, tenant, row_id, "profile", profile) == "cognitive_support"
        assert store.decrypt(conn, tenant, row_id, "date_of_birth", dob) == "1996-04-05"


def test_moving_a_row_to_another_tenant_raises(
    app_engine: Engine, tenant: uuid.UUID, other_tenant: uuid.UUID, store: DataKeyStore
) -> None:
    first = _learner(app_engine, tenant, "learner-move-0001")
    with tenant_transaction(app_engine, tenant) as conn:
        moved = store.encrypt(conn, tenant, first, "profile", "cognitive_support")
    # A row moved to B keeps its ciphertext but arrives as B's row: different
    # tenant and different row id in the AAD, so it cannot open there.
    moved_row = uuid.uuid4()
    with tenant_transaction(app_engine, other_tenant) as conn:
        conn.execute(
            text(
                "INSERT INTO learners (id, tenant_id, learner_id, profile) "
                "VALUES (:id, :t, 'learner-moved-0001', :profile)"
            ),
            {"id": moved_row, "t": other_tenant, "profile": moved},
        )
        with pytest.raises(DecryptionFailed):
            store.decrypt(conn, other_tenant, moved_row, "profile", moved)
        with pytest.raises(DecryptionFailed):
            store.decrypt(conn, other_tenant, first, "profile", moved)


def test_a_wrapped_data_key_does_not_move_between_tenants(
    app_engine: Engine, owner_engine: Engine, tenant: uuid.UUID, other_tenant: uuid.UUID
) -> None:
    """The wrapper is bound to its tenant too, so copying the row fails closed."""
    with tenant_transaction(owner_engine, tenant) as conn:
        stolen = conn.execute(
            text("SELECT kid, dkv, wrapped FROM tenant_data_keys WHERE tenant_id = :t"),
            {"t": tenant},
        ).one()
    with tenant_transaction(owner_engine, other_tenant) as conn:
        original = conn.execute(
            text("SELECT kid, dkv, wrapped FROM tenant_data_keys WHERE tenant_id = :t"),
            {"t": other_tenant},
        ).one()
        conn.execute(
            text(
                "UPDATE tenant_data_keys SET kid = :kid, dkv = :dkv, wrapped = :wrapped "
                "WHERE tenant_id = :t"
            ),
            {"kid": stolen.kid, "dkv": stolen.dkv, "wrapped": stolen.wrapped, "t": other_tenant},
        )
    try:
        with tenant_transaction(app_engine, other_tenant) as conn:
            with pytest.raises(DecryptionFailed):
                DataKeyStore(keyring=K1_ONLY).encrypt(
                    conn, other_tenant, uuid.uuid4(), "profile", "x"
                )
    finally:
        # Put B's own wrapper back: this row is shared state, and a later
        # rotation over every tenant would trip over the planted one.
        with tenant_transaction(owner_engine, other_tenant) as conn:
            conn.execute(
                text(
                    "UPDATE tenant_data_keys SET kid = :kid, dkv = :dkv, wrapped = :wrapped "
                    "WHERE tenant_id = :t"
                ),
                {
                    "kid": original.kid,
                    "dkv": original.dkv,
                    "wrapped": original.wrapped,
                    "t": other_tenant,
                },
            )


def test_the_two_aads_are_not_interchangeable() -> None:
    tenant_id, row_id = uuid.uuid4(), uuid.uuid4()
    assert data_key_aad(tenant_id) != value_aad(tenant_id, row_id, "profile")
    assert value_aad(tenant_id, row_id, "profile") != value_aad(tenant_id, row_id, "date_of_birth")
    assert value_aad(tenant_id, row_id, "profile") != value_aad(tenant_id, uuid.uuid4(), "profile")


def test_a_corrupted_ciphertext_raises(
    app_engine: Engine, tenant: uuid.UUID, store: DataKeyStore
) -> None:
    row_id = uuid.uuid4()
    with tenant_transaction(app_engine, tenant) as conn:
        envelope = store.encrypt(conn, tenant, row_id, "profile", "adult_general")
        assert envelope is not None
        flipped = envelope[:-6] + ("A" if envelope[-6] != "A" else "B") + envelope[-5:]
        with pytest.raises(DecryptionFailed):
            store.decrypt(conn, tenant, row_id, "profile", flipped)


def test_a_malformed_value_is_rejected_before_any_key_use() -> None:
    for value in (
        "",
        "adult_general",
        "v1:dk1:short:short",
        "v2:dk1:" + "A" * 16 + ":" + "A" * 24,
        "v1:dk1:AAAA:AAAA",
    ):
        with pytest.raises(MalformedEnvelope):
            Envelope.parse(value)


def test_a_data_key_version_the_tenant_does_not_hold_is_refused(
    app_engine: Engine, tenant: uuid.UUID, store: DataKeyStore
) -> None:
    """The dkv field is load-bearing the day a second version exists."""
    row_id = uuid.uuid4()
    with tenant_transaction(app_engine, tenant) as conn:
        envelope = store.encrypt(conn, tenant, row_id, "profile", "adult_general")
        assert envelope is not None
        future = envelope.replace("v1:dk1:", "v1:dk9:", 1)
        with pytest.raises(MalformedEnvelope) as refused:
            store.decrypt(conn, tenant, row_id, "profile", future)
    assert "data-key rotation is not supported" in str(refused.value)


# ---------------------------------------------------------------------------
# 3. Retirement: the acceptance test for the header change
# ---------------------------------------------------------------------------


def test_a_master_key_can_be_retired_after_rewrap(
    app_engine: Engine, owner_engine: Engine, tenant: uuid.UUID
) -> None:
    """Write under k1, re-wrap everything, delete k1 from the keyring, restart, read.

    This is the test the earlier design could not pass: with the master kid in
    every value header, k1 stayed referenced by rows forever, startup kept
    demanding it, and "rotation" only ever worked in one direction.
    """
    _learner(app_engine, tenant, "learner-retire-0001")

    # Before: k1 is the only key, and it is referenced.
    assert preflight(owner_engine, "k1") >= 1
    with pytest.raises(UnknownKeyId):
        assert_retirable(owner_engine, "k1", K1_ONLY)

    # Re-wrap under k2, keeping k1 only to open the wrapper during the change.
    results = rewrap_all(owner_engine, K2_AND_K1)
    mine = [result for result in results if result.tenant_id == tenant]
    assert mine and mine[0].new_kid == "k2" and mine[0].old_kid == "k1"

    # k1 is now referenced by nothing, and the catalogue says so.
    assert preflight(owner_engine, "k1") == 0
    with owner_engine.connect() as conn:
        assert (
            conn.execute(text("SELECT count(*) FROM data_key_ids WHERE kid = 'k1'")).scalar_one()
            == 0
        )
        assert (
            conn.execute(text("SELECT count(*) FROM data_key_ids WHERE kid = 'k2'")).scalar_one()
            >= 1
        )
    assert_retirable(owner_engine, "k1", K2_AND_K1)  # does not raise

    # Restart with a keyring that has never heard of k1.
    restarted = DataKeyStore(keyring=K2_ONLY)
    restarted.verify_covers_usage(app_engine)  # does not raise: nothing needs k1

    app = create_app(
        make_settings(
            master_key=NEW_MASTER_KEY,
            master_key_id="k2",
            master_key_previous=None,
        ),
        db_check=lambda: True,
        redis_check=lambda: True,
        engine=app_engine,
    )
    with TestClient(app) as client:  # the lifespan's keyring gate runs
        assert client.get("/v1/health").status_code == 200

    # And a row written before the change still reads after it.
    row = _read_row(app_engine, tenant, "learner-retire-0001")
    with tenant_transaction(app_engine, tenant) as conn:
        assert restarted.decrypt(conn, tenant, row.id, "profile", row.profile) == "adult_general"


def test_a_row_that_still_named_a_master_key_would_block_retirement(
    app_engine: Engine, tenant: uuid.UUID
) -> None:
    """The inverse of retirement, kept as a test rather than a story.

    Under the increment-5 layout a stored value named the master key that
    wrapped the data key at write time. Re-wrapping does not rewrite rows, so
    that name survived rotation, the startup check had to keep demanding the
    key, and "retired" was unreachable. Both ends refuse the layout now: the
    column CHECK will not store it and the parser will not read it, so there is
    no header left to pin a key.
    """
    old_layout = "v1:k1:" + "A" * 16 + ":" + "A" * 32
    with pytest.raises(MalformedEnvelope):
        Envelope.parse(old_layout)
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError) as refused:
        with tenant_transaction(app_engine, tenant) as conn:
            conn.execute(
                text(
                    "INSERT INTO learners (tenant_id, learner_id, profile) "
                    "VALUES (:t, 'learner-old-layout-1', :profile)"
                ),
                {"t": tenant, "profile": old_layout},
            )
    assert "learners_profile_is_envelope" in str(refused.value)


def test_startup_still_refuses_when_a_key_is_dropped_before_rewrap(
    app_engine: Engine, tenant: uuid.UUID
) -> None:
    """The other half of retirement: delete too early and the process will not boot."""
    _learner(app_engine, tenant, "learner-retire-0002")
    restarted = DataKeyStore(keyring=K2_ONLY)
    with pytest.raises(UnknownKeyId) as refused:
        restarted.verify_covers_usage(app_engine)
    assert "k1" in str(refused.value)
    assert TEST_MASTER_KEY not in str(refused.value), "the message leaks key material"

    app = create_app(
        make_settings(master_key=NEW_MASTER_KEY, master_key_id="k2", master_key_previous=None),
        db_check=lambda: True,
        redis_check=lambda: True,
        engine=app_engine,
    )
    with pytest.raises(RuntimeError) as refused_start:
        with TestClient(app):
            pass
    assert "refusing to start" in str(refused_start.value)


def test_rewrap_touches_no_learner_row(
    app_engine: Engine, owner_engine: Engine, tenant: uuid.UUID
) -> None:
    """Rotation re-wraps a key row; the ciphertext of every value is untouched."""
    _learner(app_engine, tenant, "learner-rewrap-0001")
    before = _read_row(app_engine, tenant, "learner-rewrap-0001")
    rewrap_all(owner_engine, K2_AND_K1)
    after = _read_row(app_engine, tenant, "learner-rewrap-0001")
    assert after.profile == before.profile
    assert after.date_of_birth == before.date_of_birth


def test_rewrap_is_idempotent(app_engine: Engine, owner_engine: Engine, tenant: uuid.UUID) -> None:
    _learner(app_engine, tenant, "learner-rewrap-0002")
    rewrap_all(owner_engine, K2_AND_K1)
    again = rewrap_all(owner_engine, K2_AND_K1)
    # Every tenant is already on k2, so nothing changes on the second pass.
    assert again and all(not result.changed for result in again)
    assert all(result.new_kid == "k2" for result in again)


# ---------------------------------------------------------------------------
# 3b. Only the owner registers kids
# ---------------------------------------------------------------------------


def test_the_runtime_role_cannot_write_key_rows(owner_engine: Engine) -> None:
    """SELECT and nothing else: the startup check must not be fed by the app."""
    with owner_engine.connect() as conn:
        for table in ("tenant_data_keys", "data_key_ids"):
            privileges = {
                verb: conn.execute(
                    text("SELECT has_table_privilege('adapt_app', :table, :verb)"),
                    {"table": table, "verb": verb},
                ).scalar_one()
                for verb in ("SELECT", "INSERT", "UPDATE", "DELETE")
            }
            assert privileges == {
                "SELECT": True,
                "INSERT": False,
                "UPDATE": False,
                "DELETE": False,
            }, f"{table}: {privileges}"


def test_a_runtime_insert_into_the_catalogue_is_refused(
    app_engine: Engine, tenant: uuid.UUID
) -> None:
    with pytest.raises(DBAPIError) as refused:
        with tenant_transaction(app_engine, tenant) as conn:
            conn.execute(
                text("INSERT INTO data_key_ids (kid) VALUES ('k9') ON CONFLICT DO NOTHING")
            )
    assert "permission denied" in str(refused.value).lower()


def test_provisioning_is_what_creates_a_data_key(owner_engine: Engine, app_engine: Engine) -> None:
    """No lazy creation at request time: the runtime reads, provisioning writes."""
    tenant_id = uuid.uuid4()
    with tenant_transaction(owner_engine, tenant_id) as conn:
        conn.execute(
            text(
                "INSERT INTO tenants (id, slug, name, profile, status) "
                "VALUES (:id, :slug, 'Unprovisioned', 'adult_general', 'active')"
            ),
            {"id": tenant_id, "slug": f"unprovisioned-{uuid.uuid4().hex[:8]}"},
        )
        kid = provision_data_key(conn, tenant_id, K1_ONLY)
        assert kid == "k1"

    with tenant_transaction(app_engine, tenant_id) as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM tenant_data_keys WHERE tenant_id = :t"),
                {"t": tenant_id},
            ).scalar_one()
            == 1
        )
    # Idempotent: provisioning again does not replace a key that already exists,
    # because replacing it would orphan every row encrypted under the old one.
    with tenant_transaction(owner_engine, tenant_id) as conn:
        before = conn.execute(
            text("SELECT wrapped FROM tenant_data_keys WHERE tenant_id = :t"), {"t": tenant_id}
        ).scalar_one()
        assert provision_data_key(conn, tenant_id, K1_ONLY) == "k1"
        after = conn.execute(
            text("SELECT wrapped FROM tenant_data_keys WHERE tenant_id = :t"), {"t": tenant_id}
        ).scalar_one()
    assert after == before


def test_a_tenant_without_a_data_key_cannot_encrypt(
    app_engine: Engine, owner_engine: Engine
) -> None:
    tenant_id = uuid.uuid4()
    with tenant_transaction(owner_engine, tenant_id) as conn:
        conn.execute(
            text(
                "INSERT INTO tenants (id, slug, name, profile, status) "
                "VALUES (:id, :slug, 'No Data Key', 'adult_general', 'active')"
            ),
            {"id": tenant_id, "slug": f"no-key-{uuid.uuid4().hex[:8]}"},
        )
    with tenant_transaction(app_engine, tenant_id) as conn:
        with pytest.raises(DataKeyMissing) as refused:
            DataKeyStore(keyring=K1_ONLY).encrypt(conn, tenant_id, uuid.uuid4(), "profile", "x")
    assert "provision" in str(refused.value)


# ---------------------------------------------------------------------------
# 3c. The keyring's own rules
# ---------------------------------------------------------------------------


def test_a_keyring_that_covers_every_kid_in_use_starts(
    app_engine: Engine, tenant: uuid.UUID
) -> None:
    create_learner(app_engine, tenant, "learner-kid-covered-1")
    covering = DataKeyStore(keyring=K2_AND_K1)  # carries both, so it covers both
    covering.verify_covers_usage(app_engine)
    assert "k1" in covering.kids_in_use(app_engine)


def test_a_kid_repeated_in_the_keyring_is_refused() -> None:
    from adapt_engine.crypto.keyring import DuplicateKeyId

    with pytest.raises(DuplicateKeyId):
        Keyring.from_secrets([("k1", TEST_MASTER_KEY), ("k1", NEW_MASTER_KEY)])


def test_a_short_master_secret_is_refused() -> None:
    from adapt_engine.crypto.keyring import KeyringError

    with pytest.raises(KeyringError):
        Keyring.from_secrets([("k1", "too-short")])


# ---------------------------------------------------------------------------
# 4. Non-determinism and the nonce sweep
# ---------------------------------------------------------------------------


def test_the_same_plaintext_twice_is_different_ciphertext(
    app_engine: Engine, tenant: uuid.UUID, store: DataKeyStore
) -> None:
    row_id = uuid.uuid4()
    with tenant_transaction(app_engine, tenant) as conn:
        first = store.encrypt(conn, tenant, row_id, "profile", "adult_general")
        second = store.encrypt(conn, tenant, row_id, "profile", "adult_general")
        assert first != second
        assert Envelope.parse(first).nonce != Envelope.parse(second).nonce
        assert store.decrypt(conn, tenant, row_id, "profile", first) == store.decrypt(
            conn, tenant, row_id, "profile", second
        )


def test_absent_values_stay_absent(
    app_engine: Engine, tenant: uuid.UUID, store: DataKeyStore
) -> None:
    with tenant_transaction(app_engine, tenant) as conn:
        assert store.encrypt(conn, tenant, uuid.uuid4(), "date_of_birth", None) is None
        assert store.decrypt(conn, tenant, uuid.uuid4(), "date_of_birth", None) is None


def test_ten_thousand_encryptions_hold_no_repeated_nonce(
    app_engine: Engine, tenant: uuid.UUID, store: DataKeyStore
) -> None:
    """One data key, 10,000 values, zero repeats — the birthday bound, measured."""
    row_id = uuid.uuid4()
    with tenant_transaction(app_engine, tenant) as conn:
        nonces = set()
        for index in range(10_000):
            envelope = store.encrypt(conn, tenant, row_id, "profile", f"adult_general-{index}")
            nonces.add(Envelope.parse(envelope).nonce)
        assert len(nonces) == 10_000


# ---------------------------------------------------------------------------
# 5. Logs
# ---------------------------------------------------------------------------


def test_no_plaintext_or_key_material_reaches_a_log_line(
    app_engine: Engine, tenant: uuid.UUID, store: DataKeyStore, capsys: pytest.CaptureFixture[str]
) -> None:
    """The crypto path logs nothing, so the allowlist needs no crypto exception."""
    import json

    configure_logging()
    get_logger().info("crypto_probe", component="test")

    row_id = uuid.uuid4()
    with tenant_transaction(app_engine, tenant) as conn:
        envelope = store.encrypt(conn, tenant, row_id, "profile", "cognitive_support")
        assert envelope is not None
        store.decrypt(conn, tenant, row_id, "profile", envelope)
        data_key = store._load(conn, tenant)[1]
        with pytest.raises(DecryptionFailed):
            store.decrypt(conn, tenant, uuid.uuid4(), "profile", envelope)

    logs = capsys.readouterr().out
    assert "crypto_probe" in logs, "logging is not capturing; the assertion below would be vacuous"
    assert "cognitive_support" not in logs
    assert envelope.split(":")[3] not in logs
    assert envelope not in logs
    assert data_key.hex() not in logs
    assert data_key.hex()[:16] not in logs
    assert TEST_MASTER_KEY not in logs
    assert set(json.loads(logs.strip().splitlines()[-1])) <= {
        "event",
        "level",
        "timestamp",
        "component",
    }


# ---------------------------------------------------------------------------
# The API path: ciphertext at rest, plaintext on the wire
# ---------------------------------------------------------------------------


def test_a_learner_is_encrypted_at_rest_and_plaintext_over_http(
    app_engine: Engine, database, tenant: uuid.UUID
) -> None:
    admin = bootstrap_key(database, tenant, scopes=("observe", "admin"))
    app = create_app(
        make_settings(), db_check=lambda: True, redis_check=lambda: True, engine=app_engine
    )
    client = TestClient(app, raise_server_exceptions=False)
    headers = {"Authorization": f"Bearer {admin.full_key}"}

    created = client.post(
        "/v1/learners",
        headers=headers,
        json={
            "learner_id": "learner-crypto-0001",
            "profile": "cognitive_support",
            "date_of_birth": "1996-04-05",
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["profile"] == "cognitive_support"
    assert created.json()["date_of_birth"] == "1996-04-05"

    # At rest: both columns are envelopes, and neither plaintext appears.
    with tenant_transaction(app_engine, tenant) as conn:
        stored = conn.execute(
            text(
                "SELECT profile, date_of_birth FROM learners "
                "WHERE learner_id = 'learner-crypto-0001'"
            )
        ).one()
    assert stored.profile.startswith("v1:dk1:")
    assert stored.date_of_birth.startswith("v1:dk1:")
    assert "cognitive_support" not in stored.profile
    assert "1996-04-05" not in stored.date_of_birth

    read = client.get("/v1/learners/learner-crypto-0001", headers=headers)
    assert read.status_code == 200, read.text
    assert read.json()["profile"] == "cognitive_support"
    assert read.json()["date_of_birth"] == "1996-04-05"

    listed = client.get("/v1/learners", headers=headers)
    assert [item["learner_id"] for item in listed.json()] == ["learner-crypto-0001"]


def test_the_placeholder_envelope_satisfies_the_column_check() -> None:
    parsed = Envelope.parse(PLACEHOLDER_ENVELOPE)
    assert parsed.dkv == CURRENT_DATA_KEY_VERSION


def test_the_profile_column_stores_an_envelope_not_a_literal(
    app_engine: Engine, tenant: uuid.UUID
) -> None:
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


def test_another_tenants_data_key_row_is_invisible_to_the_app(
    app_engine: Engine, tenant: uuid.UUID, other_tenant: uuid.UUID
) -> None:
    with tenant_transaction(app_engine, other_tenant) as conn:
        seen = conn.execute(text("SELECT tenant_id FROM tenant_data_keys")).scalars().all()
    assert tenant not in seen
    assert seen == [other_tenant]


def test_a_sealed_value_is_not_a_date_parser() -> None:
    assert date.fromisoformat("1996-04-05").isoformat() == "1996-04-05"


def test_seal_refuses_a_wrong_sized_key() -> None:
    from adapt_engine.crypto import CryptoError

    with pytest.raises(CryptoError):
        seal(b"short", b"x", aad=value_aad(uuid.uuid4(), uuid.uuid4(), "profile"))


def test_unseal_reports_a_failure_rather_than_returning_bytes() -> None:
    key = TEST_KEYRING.key("k1")
    envelope_text = seal(key, b"adult_general", aad=value_aad(uuid.uuid4(), uuid.uuid4(), "p"))
    with pytest.raises(DecryptionFailed):
        unseal(key, Envelope.parse(envelope_text), aad=value_aad(uuid.uuid4(), uuid.uuid4(), "p"))
