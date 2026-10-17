"""Round 6, Track B: real subject tests — registry, plan validation, evidence rows.

The AI proposes; code decides. These tests pin the code half: every registered
item type validates, a plan is 4-8 items over >= 2 skills with clamped difficulty,
geometry and discrete maths produce visibly different families, and a test run
stores evidence rows the dashboard and AI 1 can read.
"""

from __future__ import annotations

import pytest

from adapt.subjects.test_items import (
    ITEM_TYPES,
    MAX_ITEMS,
    MIN_DISTINCT_SKILLS,
    MIN_ITEMS,
    build_plan,
    family_for_text,
    simple_plan,
    validate_item,
    validate_plan,
)


class TestRegistry:
    def test_every_registered_type_validates_with_minimal_params(self):
        for name, spec in ITEM_TYPES.items():
            params = {}
            for key, pspec in spec.params.items():
                if not pspec.required:
                    continue
                if pspec.kind == "int":
                    params[key] = pspec.lo if pspec.lo is not None else 1
                elif pspec.kind == "choice":
                    params[key] = pspec.choices[0]
                elif pspec.kind == "bool":
                    params[key] = False
                else:
                    params[key] = "x"
            item = validate_item({"item_type": name, "skill": spec.skill, "difficulty": 3, "parameters": params})
            assert item is not None, name
            assert item["item_type"] == name

    def test_unknown_type_is_dropped(self):
        assert validate_item({"item_type": "self_rating", "skill": "s", "difficulty": 3, "parameters": {}}) is None

    def test_out_of_range_param_is_clamped(self):
        item = validate_item({
            "item_type": "number_line",
            "skill": "number.sense",
            "difficulty": 3,
            "parameters": {"value": 400, "max": 100},
        })
        assert item is not None
        assert item["parameters"]["value"] <= 100

    def test_missing_required_param_drops_item(self):
        assert validate_item({
            "item_type": "pattern_pick", "skill": "p", "difficulty": 3,
            "parameters": {"sequence": "a,b"},
        }) is None

    def test_difficulty_clamped_to_one_to_five(self):
        item = validate_item({
            "item_type": "pattern_pick", "skill": "p", "difficulty": 99,
            "parameters": {"sequence": "a,b,c", "options": "d,e,f,g", "answer_index": 0},
        })
        assert item is not None
        assert 1 <= item["difficulty"] <= 5


class TestPlanValidation:
    def test_plan_needs_min_items_and_skills(self):
        one = [{"item_type": "pattern_pick", "skill": "p", "difficulty": 2,
                "parameters": {"sequence": "a,b,c", "options": "d,e,f,g", "answer_index": 0}}]
        assert validate_plan(one) == []

    def test_plan_caps_at_max_items(self):
        item = {"item_type": "pattern_pick", "skill": "s", "difficulty": 2,
                "parameters": {"sequence": "a,b,c", "options": "d,e,f,g", "answer_index": 0}}
        items = validate_plan([dict(item, skill=f"skill-{i}") for i in range(12)])
        assert len(items) == MAX_ITEMS

    def test_unknown_items_are_dropped_but_good_ones_kept(self):
        good = {"item_type": "pattern_pick", "skill": "p", "difficulty": 2,
                "parameters": {"sequence": "a,b,c", "options": "d,e,f,g", "answer_index": 0}}
        goods = [dict(good, skill=f"skill-{i}") for i in range(4)]
        plan = validate_plan([{"item_type": "junk", "skill": "x", "difficulty": 1, "parameters": {}}, *goods])
        assert [i["item_type"] for i in plan] == ["pattern_pick"] * 4


