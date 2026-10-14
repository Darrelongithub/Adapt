#!/usr/bin/env python3
"""Create/remove two explicitly labelled, deterministic synthetic demo learners.

Run from the repository root after installing ``backend[dev]``:

    .venv/bin/python scripts/demo_seed.py
    .venv/bin/python scripts/demo_seed.py --remove

This is a local demonstration fixture, not research data.  It uses ordinary session,
attempt, and allowlisted event rows, marks attempts as pilot rows, and gives each
account a visible ``SYNTHETIC DEMO`` display name.  It never creates rows for a real
learner.  The CLI shortcut ``adapt demo-seed`` is an integration note because Agent
6 owns only the config sub-app in the shared CLI module.
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND_SRC = ROOT / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

DEMO_LEARNERS = (
    {
        "id": "demo_synthetic_example",
        "email": "demo-example@adapt.invalid",
        "name": "SYNTHETIC DEMO — Example-first",
        "rates": {"worked_example": 0.76, "explanation": 0.40},
    },
    {
        "id": "demo_synthetic_explanation",
        "email": "demo-explanation@adapt.invalid",
        "name": "SYNTHETIC DEMO — Explanation-first",
        "rates": {"worked_example": 0.40, "explanation": 0.76},
    },
)
METHODS = ("worked_example", "explanation")
OBSERVATIONS_PER_METHOD = 8


def _items_for_contrast(s):
    from adapt.db.models import Item, Topic
    from sqlalchemy import select

    rows = s.execute(
        select(Item, Topic)
        .join(Topic, Topic.id == Item.topic_id)
        .where(Item.active.is_(True), Item.kind == "mcq")
        .order_by(Topic.difficulty, Topic.id, Item.id)
    ).all()
    by_topic = {}
    for item, topic in rows:
        by_topic.setdefault(topic.id, (item, topic))
    return list(by_topic.values())[:OBSERVATIONS_PER_METHOD]


def _remove_demo_data(s) -> int:
    from adapt.db.models import Attempt, LearningSession, Student
    from sqlalchemy import delete, func, select

    total = 0
    for profile in DEMO_LEARNERS:
        session_ids = list(
            s.execute(
                select(LearningSession.id).where(
                    LearningSession.student_id == profile["id"],
                    LearningSession.mode == "synthetic_demo",
                )
            ).scalars()
        )
        if session_ids:
            total += int(
                s.execute(
                    delete(Attempt).where(Attempt.session_id.in_(session_ids))
                ).rowcount
                or 0
            )
        sessions = (
            s.execute(
                select(LearningSession).where(
                    LearningSession.student_id == profile["id"],
                    LearningSession.mode == "synthetic_demo",
                )
            )
            .scalars()
            .all()
        )
        for session in sessions:
            s.delete(session)  # event rows cascade with the session
        student = s.get(Student, profile["id"])
        if student is not None:
            remaining_sessions = int(
                s.execute(
                    select(func.count())
                    .select_from(LearningSession)
                    .where(LearningSession.student_id == student.id)
                ).scalar_one()
            )
            remaining_attempts = int(
                s.execute(
                    select(func.count())
                    .select_from(Attempt)
                    .where(Attempt.student_id == student.id)
                ).scalar_one()
            )
            # Keep an account if someone has used it for anything beyond this script's
            # synthetic sessions; cleanup must never erase subsequent learner history.
            if not remaining_sessions and not remaining_attempts:
                s.delete(student)
    s.commit()
    return total


def _seed(s, password: str) -> dict[str, object]:
    from adapt.db.models import Attempt, Item, LearningSession, Student, utcnow
    from adapt.learning import pilot as pilot_rows
    from adapt.security import auth
    from adapt.services import telemetry

    items = _items_for_contrast(s)
    if not items:
        raise RuntimeError(
            "No active multiple-choice curriculum is available; "
            "initialize the app first."
        )
    for profile in DEMO_LEARNERS:
        existing = s.get(Student, profile["id"])
        if existing is not None and not str(existing.display_name or "").startswith(
            "SYNTHETIC DEMO"
        ):
            raise RuntimeError(
                "A reserved synthetic-demo id belongs to a non-demo account; "
                "refusing to overwrite it."
            )
        if existing is not None:
            session_rows = (
                s.query(LearningSession.id, LearningSession.mode)
                .filter(LearningSession.student_id == existing.id)
                .all()
            )
            synthetic_ids = {
                session_id
                for session_id, mode in session_rows
                if mode == "synthetic_demo"
            }
            has_non_synthetic_session = any(
                mode != "synthetic_demo" for _session_id, mode in session_rows
            )
            attempt_session_ids = (
                s.query(Attempt.session_id)
                .filter(Attempt.student_id == existing.id)
                .all()
            )
            has_non_synthetic_attempt = any(
                session_id not in synthetic_ids for (session_id,) in attempt_session_ids
            )
            if has_non_synthetic_session or has_non_synthetic_attempt:
                raise RuntimeError(
                    "A synthetic-demo account has non-synthetic learner history; "
                    "refusing to reseed or overwrite it."
                )

    # Rehearsals use the same fixed account ids and remove only rows explicitly owned
    # by this script. Any non-synthetic history remains untouched.
    _remove_demo_data(s)
    start = utcnow() - timedelta(days=4)
    results: list[dict[str, object]] = []
    for learner in DEMO_LEARNERS:
        student = s.get(Student, learner["id"])
        if student is None:
            student = Student(id=learner["id"])
            s.add(student)
        student.display_name = learner["name"]
        student.email = learner["email"]
        student.email_index = auth.email_index_for(learner["email"])
        student.password_hash = auth.hash_password(password)
        student.role = "student"
        student.age_band = "16-18"
        student.onboarding_complete = True
        student.diagnostic_complete = True
        student.consent_analytics = True
        student.consent_share_with_llm = False
        student.last_active_at = start
        s.flush()

        session = LearningSession(
            student_id=student.id,
            mode="synthetic_demo",
            status="finished",
            started_at=start,
            ended_at=start + timedelta(hours=5),
            active_ms=OBSERVATIONS_PER_METHOD * len(METHODS) * 70_000,
            device_hint="mobile",
            summary={
                "synthetic_demo": True,
                "label": "SYNTHETIC DEMO DATA — not learner evidence",
                "purpose": "contrast method comparison rehearsal",
            },
        )
        s.add(session)
        s.flush()

        item_ids = [item.id for item, _topic in items]
        topic_ids = [topic.id for _item, topic in items]
        specs = [
            pilot_rows.PilotSpec(
                method=method,
                n=OBSERVATIONS_PER_METHOD,
                expected_success=0.60,
                actual_rate=float(learner["rates"][method]),
                topic_id=topic_ids[0],
                item_id=item_ids[0],
                design="synthetic_contrast",
            )
            for method in METHODS
        ]
        seeded = pilot_rows.seed_pilot_rows(
            student_id=student.id, specs=specs, start=start
        )
        groups = {
            method: [row for row in seeded if row["method"] == method]
            for method in METHODS
        }
        seq = 1
        for index in range(OBSERVATIONS_PER_METHOD):
            for method_index, method in enumerate(METHODS):
                entry = dict(groups[method][index])
                item_id = item_ids[index % len(item_ids)]
                topic_id = topic_ids[index % len(topic_ids)]
                submitted = start + timedelta(minutes=25 * index + method_index * 12)
                correct = bool(entry["correct"])
                changed = index in {2, 5}
                hints = 1 if (not correct and index % 2 == 0) else 0
                attempt = Attempt(
                    session_id=session.id,
                    student_id=student.id,
                    item_id=item_id,
                    topic_id=topic_id,
                    experience_mode="example" if method == "worked_example" else "read",
                    attempt_no=1,
                    first_answer={"choice": "option_b" if not correct else "option_a"},
                    final_answer={"choice": "option_a" if correct else "option_b"},
                    correct=correct,
                    score=float(entry["score"]),
                    graded_by="rules",
                    time_to_first_ms=1200 + (index * 170),
                    time_on_item_ms=22_000 + (index * 900),
                    submitted_at=submitted,
                    change_count=1 if changed else 0,
                    changed_from_correct=bool(changed and index % 2 == 1),
                    hints_used=hints,
                    examples_opened=1 if method == "worked_example" else 0,
                    explanation_opened=method == "explanation",
                    method=method,
                    question_kind="mcq",
                    expected_success=float(entry["expected_success"]),
                    mapping_design="synthetic_contrast",
                    method_arm=method,
                    pilot=True,
                )
                s.add(attempt)
                s.flush()
                shown = submitted - timedelta(
                    seconds=max(4, (attempt.time_on_item_ms or 0) // 1000)
                )
                stem = s.get(Item, item_id)
                stem_words = len((stem.stem or "").split()) if stem is not None else 0
                events = [
                    (
                        "question_shown",
                        {"stem_words": stem_words, "variant": "synthetic_demo"},
                        shown,
                    ),
                    (
                        "first_interaction",
                        {"ms_since_shown": attempt.time_to_first_ms or 0},
                        shown + timedelta(milliseconds=attempt.time_to_first_ms or 0),
                    ),
                ]
                if changed:
                    events.append(
                        (
                            "response_change",
                            {
                                "ms_since_shown": 11_000,
                                "from": "option_b",
                                "to": "option_a",
                                "index": 1,
                            },
                            shown + timedelta(seconds=11),
                        )
                    )
                if hints:
                    events.append(
                        (
                            "hint_opened",
                            {"hint_no": 1, "ms_since_shown": 13_000},
                            shown + timedelta(seconds=13),
                        )
                    )
                if method == "worked_example":
                    events.append(
                        (
                            "example_opened",
                            {"ms_since_shown": 9_000, "step": 1},
                            shown + timedelta(seconds=9),
                        )
                    )
                if method == "explanation":
                    events.append(
                        (
                            "explanation_opened",
                            {
                                "ms_since_shown": 8_000,
                                "chars": 210,
                                "seconds_read": 11.0,
                            },
                            shown + timedelta(seconds=11),
                        )
                    )
                events.append(
                    (
                        "answer_submitted",
                        {
                            "attempt_no": 1,
                            "correct": correct,
                            "score": float(entry["score"]),
                        },
                        submitted,
                    )
                )
                for event_type, payload, event_ts in sorted(
                    events, key=lambda event: event[2]
                ):
                    telemetry.append_event(
                        s,
                        session_id=session.id,
                        student_id=student.id,
                        type=event_type,
                        payload=payload,
                        item_id=item_id,
                        topic_id=topic_id,
                        attempt_id=attempt.id,
                        seq=seq,
                        ts=event_ts,
                        client_id=f"demo-{index}-{method_index}-{seq}",
                    )
                    seq += 1
        results.append(
            {
                "student_id": student.id,
                "display_name": learner["name"],
                "email": learner["email"],
                "session_id": session.id,
                "attempts": OBSERVATIONS_PER_METHOD * len(METHODS),
                "methods": list(METHODS),
                "label": "SYNTHETIC DEMO DATA — not learner evidence",
            }
        )
    s.commit()
    return {"learners": results, "label": "SYNTHETIC DEMO DATA — not learner evidence"}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Seed clearly labelled synthetic demo profiles."
    )
    parser.add_argument(
        "--remove",
        action="store_true",
        help="remove only these synthetic demo profiles and histories",
    )
    args = parser.parse_args()

    from adapt.config import get_settings
    from adapt.db.engine import Database
    from adapt.db.init_db import init

    settings = get_settings()
    db = Database(settings)
    init(settings, db=db)
    if args.remove:
        with db.session() as session:
            removed = _remove_demo_data(session)
        print(f"Removed synthetic demo histories ({removed} attempts).")
        return 0

    password = os.environ.get("ADAPT_DEMO_PASSWORD") or secrets.token_urlsafe(18)
    with db.session() as session:
        result = _seed(session, password)
    print(json_dump(result))
    print("Synthetic demo account password (display once): " + password)
    print(
        "Use the two .invalid addresses and this password to sign in to the local UI."
    )
    print("Do not use these demo accounts or this password on a public deployment.")
    return 0


def json_dump(value: object) -> str:
    import json

    return json.dumps(value, indent=2, ensure_ascii=False, default=str)


if __name__ == "__main__":
    raise SystemExit(main())
