"""Part E's harness, tested: the run it drives, the refusal it owes, and the red team it must catch.

The live run itself has **not** been made here — there is no key in this sandbox, and pretending
otherwise is the one thing this phase may not do. What is tested instead is everything around it:
that the harness runs the whole pipeline across the twelve topics, that it refuses to run without
a credential, that it can be driven end to end by the scripted fake, and — the part that matters
most — that each way of producing *bad* teaching is caught and fails the run.

Every one of those is run through the same `main()` the owner runs, so the exit codes and the
report in these tests are the exit codes and the report a live run would produce.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
HARNESS_PATH = ROOT / "scripts" / "live_model_check.py"
MODEL_FIXTURES = ROOT / "backend" / "tests" / "fixtures" / "model"


def _load(path: Path, name: str):
    """Import a file by path (the harness and its doubles are not part of the package)."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader, f"cannot load {path}"
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses look themselves up here while the module is built
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def harness():
    return _load(HARNESS_PATH, "live_model_check_under_test")


@pytest.fixture(scope="module")
def scripted():
    return _load(MODEL_FIXTURES / "scripted_model.py", "live_check_scripted_model")


@pytest.fixture(scope="module")
def stub():
    return _load(MODEL_FIXTURES / "contract_stub.py", "live_check_contract_stub")


# ------------------------------------------------------------------ what the run is made of --


def test_the_twelve_topics_the_brief_names_are_the_ones_that_run(harness):
    required = [
        "quantum chromodynamics",
        "how to bake bread",
        "Kiswahili verbs",
        "sorting algorithms",
        "supply and demand",
        "the French Revolution",
        "discrete maths (sets)",
        "derivatives",
        "constitutional law",
        "essay writing",
        "photosynthesis",
        "probability",
    ]
    names = [entry["topic"] for entry in harness.TOPICS]
    for topic in required:
        assert topic in names, topic
    assert len(names) >= 12
    # "including outside the authored set": the run is mostly topics the app has never seen
    assert sum(1 for entry in harness.TOPICS if not entry["authored"]) >= 10


def test_the_two_profiles_stress_every_lever_the_judge_scores(harness):
    assert len(harness.PROFILES) == 2
    langs = {profile["language"] for profile in harness.PROFILES}
    steps = {profile["step_size"] for profile in harness.PROFILES}
    leads = {profile["lead_format"] for profile in harness.PROFILES}
    tones = {profile["tone"] for profile in harness.PROFILES}
    assert {"en", "sw"} == langs, "a run where every profile is English never tests the language lever"
    assert len(steps) == 2 and len(leads) == 2 and len(tones) == 2
    for profile in harness.PROFILES:
        assert profile["max_words_per_block"] > 0


# ------------------------------------------------------------------------- the whole run ----- #


def test_the_self_test_runs_all_twelve_topics_and_passes(harness, tmp_path, monkeypatch):
    monkeypatch.delenv("ADAPT_GEMINI_API_KEY", raising=False)
    out = tmp_path / "report.md"
    code = harness.main(["--selftest", "--out", str(out)])
    assert code == 0, out.read_text()[:600]
    text = out.read_text()
    assert "Self-test, not a model run" in text
    assert "No model was contacted" in text
    assert "Verdict: PASS" in text
    assert "contract stub" in text, "the report has to say which pipeline it drove"
    for entry in harness.TOPICS:
        assert entry["topic"] in text, entry["topic"]
    assert "* none" in text.split("## Gate flags")[1].split("## Measures")[0]
    # the judge ran, and its scores are in the report with the quotes behind them
    assert "judge · no_leakage" in text
    assert "/5 —" in text


