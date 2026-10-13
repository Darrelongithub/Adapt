"""Keys, the field cipher, and what is actually on disk.

These tests read the database file as bytes. That is the point: "encrypted at rest"
is a claim about bytes, and it is easy to write a suite that only exercises the ORM
and therefore passes whether or not anything is encrypted.
"""

from __future__ import annotations

import base64
import os
import sqlite3
from pathlib import Path

import pytest


def cipher_in(tmp_path):
    """A cipher with a throwaway key, so a test never depends on the app's keyfile."""
    from adapt.crypto.fields import FieldCipher
    from adapt.crypto.keys import build_keyring, write_keyfile

    return FieldCipher(build_keyring(keyfile=write_keyfile(tmp_path / "k.key")))


def test_master_key_is_stable_and_32_bytes(tmp_path):
    """Same file, read twice -> same key; and a keyfile is never replaced.

    Uses its own path rather than ``settings.keyfile_path``: the app creates that file
    during its own bootstrap, so a test that "creates" it only works if it happens to
    run first -- and a keyfile that got overwritten would leave every stored row
    undecryptable, which is exactly what the refusal below protects.
    """
    from adapt.crypto.keys import KeyMaterialError, load_master_key, write_keyfile

    path = write_keyfile(tmp_path / "master.key")
    first = load_master_key(keyfile=path)
    second = load_master_key(keyfile=path)
    assert first == second
    raw, _source = first
    assert len(raw) == 32
    assert len(set(raw)) > 4
    with pytest.raises(KeyMaterialError):
        write_keyfile(path)


def test_keyfile_is_created_private(tmp_path):
    from adapt.crypto.keys import load_master_key

    path = tmp_path / "keys" / "adapt.key"
    _raw, source = load_master_key(keyfile=path)
    assert path.exists()
    assert "generated" in source
    assert not os.stat(path).st_mode & 0o077


def test_bad_key_length_is_refused(tmp_path):
    from adapt.crypto.keys import KeyMaterialError, load_master_key

    short = tmp_path / "short.key"
    short.write_bytes(b"too-short")
    with pytest.raises(KeyMaterialError):
        load_master_key(keyfile=short)


def test_base64_env_key_and_passphrase_are_distinct_sources(tmp_path):
    from adapt.crypto.keys import load_master_key

    raw = bytes(range(32))
    key_env, source = load_master_key(b64_key=base64.b64encode(raw).decode())
    assert key_env == raw and "ADAPT_DB_KEY" in source

    keyfile = tmp_path / "k.key"
    a, _ = load_master_key(keyfile=keyfile, passphrase="hunter two three")
    b, _ = load_master_key(keyfile=keyfile, passphrase="hunter two three")
    c, _ = load_master_key(keyfile=keyfile, passphrase="another passphrase here")
    assert a == b, "the same passphrase must not lose the learner's data on restart"
    assert a != c, "a different passphrase must produce a different key"


def test_subkeys_differ_per_purpose(tmp_path):
    from adapt.crypto.keys import build_keyring, write_keyfile

    keyring = build_keyring(keyfile=write_keyfile(tmp_path / "k.key"))
    fields = keyring.subkey("fields")
    index = keyring.subkey("blind_index")
    assert fields != index
    assert len(fields) == 32
    assert keyring.subkey("fields") == fields
    assert keyring.derive("table:students") != keyring.derive("table:attempts")
    assert "Keyring" in repr(keyring) and len(repr(keyring)) < 120  # no secrets in logs


def test_field_cipher_roundtrip_and_context_binding(tmp_path):
    from cryptography.exceptions import InvalidTag

    cipher = cipher_in(tmp_path)
    blob = cipher.encrypt("rowan@example.test", context="student.email")
    assert cipher.decrypt(blob, context="student.email") == "rowan@example.test"
    assert b"rowan" not in blob
    assert blob[:4] == b"AGC1"
    with pytest.raises(InvalidTag):
        cipher.decrypt(blob, context="student.name")
    assert cipher.encrypt("x", context="c") != cipher.encrypt("x", context="c")


