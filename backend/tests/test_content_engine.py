"""Tests for the phase 4 content engine (§7) — the half that never had a landing.

These tests are the gate the brief asks for in code form: §7.3 says validation is "mandatory before any
learner sees an item", and the only way that is true is if a regression fails the build. The same
walks run in ``scripts/teaching_quality_eval.py``, which loads this module's engine rather than a copy
of it, so the report and the suite cannot drift apart.

The rules under test are the brief's, and where a test encodes one it names the section. Where a
*defect* was found while building the engine, the test for it is named after the defect rather than
left as a silent assertion — those are the ones worth reading.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from adapt.content import symbolic as S
from adapt.content.breakdown import BREAKDOWN_SPEC, breakdown_for, concepts_for
from adapt.content.pack import PACK_SPEC, build_concept_pack, build_pack
from adapt.content.pipeline import PIPELINE_SPEC, build_and_store, generate
from adapt.content.validation import GATE_CODES, validate_for_bank, validate_item, validate_pack

REPO = Path(__file__).resolve().parents[2]

#: Every topic §6.3 asks for authored ratings for. A topic missing from here is a topic the engine
#: has never been run against, which is the failure this list exists to prevent.
BRIEF_TOPICS = (
    "calculus",
    "algebra",
    "statistics",
    "government",
    "biology",
    "chemistry",
    "world_history",
    "grammar",
    "essay_writing",
    "discrete_maths",
    "kiswahili",
)


# --------------------------------------------------------------------------- #
# §7.3: the symbolic checker
# --------------------------------------------------------------------------- #


class TestSymbolicChecker:
    def test_expands_and_recognises_equivalence(self):
        verdict, how = S.equivalent("2(x + 3)", "2x + 6")
        assert verdict is True
        assert "x" in how

    def test_reports_a_counterexample_it_can_be_checked_against(self):
        verdict, how = S.equivalent("x + 1", "x + 2")
        assert verdict is False
        assert "differ at x =" in how  # the point a human can substitute by hand

    def test_exact_arithmetic_not_binary_floats(self):
        # The school reading: 0.1 + 0.2 is 0.3. A float comparison would call this unequal.
        assert S.evaluate("0.1 + 0.2", {}) == S.evaluate("0.3", {})

    def test_fractions_stay_exact(self):
        assert S.evaluate("1/3 + 1/4", {}) == S.Fraction(7, 12)

    def test_implicit_multiplication_parses(self):
        assert S.evaluate("3(x + 1)", {"x": 1}) == 6
        assert S.evaluate("2x + 1", {"x": 3}) == 7

    def test_differentiation_power_and_quotient_rules(self):
        assert S.differentiate("x^3") == "3*x^2"
        assert S.differentiate("3x^2 + 2x - 5") == "6*x + 2"
        assert S.equivalent(S.differentiate("1/x"), "-1/x^2")[0] is True

    def test_solves_linear_and_quadratic_exactly(self):
        roots, _ = S.solve("x + 3 = 7")
        assert roots == [S.Fraction(4)]
        roots, status = S.solve("x^2 - 5x + 6 = 0")
        assert sorted(roots) == [S.Fraction(2), S.Fraction(3)]
        assert "discriminant" in status

    def test_refuses_an_irrational_root_rather_than_rounding_one(self):
        roots, status = S.solve("x^2 - 2 = 0")
        assert roots == []
        assert "no rational solution" in status

    def test_refuses_functions_it_cannot_vouch_for(self):
        # The defect this names: the first version read `sin(x)` as `s*i*n*(x)` and reported the
        # answer correct. A checker that guesses is worse than one that refuses.
        for expression in ("sin(x)", "sqrt(x)", "log(x)"):
            status, how = S.check_answer(expression=expression, answer=1, equals="1")
            assert status == "symbolic_error", expression
            assert "outside what this checker verifies" in how

    def test_refuses_ambiguous_letter_pairs(self):
        status, how = S.check_answer(expression="xy", answer=1, equals="1")
        assert status == "symbolic_error"
        assert "two letters side by side" in how

    def test_agrees_with_a_correct_key_and_refuses_a_wrong_one(self):
        ok, how = S.check_answer(expression="7 * 8", answer=56)
        assert ok == "symbolic"
        assert how == "the expression is exactly 56"
        status, how = S.check_answer(expression="7 * 8", answer=54)
        assert status == "symbolic_mismatch"
        assert "56" in how and "54" in how

    def test_checks_an_equation_by_solving_it(self):
        status, how = S.check_answer(expression="x + 3 = 7", answer=4)
        assert status == "symbolic"
        assert "linear" in how

    def test_equivalence_where_both_sides_are_defined(self):
        # (x^2-1)/(x-1) and x+1 differ at x=1 only. The pack's claim is "same where defined".
        verdict, how = S.equivalent("(x^2 - 1)/(x - 1)", "x + 1")
        assert verdict is True
        assert "not defined in the same places" in how


# --------------------------------------------------------------------------- #
# §7.1: the concept breakdown
# --------------------------------------------------------------------------- #


class TestBreakdown:
    def test_every_brief_topic_has_a_breakdown(self):
        for key in BRIEF_TOPICS:
            breakdown = breakdown_for(key)
            assert breakdown.available, f"{key}: {breakdown.reason}"
            assert breakdown.concepts, key

    def test_concepts_carry_prerequisites_titles_and_misconceptions(self):
        breakdown = breakdown_for("calculus")
        for concept in breakdown.concepts:
            assert concept.description
            assert concept.misconceptions, f"{concept.id} has no near-miss to teach against"
            assert concept.objectives, f"{concept.id} has no learning objective"
            for prereq in concept.prerequisite_titles:
                assert prereq

    def test_prerequisites_come_before_the_concepts_that_need_them(self):
        breakdown = breakdown_for("algebra")
        seen: set[str] = set()
        for concept in breakdown.concepts:
            for prereq in concept.prerequisites:
                assert prereq in seen, f"{concept.id} is taught before its prerequisite {prereq}"
            seen.add(concept.id)

    def test_objectives_quote_the_briefs_own_rung_labels(self):
        breakdown = breakdown_for("biology")
        demands = {o["demand"] for c in breakdown.concepts for o in c.objectives}
        assert "produce the key fact without a prompt" in demands  # ladder.RUNGS rung 2, quoted

    def test_a_topic_with_no_chain_says_so_instead_of_inventing_one(self):
        breakdown = breakdown_for("astrophysics")
        assert breakdown.available is False
        assert "no authored prerequisite chain" in breakdown.reason
        assert breakdown.concepts == ()

    def test_an_alias_resolves_to_the_authored_topic(self):
        assert breakdown_for("dy/dx").available is True
        assert breakdown_for("differentiation").topic_key == "calculus"

    def test_the_spec_is_recorded_so_a_cache_can_tell_a_spec_change_from_a_content_change(self):
        assert BREAKDOWN_SPEC
        assert "breakdown" in BREAKDOWN_SPEC


# --------------------------------------------------------------------------- #
# §7.2: the three variants, the pair, the hints, the checklist
# --------------------------------------------------------------------------- #


class TestConceptPack:
    def test_every_concept_of_every_brief_topic_gets_a_pack(self):
        for key in BRIEF_TOPICS:
            packs = build_pack(breakdown_for(key))
            assert packs, key
            for pack in packs:
                payload = pack.as_dict()
                assert payload["available"], f"{key}/{pack.concept_id}"
                assert payload["variants"]["written"]["text"], "no written variant"
                assert payload["variants"]["visual"]["kind"], "no visual spec"
                assert payload["variants"]["worked_example"]["steps"], "no worked example"
                assert payload["pair"]["A"] and payload["pair"]["B"], "no matched pair"
                assert len(payload["hints"]) >= 3, "fewer than three hints"
                assert len(payload["checklist"]) == 3, "the checklist is three points"

    def test_the_visual_spec_is_the_shape_the_phase_5_templates_render(self):
        pack = build_concept_pack(breakdown_for("calculus").concepts[0], topic_key="calculus", topic_title="Calculus")
        visual = pack.variants["visual"]
        assert visual["template"] is True
        assert visual["kind"] in ("flow", "comparison_table", "timeline", "labelled_diagram")
        assert visual["why"], "a picture with no stated reason"
        assert set(visual) >= {"nodes", "columns", "rows", "labels", "unfilled", "alt"}

    def test_the_pair_is_matched_on_format_and_says_it_is_not_matched_on_difficulty(self):
        pack = build_concept_pack(breakdown_for("biology").concepts[0], topic_key="biology", topic_title="Biology")
        pair = pack.pair
        assert pair["format"] == pair["A"]["kind"].replace("mcq", "choice")
        assert "NOT matched on difficulty" in pair["matched"]
        assert "expected_accuracy" in pair["expected_accuracy_note"]
        assert pair["A"]["rung"] == 3 and pair["B"]["rung"] == 4

    def test_hints_are_graded_and_none_is_the_generic_sentence(self):
        generic = (
            "write down the single fact the question is asking for",
            "what is being asked",
        )
        for key in BRIEF_TOPICS:
            for pack in build_pack(breakdown_for(key)):
                grades = [h["grade"] for h in pack.hints[:3]]
                assert grades == ["nudge", "method", "near_answer"], f"{key}/{pack.concept_id}: {grades}"
                for hint in pack.hints:
                    for sentence in generic:
                        assert sentence not in hint["text"].lower(), f"{key}/{pack.concept_id}: {hint['text']}"

    def test_the_worked_example_restates_the_question_rather_than_quoting_the_stem(self):
        # The defect this names: quoting the stem put the question's own data — and for "the median of
        # 5, 7, 7, 9, 12", the answer itself — into the example's first step, which the example gate
        # correctly read as an answer leak.
        pack = build_concept_pack(breakdown_for("statistics").concepts[0], topic_key="statistics", topic_title="Statistics")
        steps = pack.variants["worked_example"]["steps"]
        assert pack.variants["worked_example"]["verified"] is True
        assert "The question is:" not in steps[0]["text"]
        assert "in your own words" in steps[0]["text"]

    def test_every_worked_example_passes_the_banks_own_example_gate(self):
        for key in BRIEF_TOPICS:
            for pack in build_pack(breakdown_for(key)):
                example = pack.variants["worked_example"]
                assert example["verified"] is True, f"{key}/{pack.concept_id}: {example['flags']}"

    def test_misconceptions_quote_the_authored_near_miss(self):
        pack = build_concept_pack(breakdown_for("grammar").concepts[0], topic_key="grammar", topic_title="Grammar")
        for misconception in pack.misconceptions:
            assert misconception["claim"]
            assert "rung-1 near-miss" in misconception["source"]


# --------------------------------------------------------------------------- #
# §7.3: the mandatory validation
# --------------------------------------------------------------------------- #


class TestValidation:
    def test_each_named_rejection_blocks_the_item(self):
        cases = {
            "placeholder": {"kind": "mcq", "stem": "Work out {ans}.", "choices": [{"text": "1", "correct": True}], "answer_spec": {"correct_index": 0}},
            "multiple_correct_options": {"kind": "mcq", "stem": "Which is even?", "choices": [{"text": "4", "correct": True}, {"text": "6", "correct": True}], "answer_spec": {"correct_index": 0}},
            "answer_leak_in_stem": {"kind": "mcq", "stem": "Mitochondria produce ATP, so which organelle makes energy?", "choices": [{"text": "mitochondria", "correct": True}, {"text": "ribosome", "correct": False}], "answer_spec": {"correct_index": 0}},
            "no_key": {"kind": "mcq", "stem": "Which is even?", "choices": [{"text": "4", "correct": False}, {"text": "6", "correct": False}]},
            "key_disagrees": {"kind": "numeric", "stem": "Work out 7 x 8.", "answer_spec": {"value": 54, "check_expr": "7*8"}},
            "difficulty_far_from_band": {"kind": "mcq", "stem": "Which is even?", "choices": [{"text": "4", "correct": True}, {"text": "5", "correct": False}], "answer_spec": {"correct_index": 0}, "difficulty": 0.2},
        }
        for code, item in cases.items():
            verdict = validate_item(item)
            assert code in [r.code for r in verdict.rejections], code
            assert verdict.ok is False, code
            assert code in GATE_CODES, code

    def test_every_rejection_carries_a_reason(self):
        verdict = validate_item({"kind": "mcq", "stem": "Work out {ans}.", "choices": [{"text": "1", "correct": True}], "answer_spec": {"correct_index": 0}})
        for rejection in verdict.rejections:
            assert rejection.detail.strip(), rejection.code

    def test_a_stem_that_quotes_the_material_is_not_an_answer_leak(self):
        # The defect this names: "in the sentence 'Wanafunzi wazuri wanasoma kwa bidii', which word is
        # the adjective?" contains its own answer by construction, and a rule that rejected it would
        # reject every "find the part" question in the app.
        item = {
            "kind": "mcq",
            "stem": "In the sentence 'Wanafunzi wazuri wanasoma kwa bidii', which word is the adjective?",
            "choices": [{"text": "wazuri", "correct": True}, {"text": "wanasoma", "correct": False}],
            "answer_spec": {"correct_index": 0},
        }
        assert validate_item(item).ok is True

    def test_set_notation_is_not_an_unrendered_placeholder(self):
        # The defect this names: worked.PLACEHOLDER read `{3}` as a missing variable, so every set-theory
        # worked example failed its own gate.
        item = {
            "kind": "mcq",
            "stem": "A = {1, 2, 3} and B = {3, 4}. What is the intersection?",
            "choices": [{"text": "{3}", "correct": True}, {"text": "{1}", "correct": False}],
            "answer_spec": {"correct_index": 0},
        }
        assert validate_item(item).ok is True

    def test_the_two_ways_of_naming_a_key_must_agree(self):
        item = {
            "kind": "mcq",
            "stem": "Which is even?",
            "choices": [{"text": "4", "correct": False}, {"text": "6", "correct": True}],
            "answer_spec": {"correct_index": 0},
        }
        assert "multiple_correct_options" in [r.code for r in validate_item(item).rejections]

    def test_a_scaffolded_item_below_the_difficulty_floor_is_allowed(self):
        # §4.1 item 7's own carve-out: nothing under 45% expected success unless a worked example is shown.
        item = {
            "kind": "mcq",
            "stem": "Which is even?",
            "choices": [{"text": "4", "correct": True}, {"text": "5", "correct": False}],
            "answer_spec": {"correct_index": 0},
            "difficulty": 0.2,
            "solution_steps": [{"text": "An even number divides by 2 exactly."}],
        }
        verdict = validate_item(item)
        assert verdict.ok is True
        assert any("worked example is shown" in n for n in verdict.notes)

    def test_an_unverified_key_is_shown_but_never_scored(self):
        # §7.3's own wording: "reject or flag 'unverified' and never use it for scoring".
        item = {
            "kind": "mcq",
            "stem": "Who wrote the document?",
            "choices": [{"text": "the king", "correct": True}, {"text": "a merchant", "correct": False}],
            "answer_spec": {"correct_index": 0},
        }
        verdict = validate_item(item)
        assert verdict.ok is True
        assert verdict.scorable is False
        assert verdict.verification == "unverified"

    def test_a_maths_key_is_verified_by_computing(self):
        item = {
            "kind": "numeric",
            "stem": "A tank holds 40 litres and loses 3 litres a minute. How much after 6 minutes?",
            "answer_spec": {"value": 22, "check_expr": "40 - 3*6"},
        }
        verdict = validate_item(item)
        assert verdict.ok is True
        assert verdict.scorable is True
        assert verdict.verification in ("symbolic", "arithmetic")

    def test_the_bank_rules_are_opt_in_not_global(self):
        # §7.3's four rejections apply to every item; the bank-insertion rules (a stem already in the
        # bank, every distractor explained) apply only to a caller inserting a row.
        item = {
            "kind": "mcq",
            "stem": "Which is even?",
            "choices": [{"text": "4", "correct": True}, {"text": "5", "correct": False}],
            "answer_spec": {"correct_index": 0},
        }
        assert validate_item(item).ok is True
        assert validate_for_bank(item).ok is False  # no why_wrong on the distractor

    def test_a_pack_with_no_worked_example_is_not_ok(self):
        verdict = validate_pack({"variants": {"worked_example": {"steps": []}}, "pair": {}})
        assert verdict["ok"] is False
        assert any("no worked example" in n for n in verdict["notes"])


# --------------------------------------------------------------------------- #
# §7.1–§7.6: the pipeline
# --------------------------------------------------------------------------- #


class TestPipeline:
    def test_every_brief_topic_generates_validated_packs(self):
        for key in BRIEF_TOPICS:
            result = generate(key)
            assert result.available, f"{key}: {result.reason}"
            assert result.packs, key
            assert result.rejections == [], f"{key}: {result.rejections}"

    def test_coverage_counts_carry_n(self):
        coverage = generate("calculus").coverage
        assert coverage["concepts"] == coverage["worked_example"] == coverage["matched_pair"]
        assert coverage["three_hints"] == coverage["concepts"]
        assert coverage["teach_back_checklist"] == coverage["concepts"]
        assert coverage["visual_spec"] == coverage["concepts"]

    def test_a_topic_with_no_chain_returns_an_honest_empty_result(self):
        result = generate("astrophysics")
        assert result.available is False
        assert result.packs == []
        assert "no authored prerequisite chain" in result.reason

    def test_expected_performance_uses_the_learners_own_belief(self):
        result = generate("calculus", p_know=0.9)
        pack = result.packs[0]
        expected = pack.pair["expected"]
        assert expected["A"]["expected_accuracy"] > expected["B"]["expected_accuracy"]
        assert "no calibrated item difficulty" in expected["note"]

    def test_a_rejection_is_logged_with_its_reason_and_the_pack_is_not_served(self):
        # Built by hand rather than by editing the chain, so the test does not depend on authored
        # content changing: a pack whose pair has no key at all must be refused, with the reason.
        result = generate("calculus")
        pack = result.packs[0]
        payload = pack.as_dict()
        payload["pair"]["A"]["answer_spec"] = {"correct_index": 9}  # points outside its own choices
        verdict = validate_pack(payload)
        assert verdict["ok"] is False
        assert any("no_key" in n or "not one of the choices" in n for n in verdict["notes"])


class TestCache:
    def test_generating_twice_stores_once(self, tmp_path, monkeypatch):
        pytest.importorskip("sqlalchemy")
        monkeypatch.setenv("ADAPT_DB_URL", f"sqlite:///{tmp_path}/cache.db")
        from adapt.db import Database
        from adapt.state import LearnerState

        db = Database()
        db.create_all()
        state = LearnerState(db.sessionmaker)
        first = build_and_store(state, "calculus")
        assert first["stored"], "the first run must generate"
        assert first["cached"] == []
        second = build_and_store(state, "calculus")
        assert second["stored"] == [], "a second run must be a cache hit"
        assert len(second["cached"]) == len(first["stored"])
        for entry in second["cached"]:
            assert entry["validation"] == "validated"

    def test_a_changed_payload_is_a_new_pack_not_an_overwrite(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ADAPT_DB_URL", f"sqlite:///{tmp_path}/ids.db")
        from adapt.db import Database
        from adapt.state import LearnerState

        db = Database()
        db.create_all()
        state = LearnerState(db.sessionmaker)
        envelope = {"concept": "demo", "kind": "variant_set", "topic_id": None, "payload": {"a": 1}}
        first = state.content_id(envelope)
        second = state.content_id({**envelope, "payload": {"a": 2}})
        assert first != second, "the same content must give the same id and different content a new one"
        assert state.put_content(concept="demo", kind="variant_set", payload={"a": 1}) == first


class TestOrchestratorIntegration:
    def test_the_turn_cycle_finds_the_generator_and_the_validator(self):
        # The orchestrator looks these up by name (state/orchestrator.py step 3). If either is
        # renamed, content generation silently stops and the lesson falls back to "no pack".
        from adapt.ai.ai2 import EducationalIntelligence

        assert callable(getattr(EducationalIntelligence, "content_pack", None))
        assert callable(getattr(EducationalIntelligence, "validate_pack", None))

    def test_ai2_generates_a_pack_without_a_database(self):
        from adapt.ai.ai2 import EducationalIntelligence

        class FakeAI1:
            def profile(self, session, student_id):
                return {}

        ai2 = EducationalIntelligence(None, ai1=FakeAI1())
        made = ai2.content_pack(concept="calc_rate", topic_id="calculus")
        assert made["payload"]["available"] is True
        assert made["payload"]["variants"]["worked_example"]["steps"]
        assert ai2.validate_pack(made["payload"])["ok"] is True

    def test_ai2_reports_a_topic_with_no_content_instead_of_inventing_it(self):
        from adapt.ai.ai2 import EducationalIntelligence

        class FakeAI1:
            def profile(self, session, student_id):
                return {}

        ai2 = EducationalIntelligence(None, ai1=FakeAI1())
        made = ai2.content_pack(concept="anything", topic_id="astrophysics")
        assert made["payload"]["available"] is False
        assert "no authored prerequisite chain" in made["payload"]["reason"]

    def test_the_spec_is_recorded_on_every_pack(self):
        assert PIPELINE_SPEC == f"{BREAKDOWN_SPEC}+{PACK_SPEC}"
        payload = generate("calculus").packs[0].as_dict()
        assert payload["spec"] == PACK_SPEC


# --------------------------------------------------------------------------- #
# the gate and the suite share one implementation
# --------------------------------------------------------------------------- #


def _scratch_report() -> Path:
    """A scratch path for the gate's output, so a test never writes the checked-in report."""
    import tempfile

    return Path(tempfile.mkdtemp(prefix="adapt-gate-")) / "QUALITY_REPORT.md"


