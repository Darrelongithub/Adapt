"""Round 2 phase A: public privacy, access-log redaction, hints and closing prompts."""

from __future__ import annotations

import json
import logging
import re
import sys
from types import SimpleNamespace


def _anonymous(client):
    jar = client.cookies.jar
    saved = list(jar)
    for cookie in saved:
        jar.clear(cookie.domain, cookie.path, cookie.name)
    return saved


def _restore(client, saved) -> None:
    for cookie in saved:
        client.cookies.jar.set_cookie(cookie)


def test_privacy_is_public_and_names_no_service(client):
    saved = _anonymous(client)
    try:
        response = client.get("/api/v1/privacy")
        assert response.status_code == 200, response.text
        body = response.json()
    finally:
        _restore(client, saved)
    assert "student_id" not in body
    assert body["sent_to_model"]["when"]
    assert body["sent_to_model"]["redaction"]
    assert any("delete" in str(item).lower() for item in body["learner_controls"])
    blob = json.dumps(body).lower()
    for banned in (
        "gemini",
        "budget",
        "api key",
        "uvicorn",
        "get /",
        "post /",
        "provider",
        "token",
        "kokoro",
        "faster-whisper",
        "ignored_keys",
        "endpoint",
    ):
        assert banned not in blob, banned
    for field in ("sent_to_model", "voice", "learner_controls"):
        assert "model" not in json.dumps(body[field]).lower()


