"""Every registered detector runs against the context it is actually given.

There was a detector that never ran. `method_comparison` — the one comparison the whole
"which teaching works" story rests on — was written to take a bare row list, while `run_all`
passes the shared `EvidenceContext`. It raised `TypeError` on every analysis, and `run_all`
swallowed the exception on purpose ("a broken detector must not sink the analysis"), so the
catalogue advertised a detector, the log said `detector method_comparison failed`, and nothing
in the app or the suite noticed.

`test_every_detector_accepts_the_context` calls each detector *without* that safety net, so a
signature that drifts fails here instead of going quiet. The rest of the file is the
comparison's own contract: actual against expected, format held inside a method, and nothing
below the evidence bar turned into a finding.
"""

from __future__ import annotations

import pytest

from adapt.learning import patterns as P

#: A learner taught four ways on one topic. Two of the groups have the *same* raw accuracy and
#: different conclusions, which is the whole point of the comparison: it is not accuracy.
ROWS = [
    P.AttemptRow(
        id=f"att_{method}_{i}",
        topic_id="top_fraction_add",
        item_id=f"itm_{i}",
        correct=correct,
        method=method,
        question_kind="numeric",
        design="randomised",
        expected_success=expected,
        attempt_no=i + 1,
        time_to_first_ms=4_000 + i * 250,
        time_on_item_ms=30_000 + i * 500,
    )
    for method, actuals, expected in (
        # retrieval met material predicted at 50% and beat that by 30 points
        ("retrieval", [1, 1, 1, 1, 0, 1, 1, 0, 1, 1], 0.5),
        # explanation: 5/10 where 50% was predicted -- exactly on expectation
        ("explanation", [1, 0, 1, 0, 1, 0, 1, 0, 0, 1], 0.5),
        # worked example: the same 5/10, but the items were predicted at 85% (easier material,
        # served harder) -- so the same accuracy is a 35-point shortfall
        ("worked_example", [0, 1, 0, 0, 1, 0, 1, 0, 1, 1], 0.85),
        # visual is only two answers old: a hint, not a finding
        ("visual", [1, 0], 0.5),
    )
    for i, correct in enumerate(actuals)
]


def _ctx(rows=None, min_n: int = 4) -> P.EvidenceContext:
    return P.EvidenceContext(
        student_id="stu_detectors",
        attempts=rows if rows is not None else ROWS,
        prereqs={"top_fraction_add": ["top_fractions_intro"]},
        mastery={"top_fraction_add": 0.4, "top_fractions_intro": 0.6},
        half_lives={"top_fraction_add": 48.0},
        min_n=min_n,
    )


@pytest.mark.parametrize("name", sorted(P.DETECTORS))
def test_every_detector_accepts_the_context(name):
    """Called with no try/except: this is the guard the swallowed TypeError needed."""
    detector = P.DETECTORS[name]
    out = detector(_ctx())
    assert isinstance(out, list), f"{name} returned {type(out).__name__}"
    for obs in out:
        assert isinstance(obs, P.Observation), f"{name} returned {type(obs).__name__}"
        assert obs.detector, f"{name} produced an observation with no detector name"
        assert obs.n or obs.n_baseline, f"{name} produced an observation with no observations"


def test_run_all_survives_every_detector_without_logging_a_failure(caplog):
    caplog.set_level("ERROR", logger="adapt.ai1")
    out = P.run_all(_ctx())
    assert isinstance(out, list)
    failures = [r for r in caplog.records if "detector" in r.getMessage()]
    assert not failures, [r.getMessage() for r in failures]


def test_the_method_comparison_is_actual_against_expected_not_raw_accuracy():
    obs = [o for o in P.method_comparison(_ctx()) if o.detector == "method_effect"]
    by_method = {str(o.facts["method"]): o for o in obs}
    assert by_method, "the comparison produced nothing on a learner taught four ways"

    retrieval = by_method["retrieval"]
    assert retrieval.value == pytest.approx(0.8, abs=1e-6), retrieval.value
    assert retrieval.baseline == pytest.approx(0.5, abs=1e-6), retrieval.baseline
    assert retrieval.direction == "up"
    assert retrieval.n == 10 and retrieval.n_baseline == 10, retrieval
    assert retrieval.facts["residual"] > 0.2, retrieval.facts

    # Same raw accuracy (5 of 10) in two methods, opposite readings: the worked example was
    # served easier material, so its 50% is a shortfall, not a tie. Raw accuracy cannot see this,
    # which is the defect the comparison exists to fix.
    explanation = by_method["explanation"]
    worked = by_method["worked_example"]
    assert explanation.value == pytest.approx(0.5), explanation.value
    assert worked.value == pytest.approx(0.5), worked.value
    assert explanation.baseline == pytest.approx(0.5), explanation.baseline
    assert worked.baseline == pytest.approx(0.85), worked.baseline
    assert abs(explanation.facts["residual"]) < 0.05, explanation.facts
    assert worked.facts["residual"] < -0.3 and worked.direction == "down", worked.facts

    # the answer format is held inside a method and reported, so a change of format cannot be
    # read as a change of teaching
    assert retrieval.facts["formats"] == ["numeric"], retrieval.facts
    assert retrieval.candidate_strategy == "retrieval_practice"


