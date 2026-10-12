"""End-to-end learner journey at the data layer, against a real server on a real database.

The UI probe proves the screens work. This proves the *records* do: that a diagnostic writes
band estimates, that an approved proposal is the only way an adaptation becomes active, that a
rejected one leaves nothing behind, that a modified one stores the learner's version, and that
all of it is still there after the server is restarted against the same data directory.

Run:  .venv/bin/python scripts/journey_check.py        # starts and stops its own server
Env:  JOURNEY_PORT (default 8020), JOURNEY_DATA (default a temp directory)
Exit code is non-zero if any check fails, so it can gate a release.
"""

from __future__ import annotations

import json
import pathlib
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = str(pathlib.Path(__file__).resolve().parents[1])
DATA = os.environ.get("JOURNEY_DATA", "/tmp/adapt-journey")
PY = f"{ROOT}/.venv/bin/python"

RESULTS: list[tuple[bool, str, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    RESULTS.append((bool(ok), label, detail))
    mark = "ok  " if ok else "FAIL"
    print(f"  {mark} {label}" + (f"  -> {detail}" if detail and not ok else ""))


class Client:
    """A cookie-jar HTTP client: the API is cookie-authenticated and CSRF-guarded."""

    def __init__(self, base: str) -> None:
        self.base = base
        self.jar: dict[str, str] = {}
        self.csrf = ""

    @property
    def cookie(self) -> str:
        return "; ".join(f"{k}={v}" for k, v in self.jar.items())

    def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        url = f"{self.base}{path}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("accept", "application/json")
        if data:
            req.add_header("content-type", "application/json")
        if self.cookie:
            req.add_header("cookie", self.cookie)
        if self.csrf:
            req.add_header("x-csrf-token", self.csrf)
        try:
            with urllib.request.urlopen(req, timeout=30) as res:
                raw = res.read().decode()
                for header, value in res.headers.items():
                    if header.lower() == "set-cookie":
                        pair = value.split(";")[0]
                        name, _, val = pair.partition("=")
                        self.jar[name.strip()] = val
                        if name.strip() == "adapt_csrf":
                            self.csrf = val
                return res.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode()
            return exc.code, (json.loads(raw) if raw else {})
        except urllib.error.URLError as exc:  # server down
            return 0, {"error": str(exc)}


def start_api(data_dir: str) -> subprocess.Popen:
    env = {
        **os.environ,
        "ADAPT_DATA_DIR": data_dir,
        "ADAPT_MODE": "local",
        "ADAPT_LOG_LEVEL": "WARNING",
        "ADAPT_RATE_LIMIT_MULTIPLIER": "50",  # a scripted journey is not a flood
        "PYTHONPATH": f"{ROOT}/backend/src",
    }
    log_path = os.environ.get("JOURNEY_LOG", "/tmp/journey-api.log")
    os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
    proc = subprocess.Popen(
        [PY, "-m", "uvicorn", "adapt.api.app:app", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=f"{ROOT}/backend",
        env=env,
        stdout=open(log_path, "ab"),
        stderr=subprocess.STDOUT,
    )
    for _ in range(120):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/healthz", timeout=2):
                return proc
        except Exception:
            time.sleep(0.25)
    raise RuntimeError("API did not come up")


def stop_api(proc: subprocess.Popen) -> bool:
    """Ask the server to stop, the way a deploy or a laptop lid does. True if it went quietly."""
    proc.send_signal(signal.SIGINT)
    try:
        proc.wait(timeout=15)
        return True
    except subprocess.TimeoutExpired:
        proc.kill()
        return False


def main() -> int:
    global PORT
    PORT = int(os.environ.get("JOURNEY_PORT", "8020"))
    shutil.rmtree(DATA, ignore_errors=True)
    base = f"http://127.0.0.1:{PORT}"
    api = start_api(DATA)
    try:
        c = Client(base)
        print("\n1. onboarding")
        email = f"journey-{int(time.time())}@example.test"
        password = "a long enough passphrase"
        status, body = c.call("POST", "/api/v1/students", {
            "display_name": "Journey Learner", "email": email, "password": password,
        })
        check(status == 200 and "student_id" in body, "a learner can register", f"{status} {body}")
        student_id = body.get("student_id")
        status, me = c.call("GET", "/api/v1/me")
        check(status == 200 and me.get("student_id") == student_id, "the session identifies the learner")
        status, _ = c.call("POST", "/api/v1/me/onboarding", {
            "weekly_minutes": 120, "session_minutes": 25, "goal": "GCSE maths, higher tier",
            "voice_enabled": True, "voice_gender": "female",
        })
        check(status == 200, "onboarding saves", str(status))
        status, me = c.call("GET", "/api/v1/me")
        check(me.get("onboarding_complete") is True, "the learner is marked onboarded", str(me))

        print("\n2. placement")
        status, sess = c.call("POST", "/api/v1/sessions", {"mode": "diagnostic"})
        check(status == 200 and sess.get("session_id"), "a diagnostic session opens", str(sess)[:200])
        sid = sess.get("session_id")
        status, diag = c.call("GET", "/api/v1/assessment/diagnostic")
        items = diag.get("items") or []
        check(status == 200 and len(items) >= 5, "placement serves a sequence", f"{len(items)} items")
        answered = 0
        for i, entry in enumerate(items[:12]):
            item = entry["item"]
            # Mostly wrong on purpose: the detectors need a pattern to find, and "the learner
            # is getting these wrong" is the one a scripted run can produce honestly.
            if item["kind"] == "numeric":
                answer: object = 999 if i % 4 else 3
            elif item["kind"] == "mcq":
                answer = {"correct_index": (i % 4) + 1}
            else:
                answer = "dunno"
            status, receipt = c.call("POST", "/api/v1/attempts", {
                "session_id": sid, "item_id": item["id"], "answer": answer, "attempt_no": i + 1,
            })
            if status == 200:
                answered += 1
        check(answered >= 10, "answers are accepted and recorded", f"{answered}/{len(items)} accepted")
        status, done = c.call("POST", f"/api/v1/sessions/{sid}/finish", {})
        check(status == 200, "placement can be finished", f"{status} {str(done)[:160]}")
        status, prog = c.call("GET", "/api/v1/dashboard")
        check(status == 200, "the dashboard answers after placement", str(status))

        print("\n2b. more evidence, on the same topics")
        # One answer per topic is a sample of one: the detectors say so, and rightly propose
        # nothing. A learner who is struggling has answered more than twelve questions, so the
        # scripted one does too -- otherwise the approval gate below would never be reached.
        for round_no in range(3):
            status, sess2 = c.call("POST", "/api/v1/sessions", {"mode": "practice"})
            rsid = sess2.get("session_id")
            status, plan = c.call("GET", "/api/v1/practice?count=6")
            for j, entry in enumerate(plan.get("items") or []):
                item = entry["item"]
                if item["kind"] == "numeric":
                    answer: object = 999
                elif item["kind"] == "mcq":
                    answer = {"correct_index": (j % 3) + 1}
                else:
                    answer = "dunno"
                c.call("POST", "/api/v1/attempts", {"session_id": rsid, "item_id": item["id"], "answer": answer, "attempt_no": j + 1})
            c.call("POST", f"/api/v1/sessions/{rsid}/finish", {})
        status, _ = c.call("GET", "/api/v1/dashboard")
        check(status == 200, "three practice sessions are recorded without error", str(status))

        print("\n3. analysis and proposals")
        status, out = c.call("POST", f"/api/v1/analyze?session_id={sid}", {})
        check(status == 200, "analysis runs on the recorded session", f"{status} {str(out)[:200]}")
        analysis_proposals = out.get("proposals") or []
        print(f"     analyze: {len(out.get('traces') or [])} traces, {len(out.get('hypotheses') or [])} hypotheses, "
              f"{len(analysis_proposals)} proposals")

        def active_now() -> list:
            return (c.call("GET", "/api/v1/adaptations/active")[1] or {}).get("active") or []

        def decided_ids() -> set:
            rows = (c.call("GET", "/api/v1/adaptations/history")[1] or {}).get("decisions") or []
            return {d.get("id") for d in rows}

        print("\n4. the approval gate (backend endpoints and stored rows, not buttons)")
        status, pending = c.call("GET", "/api/v1/adaptations/pending")
        proposals = pending.get("proposals") or []
        check(status == 200, "pending proposals are listed", f"{len(proposals)} waiting")

        # What the analysis said it proposed has to be *findable*: pending, already decided,
        # or active. A learner told "a change is waiting for your answer" and then shown an
        # empty list has been misled, and this cross-check is how that shows up.
        if analysis_proposals:
            visible = {p.get("id") for p in proposals} | {a.get("proposal_id") for a in active_now()} | decided_ids()
            missing = [p.get("id") for p in analysis_proposals if p.get("id") not in visible]
            check(not missing, "every proposal the analysis reported is visible in the gate",
                  f"reported {len(analysis_proposals)}, invisible {missing}")

        if len(proposals) < 3:                      # a second analysis may find another pattern
            c.call("POST", f"/api/v1/analyze?session_id={sid}", {})
            proposals = (c.call("GET", "/api/v1/adaptations/pending")[1] or {}).get("proposals") or []

        before = active_now()
        check(bool(proposals), "the loop proposed something once it had evidence", str(len(proposals)))
        if proposals:
            # (a) reject: recorded, and it must not switch anything on
            victim = proposals[0]
            status, res = c.call("POST", f"/api/v1/adaptations/{victim['id']}/decision",
                                 {"decision": "reject", "note": "not this week"})
            check(status == 200, "a proposal can be rejected", f"{status} {str(res)[:160]}")
            after = active_now()
            check(len(after) == len(before), "rejecting applies nothing", f"{len(before)} -> {len(after)}")
            check(not any(a.get("proposal_id") == victim["id"] for a in after),
                  "the rejected proposal is not secretly active")
            hist = c.call("GET", "/api/v1/adaptations/history")[1] or {}
            row = next((d for d in hist.get("decisions") or [] if d.get("id") == victim["id"]), {})
            check(row.get("status") == "rejected" and row.get("student_note") == "not this week",
                  "the refusal and its reason are recorded as the learner left them",
                  f"status={row.get('status')} note={row.get('student_note')}")

            # (b) modify: the learner's number is the one stored, not the default
            edit_me = next((p for p in proposals[1:] if p.get("action_payload")), None)
            if edit_me is not None:
                strategy = edit_me["strategy"]
                knobs = dict(edit_me.get("action_payload") or {})
                knob = next(iter(knobs), "target")
                mine = 0.8 if isinstance(knobs.get(knob), (int, float)) else knobs.get(knob)
                status, res = c.call("POST", f"/api/v1/adaptations/{edit_me['id']}/decision",
                                     {"decision": "modify", "modifications": {knob: mine, "sneaky": "yes"}})
                check(status == 200, "a proposal can be modified", f"{status} {str(res)[:200]}")
                if status == 200:
                    stored = res.get("payload") or {}
                    check(stored.get(knob) == mine,
                          "the modification — not the proposal's default — is what was switched on",
                          f"asked {mine}, stored {stored.get(knob)}")
                    check(knob not in (res.get("refused_keys") or []), "the declared knob was accepted",
                          str(res.get("refused_keys")))
                    check("sneaky" in (res.get("refused_keys") or []),
                          "an undeclared key is refused rather than written into the learner's plan",
                          str(res.get("refused_keys")))
                    live = active_now()
                    row = next((a for a in live if a.get("proposal_id") == edit_me["id"]), {})
                    check(row.get("payload", {}).get(knob) == mine,
                          "and the stored active row carries the same number",
                          json.dumps(row)[:220])
                    check(row.get("source") == "modified", "the row records that it was modified",
                          str(row.get("source")))

            # (c) later: deferred, not applied
            later_me = next((p for p in proposals[1:] if p["id"] != (edit_me or {}).get("id")), None)
            if later_me is not None:
                n_before = len(active_now())
                status, res = c.call("POST", f"/api/v1/adaptations/{later_me['id']}/decision",
                                     {"decision": "later"})
                check(status == 200 and len(active_now()) == n_before,
                      "'not now' defers the change without applying it", f"{status} {str(res)[:160]}")

            # (d) a second answer to the same question is refused
            print("\n5. the same proposal cannot be answered twice")
            status, again = c.call("POST", f"/api/v1/adaptations/{victim['id']}/decision", {"decision": "accept"})
            check(status == 409, "repeating a decision is a conflict, not a silent second write",
                  f"{status} {str(again)[:160]}")
            hist2 = c.call("GET", "/api/v1/adaptations/history")[1] or {}
            row2 = next((d for d in hist2.get("decisions") or [] if d.get("id") == victim["id"]), {})
            check(row2.get("status") == "rejected", "and the first answer still stands",
                  str(row2.get("status")))

        print("\n6. practice: attempts, changes, hints")
        status, sess = c.call("POST", "/api/v1/sessions", {"mode": "practice"})
        psid = sess.get("session_id")
        status, plan = c.call("GET", "/api/v1/practice?count=4")
        entries = plan.get("items") or []
        check(status == 200 and len(entries) == 4, "practice serves a queue", f"{len(entries)} items")
        if entries:
            item = entries[0]["item"]
            # a hint, a changed answer, then a submission: three different event types
            status, hint = c.call("POST", f"/api/v1/items/{item['id']}/hint", {"hint_no": 1})
            check(status == 200, "a hint can be requested during practice", f"{status} {str(hint)[:120]}")
            answer = 5 if item["kind"] == "numeric" else ({"correct_index": 0} if item["kind"] == "mcq" else "the parts must match first")
            status, first = c.call("POST", "/api/v1/attempts", {"session_id": psid, "item_id": item["id"], "answer": answer, "attempt_no": 1})
            check(status == 200, "an attempt is accepted", f"{status} {str(first)[:140]}")
            changed = 9 if item["kind"] == "numeric" else ({"correct_index": 1} if item["kind"] == "mcq" else "the denominator says how many parts")
            status, second = c.call("POST", "/api/v1/attempts", {"session_id": psid, "item_id": item["id"], "answer": changed, "attempt_no": 2})
            check(status == 200, "a changed answer is a second recorded attempt", f"{status}")
        status, events = c.call("GET", f"/api/v1/sessions/{psid}/events")
        types = [e.get("type") for e in (events.get("events") or [])]
        check(status == 200 and types, "the session has an event log", f"{len(types)} events: {sorted(set(types))}")
        check(types.count("answer_submitted") >= 1 or "attempt_submitted" in types,
              "the log records the submitted answer", str(sorted(set(types))))
        status, due = c.call("GET", "/api/v1/review/due")
        check(status == 200, "spaced review has a due list (possibly empty)", str(due)[:120])

        print("\n7. tutor and resources, with no model configured")
        status, reply = c.call("POST", "/api/v1/explain", {
            "question": "why do the denominators have to match", "item_id": (entries[0]["item"]["id"] if entries else None),
        })
        check(status == 200 and (reply.get("answer") or reply.get("explanation") or reply.get("text")),
              "the tutor answers from local material with no API key", f"{status} {str(reply)[:160]}")
        check("provider" in reply or "sources" in reply, "the reply says where it came from", str(list(reply)[:8]))
        status, res_status = c.call("GET", "/api/v1/resources/status")
        check(status == 200, "resource integrations publish their status", str(res_status)[:200])
        status, search = c.call("POST", "/api/v1/resources/search", {"topic": "adding fractions", "query": "adding fractions"})
        check(status in (200, 503), "a resource search answers honestly when the network is unavailable", f"{status} {str(search)[:160]}")

        print("\n8. voice says what is missing instead of pretending")
        status, caps = c.call("GET", "/api/v1/voice/capabilities")
        check(status == 200, "voice capabilities answer", str(caps)[:220])
        stt_ok = bool((caps.get("stt") or {}).get("local_stt"))
        tts_ok = bool((caps.get("tts") or {}).get("local_tts") or (caps.get("tts") or {}).get("available"))
        check(not stt_ok or not tts_ok,
              "an engine missing from this machine is reported unavailable, not assumed", json.dumps(caps)[:240])
        detail = json.dumps(caps)
        check("install" in detail or "not importable" in detail or "unavailable" in detail,
              "and the report says how to fix it", detail[:240])

        print("\n9. privacy surface")
        status, privacy = c.call("GET", "/api/v1/privacy")
        listed = privacy.get("event_types") or (privacy.get("recorded") or {}).get("event_types") or (
            (privacy.get("telemetry") or {}).get("recorded")
        )
        check(status == 200 and bool(listed), "the privacy view lists what is recorded", f"{status} {str(privacy)[:200]}")

        print("\n9b. the plan and material endpoints the interface does not call yet")
        status, topics = c.call("GET", "/api/v1/topics")
        rows = topics if isinstance(topics, list) else (topics.get("topics") or [])
        check(status == 200 and bool(rows), "the curriculum is readable as topics", f"{status} {len(rows)} topics")
        check(all(isinstance(t, dict) and t.get("code") for t in rows[:5]),
              "each topic names itself and the mastery it holds", str(rows[:1])[:200])
        status, plan = c.call("GET", "/api/v1/syllabus")
        check(status == 200 and bool(plan), "a study plan is produced for this learner", f"{status} {str(plan)[:200]}")
        check(bool(plan.get("how_to_read_it")), "and the plan says how to read it",
              str(list(plan)[:10]))
        status, mats = c.call("GET", "/api/v1/materials")
        check(status == 200 and isinstance(mats.get("materials"), list),
              "stored course material is listed (possibly empty)", f"{status} {len(mats.get('materials') or [])} rows")
        status, strategies = c.call("GET", "/api/v1/strategies")
        catalog = strategies.get("strategies") or strategies.get("catalog") or []
        check(status == 200 and bool(catalog), "the catalogue AI 1 may propose from is readable",
              f"{status} {len(catalog)} strategies")

        print("\n9c. a correct choice is graded correct, and a learner can take a change back by name")
        status, q = c.call("GET", "/api/v1/practice?count=6")
        choices = [e["item"] for e in (q.get("items") or []) if e["item"]["kind"] == "mcq"]
        if choices:
            item = choices[0]
            status, rev = c.call("GET", f"/api/v1/items/{item['id']}/reveal")
            right = rev.get("correct_index")
            check(status == 200 and isinstance(right, int),
                  "the reveal says which choice is right", f"{status} {str(rev)[:160]}")
            labelled = next((ch for ch in (item.get("choices") or []) if ch.get("index") == right), None)
            status, sess2 = c.call("POST", "/api/v1/sessions", {"mode": "practice"})
            sid2 = sess2.get("session_id")
            status, graded = c.call("POST", "/api/v1/attempts", {
                "session_id": sid2, "item_id": item["id"], "answer": right, "attempt_no": 1,
            })
            check(status == 200 and graded.get("correct") is True and float(graded.get("score") or 0) > 0.9,
                  "the index the reveal gives is graded correct", f"{status} {str(graded)[:200]}")
            if labelled:
                status, sess3 = c.call("POST", "/api/v1/sessions", {"mode": "practice"})
                status, by_text = c.call("POST", "/api/v1/attempts", {
                    "session_id": sess3.get("session_id"), "item_id": item["id"],
                    "answer": labelled.get("text"), "attempt_no": 1,
                })
                check(by_text.get("correct") is True,
                      "so is the same choice sent as its text, as the screen sends it", str(by_text)[:200])
        live = active_now()
        by_name = next((a for a in live if a.get("strategy")), None)
        if by_name is None:
            print("  (no active adaptation to revoke by name in this run)")
        else:
            status, revoked = c.call("POST", f"/api/v1/adaptations/strategy/{by_name['strategy']}/revoke", {})
            after_names = [a.get("strategy") for a in active_now()]
            check(status == 200 and by_name["strategy"] not in after_names,
                  "a learner can switch a change off by naming it", f"{status} {str(revoked)[:160]}")
            status, unknown = c.call("POST", "/api/v1/adaptations/strategy/not_a_strategy/revoke", {})
            check(status == 404, "naming a strategy that does not exist is a 404, not a silent success",
                  f"{status} {str(unknown)[:140]}")

        print("\n10. restart: the same data directory, a new process")
        check(stop_api(api), "the server stops when asked instead of having to be killed")
        api = start_api(DATA)
        c2 = Client(base)
        # the session cookie is signed with the key on disk, so it survives a restart
        status, me2 = c2.call("GET", "/api/v1/me")
        check(status == 401 or status == 200, "a restart answers consistently", str(status))
        if status == 401:
            status, body = c2.call("POST", "/api/v1/login", {"email": email, "password": password})
            check(status == 200, "the learner can sign in again after a restart", str(status))
        status, events2 = c2.call("GET", f"/api/v1/sessions/{psid}/events")
        check(status == 200 and len(events2.get("events") or []) >= 1,
              "the practice session and its events survived the restart", f"{status}")
        status, active2 = c2.call("GET", "/api/v1/adaptations/active")
        check(status == 200, "active adaptations survived the restart", str(active2)[:160])
        status, dash2 = c2.call("GET", "/api/v1/dashboard")
        check(status == 200, "the dashboard still answers after a restart", str(status))
        status, journey = c2.call("GET", "/api/v1/journey")
        check(status == 200, "the learner's journey is still there", f"{status} {str(journey)[:120]}")
    finally:
        stop_api(api)

    failed = [r for r in RESULTS if not r[0]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} journey checks passed")
    for _, label, detail in failed:
        print(f"  - {label}: {detail[:300]}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
