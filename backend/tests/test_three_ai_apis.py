"""End-to-end tests for ADAPT's three dedicated AI API slots (`ai1`, `ai2`, `ai3`).

Verifies:
1. Slot 1 (`ai1`): Psychology & Learning-Science API (custom HTTPS site + PubMed + OpenAlex)
   enriching AI 1 hypotheses and learner proposals.
2. Slot 2 (`ai2`): Education Content API (custom HTTPS site + Oak full lesson summaries +
   Wikipedia plain-text extracts) grounding ContentPacks and AI 2 tutoring context.
3. Slot 3 (`ai3`): Lesson Adaptation API (custom HTTPS adaptation endpoint, OpenAI-compatible
   `/chat/completions`, and dedicated Gemini key/model pin `ADAPT_GEMINI_MODEL_AI3`),
   enforcing PII redaction, learner consent, and whole-plan validation fallback.
4. Console-only management (`adapt config set-key`, `set-url`, `remove-url`, `status`, `test`)
   and secrecy scrubbing across all 3 slots.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest
from adapt import services
from adapt.ai.routing import plan as route_plan
from adapt.cli import app as cli_app
from adapt.config import Settings
from adapt.config_secrets import config_file, settings_with_managed_values
from adapt.connectors import ai1_psychology, ai2_education, slots
from adapt.content.skeleton.service import _skeleton_prompts, get_pack
from adapt.db.engine import Database
from adapt.db.models import Course, Student, Topic
from adapt.learning import hypotheses as H
from adapt.security import auth
from adapt.security.secrecy import scrub_public_payload
from fastapi.testclient import TestClient
from typer.testing import CliRunner


def _isolated_settings(tmp_path: Path, **overrides: Any) -> Settings:
    data_dir = tmp_path / "var"
    shared_keyfile = Path(os.environ["ADAPT_KEYFILE"])
    base: dict[str, Any] = {
        "data_dir": data_dir,
        "db_path": data_dir / "adapt.db",
        "keyfile": shared_keyfile,
        "db_encryption": "field-only",
        "gemini_api_key": None,
        "ai1_api_url": None,
        "ai1_api_key": None,
        "ai1_psychology_enabled": False,
        "pubmed_api_key": None,
        "ai2_api_url": None,
        "ai2_api_key": None,
        "ai2_education_enabled": False,
        "oak_api_key": None,
        "ai3_api_url": None,
        "ai3_api_key": None,
        "gemini_model_ai3": None,
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


# --------------------------------------------------------------------------- #
# 1. Slot 1 — AI 1 Psychology & Learning-Science API
# --------------------------------------------------------------------------- #


def test_slot1_custom_psychology_api_enriches_ai1_proposals(tmp_path: Path) -> None:
    seen_headers: list[dict[str, str]] = []

    def psych_transport(
        method: str, url: str, headers: dict[str, str], params: dict[str, Any], timeout: float
    ) -> tuple[int, Any]:
        assert method == "GET"
        seen_headers.append(dict(headers))
        assert url == "https://psych.example.org/api/findings"
        return (
            200,
            {
                "attribution": "Cognitive Lab API (CC BY 4.0)",
                "findings": [
                    {
                        "finding": "<b>Worked examples</b> reduce extraneous load during early skill acquisition.",
                        "citation": "Sweller et al. (2019)",
                        "url": "https://psych.example.org/sweller2019",
                    }
                ],
            },
        )

    st = _isolated_settings(
        tmp_path,
        ai1_api_url="https://psych.example.org/api/findings",
        ai1_api_key="ai1-secret-psych-key-9999",
        ai1_api_header="X-API-Key",
    )
    db = Database(st)
    db.create_all()
    try:
        svcs = services.build(st, db=db, with_resources=False)
        svcs.ai1.psychology_transport = psych_transport
        with db.session() as s:
            student = Student(display_name="Ada")
            s.add(student)
            s.commit()
            sid = student.id

        draft = H.Draft(
            detector="worked_example_need",
            strategy="worked_examples",
            statement="Accuracy rose from 40% to 85% after opening an example on 8 items.",
            mechanism="Seeing a complete procedure lowers working-memory search load.",
            alternatives=["item difficulty shifted"],
            falsification="Disproved if accuracy stops improving over the next 8 items.",
            citations=["Sweller (1988)"],
            confidence=0.72,
            evidence_ids=[],
        )
        proposals = svcs.ai1.propose(sid, [draft])
        assert len(proposals) == 1
        prop = proposals[0]
        assert "Sweller et al. (2019)" in draft.citations
        assert "Worked examples reduce extraneous load" in prop["hypothesis"]
        assert "<b>" not in prop["hypothesis"]
        assert seen_headers and seen_headers[0].get("X-API-Key") == "ai1-secret-psych-key-9999"
    finally:
        db.dispose()


def test_slot1_builtin_pubmed_and_openalex_connectors(tmp_path: Path) -> None:
    def multi_transport(
        method: str, url: str, headers: dict[str, str], params: dict[str, Any], timeout: float
    ) -> tuple[int, Any]:
        parsed = urlparse(url)
        if parsed.hostname == "eutils.ncbi.nlm.nih.gov" and parsed.path.endswith("esearch.fcgi"):
            assert params.get("api_key") == "pubmed-key-1234"
            return 200, {"esearchresult": {"idlist": ["38100123"]}}
        if parsed.hostname == "eutils.ncbi.nlm.nih.gov" and parsed.path.endswith("esummary.fcgi"):
            return (
                200,
                {
                    "result": {
                        "uids": ["38100123"],
                        "38100123": {
                            "title": "Retrieval practice strengthens delayed mathematical transfer.",
                            "pubdate": "2024 May",
                            "source": "J Educ Psychol",
                            "authors": [{"name": "Roediger HL"}],
                        },
                    }
                },
            )
        if parsed.hostname == "api.openalex.org":
            return (
                200,
                {
                    "results": [
                        {
                            "id": "https://openalex.org/W1",
                            "title": "Spacing schedules and long-term retention in classroom practice",
                            "publication_year": 2023,
                            "authorships": [{"author": {"display_name": "Cepeda N"}}],
                            "abstract_inverted_index": {
                                "Distributed": [0],
                                "practice": [1],
                                "improves": [2],
                                "long-term": [3],
                                "memory": [4],
                                "retention.": [5],
                            },
                        }
                    ]
                },
            )
        raise AssertionError(f"unexpected URL {url}")

    st = _isolated_settings(
        tmp_path,
        pubmed_api_key="pubmed-key-1234",
        ai1_psychology_enabled=True,
    )
    bundle = ai1_psychology.fetch_psychology_facts(
        "spaced_retrieval",
        settings=st,
        transport=multi_transport,
        limit=4,
    )
    assert bundle.enabled is True
    assert "pubmed" in bundle.sources_used
    assert "openalex" in bundle.sources_used
    assert any("Roediger HL" in f.citation for f in bundle.facts)
    assert any("Distributed practice improves long-term memory retention" in f.finding for f in bundle.facts)
    verdict = slots.check_slot("ai1", st, transport=multi_transport)
    assert verdict["ok"] is True


# --------------------------------------------------------------------------- #
# 2. Slot 2 — AI 2 Education Content API
# --------------------------------------------------------------------------- #


def test_slot2_custom_education_api_builds_validated_pack_and_grounds_prompts(tmp_path: Path) -> None:
    def edu_transport(
        method: str, url: str, headers: dict[str, str], params: dict[str, Any], timeout: float
    ) -> tuple[int, Any]:
        assert headers.get("authorization") == "Bearer ai2-edu-secret-5678"
        return (
            200,
            {
                "title": "Cellular Respiration",
                "attribution": "OpenBio Curriculum API (CC BY 4.0)",
                "summary": "Cellular respiration converts glucose and oxygen into ATP energy, water, and carbon dioxide.",
                "facts": [
                    "Glycolysis splits one glucose molecule into two pyruvate molecules in the cytoplasm.",
                    "The Krebs cycle generates electron carriers inside the mitochondrial matrix.",
                    "Oxidative phosphorylation produces the majority of ATP across the inner mitochondrial membrane.",
                ],
                "misconceptions": [
                    {
                        "wrong_belief": "Plants only photosynthesize and never respire",
                        "why_tempting": "Photosynthesis is taught first as the plant energy process",
                        "correction": "Plant cells perform cellular respiration continuously to power cellular work.",
                    }
                ],
            },
        )

    st = _isolated_settings(
        tmp_path,
        ai2_api_url="https://edu.example.org/v1/topic",
        ai2_api_key="ai2-edu-secret-5678",
    )
    pack = get_pack(
        "cellular respiration",
        level=2,
        language="en",
        settings=st,
        education_transport=edu_transport,
    )
    assert pack.source == "authored"
    assert len(pack.concepts) >= 1
    assert any("Glycolysis" in f.statement for c in pack.concepts for f in c.key_facts)
    assert len(pack.items) >= 2
    assert all(not item.scorable for item in pack.items)

    mat = ai2_education.fetch_educational_material(
        "cellular respiration", settings=st, transport=edu_transport
    )
    _sys, user_prompt = _skeleton_prompts(
        topic="cellular respiration",
        level=2,
        language="en",
        external_reference=mat.grounding_dict(),
    )
    assert "verified_reference" in user_prompt
    assert "Glycolysis splits one glucose molecule" in user_prompt


def test_slot2_builtin_oak_lessons_and_wikipedia_feed_ai2_tutor_context(tmp_path: Path) -> None:
    def edu_builtin_transport(
        method: str, url: str, headers: dict[str, str], params: dict[str, Any], timeout: float
    ) -> tuple[int, Any]:
        parsed = urlparse(url)
        if parsed.hostname == "open-api.thenational.academy":
            assert headers.get("authorization") == "Bearer oak-secret-7777"
            if parsed.path.endswith("/search/lessons"):
                return 200, {"lessons": [{"lessonSlug": "osmosis-in-cells", "lessonTitle": "Osmosis in cells"}]}
            if parsed.path.endswith("/lessons/osmosis-in-cells/summary"):
                return (
                    200,
                    {
                        "lessonTitle": "Osmosis in cells",
                        "pupilLessonOutcome": "Explain osmosis as net movement of water across a partially permeable membrane.",
                        "keyLearningPoints": [
                            {"keyLearningPoint": "Water moves from a dilute solution to a concentrated solution."}
                        ],
                        "lessonKeywords": [
                            {
                                "keyword": "Partially permeable",
                                "description": "Lets small water molecules pass but blocks larger solutes",
                            }
                        ],
                        "misconceptionsAndCommonMistakes": [
                            {
                                "misconception": "Solute particles move during osmosis",
                                "response": "Osmosis specifically tracks net water movement across the membrane.",
                            }
                        ],
                    },
                )
        if parsed.hostname == "en.wikipedia.org":
            return (
                200,
                {
                    "query": {
                        "pages": [
                            {
                                "title": "Osmosis",
                                "extract": (
                                    "Osmosis is the spontaneous net movement of solvent molecules through a "
                                    "selectively permeable membrane. It equalizes solute concentrations on "
                                    "the two sides of the membrane."
                                ),
                            }
                        ]
                    }
                },
            )
        raise AssertionError(f"unexpected URL {url}")

    st = _isolated_settings(
        tmp_path,
        oak_api_key="oak-secret-7777",
        ai2_education_enabled=True,
    )
    db = Database(st)
    db.create_all()
    try:
        svcs = services.build(st, db=db, with_resources=False)
        svcs.ai2.education_transport = edu_builtin_transport

        with db.session() as s:
            student = Student(display_name="Grace")
            course = Course(title="Biology")
            s.add_all([student, course])
            s.flush()
            topic = Topic(course_id=course.id, code="osmosis", title="Osmosis", description="")
            s.add(topic)
            s.commit()
            sid, tid = student.id, topic.id

        ctx = svcs.ai2.context(sid, topic_id=tid)
        assert ctx["external_material"] is not None
        assert "oak" in ctx["external_material"]["sources_used"]
        assert "wikipedia" in ctx["external_material"]["sources_used"]
        joined_materials = " ".join(m[2] for m in ctx["materials"])
        assert "Water moves from a dilute solution to a concentrated solution" in joined_materials
        assert "spontaneous net movement of solvent molecules" in joined_materials
    finally:
        db.dispose()


# --------------------------------------------------------------------------- #
# 3. Slot 3 — AI 3 Lesson Adaptation API (Custom REST, OpenAI-compat, Gemini)
# --------------------------------------------------------------------------- #


def test_slot3_end_to_end_adapts_slot2_material_and_enforces_consent_and_validation(
    client: TestClient, learner: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    sent_ai3_bodies: list[dict[str, Any]] = []
    return_invalid_plan = {"flag": False}

    def edu_transport(
        method: str, url: str, headers: dict[str, str], params: dict[str, Any], timeout: float
    ) -> tuple[int, Any]:
        return (
            200,
            {
                "title": "Momentum Conservation",
                "summary": (
                    "Total momentum of a closed system stays constant when no net force acts."
                ),
                "facts": [
                    "Momentum equals mass multiplied by velocity.",
                    "In a closed collision total momentum before equals total momentum after.",
                ],
                "questions": [{"stem": "What stays constant in a closed collision?"}],
            },
        )

    def ai3_post_transport(
        method: str, url: str, headers: dict[str, str], body: dict[str, Any], timeout: float
    ) -> tuple[int, Any]:
        assert method == "POST"
        assert url == "https://adapt-engine.example.org/v1/chat/completions"
        assert headers.get("authorization") == "Bearer ai3-key-bbbb2222"
        sent_ai3_bodies.append(dict(body))
        if return_invalid_plan["flag"]:
            bad_content = json.dumps(
                {
                    "blocks": [
                        {"kind": "text", "content": {"text": "<b>Bad markup</b> without check."}}
                    ]
                }
            )
            return 200, {"choices": [{"message": {"content": bad_content}}]}
        valid_content = json.dumps(
            {
                "blocks": [
                    {
                        "kind": "text",
                        "content": {
                            "text": "Momentum equals mass multiplied by velocity in motion."
                        },
                    },
                    {
                        "kind": "check",
                        "content": {
                            "question": "Which two quantities multiply to give momentum?",
                            "questions": 1,
                        },
                    },
                    {
                        "kind": "reflect",
                        "content": {
                            "text": "Review how closed collisions conserve total momentum."
                        },
                    },
                ]
            }
        )
        return 200, {"choices": [{"message": {"content": valid_content}}]}

    svcs = services.current()
    assert svcs.ai3_adapter is not None
    monkeypatch.setattr(svcs.settings, "ai2_api_url", "https://edu.example.org/v1/lesson")
    monkeypatch.setattr(svcs.settings, "ai2_api_key", "ai2-key-aaaa1111")
    monkeypatch.setattr(
        svcs.settings, "ai3_api_url", "https://adapt-engine.example.org/v1/chat/completions"
    )
    monkeypatch.setattr(svcs.settings, "ai3_api_key", "ai3-key-bbbb2222")
    monkeypatch.setattr(svcs.settings, "gemini_model_ai3", "custom-adapt-model-v1")
    monkeypatch.setattr(svcs.ai2, "education_transport", edu_transport)
    monkeypatch.setattr(svcs.ai3_adapter, "transport", ai3_post_transport)

    sid = learner["student_id"]
    headers = learner["headers"]

    with svcs.db.session() as s:
        st_row = s.get(Student, sid)
        assert st_row is not None
        st_row.display_name = "amina@example.com"
        st_row.consent_share_with_llm = False
        s.commit()

    r_no_consent = client.post(
        "/api/v1/lesson-plan",
        headers=headers,
        json={"concept": "momentum", "title": "Momentum Conservation"},
    )
    assert r_no_consent.status_code == 200
    assert len(sent_ai3_bodies) == 0

    with svcs.db.session() as s:
        st_row = s.get(Student, sid)
        assert st_row is not None
        st_row.consent_share_with_llm = True
        s.commit()

    r_adapted = client.post(
        "/api/v1/lesson-plan",
        headers=headers,
        json={"concept": "momentum", "title": "Momentum Conservation"},
    )
    assert r_adapted.status_code == 200
    plan = r_adapted.json()["plan"]
    assert any(
        "Momentum equals mass multiplied by velocity" in str(b["content"])
        for b in plan["blocks"]
    )
    assert len(sent_ai3_bodies) == 1
    raw_sent = json.dumps(sent_ai3_bodies[0])
    assert "amina@example.com" not in raw_sent
    assert sent_ai3_bodies[0]["model"] == "custom-adapt-model-v1"

    return_invalid_plan["flag"] = True
    r_fallback = client.post(
        "/api/v1/lesson-plan",
        headers=headers,
        json={"concept": "momentum", "title": "Momentum Conservation"},
    )
    assert r_fallback.status_code == 200
    fb_plan = r_fallback.json()["plan"]
    assert fb_plan.get("rejected_proposal_reasons")
    assert all("<b>" not in str(b["content"]) for b in fb_plan["blocks"])


def test_slot3_model_pin_and_distinct_per_slot_gemini_keys(tmp_path: Path) -> None:
    st = _isolated_settings(
        tmp_path,
        ai1_api_key="ai1-gemini-key-1111",
        ai2_api_key="ai2-gemini-key-2222",
        ai3_api_key="ai3-gemini-key-3333",
        gemini_model_ai1="gemini-ai1-pin",
        gemini_model_ai2="gemini-ai2-pin",
        gemini_model_ai3="gemini-ai3-pin",
    )
    assert route_plan("ai1_propose", st, role="ai1").model == "gemini-ai1-pin"
    assert route_plan("ai2_explain", st, role="ai2").model == "gemini-ai2-pin"
    assert route_plan("ai3_lesson_plan", st, role="ai3").model == "gemini-ai3-pin"

    db = Database(st)
    db.create_all()
    try:
        svcs = services.build(st, db=db, with_resources=False)
        assert svcs.ai1.llm.settings.gemini_api_key == "ai1-gemini-key-1111"
        assert svcs.ai2.llm.settings.gemini_api_key == "ai2-gemini-key-2222"
        assert svcs.ai3 is not None and svcs.ai3.llm.settings.gemini_api_key == "ai3-gemini-key-3333"
        assert svcs.ai3_adapter is not None and svcs.ai3_adapter.llm.settings.gemini_api_key == "ai3-gemini-key-3333"
    finally:
        db.dispose()


# --------------------------------------------------------------------------- #
# 4. Console-only CLI (`adapt config`) & Secrecy Scrubbing
# --------------------------------------------------------------------------- #


def test_cli_config_manages_all_three_slots_without_leaking_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_dir = tmp_path / "var"
    monkeypatch.setenv("ADAPT_DATA_DIR", str(data_dir))
    monkeypatch.setenv("ADAPT_DB_PATH", str(data_dir / "adapt.db"))
    monkeypatch.setenv("ADAPT_DB_ENCRYPTION", "field-only")
    for env_key in (
        "ADAPT_GEMINI_API_KEY",
        "ADAPT_AI1_API_KEY",
        "ADAPT_AI2_API_KEY",
        "ADAPT_AI3_API_KEY",
        "ADAPT_AI1_API_URL",
        "ADAPT_AI2_API_URL",
        "ADAPT_AI3_API_URL",
    ):
        monkeypatch.delenv(env_key, raising=False)

    from adapt.config import get_settings

    get_settings.cache_clear()
    runner = CliRunner()

    # Reject insecure http:// URLs and embedded credentials on console `set-url`.
    bad_http = runner.invoke(
        cli_app, ["config", "set-url", "--slot", "ai1", "--url", "http://insecure.example.org/api"]
    )
    assert bad_http.exit_code == 2

    bad_creds = runner.invoke(
        cli_app,
        ["config", "set-url", "--slot", "ai2", "--url", "https://user:pass@edu.example.org/api"],
    )
    assert bad_creds.exit_code == 2

    # Configure all 3 slots via console CLI (`set-url` + hidden-prompt `set-key`).
    secrets = {
        "ai1": "slot1-super-secret-key-alpha91",
        "ai2": "slot2-super-secret-key-bravo92",
        "ai3": "slot3-super-secret-key-delta93",
    }
    urls = {
        "ai1": "https://psych-slot1.example.org/v1/facts",
        "ai2": "https://edu-slot2.example.org/v1/lessons",
        "ai3": "https://adapt-slot3.example.org/v1/adapt",
    }
    for slot_id in ("ai1", "ai2", "ai3"):
        r_url = runner.invoke(
            cli_app,
            ["config", "set-url", "--slot", slot_id, "--url", urls[slot_id], "--header", "X-API-Key"],
        )
        assert r_url.exit_code == 0, r_url.output
        r_key = runner.invoke(
            cli_app,
            ["config", "set-key", "--service", slot_id],
            input=f"{secrets[slot_id]}\n{secrets[slot_id]}\n",
        )
        assert r_key.exit_code == 0, r_key.output

    secret_file = config_file(data_dir)
    assert secret_file.exists()
    if os.name == "posix":
        assert stat.S_IMODE(secret_file.stat().st_mode) == 0o600

    get_settings.cache_clear()
    r_status = runner.invoke(cli_app, ["config", "status"])
    assert r_status.exit_code == 0, r_status.output
    assert "slot ai1 (Psychology & Learning-Science API): enabled · custom-url" in r_status.output
    assert "slot ai2 (Education Content API): enabled · custom-url" in r_status.output
    assert "slot ai3 (Lesson Adaptation API): enabled · custom-url" in r_status.output

    # Neither raw keys nor full secret URLs may appear in status output.
    for val in [*secrets.values(), *urls.values()]:
        assert val not in r_status.output

    # Verify secrecy scrubber strips all 3-slot keys and URLs from public payloads.
    managed = settings_with_managed_values(get_settings())
    leaked = {
        "ai1_api_key": secrets["ai1"],
        "note": f"Reached {urls['ai2']} using {secrets['ai3']}",
    }
    scrubbed = scrub_public_payload(leaked, managed)
    assert "ai1_api_key" not in scrubbed
    for val in [*secrets.values(), *urls.values()]:
        assert val not in str(scrubbed)

    # Remove URL via CLI cleanly.
    r_rm = runner.invoke(cli_app, ["config", "remove-url", "--slot", "ai1"], input="y\n")
    assert r_rm.exit_code == 0
    get_settings.cache_clear()
