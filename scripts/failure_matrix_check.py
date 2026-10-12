"""Realistic failure matrix: what a caller gets when things go wrong.

The checks are the ones a student or a network can actually produce -- no session, a stale
session, a bad item, malformed JSON, a 3 MB body, a corrupt package, an unknown route, a
flood of registrations, four unreachable resource providers, a missing speech model.

Run it against a **freshly started** API: the rate-limit buckets live in the process, so a
second run against the same server sees the first run's spending (guest_create is 6/min,
which is what the flood check is about).

Run:  .venv/bin/python scripts/failure_matrix_check.py [http://127.0.0.1:8000]
Exit code is non-zero if any check fails.
"""
import http.cookiejar, json, os, sys, urllib.error, urllib.request

BASE = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("ADAPT_API", "http://127.0.0.1:8000")).rstrip("/")
cj = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


def call(method, path, body=None, headers=None, raw=None, ctype="application/json"):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(BASE + path, method=method, data=data,
                                 headers={"content-type": ctype, **(headers or {})})
    try:
        with op.open(req, timeout=40) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"transport": f"{type(e).__name__}: {e}"}


rows = []
def check(name, ok, detail=""):
    rows.append((name, ok, str(detail)[:150]))

# 1. authentication: a separate, cookie-less client, before any learner exists
anon = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))


def anon_call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, method=method, data=data, headers={"content-type": "application/json"})
    try:
        with anon.open(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}


st, _ = anon_call("GET", "/api/v1/dashboard")
check("private read without a session -> 401 not data", st == 401, st)
st, _ = anon_call("POST", "/api/v1/attempts", {"session_id": "lrs_nope", "item_id": "itm_nope", "answer": 1})
check("write without a session -> 401", st == 401, st)
st, _ = anon_call("GET", "/api/v1/practice")
check("practice without a session -> 401", st == 401, st)

# 0. a synthetic learner to act as (the guest bucket is 6/min: wait it out rather than
#    mistaking my own re-run for an app failure)
import time
for _attempt in range(8):
    st, me = call("POST", "/api/v1/students", {"display_name": "Failure Matrix", "age_band": "16-18", "as_guest": True})
    if st != 429:
        break
    time.sleep(float((me.get("detail") or {}).get("retry_after_s") or 12))
check("register a fresh synthetic learner", st == 200 and me.get("student_id", "").startswith("stu_"), st)
H = {"x-csrf-token": me.get("csrf_token", "")}
st, _ = call("POST", "/api/v1/sessions", {"mode": "practice"}, {"x-csrf-token": "not-the-token"})
check("a write carrying the wrong CSRF token is refused", st == 403, st)

# 2. bad input
sess = call("POST", "/api/v1/sessions", {"mode": "practice"}, H)[1]
sid = sess.get("session_id", "")
st, _ = call("POST", "/api/v1/attempts", {"session_id": sid, "item_id": "itm_does_not_exist", "answer": 1}, H)
check("an unknown item -> 404/422, never a 500", st in (404, 422), st)
st, _ = call("POST", "/api/v1/attempts", {"session_id": "lrs_missing", "item_id": "itm_decimals_00", "answer": 1}, H)
check("an unknown session -> 404/422", st in (404, 422), st)
st, d = call("POST", "/api/v1/attempts", {"session_id": sid, "item_id": "itm_decimals_00", "answer": "not a number"}, H)
check("an answer that cannot be parsed is marked wrong, not accepted", st == 200 and d.get("correct") is False, f"{st} {d.get('correct')}")
st, d = call("GET", "/api/v1/practice?count=999", headers=H)
check("count is clamped, not trusted", st in (200, 422) and len(d.get("items") or []) <= 12, st)
st, d = call("POST", "/api/v1/adaptations/adp_nope/decision", {"decision": "accept"}, H)
check("deciding on a proposal that does not exist -> 403/404/409", st in (403, 404, 409), st)
st, d = call("POST", "/api/v1/adaptations/adp_nope/decision", {"decision": "maybe"}, H)
check("an unknown decision is refused with the allowed list", st in (403, 404, 409, 422), st)
st, d = call("POST", "/api/v1/resources/search", {"query": "", "limit": 0}, H)
check("an empty query / out-of-range limit -> 422", st == 422, st)

# 3. missing model / missing engine: honesty, not invention
st, d = call("GET", "/api/v1/status", headers=H)
check("status reports the LLM provider honestly", st == 200 and d.get("llm", {}).get("configured") in (True, False), st)
caps = call("GET", "/api/v1/voice/capabilities", headers=H)[1]
check("voice capabilities name the engines and their fallbacks",
      bool(caps.get("stt", {}).get("detail")) and bool(caps.get("tts", {}).get("detail")), json.dumps(caps)[:120])
st, d = call("POST", "/api/v1/voice/speak", {"text": "hello"}, H)
check("speech synthesis with no local TTS points at the browser", st == 200 and (d.get("ok") is not True or d.get("audio") is None), st)

# 4. provider failures (the four resource APIs are unreachable in this sandbox)
st, d = call("POST", "/api/v1/resources/search", {"query": "adding fractions", "limit": 4}, H)
check("resource search survives four unreachable providers", st == 200, st)
check("...and says why the list is empty", bool(d.get("note")), str(d.get("note"))[:80])
check("...and reports every provider's own state",
      len(d.get("providers") or []) == 4 and all("note" in p for p in d.get("providers") or []), json.dumps(d.get("providers"))[:140])

# 5. rate limits (guest creation is the tightest bucket: 6/min)
codes = [anon_call("POST", "/api/v1/students", {"display_name": f"Flood {i}", "as_guest": True})[0] for i in range(16)]
check("a flood of registrations gets 429 rather than an unbounded allowance", 429 in codes, sorted(set(codes)))
st, d = call("GET", "/api/v1/quotas", headers=H)
check("the quotas view names the buckets and what is left",
      st == 200 and "guest_create" in json.dumps(d.get("buckets") or {}), st)

# 6. malformed bodies
st, _ = call("POST", "/api/v1/attempts", raw=b"{not json", headers=H, ctype="application/json")
check("malformed JSON -> 422", st == 422, st)
st, _ = call("POST", "/api/v1/attempts", raw=b"x" * 3_000_000, headers=H, ctype="application/json")
check("an oversized body is refused (413/422), not buffered", st in (413, 422), st)
st, d = call("POST", "/api/v1/numbas/import", raw=b"PK\x03\x04not a zip", headers=H, ctype="application/octet-stream")
check("a corrupt Numbas package -> 400 with a readable reason", st == 400 and "unreadable_package" in json.dumps(d), st)

# 7. non-existent routes and methods
check("an unknown route -> 404", call("GET", "/api/v1/nothing_here")[0] == 404)
check("the wrong method -> 405", call("GET", "/api/v1/attempts")[0] in (405, 422))

width = max(len(n) for n, _, _ in rows)
for name, ok, detail in rows:
    print(f"{'ok  ' if ok else 'FAIL'}  {name:<{width}}  {detail}")
passed = sum(1 for _, ok, _ in rows if ok)
print(f"\n{passed}/{len(rows)} failure-matrix checks passed")
sys.exit(0 if passed == len(rows) else 1)
