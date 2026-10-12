"""Does the tutor answer differ for two learners who are in different states?

"AI 2 answers with learner-profile adaptation" is a claim about a *difference*: one question,
two learners, different evidence about them. This drives two synthetic learners to opposite
profiles through the public API -- one answers every placement question correctly (using the
app's own reveal so the answers are really right), the other answers them all wrong -- and then
diffs what each is told.

Run it against a **freshly started** API (the rate-limit buckets are per process), and note that
with no model key this exercises the *local* provider: the adaptation being checked is the
app's, not the model's.

Run:  .venv/bin/python scripts/adaptation_check.py [http://127.0.0.1:8000]
Exit code is non-zero when the two profiles get the same answer.
"""
from __future__ import annotations

import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("ADAPT_API", "http://127.0.0.1:8000")).rstrip("/")
QUESTION = "why do denominators have to match before I can add fractions"


def client():
    cj = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


def call(op, method, path, body=None, headers=None):
    req = urllib.request.Request(
        BASE + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"content-type": "application/json", **(headers or {})},
    )
    try:
        with op.open(req, timeout=60) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}


def learner(name: str):
    op = client()
    st, me = call(op, "POST", "/api/v1/students", {"display_name": name, "age_band": "16-18", "as_guest": True})
    if st != 200:
        print(f"{name}: registration answered {st}: {me}")
        sys.exit(2)
    h = {"x-csrf-token": me["csrf_token"]}
    call(op, "POST", "/api/v1/me/onboarding", {"weekly_minutes": 120, "session_minutes": 25, "confidence": 2}, h)
    return op, h


def place(op, h, *, correct: bool) -> list[str]:
    st, diag = call(op, "GET", "/api/v1/assessment/diagnostic")
    items = [e["item"] for e in (diag.get("items") or [])]
    st, sess = call(op, "POST", "/api/v1/sessions", {"mode": "diagnostic"}, h)
    sid = sess["session_id"]
    topics = []
    for i, item in enumerate(items):
        topics.append(item["topic_id"])
        if correct:
            st, rev = call(op, "GET", f"/api/v1/items/{item['id']}/reveal", None, h)
            if item["kind"] == "mcq":
                # the client contract for a choice item is a bare index (or the choice text),
                # not the reveal's own key name
                answer: object = int(rev.get("correct_index") or 0)
            elif item["kind"] == "numeric":
                answer = rev.get("answer")
            else:
                answer = rev.get("answer") or "the parts have to be the same size"
        else:
            if item["kind"] == "mcq":
                st_r, rev = call(op, "GET", f"/api/v1/items/{item['id']}/reveal", None, h)
                n = len(item.get("choices") or [])
                answer = (int(rev.get("correct_index") or 0) + 1) % max(1, n)  # a wrong one, deliberately
            else:
                answer = 999 if item["kind"] == "numeric" else "dunno"
        st_a, res = call(op, "POST", "/api/v1/attempts", {"session_id": sid, "item_id": item["id"], "answer": answer, "attempt_no": i + 1}, h)
        if correct and i < 3:
            print(f"    correct answer submitted: kind={item['kind']} -> correct={res.get('correct')} score={res.get('score')} (reveal gave {str(rev)[:60]})")
    call(op, "POST", f"/api/v1/sessions/{sid}/finish", {})
    call(op, "POST", f"/api/v1/analyze?session_id={sid}", {}, h)
    return topics


def ask(op, h, topic_id: str | None) -> dict:
    body = {"question": QUESTION}
    if topic_id:
        body["topic_id"] = topic_id
    st, out = call(op, "POST", "/api/v1/explain", body, h)
    out["_status"] = st
    return out


strong_op, strong_h = learner("Adapt Strong")
weak_op, weak_h = learner("Adapt Weak")
strong_topics = place(strong_op, strong_h, correct=True)
weak_topics = place(weak_op, weak_h, correct=False)
topic = strong_topics[0] if strong_topics else None

a = ask(strong_op, strong_h, topic)
b = ask(weak_op, weak_h, topic)

def shape(out: dict) -> dict:
    return {
        "status": out.get("_status"),
        "level": out.get("level"),
        "intent": out.get("intent"),
        "adaptations_applied": out.get("adaptations_applied"),
        "next_action": out.get("next_action"),
        "sections": list((out.get("sections") or {}).keys()) if isinstance(out.get("sections"), dict) else (out.get("sections") or [])[:3],
        "answer_words": len(str(out.get("answer") or "").split()),
        "sources": len(out.get("sources") or []),
    }

print("strong learner :", json.dumps(shape(a)))
print("weak learner   :", json.dumps(shape(b)))
print("same answer text:", (a.get("answer") or "") == (b.get("answer") or ""))
differ = {k for k in shape(a) if shape(a)[k] != shape(b)[k]}
print("fields that differ:", sorted(differ))
print("VERDICT:", "adapts to the profile" if differ else "identical answer for both profiles")
sys.exit(0 if differ else 1)
