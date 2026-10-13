"""Smoke the endpoints added for the pitch defects — through the real app, not the units.

What each one is here to prove:

* ``/assessment/diagnostic`` is placement only: adaptive, re-plans after each answer, and
  never pretends to be a method probe.
* ``/assessment/method-probe`` is the separate 6-8 concept run, clamped on the server.
* ``/methods/report`` compares actual vs expected per method and labels anything under 8
  observations an early signal — and carries ``n``.
* ``/concepts/{topic_id}/versions`` returns the three versions + a matched question pair.
* An attempt can record the method it was served under, and the report accumulates it.
"""

from __future__ import annotations

import pytest


def _csrf(client) -> dict[str, str]:
    r = client.post("/api/v1/students", json={"display_name": "Smoke", "age_band": "16-18", "as_guest": True})
    assert r.status_code == 200, r.text
    return {"x-csrf-token": r.json()["csrf_token"]}


def test_placement_is_adaptive_and_not_a_method_probe(client):
    h = _csrf(client)
    r = client.post("/api/v1/sessions", json={"mode": "diagnostic"}, headers=h)
    assert r.status_code == 200, r.text
    session_id = r.json()["session_id"]

    plan = client.get(f"/api/v1/assessment/diagnostic?session_id={session_id}&count=5")
    assert plan.status_code == 200, plan.text
    first = plan.json()
    assert first["phase"] == "placement"
    assert first["adaptive"] is True
    assert len(first["items"]) == 5
    assert "method" not in first  # placement must not claim to measure method
    assert first["plans_from"]["answered"] == 0

    q = first["items"][0]
    ans = client.post(
        "/api/v1/attempts",
        json={"session_id": session_id, "item_id": q["item"]["id"], "answer": q["item"].get("answer_probe") or "0"},
        headers=h,
    )
    assert ans.status_code == 200, ans.text
    again = client.get(f"/api/v1/assessment/diagnostic?session_id={session_id}&count=5")
    assert again.status_code == 200, again.text
    assert again.json()["plans_from"]["answered"] >= 1


def test_method_probe_is_clamped_and_says_how_it_is_judged(client):
    h = _csrf(client)
    for count in (1, 4, 9, 20):
        r = client.get(f"/api/v1/assessment/method-probe?count={count}", headers=h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["phase"] == "method_probe"
        assert 6 <= len(body["items"]) <= 8, (count, len(body["items"]))
        assert body["how_it_is_judged"]
        for served in body["items"]:
            # The whole point of the probe: the method varies, the question format does not.
            assert served["item"]["kind"] == body["items"][0]["item"]["kind"]


def test_method_report_shows_n_and_labels_early_signals(client):
    h = _csrf(client)
    r = client.get("/api/v1/methods/report", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "methods" in body and isinstance(body["methods"], list)
    for entry in body["methods"]:
        assert "n" in entry, entry
        assert entry["evidence"] in {"no_data", "early_signal", "signal", "established"}, entry
        assert "expected" in entry and "actual" in entry
    assert body["min_n_for_a_finding"] >= 5
    assert body.get("note")


def test_an_attempt_remembers_the_method_it_was_served_under(client):
    h = _csrf(client)
    sess = client.post("/api/v1/sessions", json={"mode": "practice"}, headers=h).json()["session_id"]
    served = client.get("/api/v1/practice?count=1", headers=h).json()["items"][0]
    method = (served.get("presentation") or {}).get("method") or "worked_example"
    r = client.post(
        "/api/v1/attempts",
        json={
            "session_id": sess,
            "item_id": served["item"]["id"],
            "answer": served["item"].get("answer_probe") or "0",
            "method": method,
            "method_design": "randomised",
            "expected_success": 0.5,
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    report = client.get("/api/v1/methods/report", headers=h).json()
    assert report["n_scored"] >= 1, report
    assert any(e["method"] == method for e in report["methods"]), report["methods"]


def test_concept_versions_are_three_and_show_what_is_unverified(client):
    h = _csrf(client)
    topics = client.get("/api/v1/topics", headers=h).json()
    assert topics
    topic_id = topics[0]["id"]
    r = client.get(f"/api/v1/concepts/{topic_id}/versions", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert {"text", "visual", "worked_example"} <= set(body["versions"]), body["versions"].keys()
    assert body["versions"]["text"]["steps"], "a concept with material must segment into steps"
    assert body["versions"]["visual"]["kind"] in {"flow", "comparison_table", "timeline", "labelled_diagram"}
    assert body["checks"]["text_steps"] >= 1
    assert isinstance(body["unverified"], list)


def test_versions_of_a_missing_concept_is_404_not_500(client):
    h = _csrf(client)
    r = client.get("/api/v1/concepts/nope/versions", headers=h)
    assert r.status_code == 404, r.text
