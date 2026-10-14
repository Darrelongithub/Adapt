"""Round 2 / Part A regressions: public privacy list, secrecy-safe logging.

Task 1: the "what is recorded" list is static and non-personal, so a
signed-out visitor has to be able to read it — before, ``GET /privacy``
answered 401 and the consent screen told learners the list "could not be
read just now".

Task 3: the secrecy log filter used to rebuild ``record.args`` as a
rendered string/dict, which broke uvicorn's access formatter (it expects
the original tuple shape) and produced a ``Logging error`` traceback on
every request. The filter now redacts the message and each argument in
place, keeping shapes and types.
"""

from __future__ import annotations

import logging

from adapt.config import get_settings
from adapt.security.secrecy import _redact_log_record


SECRET = "sk-proj-AAAABBBBCCCCDDDDEEEEFFFF"
API_KEY_VALUE = "AIzaSyAAAABBBBCCCCDDDDEEEEFFFF00001111"
SETTINGS = get_settings()


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v)


# --------------------------------------------------------------------- task 1


def test_privacy_list_is_readable_signed_out(client):
    """No session, no sign-in: the static recorded-fields list still answers."""
    r = client.get("/api/v1/privacy")
    assert r.status_code == 200, r.text
    body = r.json()
    for key in ("telemetry", "sent_to_model", "voice", "storage", "learner_controls"):
        assert key in body, f"privacy payload lost {key!r}"
    assert body["telemetry"]["recorded"], "the recorded-fields list must not be empty"


def test_privacy_payload_names_no_developer_terms(client):
    """The learner-facing strings in the payload name no keys, engines or routes."""
    r = client.get("/api/v1/privacy")
    assert r.status_code == 200
    banned = ("api key", "gemini", "provider", "uvicorn", "endpoint", " ai 1", " ai 2", " ai 3")
    for text in _strings(r.json()):
        low = f" {text.lower()} "
        for word in banned:
            assert word not in low, f"{word!r} leaked into privacy text: {text!r}"


# --------------------------------------------------------------------- task 3


def _record(msg, args, name="adapt.test"):
    logger = logging.getLogger(name)
    return logger.makeRecord(name, logging.INFO, __file__, 10, msg, args, None)


def test_secrecy_filter_preserves_argument_shapes():
    """Tuple/list/mapping args keep their shape and non-string members."""
    mapping = {"path": f"/x?key={SECRET}", "count": 3}
    listing = [f"api_key={API_KEY_VALUE}", 7]
    record = _record("hit %s %s %s %d", (mapping, listing, "plain", 42))
    _redact_log_record(record, SETTINGS)
    assert isinstance(record.args, tuple) and len(record.args) == 4
    assert isinstance(record.args[0], dict) and isinstance(record.args[1], list)
    assert record.args[1][1] == 7
    assert record.args[2] == "plain" and record.args[3] == 42
    rendered = logging.Formatter("%(message)s").format(record)
    assert SECRET not in rendered and API_KEY_VALUE not in rendered


def test_secrecy_filter_redacts_the_message_itself():
    record = _record(f"booting with api_key={SECRET} configured", ())
    _redact_log_record(record, SETTINGS)
    rendered = logging.Formatter("%(message)s").format(record)
    assert SECRET not in rendered
    assert "configured" in rendered


def test_uvicorn_access_record_still_formats_after_filtering():
    """The exact failure from the bug: uvicorn's AccessFormatter must not raise."""
    from uvicorn.logging import AccessFormatter

    logger = logging.getLogger("uvicorn.access")
    record = logger.makeRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        10,
        "%s - \"%s %s HTTP/%s\" %d",
        ("127.0.0.1:5000", "GET", f"/api/v1/status?key={SECRET}", "1.1", 200),
        None,
    )
    shape_before = (type(record.args), len(record.args))
    _redact_log_record(record, SETTINGS)
    assert (type(record.args), len(record.args)) == shape_before
    rendered = AccessFormatter(fmt='%(client_addr)s - "%(request_line)s" %(status_code)s').format(record)
    assert SECRET not in rendered
    assert "200" in rendered