def test_tampered_ciphertext_does_not_decrypt(tmp_path):
    from cryptography.exceptions import InvalidTag

    cipher = cipher_in(tmp_path)
    blob = bytearray(cipher.encrypt("display name", context="student.name"))
    blob[-1] ^= 0x01
    with pytest.raises(InvalidTag):
        cipher.decrypt(bytes(blob), context="student.name")


def test_blind_index_is_normalized_deterministic_and_scoped(tmp_path, settings):
    """Deterministic per (column, value), keyed, and 128 bits of hex.

    Uses the app's own keyring rather than a throwaway one: ``configure_cipher``
    refuses a key swap precisely so this global cannot be re-pointed by a test.
    """
    from adapt.crypto.fields import blind_index_for

    cipher = cipher_in(tmp_path)
    a = cipher.blind_index("  Rowan@Example.TEST ", column="student.email")
    b = cipher.blind_index("rowan@example.test", column="student.email")
    assert a == b
    assert len(a) == 32 and all(c in "0123456789abcdef" for c in a)
    assert cipher.blind_index("rowan@example.test", column="guardian.email") != a
    # The module-level helper goes through the process-wide cipher. Before the app
    # configures one it must return None rather than crash, because `adapt init`
    # and migrations run in that window.
    from adapt.crypto.fields import configure_cipher, get_cipher, set_cipher

    previous = get_cipher()
    try:
        set_cipher(None)
        assert blind_index_for("student.email", "rowan@example.test") is None
        from adapt.crypto.keys import build_keyring

        configure_cipher(build_keyring(keyfile=settings.keyfile_path))
        index = blind_index_for("student.email", "rowan@example.test")
        assert index, "the module helper needs the process-wide cipher"
    finally:
        set_cipher(previous)
    with_cipher = blind_index_for("student.email", "rowan@example.test")
    assert blind_index_for("student.email", "rowan@example.test") == with_cipher, (
        "the helper must not return a different value per call"
    )
    configured = get_cipher()
    if configured is not None:
        # Whichever cipher the process is using (the app's own, or none), the helper
        # has to agree with it -- not with a throwaway key from this test.
        assert with_cipher == configured.blind_index("rowan@example.test", column="student.email")


def test_plaintext_rows_are_tolerated_not_claimed(tmp_path):
    """A hand-written SQL row has no AGC1 header: reads must not crash.

    The encryption report is where that fact has to surface, because a column that
    is *sometimes* ciphertext is not an encrypted column.
    """
    cipher = cipher_in(tmp_path)
    assert cipher.decrypt(b"plain legacy value", context="student.email") == "plain legacy value"


def test_every_encryption_claim_matches_the_schema():
    """The status report is a promise; the column types are the receipt.

    ``/api/v1/status`` lists what is encrypted. If someone adds a sensitive column,
    or moves one off ``EncryptedText``, the promise becomes false and this test
    fails -- which is the only reason the promise is worth reading.
    """
    from adapt.crypto.fields import EncryptedJSON, EncryptedText
    from adapt.db.models import Base

    expected = {
        "students": {"display_name", "email"},
        "attempts": {"first_answer", "final_answer", "ai_feedback"},
        "learner_events": {"payload"},
        "adaptation_proposals": {"action_payload", "student_note", "modifications"},
        "evidence": {"facts"},
        "profile_snapshots": {"profile"},
        "chat_messages": {"content"},
        "voice_turns": {"text"},
        "learning_materials": {"body"},
        "learning_sessions": {"summary"},
        "approved_adaptations": {"payload"},
        "audit_log": {"detail"},
    }
    for table, columns in expected.items():
        relation = Base.metadata.tables[table]
        for column in columns:
            type_ = relation.columns[column].type
            assert isinstance(type_, (EncryptedText, EncryptedJSON,)) or isinstance(
                getattr(type_, "impl", None), (EncryptedText, EncryptedJSON)
            ), f"{table}.{column} is listed as encrypted but is a plain {type_.__class__.__name__}"