def test_a_hint_is_not_a_finding():
    """Two answers on the visual method must not become an observation.

    The numbers stay in `/methods/report` — with `n` and the words "early signal" — which is
    where a learner can see them without them being treated as a result.
    """
    obs = [o for o in P.method_comparison(_ctx()) if o.detector == "method_effect"]
    assert "visual" not in {str(o.facts["method"]) for o in obs}

    report = P.method_comparison(_ctx())  # still no observation ...
    assert report == [] or all(o.detector == "method_effect" for o in report)
    # ... and the row is not deleted from the report a screen reads
    from adapt.learning import methods as M

    full = M.method_report(
        [
            {
                "method": a.method,
                "correct": bool(a.correct),
                "expected_success": a.expected_success,
                "question_kind": a.question_kind,
            }
            for a in ROWS
        ]
    )
    visual = next(r for r in full["methods"] if r["method"] == "visual")
    assert visual["n"] == 2 and visual["evidence"] == "early_signal", visual


def test_a_half_assigned_comparison_is_not_called_randomised():
    """One arm assigned by the server and the other picked by the learner is not an experiment.

    The detector falls back to the click-based group when an arm is thin. When it does, the
    observation has to say `observational` -- otherwise the self-selection the arms exist to
    remove comes back wearing the word "randomised".
    """
    rows = [
        P.AttemptRow(
            id=f"att_{i}",
            topic_id="top_fraction_add",
            item_id=f"itm_{i}",
            correct=True,
            method="worked_example",
            question_kind="numeric",
            design="randomised",
            arm="example_first",
            expected_success=0.5,
        )
        for i in range(5)
    ] + [
        # the other arm was never assigned: these are learners' own choices, not assignments
        P.AttemptRow(
            id=f"click_{i}",
            topic_id="top_fraction_add",
            item_id=f"itm_{i}",
            correct=False,
            method="explanation",
            question_kind="numeric",
            design="observational",
            arm="",
            explanation_opened=True,
            expected_success=0.5,
        )
        for i in range(5)
    ]
    found = P.detect_worked_example_need(_ctx(rows))
    obs = [o for o in found if o.detector == "worked_example_need"]
    assert obs, "the comparison should still be reported"
    assert obs[0].design == "observational", obs[0].as_dict()
    assert "chose to do" in str(obs[0].facts["comparison"]), obs[0].facts
    assert "self selected" in str(obs[0].facts["after"]), obs[0].facts


def test_a_method_that_beats_expectation_can_become_a_proposal():
    """The comparison is only half a loop if nothing can come out of it.

    `method_effect` used to be computed and then dropped: `hypotheses.draft` had no template for
    it, so the one measurement the "which teaching works" story rests on never became a hypothesis
    and never reached the learner as a change to approve. The template names no strategy of its
    own — which method won *is* the finding — so it takes the observation's candidate.
    """
    from adapt.learning import hypotheses as H

    by_method = {str(o.facts["method"]): o for o in P.method_comparison(_ctx())}
    retrieval = by_method["retrieval"]
    assert retrieval.candidate_strategy == "retrieval_practice"

    draft = H.draft(retrieval)
    assert draft is not None, "a method beating its own prediction produced no hypothesis"
    assert draft.strategy == "retrieval_practice"
    assert draft.detector == "method_effect"
    # the numbers are in the sentence, in the form a learner can check against the screen
    assert "80%" in draft.statement and "50%" in draft.statement, draft.statement
    assert "+30 points" in draft.statement, draft.statement
    assert str(retrieval.facts["label"]) in draft.statement, draft.statement
    assert "10 answers" in draft.statement, draft.statement
    assert draft.citations and draft.mechanism and draft.alternatives
    assert draft.falsification and "lands back at" in draft.falsification
    assert H.check_language(draft.statement) == []


