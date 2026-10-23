"""Regression: first-lesson reflection text is durable and readable by its learner."""

from sqlalchemy import func, select

from adapt.journey.models import JourneyStage


def test_reflection_is_saved_with_completion_and_repeat_finish_is_not_a_duplicate(client, services, learner):
    text = "Cells are alive and have specialised parts; audit reflection 42."
    response = client.post(
        "/api/v1/journey/advance",
        json={"stage": "first_lesson", "status": "done", "note": text},
        headers=learner["headers"],
    )
    assert response.status_code == 200, response.text
    row = next(row for row in response.json()["stages"] if row["id"] == "first_lesson")
    assert row["note"] == text

    read_back = client.get("/api/v1/journey/state", headers=learner["headers"])
    assert next(row for row in read_back.json()["stages"] if row["id"] == "first_lesson")["note"] == text
    repeated = client.post(
        "/api/v1/journey/advance",
        json={"stage": "first_lesson", "status": "done", "note": text},
        headers=learner["headers"],
    )
    assert repeated.status_code == 200
    with services.scope() as session:
        rows = session.execute(select(JourneyStage).where(JourneyStage.student_id == learner["student_id"], JourneyStage.stage == "first_lesson")).scalars().all()
        assert len(rows) == 1 and rows[0].note == text
        count = session.execute(select(func.count()).select_from(JourneyStage).where(JourneyStage.student_id == learner["student_id"])).scalar_one()
        assert count == 1


def test_reflection_over_the_existing_limit_is_refused_not_truncated(client, learner):
    response = client.post(
        "/api/v1/journey/advance",
        json={"stage": "first_lesson", "status": "done", "note": "x" * 201},
        headers=learner["headers"],
    )
    assert response.status_code == 422