def test_preference_note_is_admitted_as_unencrypted(client, learner):
    """Preferences are stored as readable JSON, and the report says so."""
    report = client.get("/api/v1/status").json()["data_protection"]
    assert any("preference" in line.lower() for line in report["not_protected"]), (
        "the report must not imply that learner-written free text is encrypted"
    )


def test_student_pii_is_ciphertext_in_the_file(client, services, registered_learner, settings):
    """The ORM returns a clean string; the file must not."""
    email = registered_learner["email"]
    with services.scope() as s:
        from adapt.db.models import Student

        row = s.get(Student, registered_learner["student_id"])
        assert row.email == email
        assert row.email_index and len(row.email_index) == 32

    conn = sqlite3.connect(str(settings.db_file))
    try:
        stored, stored_index, name = conn.execute(
            "SELECT email, email_index, display_name FROM students WHERE id = ?",
            (registered_learner["student_id"],),
        ).fetchone()
    finally:
        conn.close()
    body = f"{stored} {name}".encode() if isinstance(stored, str) else b" ".join(x for x in (stored, name) if isinstance(x, bytes))
    assert email.encode() not in body
    assert len(str(stored_index)) == 32


def test_answers_are_ciphertext_in_the_file(client, services, settings, learner, items):
    """Learner answers are the most sensitive thing we store; check them at rest."""
    sid = client.post("/api/v1/sessions", json={"mode": "practice"}, headers=learner["headers"]).json()["session_id"]
    entry = items[0]
    r = client.post(
        "/api/v1/attempts",
        json={"session_id": sid, "item_id": entry["item"]["id"], "answer": "squiggly-answer-token"},
        headers=learner["headers"],
    )
    assert r.status_code == 200, r.text
    conn = sqlite3.connect(str(settings.db_file))
    try:
        rows = conn.execute("select final_answer, first_answer from attempts").fetchall()
    finally:
        conn.close()
    joined = b" ".join(x if isinstance(x, bytes) else str(x).encode() for row in rows for x in row if x)
    assert b"squiggly-answer-token" not in joined


def test_field_only_mode_leaves_the_container_plaintext(settings):
    """Honesty check for the fallback mode: the *file* is not encrypted."""
    from adapt.db.engine import Database

    import tempfile as _tf

    with _tf.TemporaryDirectory() as tmp:
        st = settings.model_copy(update={"db_encryption": "field-only"})
        db = Database(st, db_file=Path(tmp) / "plain.db")
        try:
            db.create_all()  # without a file there is no header to inspect
            assert db.driver.name != "sqlcipher"
            assert db.header_is_plaintext(), "field-only mode must not claim whole-file encryption"
            assert not db.file_encrypted()
        finally:
            db.dispose()


def test_sqlcipher_container_is_not_readable_without_the_key(settings, tmp_path):
    """If the driver is installed, the whole file must be opaque. Skipped otherwise."""
    pytest.importorskip("sqlcipher3")
    from adapt.db.engine import Database

    # The same master key as the running app, whole-file mode instead of column mode:
    # a second key in one process is refused by configure_cipher, and that is the
    # behaviour we want to keep.
    keyfile = settings.keyfile_path
    st = settings.model_copy(update={"db_encryption": "sqlcipher", "keyfile": keyfile})
    db_file = tmp_path / "encrypted.db"
    db = Database(st, db_file=db_file)
    db.create_all()
    with db.session() as s:
        from adapt.db.models import Student

        s.add(Student(id="stu_cipher_probe", display_name="Cipher Probe"))
    head = db_file.read_bytes()[:16]
    assert b"SQLite format 3" not in head
    assert db.file_encrypted()
    db.dispose()

    with pytest.raises(sqlite3.DatabaseError):
        conn = sqlite3.connect(str(db_file))
        try:
            conn.execute("SELECT count(*) FROM students").fetchone()
        finally:
            conn.close()