def _load_eval_script():
    path = REPO / "scripts" / "teaching_quality_eval.py"
    spec = importlib.util.spec_from_file_location("teaching_quality_eval", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class TestSharedGate:
    def test_the_gate_runs_the_same_engine_the_tests_do(self, monkeypatch):
        script = _load_eval_script()
        assert hasattr(script, "main")
        # The gate's argparse reads sys.argv, which belongs to pytest while this runs. Hand it a clean
        # one and a scratch output path, so the test never overwrites the checked-in report.
        monkeypatch.setattr(sys, "argv", ["teaching_quality_eval.py", "--out", str(_scratch_report())])
        result = script.main()
        assert result == 0

    def test_the_gate_reports_zero_flags_on_the_deterministic_path(self):
        script = _load_eval_script()
        report = script.build_report() if hasattr(script, "build_report") else None
        if report is None:  # pragma: no cover - the script's own shape changed
            pytest.skip("the gate script does not expose build_report")
        flags = report.get("gate_flags") or []
        assert flags == [], f"the deterministic path must carry zero gate flags, got {flags}"


# --------------------------------------------------------------------------- #
# the route (§7 over HTTP)
# --------------------------------------------------------------------------- #


class TestContentRoute:
    def test_a_breakdown_arrives_without_the_kits(self, client, learner):
        body = client.get("/api/v1/content/breakdown?topic=biology", headers=learner["headers"]).json()
        assert body["available"] is True
        assert body["concepts"]
        # The kit is the server's copy: it carries every rung's answer key.
        assert all("kit" not in c for c in body["concepts"])
        assert body["concepts"][0]["misconceptions"]

    def test_a_topic_with_no_chain_says_so_over_http(self, client, learner):
        response = client.get("/api/v1/content/breakdown?topic=astrophysics", headers=learner["headers"])
        assert response.status_code == 200
        body = response.json()
        assert body["available"] is False
        assert "no authored prerequisite chain" in body["reason"]

    def test_the_pack_arrives_with_its_key_stripped(self, client, learner):
        body = client.get(
            "/api/v1/content/pack?topic=calculus&concept=calc_rate", headers=learner["headers"]
        ).json()
        assert body["concept_id"] == "calc_rate"
        for leg in ("A", "B"):
            item = body["pair"][leg]
            # The defect this names: the bank shape's key is ``answer_spec``, which the repo's private
            # field list does not know about, so a bank-shaped item handed to it sent the answer.
            assert "answer_spec" not in item, f"leg {leg} still carries its key"
            assert "answer" not in item, f"leg {leg} still carries its key"
            assert "why" not in item, f"leg {leg} still carries its answer explanation"
            assert item["options"], f"leg {leg} has nothing to answer"
        assert body["variants"]["worked_example"]["steps"]
        assert len(body["hints"]) >= 3
        assert len(body["checklist"]) == 3
        assert body["stored"] is False, "a GET must never write"

    def test_two_learners_do_not_share_an_answer_key_by_position(self, client):
        # Two learners, created one after the other so each request runs under its own cookie and
        # its own CSRF header — the shuffle seed is (learner, concept), so the orders must differ
        # while the option *set* stays the same question.
        orders = []
        for name in ("Rowan One", "Rowan Two"):
            created = client.post(
                "/api/v1/students", json={"display_name": name, "age_band": "16-18", "as_guest": True}
            )
            assert created.status_code == 200, created.text
            headers = {"x-csrf-token": created.json()["csrf_token"]}
            orders.append(
                client.get(
                    "/api/v1/content/pack?topic=calculus&concept=calc_rate", headers=headers
                ).json()["pair"]["A"]["options"]
            )
        assert sorted(orders[0]) == sorted(orders[1]), "the same question, so the same options"
        assert orders[0] != orders[1], "an 'always pick the second one' habit must not score"

    def test_an_unknown_concept_names_the_ones_that_exist(self, client, learner):
        response = client.get(
            "/api/v1/content/pack?topic=calculus&concept=nope", headers=learner["headers"]
        )
        assert response.status_code == 404
        assert "calc_rate" in response.json()["detail"]

    def test_the_hint_ladder_climbs_and_then_stops(self, client, learner):
        seen = []
        for index in (0, 1, 2, 9):
            body = client.get(
                f"/api/v1/content/hint?topic=calculus&concept=calc_rate&index={index}",
                headers=learner["headers"],
            ).json()
            assert body["grade"] in ("nudge", "method", "near_answer")
            assert body["of"] == 3
            seen.append(body["grade"])
        assert seen == ["nudge", "method", "near_answer", "near_answer"]
        # The fourth pack entry is the answer's explanation; it must not be reachable as a hint.
        texts = [
            client.get(
                f"/api/v1/content/hint?topic=calculus&concept=calc_rate&index={i}",
                headers=learner["headers"],
            ).json()["text"]
            for i in (0, 1, 2)
        ]
        assert len(set(texts)) == 3

    def test_the_route_refuses_an_unsigned_request(self, client):
        # The CSRF middleware runs before the auth dependency, so an anonymous request is refused
        # with 403 rather than 401. Either code is a refusal; asserting the exact one here would be
        # asserting the middleware's order rather than the route's behaviour.
        assert client.get("/api/v1/content/breakdown?topic=calculus").status_code in (401, 403)
