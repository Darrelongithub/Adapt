# Known gaps — unverified claims

Every line here is something the code assumes but nobody has executed in the
environment it claims to be for. They are listed rather than implied: an
unverified acceptance line that nobody wrote down becomes a false claim in a
month.

Last updated: increment 2 (tenancy and RLS).

## 1. `docker compose up` has never been run

**What is claimed:** the stack in `engine/docker-compose.yml` starts, the
`migrate` service bootstraps the two roles and runs `alembic upgrade head`, and
`curl -s localhost:8000/v1/health` answers.

**What was actually verified:** everything except the container run. The same
migration was applied to a real PostgreSQL 16.2 server driven by the test
fixture (`SELECT version()` → `PostgreSQL 16.2 ... Red Hat 10.2.1`), and the
same role bootstrap (`scripts/bootstrap_db.py` uses the same
`ensure_roles`) was exercised by the test suite. What is unverified is the
composition itself: image build, `depends_on` conditions, service DNS names,
and whether `migrate` exits 0 before `api` starts.

**Why:** the build environment has no Docker binary, no daemon socket, and no
route to a package registry (`apt-get update` cannot reach `deb.debian.org`).

**What closes it:** on a machine with Docker:

```sh
cd engine && docker compose up -d --build
docker compose ps                       # migrate must have exited 0
curl -s localhost:8000/v1/health        # 200, db:true, redis:true
docker compose down -v
```

## 2. The image has never been built, so Python 3.12 has never run this code

**What is claimed:** `engine/Dockerfile` builds on `python:3.12-slim`,
`pip install .` succeeds, and `python -m adapt_engine.main` serves.

**What was actually verified:** `pip install .` (non-editable) into a clean
virtualenv, importing from `site-packages`, the entrypoint failing closed with
a non-zero exit, and the full suite green against the installed copy — all on
**Python 3.11.2**.

**Why:** no Docker, and no 3.12 interpreter obtainable — `uv python install
3.12` fails at `release-assets.githubusercontent.com` (TLS `UnknownIssuer`,
host outside the sandbox allowlist), and there are no C headers in the image
(`openssl/ssl.h`, `zlib.h` absent), so a source build could not run pip.

**What closes it:** `docker build engine/` on any machine with Docker, then
`docker run --rm <image> python -m pytest` with the suite mounted, and record
`python -V` in the output. `pyproject.toml` declares `requires-python >= 3.12`,
so a 3.11 install is refused — that refusal is the only 3.12-specific
behaviour observed so far.

## 3. Redis has never been reachable

`/v1/health` reports `redis: false` in every run so far, because no Redis was
available. The probe is bounded and its failure mode is tested; a successful
`PING` path is not. Closes with gap 1 (`docker compose up` brings `redis:7`).

## 4. ~~No HTTP surface is tenant-scoped yet~~ — closed by increment 3

`/v1/learners` and `/v1/learners/{learner_id}` are the first routes to run under
`tenant_transaction`, reached through an API key. `tests/test_auth_http.py`
covers the wiring: a key for tenant A gets a 404 for tenant B's learner id (not
403 — that would confirm the id exists elsewhere), and a learner created with
`tenant_id` in the body still lands in the caller's tenant. The mechanism was
already exercised directly in increment 2; what was untested was the path from
`Authorization` header to `SET LOCAL adapt.tenant_id`.

## 5. The error-code guard now passes, and only because the doc says so

Rule (c) of `scripts/check_openapi.py` parses **every** row of the §6 table,
including the `any other | unmapped_status` sentinel, so the guard exits 0 with
16 documented codes at docs ref `9d1739d`. It is still absolute: remove the
sentinel row and the guard fails (`test_entrypoint_fails_when_the_sentinel_row_is_removed`).
There is no whitelist in the checker; the document is the only allowlist.

## 6. The rate limiter is per process, not per cluster

`FailureLimiter` is in-memory (DECISIONS.md §6). With N replicas, an attacker
gets N × 20 failures per minute per IP, and a restart clears every window.
Redis is not reachable here (gap 3), so a shared limiter could not be tested.
The check runs before the key is validated, so a shared client IP (an office
NAT, a CI runner) that trips the window also blocks *valid* keys from that IP
until the window drains — fail-closed, and worth revisiting when the limiter
becomes per-key as well as per-IP. **What closes it:** a Redis-backed window,
once gap 3 closes and the credit ledger needs the same infrastructure.

## 7. Keys cannot be rotated on a schedule and never expire

`api_keys` has `revoked_at` but no `expires_at`, no rotation route and no
"issue → overlap → revoke old" workflow. Revocation is immediate
(`test_revoked_key_fails_immediately_not_at_cache_expiry`), which is the
property that matters for a compromised key, but nothing prompts a tenant to
rotate a key that is merely old.

## 8. Issuing and revoking keys is admin-only over HTTP, and there is no audit trail

`POST /v1/admin/keys` requires the `admin` scope, and the key it issues can
itself carry `admin` — so authority is transitive and not separable. Key
issuance and revocation do not write to `audit_log`: the table exists and is
tenant-scoped, but its first writer arrives with the decision/observation
pipeline in a later increment. Until then, a tenant cannot see who issued or
revoked a key.

## 9. `last_used_at` granularity is a minute, and it is coarse on purpose

The debounce means `last_used_at` can lag reality by up to 60 seconds. It is a
"was this key in use" signal, not an audit log. See DECISIONS.md §5.