def test_the_header_sniff_tells_the_two_policies_apart(tmp_path):
    """The check the mismatch refusal is built on, with no driver involved."""
    from adapt.db.engine import _on_disk_format

    plain = tmp_path / "plain.db"
    plain.write_bytes(b"SQLite format 3\x00" + b"\x00" * 80)
    assert _on_disk_format(plain) == "plaintext"

    opaque = tmp_path / "opaque.db"
    opaque.write_bytes(b"\x1a\x93i}" + bytes(range(64)))  # ciphertext from byte zero, as SQLCipher writes it
    assert _on_disk_format(opaque) == "opaque"

    assert _on_disk_format(tmp_path / "absent.db") == "missing"
    empty = tmp_path / "empty.db"
    empty.touch()
    assert _on_disk_format(empty) == "empty"


def test_a_policy_that_cannot_read_the_file_says_so_instead_of_the_driver(tmp_path):
    """Both directions of the mismatch, at the point where the sentence is composed.

    The driver's own complaint is ``file is not a database`` raised from inside SQLAlchemy's pool:
    it names neither the file nor the policy, and the fix is an environment variable the operator
    has no way of guessing from it. The audit called this out as unresolved; this is the guard.
    """
    from adapt.db.engine import _refuse_mismatch

    db_file = tmp_path / "adapt.db"
    with pytest.raises(RuntimeError) as sqlite_vs_encrypted:
        _refuse_mismatch(db_file, reader="sqlite", on_disk="opaque", mode="field-only")
    message = str(sqlite_vs_encrypted.value)
    assert str(db_file) in message and "ADAPT_DB_ENCRYPTION=field-only" in message
    assert "ADAPT_DATA_DIR" in message, "the message must name a way out, not just the problem"
    assert "key" not in message.lower(), "a refusal is never a place to print key material"

    with pytest.raises(RuntimeError) as sqlcipher_vs_plaintext:
        _refuse_mismatch(db_file, reader="sqlcipher", on_disk="plaintext", mode="auto")
    message = str(sqlcipher_vs_plaintext.value)
    assert "unencrypted SQLite database" in message and "field-only" in message

    # nothing to complain about: absent, empty, or matching
    for reader, on_disk in (("sqlite", "missing"), ("sqlcipher", "empty"), ("sqlite", "plaintext"),
                            ("sqlcipher", "opaque"), ("sqlite", "unknown"), ("sqlcipher", "unknown")):
        _refuse_mismatch(db_file, reader=reader, on_disk=on_disk, mode="auto")


def test_opening_an_encrypted_database_under_field_only_explains_itself(settings, tmp_path):
    """The reproduced case: a database written under the default policy, opened with ``field-only``.

    Without the sniff this is ``sqlite3.DatabaseError: file is not a database`` from inside the
    connection pool -- a traceback, and no mention of the file or the policy that would open it.
    """
    pytest.importorskip("sqlcipher3")
    from adapt.db.engine import Database

    db_file = tmp_path / "enc.db"
    encrypted = Database(settings.model_copy(update={"db_encryption": "sqlcipher"}), db_file=db_file)
    try:
        encrypted.create_all()
        assert encrypted.file_encrypted(), "the fixture is only meaningful if the file really is ciphertext"
    finally:
        encrypted.dispose()

    with pytest.raises(RuntimeError) as refusal:
        Database(settings.model_copy(update={"db_encryption": "field-only"}), db_file=db_file)
    assert "does not start with the SQLite header" in str(refusal.value)
    assert str(db_file) in str(refusal.value)

    # and the policy that wrote it still opens it: the sniff must not refuse the working case
    again = Database(settings.model_copy(update={"db_encryption": "sqlcipher"}), db_file=db_file)
    try:
        assert again.student_count() == 0
    finally:
        again.dispose()