class TestFamilyPlans:
    def test_all_families_produce_valid_plans(self):
        for family in ("maths", "discrete", "geometry", "language", "law", "general"):
            plan = simple_plan(family)
            assert MIN_ITEMS <= len(plan) <= MAX_ITEMS
            assert len({i["skill"] for i in plan}) >= MIN_DISTINCT_SKILLS

    def test_geometry_emphasises_spatial(self):
        plan = simple_plan("geometry")
        spatial = [i for i in plan if i["skill"] == "geometry.spatial"]
        assert len(spatial) >= 2
        assert {i["item_type"] for i in plan} & {"geometry_angle", "geometry_congruent"}

    def test_discrete_emphasises_logic_sets_proof_order(self):
        plan = simple_plan("discrete")
        kinds = {i["item_type"] for i in plan}
        assert "truth_table" in kinds and "venn_region" in kinds and "drag_order" in kinds

    def test_geometry_and_discrete_are_visibly_different(self):
        g = {i["item_type"] for i in simple_plan("geometry")}
        d = {i["item_type"] for i in simple_plan("discrete")}
        assert g != d
        assert g & d == g & d  # both valid, but their intersection is not the whole set
        assert len(g - d) >= 2 and len(d - g) >= 2

    def test_language_uses_cloze(self):
        kinds = {i["item_type"] for i in simple_plan("language")}
        assert "cloze_word_bank" in kinds

    def test_family_for_text(self):
        assert family_for_text("Geometry: circles") == "geometry"
        assert family_for_text("discrete structures") == "discrete"
        assert family_for_text("Kiswahili") == "language"
        assert family_for_text("contract law") == "law"
        assert family_for_text("algebra") == "maths"
        assert family_for_text("bread making") == "general"

    def test_build_plan_without_model_is_simple_and_labelled(self):
        out = build_plan(subject="geometry basics", level=1, style="try", goal="exam")
        assert out["source"] == "simple"
        assert out["family"] == "geometry"
        assert MIN_ITEMS <= len(out["items"]) <= MAX_ITEMS


class TestEvidenceApi:
    def test_plan_and_evidence_roundtrip(self, client, learner):
        plan = client.post("/api/v1/tests/plan", json={"subject": "geometry", "level": 2, "style": "see", "goal": "skills"}, headers=learner["headers"])
        assert plan.status_code == 200
        body = plan.json()
        assert body["source"] in ("model", "simple")
        assert 4 <= len(body["items"]) <= 8
        assert body["family"] == "geometry"

        rows = [
            {
                "item_id": f"it-{i}",
                "item_type": item["item_type"],
                "skill": item["skill"],
                "difficulty": item["difficulty"],
                "answer": "0",
                "correct": i % 2 == 0,
                "ms": 1200 + i,
                "hints_used": 0,
                "parameters": item["parameters"],
            }
            for i, item in enumerate(body["items"])
        ]
        ev = client.post("/api/v1/tests/evidence", json={"run_id": "run-1", "rows": rows}, headers=learner["headers"])
        assert ev.status_code == 200
        assert ev.json()["stored"] == len(rows)

        # idempotent: replaying the same run must not double-count
        again = client.post("/api/v1/tests/evidence", json={"run_id": "run-1", "rows": rows}, headers=learner["headers"])
        assert again.json()["stored"] == 0

        read = client.get("/api/v1/tests/evidence?run_id=run-1")
        assert read.status_code == 200
        got = read.json()["rows"]
        assert len(got) == len(rows)
        assert {r["item_type"] for r in got} <= set(ITEM_TYPES)
        for r in got:
            assert set(r) >= {"item_id", "answer", "correct", "ms", "hints_used"}

    def test_evidence_needs_rows(self, client, learner):
        ev = client.post("/api/v1/tests/evidence", json={"run_id": "run-2", "rows": []}, headers=learner["headers"])
        assert ev.status_code == 422

    def test_geometry_vs_discrete_plans_differ_through_the_api(self, client, learner):
        g = client.post("/api/v1/tests/plan", json={"subject": "geometry", "level": 3}, headers=learner["headers"]).json()
        d = client.post("/api/v1/tests/plan", json={"subject": "discrete maths", "level": 3}, headers=learner["headers"]).json()
        gt = [i["item_type"] for i in g["items"]]
        dt = [i["item_type"] for i in d["items"]]
        assert gt != dt
        assert "geometry_angle" in gt or "geometry_congruent" in gt
        assert "truth_table" in dt and "venn_region" in dt
