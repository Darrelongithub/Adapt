from __future__ import annotations

import uuid

from adapt.content.skeleton import cache_identity, canonical_topic_key
from adapt.content.skeleton.cache import (
    ContentPrepareJob,
    PackCacheEntry,
    get_cached,
    record_item_attempt,
    store_pack,
)
from adapt.content.skeleton.service import get_pack
from adapt.content.skeleton.simple import simple_pack

from .fakes import ScriptedModel


def test_cache_identity_includes_topic_level_language_and_spec_version():
    base = cache_identity("history-paper", level=2, language="en", spec_version="spec-1")
    assert base == cache_identity("history-paper", level=2, language="EN", spec_version="spec-1")
    assert (
        base.cache_key
        != cache_identity("history-paper", level=3, language="en", spec_version="spec-1").cache_key
    )
    assert (
        base.cache_key
        != cache_identity("history-paper", level=2, language="sw", spec_version="spec-1").cache_key
    )
    assert (
        base.cache_key
        != cache_identity("history-paper", level=2, language="en", spec_version="spec-2").cache_key
    )
    assert "history-paper" not in base.topic_key_hash


def test_canonical_topic_key_matches_get_pack_resolution():
    assert canonical_topic_key("  Calculus  ") == canonical_topic_key("calculus") == "calculus"


def test_encrypted_pack_cache_round_trips_content(services):
    topic_key = f"test-cache-{uuid.uuid4().hex[:12]}"
    identity = cache_identity(topic_key, level=4, language="en")
    pack = simple_pack(
        topic_key=topic_key,
        topic_title="Your topic",
        subject_family="generic",
        language="en",
    )
    with services.scope() as session:
        row = store_pack(session, identity, pack)
        saved_hash = row.content_hash
        assert saved_hash
        assert row.source == "simple"

    with services.scope() as session:
        cached = get_cached(session, identity)
        assert cached is not None
        restored, digest = cached
        assert restored.as_dict() == pack.as_dict()
        assert digest == saved_hash
        assert session.get(PackCacheEntry, identity.primary_key) is not None


def test_pack_item_observations_use_shared_calibration_and_retirement_rules(services):
    pack = get_pack("Ancient pottery", level=2, language="en", model=ScriptedModel())
    identity = cache_identity(pack.topic_key, level=2, language="en")
    item = pack.items[0]
    assert item.calibration is not None
    assert item.calibration.declared_difficulty == 0.60
    assert item.calibration.observed_difficulty == 0.60
    assert "calibration" not in pack.as_dict()["items"][0]

    with services.scope() as session:
        store_pack(session, identity, pack)
        state = None
        for _ in range(4):
            state = record_item_attempt(session, item.id, correct=False)
        assert state is not None
        assert state.n_attempts == 4
        assert state.n_correct == 0
        assert state.observed_difficulty == 0.4
        assert state.active is False
        assert state.retirement_reason == "never_answered_correctly"

    with services.scope() as session:
        cached = get_cached(session, identity)
        assert cached is not None
        restored, _digest = cached
        restored_item = next(candidate for candidate in restored.items if candidate.id == item.id)
        assert restored_item.calibration is not None
        assert restored_item.calibration.n_attempts == 4
        assert restored_item.calibration.active is False
        assert not restored_item.scorable
        assert len(restored.scorable_items()) == len(pack.items) - 1


def test_prepare_router_job_and_status_contract(monkeypatch, services):
    from adapt.api.routes import content_prepare as prepare

    class DeferredExecutor:
        def __init__(self):
            self.submissions = []

        def submit(self, fn, *args):
            self.submissions.append((fn, args))
            return object()

    executor = DeferredExecutor()
    monkeypatch.setattr(prepare, "_WORKERS", executor)
    topic = f"unlisted social history {uuid.uuid4().hex[:8]}"
    body = prepare.PrepareBody(topic=topic, level=2, language="en")
    with services.scope() as session:
        response = prepare.prepare_content(body, session=session, _student=None)
        job_id = response["job_id"]
        row = session.get(ContentPrepareJob, job_id)
        assert row is not None
        assert row.state == "running"
        assert (
            row.cache_key
            == prepare.cache_identity(canonical_topic_key(topic), level=2, language="en").cache_key
        )
        assert not hasattr(row, "topic")

    assert len(executor.submissions) == 1
    fn, args = executor.submissions[0]
    assert fn is prepare._run_prepare
    assert args == (job_id, topic, 2, "en")
    with services.scope() as session:
        status = prepare.prepare_status(job_id, session=session, _student=None)
        assert status == {"state": "running", "pack_source": None}

    route_shapes = {
        (route.path, method) for route in prepare.router.routes for method in route.methods
    }
    assert ("/api/v1/content/prepare", "POST") in route_shapes
    assert ("/api/v1/content/prepare/{job_id}", "GET") in route_shapes


def test_prepare_returns_ready_job_for_cache_hit(monkeypatch, services):
    from adapt.api.routes import content_prepare as prepare

    class NeverSubmit:
        def submit(self, *_args, **_kwargs):
            raise AssertionError("a cache hit must not enqueue a worker")

    monkeypatch.setattr(prepare, "_WORKERS", NeverSubmit())
    topic = f"cached history topic {uuid.uuid4().hex[:8]}"
    key = canonical_topic_key(topic)
    identity = cache_identity(key, level=1, language="en")
    pack = simple_pack(
        topic_key=key,
        topic_title="Your topic",
        subject_family="generic",
        language="en",
    )
    with services.scope() as session:
        store_pack(session, identity, pack)
    with services.scope() as session:
        result = prepare.prepare_content(
            prepare.PrepareBody(topic=topic, level=1, language="en"),
            session=session,
            _student=None,
        )
        job = session.get(ContentPrepareJob, result["job_id"])
        assert job is not None
        assert job.state == "fallback"
        assert job.pack_source == "simple"
    with services.scope() as session:
        status = prepare.prepare_status(result["job_id"], session=session, _student=None)
        assert status == {"state": "fallback", "pack_source": "simple"}