def test_a_method_that_fell_short_is_recorded_but_not_proposed():
    """Underperforming is a finding. "Do more of it" is not a change worth asking for."""
    from adapt.learning import hypotheses as H

    by_method = {str(o.facts["method"]): o for o in P.method_comparison(_ctx())}
    worked = by_method["worked_example"]
    assert worked.direction == "down" and worked.facts["residual"] < 0
    assert worked.candidate_strategy == "", worked.as_dict()
    assert worked.facts["why_no_proposal"], "the reason has to be on the record, not implied"
    assert H.draft(worked) is None, "an underperforming method must not propose itself"

    # exactly on expectation is not a win either
    explanation = by_method["explanation"]
    assert abs(explanation.facts["residual"]) < 0.05
    assert explanation.candidate_strategy == "" and H.draft(explanation) is None


def test_a_replayed_finding_arrives_with_its_design_and_evidence(services, learner, monkeypatch):
    """`update_hypotheses` rebuilds the observation from the stored dict.

    The dict carries `direction`, `design` and `evidence`; the rebuild used to ignore them, so
    every replayed finding came back as "up / observational / early_signal" — which is the
    opposite of what a randomised, above-the-bar comparison is, and would mislabel anything a
    future template reads from those fields.
    """
    from adapt.learning import hypotheses as H

    captured: dict[str, P.Observation] = {}
    real_draft = H.draft

    def spy(obs, **kwargs):
        captured["obs"] = obs
        return real_draft(obs, **kwargs)

    monkeypatch.setattr(H, "draft", spy)

    retrieval = next(o for o in P.method_comparison(_ctx()) if o.facts["method"] == "retrieval")
    payload = {"id": "evd_replay_1", **retrieval.as_dict()}
    drafts = services.ai1.update_hypotheses(learner["student_id"], [payload])

    seen = captured["obs"]
    assert seen.design == "randomised", seen.as_dict()
    assert seen.evidence == "signal", seen.as_dict()
    assert seen.direction == "up", seen.as_dict()
    assert seen.candidate_strategy == "retrieval_practice"
    assert drafts and drafts[0].strategy == "retrieval_practice"

    from sqlalchemy import select

    from adapt.db.models import Hypothesis

    with services.scope() as s:
        row = s.execute(
            select(Hypothesis).where(
                Hypothesis.student_id == learner["student_id"],
                Hypothesis.detector == "method_effect",
            )
        ).scalar_one()
        assert row.proposed_strategy == "retrieval_practice"
        assert "80%" in row.statement, row.statement


def test_a_method_that_keeps_beating_expectation_reaches_the_learner_as_a_change(
    services, learner, client
):
    """The whole chain, on the seeded pilot: rows -> detector -> hypothesis -> a proposal.

    Nine scored answers under one method is more than a test should fake by hand, and the pilot
    seeder is the honest way to get them: real ``Attempt`` rows, flagged as what they are. Which
    is also the point of the last assertion -- the sentence the learner is asked to approve says
    that the answers behind it were seeded before a demo.
    """
    from adapt.learning.pilot import PILOT_LABEL, PilotSpec
    from adapt.services import pilot as PS

    with services.scope() as s:
        report = PS.seed(
            s,
            learner["student_id"],
            specs=[PilotSpec(method="retrieval", n=9, expected_success=0.5, actual_rate=0.9)],
        )
    assert report["seeded"] == 9, report

    analyzed = client.post(
        "/api/v1/analyze", params={"use_llm": "false"}, headers=learner["headers"]
    ).json()
    detectors = {str(e.get("detector")) for e in analyzed.get("evidence") or []}
    assert "method_effect" in detectors, sorted(detectors)

    pending = client.get("/api/v1/adaptations/pending", headers=learner["headers"]).json()
    mine = [p for p in pending["proposals"] if p["strategy"] == "retrieval_practice"]
    assert mine, [p["strategy"] for p in pending["proposals"]]
    row = mine[0]
    assert row["strategy_label"] and row["what_it_does"] and row["citations"], row
    # `facts` is the observation in words (the numbers), `hypothesis` is the mechanism, and the
    # two are kept apart on purpose: what was measured is not the same claim as why it happened.
    sentence = str(row["facts"] or "")
    assert "Retrieval first" in sentence, sentence
    assert "%" in sentence and "points against expectation" in sentence, sentence
    assert PILOT_LABEL in sentence, f"a pilot-derived number without its label: {sentence}"
    assert "9 answers" in sentence, sentence
    assert row["hypothesis"] and row["hypothesis"] != sentence, row["hypothesis"]
    assert row["alternatives"], "the learner is told what else could explain it"
    assert row["status"] == "pending" and not row["decided_at"], row