def test_it_refuses_to_run_without_a_credential_and_says_how_to_set_one(harness, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("ADAPT_GEMINI_API_KEY", raising=False)
    report = tmp_path / "report.md"
    report.write_text("untouched", encoding="utf-8")
    code = harness.main(["--out", str(report)])
    assert code == 2, "without a key the run must refuse, not fall back to a fake"
    err = capsys.readouterr().err
    assert "adapt config set-key" in err and "adapt config test" in err
    assert report.read_text(encoding="utf-8") == "untouched", "a refused run must not write a report"


def test_a_partial_run_is_incomplete_unless_a_verdict_on_it_was_asked_for(harness, tmp_path):
    silent = tmp_path / "silent.md"
    assert harness.main(["--selftest", "--topics", "probability", "--out", str(silent)]) == 3
    assert "Verdict: INCOMPLETE" in silent.read_text()

    asked = tmp_path / "asked.md"
    assert harness.main(["--selftest", "--topics", "probability", "--allow-partial", "--out", str(asked)]) == 0
    text = asked.read_text()
    assert "Verdict: PASS" in text
    assert "**partial:**" in text, "a subset run must still say it was one"


def test_a_stopped_run_exits_three_and_names_the_reason(harness, tmp_path):
    def exhausted():
        def call(purpose, system, user, schema, max_tokens):
            raise harness.ModelUnavailable("quota", "daily call budget reached")

        return call

    out = tmp_path / "stopped.md"
    code = harness.main(["--selftest", "--out", str(out)], model_factory=exhausted)
    assert code == 3, "a run the budget stopped has not earned a verdict"
    text = out.read_text()
    assert "Verdict: INCOMPLETE" in text
    assert "budget" in text


def test_the_call_budget_stops_a_run_rather_than_overspending(harness, tmp_path):
    out = tmp_path / "budget.md"
    code = harness.main(["--selftest", "--max-calls", "3", "--out", str(out)])
    assert code == 3
    text = out.read_text()
    assert "Verdict: INCOMPLETE" in text
    assert "per-run call limit of 3 is spent" in text, text[-800:]
    # the reason has to survive the secrecy scrubber intact: it is what the owner reads
    assert "optional service limit" not in text


# ---------------------------------------------------------------------------- the red team -- #


@pytest.mark.parametrize(
    "variant",
    [
        "leaky",
        "devwords",
        "placeholder",
        "bare_judge",
        "refusing",
        "key_mismatch",
        "unverified",
        "wrong_language",
        "keypaste",
    ],
)
def test_each_way_of_producing_bad_teaching_fails_the_run(harness, scripted, tmp_path, variant):
    """A check that cannot fail is not a check: every variant has to be caught."""
    out = tmp_path / f"{variant}.md"
    code = harness.main(
        [
            "--selftest",
            "--variant",
            variant,
            "--topics",
            "probability",
            "--allow-partial",
            "--out",
            str(out),
        ],
        model_factory=lambda: scripted.make(variant),
    )
    text = out.read_text()
    assert code == 1, f"{variant} was not caught:\n{text[:600]}"
    assert "Verdict: PASS" not in text


def test_the_red_team_variants_do_not_all_fail_for_the_same_reason(harness, scripted, tmp_path):
    """Each variant has to trip *its own* check, or the suite is testing one thing nine times."""
    expected = {
        "leaky": "answer_leak",
        "devwords": "developer_text",
        "placeholder": "placeholder",
        "key_mismatch": "key_mismatch",
        "wrong_language": "language",
        "unverified": "passed verification",
        "bare_judge": "quote",
        "keypaste": "credential_in_copy",
    }
    for variant, marker in expected.items():
        out = tmp_path / f"{variant}-why.md"
        harness.main(
            ["--selftest", "--topics", "probability", "--allow-partial", "--out", str(out)],
            model_factory=lambda v=variant: scripted.make(v),
        )
        assert marker in out.read_text(), f"{variant} did not trip {marker!r}"


# ----------------------------------------------------------- the model the harness uses ----- #


def test_the_harness_scrubs_the_credential_out_of_what_it_writes(harness, monkeypatch):
    canary = "YTCANARY-LIVE-CHECK-7c41"
    monkeypatch.setenv("ADAPT_GEMINI_API_KEY", canary)
    settings = harness._load_settings()
    assert settings is not None, "a key in the environment is a configured key"
    text = harness._scrub(f"the run used {canary} and mail sam@example.test", settings, redact_identity=True)
    assert canary not in text
    assert "sam@example.test" not in text


def test_a_replay_fixture_answers_by_content_not_by_call_order(scripted):
    key = scripted.prompt_key
    records = [
        {
            "purpose": "lesson",
            "prompt_key": key("lesson", "system", "the first request"),
            "refused": False,
            "reply": {"which": "first"},
        },
        {
            "purpose": "lesson",
            "prompt_key": key("lesson", "system", "the second request"),
            "refused": False,
            "reply": {"which": "second"},
        },
    ]
    model = scripted.ReplayModel(records)
    assert model("lesson", "system", "the second request", {}, 10) == {"which": "second"}
    assert model("lesson", "system", "the first request", {}, 10) == {"which": "first"}

    # a fixture written before keys existed still replays, in recorded order, by purpose
    old = [dict(record) for record in records]
    for record in old:
        record.pop("prompt_key")
    model = scripted.ReplayModel(old)
    assert model("lesson", "system", "the first request", {}, 10) == {"which": "first"}
    assert model("lesson", "system", "the second request", {}, 10) == {"which": "second"}


def test_a_run_with_a_fake_can_save_fixtures_and_replay_them_without_a_key(
    harness, scripted, tmp_path, monkeypatch, capsys
):
    canary = "YTCANARY-LIVE-CHECK-7c41"
    monkeypatch.setenv("ADAPT_GEMINI_API_KEY", canary)
    fixtures = tmp_path / "fixtures"
    report = tmp_path / "report.md"
    code = harness.main(
        [
            "--topics",
            "probability",
            "--allow-partial",
            "--save-fixtures",
            "--fixture-dir",
            str(fixtures),
            "--out",
            str(report),
        ],
        model_factory=lambda: scripted.make("good"),
    )
    assert code == 0, report.read_text()[:600]
    saved = fixtures / "probability.json"
    assert saved.exists(), "a run that passed --save-fixtures has to leave a fixture behind"
    text = saved.read_text()
    assert canary not in text and canary not in report.read_text()
    assert canary not in capsys.readouterr().out

    payload = json.loads(text)
    assert payload["model"] == "scripted-fake", "a fixture has to say what produced it"
    purposes = [record["purpose"] for record in payload["responses"]]
    for purpose in ("skeleton", "items", "verify", "lesson", "explain_wrong", "reteach", "judge"):
        assert purpose in purposes, purpose
    assert payload["packs"]["en"]["source"] == "model"
    assert payload["profiles"][0]["language"] == "en"

    # the point of a fixture: a later run can assert against the same replies with no key at all
    monkeypatch.delenv("ADAPT_GEMINI_API_KEY", raising=False)
    replay_report = tmp_path / "replay.md"
    replay_code = harness.main(
        ["--selftest", "--topics", "probability", "--allow-partial", "--out", str(replay_report)],
        model_factory=lambda: scripted.load_fixture(saved),
    )
    assert replay_code == 0, replay_report.read_text()[:600]


def test_a_run_that_fails_still_does_not_print_the_credential(harness, tmp_path, monkeypatch, capsys):
    canary = "YTCANARY-LIVE-CHECK-7c41"
    monkeypatch.setenv("ADAPT_GEMINI_API_KEY", canary)
    out = tmp_path / "failing.md"
    code = harness.main(
        ["--selftest", "--variant", "devwords", "--topics", "probability", "--allow-partial", "--out", str(out)]
    )
    assert code == 1, out.read_text()[:400]
    text = out.read_text()
    assert "developer_text" in text, "the failing run has to say what it caught"
    assert canary not in text
    assert canary not in capsys.readouterr().out


def test_a_model_that_echoes_the_credential_does_not_get_it_into_anything_this_run_writes(
    harness, scripted, tmp_path, monkeypatch, capsys
):
    """The model can put the key in its own reply; the report, the fixture and stdout may not."""
    canary = "YTCANARY-LIVE-CHECK-7c41"
    monkeypatch.setenv("ADAPT_GEMINI_API_KEY", canary)
    base = scripted.make("good")

    def echoing(purpose, system, user, schema, max_tokens):
        reply = base(purpose, system, user, schema, max_tokens)
        if purpose == "lesson" and reply.get("blocks"):
            first = dict(reply["blocks"][0])
            first["body"] = f"Config note: api_key: {canary}. " + str(first.get("body") or "")
            reply = {**reply, "blocks": [first, *reply["blocks"][1:]]}
        return reply

    fixtures = tmp_path / "fixtures"
    out = tmp_path / "echoed.md"
    code = harness.main(
        [
            "--selftest",
            "--topics",
            "probability",
            "--allow-partial",
            "--save-fixtures",
            "--fixture-dir",
            str(fixtures),
            "--out",
            str(out),
        ],
        model_factory=lambda: echoing,
    )
    assert code == 1, "a lesson that pastes a credential in is not a passing run"
    assert "credential_in_copy" in out.read_text(), "and the report has to name what it caught"
    assert canary not in out.read_text(), "the report quoted a credential back"
    fixture = (fixtures / "probability.json").read_text()
    assert canary not in fixture, "the fixture kept the credential"
    assert canary not in capsys.readouterr().out, "stdout carried the credential"


def test_the_scripted_fake_says_so_in_the_report_it_produces(harness, tmp_path):
    out = tmp_path / "fake.md"
    harness.main(["--selftest", "--topics", "probability", "--allow-partial", "--out", str(out)])
    assert "scripted fake" in out.read_text()


def test_asking_for_the_real_pipeline_without_it_merged_refuses_like_other_bad_usage(
    harness, tmp_path, monkeypatch, capsys
):
    if importlib.util.find_spec("adapt.content.skeleton") and importlib.util.find_spec(
        "adapt.ai.lesson_writer"
    ):
        pytest.skip("Phases B and C are merged in this checkout: --pipeline real has something to run")
    monkeypatch.setenv("ADAPT_GEMINI_API_KEY", "YTCANARY-LIVE-CHECK-7c41")
    out = tmp_path / "real.md"
    code = harness.main(["--pipeline", "real", "--out", str(out)])
    assert code == 2, "asking for a pipeline that is not here is a refusal, not a crash"
    err = capsys.readouterr().err
    assert "refused" in err, err
    # the operator is told which pipeline was wanted, not just that something was missing
    assert "adapt.content.skeleton" in err and "adapt.ai.lesson_writer" in err, err
    assert not out.exists(), "a refused run writes nothing"


# --------------------------------------------------- the bridge to adapt.ai.llm (frozen) ----- #

def _stub_client(monkeypatch, results, seen):
    """Stand in for the frozen async ``GeminiLLM``: one reply per call, no network."""
    from adapt.ai.llm import LLMResult

    class _Client:
        def __init__(self, settings=None, *, limiter=None) -> None:
            self.settings = settings
            self.closed = False
            self.replies = list(results)

        async def complete(self, **kwargs) -> LLMResult:
            seen.append(kwargs)
            return self.replies.pop(0) if self.replies else LLMResult(ok=False, status="error")

        async def aclose(self) -> None:
            self.closed = True

    import adapt.ai.llm as llm

    monkeypatch.setattr(llm, "GeminiLLM", _Client)
    return _Client


def test_a_configured_key_drives_the_whole_live_branch_end_to_end(harness, scripted, tmp_path, monkeypatch, capsys):
    """Everything except the network: `main()` with a key and no injected factory.

    This is the branch a real run takes, and the only thing faked is the HTTP client underneath
    the frozen async wrapper -- so the harness's own settings load, its bridge, its fixtures
    labelled as a live run, and its report are all exercised together.
    """
    from adapt.ai.llm import LLMResult

    canary = "YTCANARY-LIVE-CHECK-7c41"
    monkeypatch.setenv("ADAPT_GEMINI_API_KEY", canary)
    brain = scripted.make("good")
    seen: list[str] = []

    class _Client:
        def __init__(self, settings=None, *, limiter=None) -> None:
            self.settings = settings

        async def complete(self, **kwargs) -> LLMResult:
            seen.append(str(kwargs.get("purpose")))
            reply = brain(kwargs["purpose"], kwargs["system"], kwargs["user"], kwargs.get("response_spec") or {}, kwargs["max_output_tokens"])
            return LLMResult(ok=True, text=json.dumps(reply), data=reply)

        async def aclose(self) -> None:
            return None

    import adapt.ai.llm as llm

    monkeypatch.setattr(llm, "GeminiLLM", _Client)

    fixtures = tmp_path / "fixtures"
    out = tmp_path / "live.md"
    code = harness.main(
        [
            "--topics",
            "probability",
            "--allow-partial",
            "--save-fixtures",
            "--fixture-dir",
            str(fixtures),
            "--out",
            str(out),
            "--quiet",
        ]
    )
    assert code == 0, out.read_text()[:600]
    text = out.read_text()
    # a configured key is what makes this a live run, and the report says so
    assert "**Live run.**" in text and "scripted fake answered" not in text
    # ...while the pipeline label stays honest about Phases B and C not being merged
    assert "local contract stub" in text
    assert canary not in text and canary not in capsys.readouterr().out

    payload = json.loads((fixtures / "probability.json").read_text())
    assert payload["model"] == "live"
    assert payload["packs"]["en"]["source"] == "model"
    assert set(payload["checks"][0]) == {"name", "ok", "detail", "kind"}

    purposes = set(seen)
    for purpose in ("skeleton", "items", "lesson", "explain_wrong", "reteach", "judge"):
        assert purpose in purposes, f"the live branch never called {purpose}"


def test_the_live_model_bridges_the_frozen_async_client(harness, monkeypatch):
    """The one path a keyless sandbox never runs: the sync-over-async call into ai/llm.py."""
    from adapt.ai.llm import LLMResult

    _stub_client(monkeypatch, [LLMResult(ok=True, text='{"a": 1}', data={"a": 1}), LLMResult(ok=True, data={"b": 2})], seen := [])

    model = harness.LiveModel(object())  # the client is stubbed, so the settings are never read
    try:
        assert model("skeleton", "system", "user", {"a": "int"}, 300) == {"a": 1}
        assert model.last_raw_text == '{"a": 1}'
        # a second call on the same loop: the loop is kept open for the whole run on purpose
        assert model("items", "system", "user", {}, 512) == {"b": 2}
    finally:
        model.close()

    assert len(seen) == 2, "both calls went through the frozen client"
    first = seen[0]
    assert first["system"] == "system" and first["user"] == "user"
    assert first["purpose"] == "skeleton" and first["role"] == "ai2"
    assert first["temperature"] == 0.2, "the harness runs the pipeline at a low temperature"
    assert first["max_output_tokens"] == 300 and first["response_spec"] == {"a": "int"}
    assert seen[1]["max_output_tokens"] == 512
    assert model.llm.closed, "close() has to shut the client down"
    assert model.loop.is_closed(), "and the loop it opened"


@pytest.mark.parametrize("status", ["quota", "disabled", "throttled"])
def test_the_live_model_turns_a_refusal_into_a_named_stop(harness, monkeypatch, status):
    from adapt.ai.llm import LLMResult

    _stub_client(monkeypatch, [LLMResult(ok=False, status=status, error="not this time")], [])
    model = harness.LiveModel(object())
    try:
        with pytest.raises(harness.ModelUnavailable) as caught:
            model("skeleton", "system", "user", {}, 200)
        assert caught.value.status == status
    finally:
        model.close()


def test_the_live_model_treats_a_reply_that_is_not_an_object_as_unparsed(harness, monkeypatch):
    from adapt.ai.llm import LLMResult

    _stub_client(monkeypatch, [LLMResult(ok=True, text="not json at all", data=None)], [])
    model = harness.LiveModel(object())
    try:
        with pytest.raises(harness.ModelUnavailable) as caught:
            model("skeleton", "system", "user", {}, 200)
        assert caught.value.status == "parse_error"
        assert model.last_raw_text == "not json at all", "the raw reply is kept for the fixtures"
    finally:
        model.close()


@pytest.mark.parametrize("status", ["quota", "disabled", "throttled"])
def test_a_run_the_daily_allowance_stops_is_incomplete_not_a_verdict(harness, tmp_path, status):
    def stopped():
        def call(purpose, system, user, schema, max_tokens):
            raise harness.ModelUnavailable(status, "refused by the configured allowance")

        return call

    out = tmp_path / f"{status}.md"
    code = harness.main(["--selftest", "--topics", "probability", "--out", str(out)], model_factory=stopped)
    assert code == 3, f"a run stopped by {status} has not earned a verdict"
    text = out.read_text()
    assert "Verdict: INCOMPLETE" in text and status in text


# ------------------------------------------------- phases B and C: dataclasses, not dicts ----- #

class _DataclassPipeline:
    """A Phase B/C stand-in that returns dataclasses, which is what the real modules may do.

    The frozen contracts name fields, not a container type, so the harness has to survive either.
    Both directions are asserted here: the harness reads the pack as data, and the writer gets
    back the very object `get_pack` made -- not a copy of it, and not a plain dict.
    """

    def __init__(self, inner, harness) -> None:
        self.inner = inner
        self.harness = harness
        self.packs_seen: list[Any] = []

    def get_pack(self, topic, *, level, language, model=None, force_simple=False):
        return _as_dataclass(self.inner.get_pack(topic, level=level, language=language, model=model))

    def write_lesson(self, pack, concept_id, snapshot, *, model=None):
        assert dataclasses.is_dataclass(pack), "the writer has to get the pack Phase B made"
        assert pack.concepts, "and read it the way Phase B wrote it"
        self.packs_seen.append(type(pack).__name__)
        return _as_dataclass(self.inner.write_lesson(dict(self.harness._wrap(pack)), concept_id, snapshot, model=model))

    def explain_after_wrong(self, pack, item_id, learner_answer, snapshot, memory, *, model=None):
        assert dataclasses.is_dataclass(pack)
        return _as_dataclass(
            self.inner.explain_after_wrong(
                dict(self.harness._wrap(pack)), item_id, learner_answer, snapshot, memory, model=model
            )
        )

    def reteach(self, pack, concept_id, snapshot, memory, *, model=None):
        assert dataclasses.is_dataclass(pack)
        return _as_dataclass(
            self.inner.reteach(dict(self.harness._wrap(pack)), concept_id, snapshot, memory, model=model)
        )


def _as_dataclass(value):
    """Every dict in a value, as a dataclass with the same fields (nested ones too)."""
    if isinstance(value, dict):
        fields = {str(k): _as_dataclass(v) for k, v in value.items() if str(k).isidentifier()}
        cls = dataclasses.make_dataclass("Node", [(name, object) for name in fields])
        return cls(**fields)
    if isinstance(value, (list, tuple)):
        return [_as_dataclass(item) for item in value]
    return value


def test_a_pipeline_that_returns_dataclasses_still_reads_and_still_serialises(
    harness, scripted, stub, tmp_path
):
    pipeline = _DataclassPipeline(stub, harness)
    calls: list[dict] = []
    entry = [e for e in harness.TOPICS if e["topic"] == "probability"][0]
    result = harness.run_topic(
        entry,
        harness.PROFILES,
        pipeline,
        lambda topic, profile: harness.recording_model(
            scripted.make("good"), calls, topic=topic, profile=profile, max_calls=400
        ),
    )

    # the harness read a dataclass-shaped pack as data...
    assert result["packs"]["en"]["source"] == "model"
    assert result["packs"]["en"]["items"][0]["verification"] in ("computed", "cross_checked", "unverified")
    assert result["packs"]["en"]["concepts"][0]["key_idea"]
    assert result["lessons"][0]["plan"]["blocks"][0]["body"]
    # ...and the writer got its own objects back, twice, once per profile
    assert len(pipeline.packs_seen) == 2, pipeline.packs_seen

    checks: list[dict] = []
    for part in (harness.check_topic(result), harness.check_lessons(result), harness.check_text(result), harness.check_moments(result)):
        checks += part[0]
    failed = [c["name"] for c in checks if not c["ok"]]
    assert not failed, failed

    # a run that ends in the fixture writer must not fall over on a dataclass shape
    saved = harness.save_fixtures([result], calls, tmp_path, None, live=False)
    assert saved == ["probability.json"], saved
    payload = json.loads((tmp_path / "probability.json").read_text())
    assert payload["packs"]["en"]["source"] == "model"
    assert payload["packs"]["en"]["items"][0]["stem"]
    assert payload["model"] == "scripted-fake"


# ------------------------------------------------------------------------ the judge rules ---- #


def test_a_judge_score_without_evidence_inside_the_artefact_is_refused(harness):
    without_quote = {"correctness": 5, "correctness_evidence": ""}
    scores, problems = harness.parse_judge(without_quote, "the teaching text")
    assert scores == {} and problems

    invented = {"correctness": 5, "correctness_evidence": "a sentence nobody ever wrote"}
    scores, problems = harness.parse_judge(invented, "the teaching text")
    assert "correctness" not in scores
    assert any("not in the artefact" in problem for problem in problems)

    # a reply that scores one criterion and forgets the other four is not a clean reply: the
    # silence is reported rather than scored as a pass
    silent = {"correctness": 5, "correctness_evidence": "the teaching text"}
    scores, problems = harness.parse_judge(silent, "the teaching text")
    assert scores["correctness"] == {"score": 5, "evidence": "the teaching text"}
    assert any("explains_why" in problem and "no score" in problem for problem in problems)

    # a full, fully-evidenced reply is accepted with nothing to report
    artefact = (
        "Flour and water make the dough. The yeast makes it rise, which is why it doubles. "
        "You said the salt was the yeast: the salt only adds flavour."
    )
    quotes = {
        "correctness": "Flour and water make the dough",
        "explains_why": "The yeast makes it rise, which is why",
        "uses_learner_answer": "You said the salt was the yeast",
        "fits_profile": "the salt only adds flavour",
        "no_leakage": "which is why it doubles",
    }
    good = {name: 5 for name in quotes}
    good.update({f"{name}_evidence": quote for name, quote in quotes.items()})
    scores, problems = harness.parse_judge(good, artefact)
    assert problems == [], problems
    assert scores["correctness"] == {"score": 5, "evidence": quotes["correctness"]}
    assert set(scores) == set(harness.JUDGE_CRITERIA)


def test_a_score_outside_the_scale_is_refused(harness):
    scores, problems = harness.parse_judge(
        {"no_leakage": 9, "no_leakage_evidence": "the teaching text"}, "the teaching text"
    )
    assert "no_leakage" not in scores
    assert any("outside 1-5" in problem for problem in problems)


def test_the_judge_prompt_separates_the_artefact_from_the_instructions(harness):
    result = {
        "topic": "probability",
        "packs": {},
        "lessons": [{"profile": harness.PROFILES[0], "plan": {"blocks": [{"type": "text", "body": "The idea is chance."}]}}],
        "moments": {},
    }
    prompt = harness.judge_prompt(result)
    assert harness.ARTEFACT_MARK in prompt and harness.END_ARTEFACT_MARK in prompt
    inside = prompt.split(harness.ARTEFACT_MARK)[1].split(harness.END_ARTEFACT_MARK)[0]
    assert "The idea is chance." in inside
    assert harness.ARTEFACT_MARK in harness.judge_prompt(result)
    assert harness.artefact_text(result).strip() == "The idea is chance."


# ------------------------------------------------------------------- the double is honest ---- #


def test_the_stub_says_when_it_has_nothing_rather_than_inventing_a_topic(stub):
    pack = stub.get_pack("a topic with no model available", level=3, language="en")
    assert pack["source"] == "simple"
    assert pack["items"] == []
    assert pack["concepts"][0]["key_idea"], "even a fallback has to say something"
    assert "a topic with no model available" in pack["concepts"][0]["key_idea"]


def test_the_stub_returns_the_frozen_contract_shapes(harness, stub, scripted):
    model = scripted.make("good")
    pack = stub.get_pack("probability", level=3, language="en", model=model)
    assert pack["source"] == "model"
    assert {"topic_key", "topic_title", "subject_family", "source", "concepts", "prereq_edges", "items"} <= set(pack)
    concept = pack["concepts"][0]
    assert {"id", "name", "key_idea", "why_it_works", "prerequisites", "rung", "key_facts", "misconceptions"} <= set(
        concept
    )
    item = pack["items"][0]
    assert {"id", "concept_id", "kind", "stem", "options", "key", "hints", "teachback", "fact_ids", "verification"} <= set(
        item
    )
    assert item["verification"] in ("computed", "cross_checked", "unverified")

    snapshot = harness.profile_snapshot(harness.PROFILES[0])
    plan = stub.write_lesson(pack, concept["id"], snapshot, model=model)
    assert plan["source"] in ("written_for_you", "simple")
    assert {"source", "concept_id", "blocks"} <= set(plan)
    for block in plan["blocks"]:
        assert {"type", "body", "fact_ids", "why"} <= set(block)

    explanation = stub.explain_after_wrong(pack, item["id"], "a wrong answer", snapshot, {"methods": []}, model=model)
    assert {"blocks", "method", "analogy_id", "source"} <= set(explanation)
    first = stub.reteach(pack, concept["id"], snapshot, {"methods": []}, model=model)
    second = stub.reteach(pack, concept["id"], snapshot, {"methods": [first["method"]]}, model=model)
    assert first["method"] != second["method"], "'I don't get it' has to be a different explanation"