def test_doctor_reports_a_database_the_policy_cannot_open(settings, tmp_path):
    """`adapt doctor` is the pre-flight screen: it must not call a data dir fine while the app
    cannot read a byte of what is in it. Run as a subprocess for the same reason the migration
    tests are -- a scratch directory with its own key and its own policy."""
    import json
    import subprocess
    import sys

    pytest.importorskip("sqlcipher3")
    from adapt.db.engine import Database

    backend = Path(__file__).resolve().parents[1]
    db_file = tmp_path / "adapt.db"
    # The session's key, named explicitly: a second key in one process is refused (correctly), and
    # a subprocess that generates its *own* key would report a wrong-key failure -- a real operator
    # problem, but not the one this test is about. Matching the key isolates the policy.
    keyfile = settings.keyfile_path
    db = Database(settings.model_copy(update={"db_encryption": "sqlcipher", "keyfile": keyfile}), db_file=db_file)
    try:
        db.create_all()
        assert db.file_encrypted()
    finally:
        db.dispose()

    def doctor(*, encryption: str, data_dir: Path) -> dict:
        env = {
            **os.environ,
            "ADAPT_DATA_DIR": str(data_dir),
            "ADAPT_DB_ENCRYPTION": encryption,
            "ADAPT_KEYFILE": str(keyfile),
            "PYTHONPATH": str(backend / "src"),
        }
        for key in ("ADAPT_DB_KEY", "ADAPT_DB_PATH"):
            env.pop(key, None)
        out = subprocess.run(
            [sys.executable, "-m", "adapt.cli", "doctor", "--json"],
            cwd=str(backend), env=env, capture_output=True, text=True, timeout=120,
        )
        assert out.returncode == 0, out.stderr[-600:]
        return json.loads(out.stdout)

    mismatched = doctor(encryption="field-only", data_dir=tmp_path)
    assert mismatched["database"]["ok"] is False, mismatched.get("database")
    assert "field-only" in mismatched["database"]["detail"]
    assert mismatched["database"]["fix"], "a warning with no remedy is half a warning"

    matching = doctor(encryption="auto", data_dir=tmp_path)
    assert matching["database"]["ok"] is True, matching.get("database")
    assert "driver=sqlcipher" in matching["database"]["detail"]

    # nothing on disk: a first run has no opinion to offer, so the line is absent rather than green
    empty = tmp_path / "fresh"
    empty.mkdir()
    assert "database" not in doctor(encryption="auto", data_dir=empty)


def test_status_endpoint_names_the_real_driver(client, learner):
    response = client.get("/api/v1/status", headers=learner["headers"])
    assert response.status_code == 200
    # The learner-safe report (§3 keeps deployment configuration off HTTP). The honesty property
    # is unchanged and is the point of the test: the answer must name what actually protects the
    # file, and must say what is *not* protected rather than implying everything is.
    report = response.json()["data_protection"]
    assert report["file_encrypted"] in (True, False)
    assert "sqlite" in report["underlying_engine"].lower()
    assert report["protected"] and report["not_protected"], (
        "the report must say what is *not* protected, not only what is"
    )


def test_startup_key_check_reads_real_rows_and_does_not_false_alarm(client):
    """`verify_stored_data` runs on the same code path the columns use, so a healthy
    instance with the right key must report zero unreadable values. If this ever fails,
    the guard is misdiagnosing and would block a good startup -- which is far worse than
    the confusing crash it exists to prevent.
    """
    from adapt.crypto.fields import verify_stored_data
    from adapt.api.app import app as _app  # noqa: F401  (import guard: the module owns lifespan)
    from adapt.services import current

    db = current().db
    report = verify_stored_data(db.engine)
    assert report["checked"] >= 1, "no encrypted rows were sampled: the test has stopped testing anything"
    assert report["unreadable"] == 0, report
    assert report["columns"] >= 1


