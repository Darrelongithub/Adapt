"""Regression: successful retention checks never pull a review forward."""

from datetime import timedelta

from adapt.db.models import RetentionSchedule, utcnow
from adapt.learning import spacing as SP


class ShrinkingModel:
    weights = [0.0] * 6

    def predict_hours(self, _features):
        return 1.0


def _force_shrinking_fit(monkeypatch, ai1):
    monkeypatch.setattr(SP, "fit_half_life", lambda *_args, **_kwargs: ShrinkingModel())
    monkeypatch.setattr(ai1, "_review_history", lambda _session, **_kwargs: [])


def test_success_does_not_reduce_half_life_or_due_date(services, learner, items, monkeypatch):
    _force_shrinking_fit(monkeypatch, services.ai1)
    topic_id = items[0]["item"]["topic_id"]
    previous_due = utcnow() + timedelta(hours=10)
    with services.scope() as session:
        session.add(RetentionSchedule(
            student_id=learner["student_id"], topic_id=topic_id,
            half_life_hours=12.0, due_at=previous_due, reps=2, lapses=1,
        ))
        session.flush()
        updated = services.ai1._update_schedule(
            session,
            student_id=learner["student_id"], topic_id=topic_id,
            correct=True, difficulty=0.5, self_explain=0.0,
            lag_hours=0.05, prior_correct_count=2, n_obs=2,
        )
        assert updated.last_result is True
        assert updated.reps == 3
        assert updated.lapses == 1
        assert updated.half_life_hours == 12.0
        assert updated.due_at == previous_due


def test_lapse_still_reduces_half_life_and_due_date(services, learner, items, monkeypatch):
    _force_shrinking_fit(monkeypatch, services.ai1)
    topic_id = next(
        entry["item"]["topic_id"] for entry in items
        if entry["item"]["topic_id"] != items[0]["item"]["topic_id"]
    )
    previous_due = utcnow() + timedelta(hours=10)
    with services.scope() as session:
        session.add(RetentionSchedule(
            student_id=learner["student_id"], topic_id=topic_id,
            half_life_hours=12.0, due_at=previous_due, reps=2, lapses=1,
        ))
        session.flush()
        updated = services.ai1._update_schedule(
            session,
            student_id=learner["student_id"], topic_id=topic_id,
            correct=False, difficulty=0.5, self_explain=0.0,
            lag_hours=0.05, prior_correct_count=2, n_obs=2,
        )
        assert updated.last_result is False
        assert updated.reps == 3
        assert updated.lapses == 2
        assert updated.half_life_hours < 12.0
        assert updated.due_at < previous_due