def test_access_log_keeps_uvicorn_arg_shape_and_hides_the_secret(capsys):
    from uvicorn.logging import AccessFormatter

    from adapt.config import get_settings
    from adapt.security.secrecy import install_log_redaction

    canary = "access-path-canary-KEY-55aa"
    settings = SimpleNamespace(
        gemini_api_key=canary,
        youtube_api_key="",
        gemini_model_ai1="canary-model-ACCESS",
        gemini_model_ai2="",
        gemini_base_url="",
        openalex_mailto="",
    )
    install_log_redaction(settings)
    logger = logging.getLogger("adapt.test.access")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.INFO)
    try:
        handler = logging.StreamHandler()
        handler.setFormatter(
            AccessFormatter('%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s')
        )
        logger.addHandler(handler)
        path = f"/api/v1/privacy?q={canary}&also=canary-model-ACCESS"
        logger.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:9", "GET", path, "1.1", 200)
        logger.removeHandler(handler)
        record = handler  # placate linters; the record is captured below
        del record
        # Re-emit through a capturing handler so we can inspect the record itself.
        captured: list[logging.LogRecord] = []

        class _Grab(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                captured.append(record)

        grab = _Grab()
        logger.addHandler(grab)
        logger.info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:9", "GET", path, "1.1", 201)
        logger.removeHandler(grab)
        assert captured, "the access record was created"
        record = captured[-1]
        assert isinstance(record.args, tuple) and len(record.args) == 5
        assert type(record.args[4]) is int and record.args[4] == 201
        assert canary not in str(record.args)
        assert "canary-model-ACCESS" not in str(record.args)
        assert canary not in record.getMessage()
        line = AccessFormatter(
            '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s'
        ).format(record)
        assert canary not in line
        assert "canary-model-ACCESS" not in line
        assert "201" in line
        err = capsys.readouterr()
        assert "Logging error" not in err.err
        assert "Logging error" not in err.out
        assert "Logging error" not in line
    finally:
        install_log_redaction(get_settings())


def test_mcq_hints_come_from_distractors_and_do_not_name_the_answer():
    from adapt.assessment import bank as BANK
    from adapt.assessment.worked import correct_choice, ensure_hints, generate_hints

    seen = 0
    for topic in BANK.TOPICS:
        for raw in BANK.ITEMS.get(topic["code"], []):
            if not raw.get("choices"):
                continue
            item = dict(BANK.render_item_for(dict(raw), seed=0))
            generated = generate_hints(item)
            served = ensure_hints(item)
            blob = " ".join(generated + served).lower()
            assert "which one is right" not in blob
            correct = str((correct_choice(item) or {}).get("text") or "").strip()
            quoted = re.findall("“([^”]+)”", " ".join(generated))
            if correct:
                assert correct not in quoted, (correct, quoted)
            assert len(generated) == 3
            assert len({h.strip().lower() for h in generated}) == 3
            seen += 1
    assert seen >= 10


def test_closing_prompt_follows_item_kind_and_is_absent_when_nothing_fits():
    from adapt.ai.local_tutor import BM25, _closing_prompt, explain
    from adapt.assessment import bank as BANK

    numeric = next(item for item in BANK.ITEMS["integer_ops"] if item["kind"] == "numeric")
    choice = next(item for item in BANK.ITEMS["integer_ops"] if item["kind"] == "mcq")
    factual = next(item for item in BANK.ITEMS["history_ww1"] if item["kind"] == "text")
    teach = next(item for item in BANK.ITEMS["fractions_intro"] if item["kind"] == "teachback")
    assert "change only the numbers" in (_closing_prompt(numeric) or "")
    assert "Cover the options" in (_closing_prompt(choice) or "")
    assert "without looking" in (_closing_prompt(factual) or "")
    assert _closing_prompt(teach) is None
    assert _closing_prompt(None) is None
    assert _closing_prompt({"kind": "worked_example", "stem": "watch this"}) is None

    index = BM25([])
    rendered = dict(BANK.render_item_for(dict(numeric), seed=1))
    rendered["topic_id"] = "integer_ops"
    rendered["topic_title"] = "Integer operations"
    out = explain(
        rendered["stem"],
        index=index,
        topic_title="Integer operations",
        topic_id="integer_ops",
        p_know=0.2,
        item=rendered,
        seed="numeric",
    )
    assert "change only the numbers" in out.text
    assert "Key idea:" in out.text and "Why it works:" in out.text
    assert out.text.index("Key idea:") < out.text.index(rendered["solution_steps"][0]["text"])

    teach_out = explain(
        teach["stem"],
        index=index,
        topic_title="What a fraction means",
        topic_id="fractions_intro",
        p_know=0.2,
        item=dict(teach),
        seed="teach",
    )
    assert "your turn" not in " ".join(sec["label"] for sec in teach_out.sections)
    assert "change only the numbers" not in teach_out.text


def test_key_idea_matches_bank_code_and_title_but_not_a_database_id():
    from adapt.content.concept_why import CONCEPT_WHY, sentences_for

    pair = sentences_for(topic_id="fraction_add")
    assert pair is not None
    assert pair[0].endswith(".") and pair[1].endswith(".")
    assert "\n" not in pair[0] and "\n" not in pair[1]
    titled = sentences_for(topic_id="top_db_99", topic_title="Adding and subtracting fractions")
    assert titled == pair
    assert sentences_for(topic_id="top_db_99", topic_title="the topic") is None
    assert sentences_for(topic_title="the topic") is None
    assert sentences_for(tags=["fraction_add"]) == pair
    assert len(CONCEPT_WHY) == 22
    for row in CONCEPT_WHY.values():
        assert row["key_idea"].endswith(".")
        assert row["why_it_works"].endswith(".")


def test_identical_hint_residue_is_flagged_and_the_bank_is_not():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "scripts" / "teaching_quality_eval.py"
    spec = importlib.util.spec_from_file_location("teaching_quality_eval_a", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    flags: list = []
    module._flag_identical_hints(
        [
            ("a", "read every option before you choose"),
            ("b", "read every option before you choose"),
            ("c", "read every option before you choose"),
        ],
        flags,
    )
    assert flags and flags[0].kind == "generic_hint"
    flags.clear()
    module._flag_identical_hints(
        [("a", "unique hint one about halves"), ("b", "unique hint two about thirds")],
        flags,
    )
    assert flags == []