def test_startup_key_check_ignores_a_model_column_the_file_has_not_got_yet(services, tmp_path):
    """A database made by an earlier build is missing columns this build declares.

    It must be reported as "not migrated", not as "your key is wrong". SQLite answers a query
    for a column the file does not have with that column's own name as a value, and a value
    that is not a ciphertext cannot be opened: on a file this build wrote, the sample comes
    back as ``TypeError`` and the operator is told that encrypted values are unreadable when
    the key is fine. The guard that fixes it is the live-schema read, and this test fails if
    that read stops happening.

    A second file rather than the shared one on purpose: the point is the sampler's behaviour,
    and rewriting the live test database's schema to make the point would couple every other
    test in the run to whether this one had executed yet. Rows go in through the ORM so the
    column types encrypt them exactly as the app would.
    """
    from sqlalchemy import text as sql_text

    from adapt.crypto.fields import verify_stored_data
    from adapt.db.engine import Database
    from adapt.db.models import Student

    older = Database(services.settings, db_file=tmp_path / "earlier-build.db")
    older.create_all()
    with older.session() as s:
        s.add(Student(id="stu_olderbuild", display_name="Rowan Older", email="older@example.test",
                      email_index="older-index", username="rowan.old", username_index="rowan-old-index",
                      role="student"))
    baseline = verify_stored_data(older.engine)
    assert baseline["unreadable"] == 0, baseline
    assert baseline["missing"] == [], f"nothing should be missing on this build: {baseline}"
    assert baseline["checked"] >= 2, baseline

    # Now make the file look like one an earlier build wrote: the column is gone from the
    # file while the model still declares it.
    with older.engine.begin() as cx:
        cx.execute(sql_text("ALTER TABLE students RENAME TO students_old"))
        cx.execute(sql_text("CREATE TABLE students AS SELECT * FROM students_old"))
        cx.execute(sql_text("ALTER TABLE students DROP COLUMN username"))
    # Sampled after the DDL has committed: inside the transaction the sampler's own connection
    # still sees the old table, which would make this test pass while proving nothing (a
    # mutation that removes the guard has to fail this test).
    reduced = verify_stored_data(older.engine)
    assert reduced["unreadable"] == 0, (
        "a column the file does not have was sampled anyway, and the literal column name was "
        "counted as a value the key cannot open: " + repr(reduced)
    )
    assert "students.username" in reduced["missing"], (
        "the column was not reported as missing, so nothing tells the operator to migrate: "
        + repr(reduced)
    )
    assert reduced["checked"] < baseline["checked"], (
        "the missing column still contributed a sampled value: " + repr(reduced)
    )


def test_startup_key_check_notices_a_foreign_blob():
    """The guard reads through the column type, so a value written by another key (or
    corrupted) is reported instead of surfacing as a 500 to a learner mid-question.

    An isolated in-memory engine on purpose: this must not touch the process-wide cipher
    the other tests in this file share. Every NOT NULL column is filled from its own type,
    so the probe row keeps working when the schema grows.
    """
    from sqlalchemy import Integer, create_engine, text as sql_text

    from adapt.crypto.fields import EncryptedText, verify_stored_data
    from adapt.db.models import Base

    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    tbl = Base.metadata.tables["students"]
    vals: dict[str, object] = {}
    for col in tbl.columns:
        if isinstance(col.type, EncryptedText):
            # Raw text where an AEAD blob belongs: exactly what a foreign key produces.
            vals[col.name] = "not-a-ciphertext-at-all"
            continue
        if col.nullable and not col.primary_key:
            continue
        if isinstance(col.type, Integer):
            vals[col.name] = 0
        else:
            vals[col.name] = "2026-01-01T00:00:00" if col.name.endswith("_at") else "probe"
    cols = ", ".join(f'"{c}"' for c in vals)
    binds = ", ".join(f":{c}" for c in vals)
    with eng.begin() as cx:
        cx.execute(sql_text(f"INSERT INTO students ({cols}) VALUES ({binds})"), vals)

    report = verify_stored_data(eng)
    assert report["checked"] >= 1, "the probe row was not sampled, so this test proves nothing"
    assert report["unreadable"] >= 1, report
    assert report["note"], "a failure was counted without recording what failed"
    # The report is logged as well as raised, and it describes encrypted learner data:
    # it must never carry the value it could not open.
    assert "not-a-ciphertext" not in repr(report)
