"""``adapt`` -- the whole backend from a terminal.

Typer-based so ``--help`` is self-documenting. The commands a judge needs:

  adapt init            create the schema, seed the curriculum, report encryption
  adapt doctor            what is installed, what is missing, what will fall back
  adapt demo              one full loop on a seeded learner, printed as a story
  adapt analyze --for X   run AI 1 over a learner's evidence
  adapt proposals / decide  read and answer adaptation proposals
  adapt export-numbas / import-numbas
  adapt train-bkt         refit cohort BKT parameters by EM
  adapt serve             run uvicorn (API only)
  adapt host              one address for a room on the same wifi: API + web app,
                          the LAN host allowlisted, the microphone caveat printed
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Sequence
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import typer
from sqlalchemy import select

from .config import MODEL_ALIAS_SUFFIX, get_settings

app = typer.Typer(
    name="adapt",
    help="ADAPT: adaptive learning with a Learning Intelligence and an Educational Intelligence.",
    no_args_is_help=True,
    add_completion=False,
)
db_app = typer.Typer(help="database lifecycle and inspection", no_args_is_help=True)
voice_app = typer.Typer(help="speech engine checks", no_args_is_help=True)
config_app = typer.Typer(help="console-only provider credentials and budgets", no_args_is_help=True)
app.add_typer(db_app, name="db")
app.add_typer(voice_app, name="voice")
app.add_typer(config_app, name="config")


def _settings():
    return get_settings()


def _echo(obj: Any) -> None:
    typer.echo(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def _run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# init / doctor
# --------------------------------------------------------------------------- #


@app.command("init")
def init_cmd(
    seed_demo: bool = typer.Option(False, "--demo", help="also create one learner with a real evidence history"),
    reset: bool = typer.Option(False, "--reset", help="drop all tables first (destructive)"),
) -> None:
    """Create the schema and seed the curriculum."""
    from .db.engine import Database
    from .db.init_db import init

    st = _settings()
    db = Database(st)
    if reset:
        db.drop_all()
    report = init(st, seed_demo=seed_demo, db=db)
    _echo(report.as_dict())


@db_app.command("counts")
def counts_cmd() -> None:
    from sqlalchemy import func, select

    from .db.engine import get_db
    from .db.init_db import counts as _counts

    db = get_db()
    with db.session() as s:
        out = _counts(s)
        from .db.models import Attempt, LearnerEvent, Student

        out["students"] = int(s.execute(select(func.count()).select_from(Student)).scalar_one())
        out["attempts"] = int(s.execute(select(func.count()).select_from(Attempt)).scalar_one())
        out["learner_events"] = int(s.execute(select(func.count()).select_from(LearnerEvent)).scalar_one())
    _echo(out)


@db_app.command("purge-student")
def purge_student(
    student_id: str = typer.Option(..., "--student-id"),
    confirm: bool = typer.Option(False, "--confirm", help="required: this deletes learner history for real"),
) -> None:
    """Hard-delete one learner's rows (the 'right to be forgotten' path)."""
    if not confirm:
        typer.secho("refusing without --confirm; this is irreversible", fg=typer.colors.RED)
        raise typer.Exit(code=2)
    from sqlalchemy import select

    from .db.engine import get_db
    from .ai.monitor.models import LearnerCheckIn
    from .curriculum.models import ApprovedGuideline, ScanCacheEntry
    from .db.models import (
        ActiveAdaptation,
        AdaptationProposal,
        Attempt,
        ChatMessage,
        EvidenceRecord,
        Hypothesis,
        LearnerEvent,
        LearningSession,
        ProfileSnapshot,
        RetentionCheck,
        RetentionSchedule,
        StrategyStat,
        Student,
        StudentPreference,
        VoiceTurn,
    )

    db = get_db()
    deleted: dict[str, int] = {}
    with db.session() as s:
        ids = [row[0] for row in s.execute(select(LearningSession.id).where(LearningSession.student_id == student_id)).all()]
        for model, attr in (
            (LearnerCheckIn, "student_id"),
            (ApprovedGuideline, "student_id"),
            (LearnerEvent, "student_id"),
            (Attempt, "student_id"),
            (ChatMessage, "student_id"),
            (VoiceTurn, "student_id"),
            (EvidenceRecord, "student_id"),
            (Hypothesis, "student_id"),
            (AdaptationProposal, "student_id"),
            (ActiveAdaptation, "student_id"),
            (ProfileSnapshot, "student_id"),
            (RetentionCheck, "student_id"),
            (RetentionSchedule, "student_id"),
            (StrategyStat, "student_id"),
            (StudentPreference, "student_id"),
        ):
            n = s.execute(model.__table__.delete().where(getattr(model, attr) == student_id)).rowcount or 0
            deleted[model.__tablename__] = int(n)
        if ids:
            n = s.execute(LearningSession.__table__.delete().where(LearningSession.id.in_(ids))).rowcount or 0
            deleted["learning_sessions"] = int(n)
        s.execute(Student.__table__.delete().where(Student.id == student_id))
        # The learner's last scan candidates are keyed by their ids (payloads
        # encrypted); shared syllabus scans carry no learner reference and stay.
        n = s.execute(
            ScanCacheEntry.__table__.delete().where(ScanCacheEntry.key.like(f"last:{student_id}:%"))
        ).rowcount or 0
        deleted["scan_cache"] = int(n)
        deleted["students"] = 1
        s.commit()
    _echo({"purged": student_id, "rows_deleted": deleted})


@db_app.command("verify-encryption")
def verify_encryption() -> None:
    """Read the file header directly: is learner data actually unreadable?"""
    st = _settings()
    path = Path(st.db_file)
    if not path.exists():
        typer.secho(f"no database at {path} yet (run `adapt init`)", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)
    head = path.read_bytes()[:16]
    plaintext = head == b"SQLite format 3\x00"
    from sqlalchemy import text

    from .db.engine import get_db

    db = get_db()
    readable = None
    try:
        import sqlite3 as _sql

        probe = _sql.connect(path)
        try:
            readable = probe.execute("select count(*) from students").fetchone()[0]
        finally:
            probe.close()
    except Exception as exc:
        readable = f"refused: {type(exc).__name__}"
    # and confirm the app can read it with the key
    with db.session() as s:
        n = s.execute(text("select count(*) from students")).scalar_one()
    _echo(
        {
            "path": str(path),
            "header_is_plaintext_sqlite": plaintext,
            "opened_by_plain_sqlite": readable,
            "opened_by_adapt": int(n),
            "driver": db.driver.name,
            "field_encryption": bool(db.driver.name == "sqlite" and n >= 0),
            "verdict": (
                "file-level encryption active"
                if not plaintext
                else "header is plaintext: SQLCipher not active -- encrypted columns still hold, but install sqlcipher3-binary for whole-file protection"
            ),
        }
    )


def _database_check(st) -> tuple[bool, str, str] | None:
    """Can the configured policy actually open the database in this data dir?

    None when there is nothing on disk yet (a first run has no opinion to offer). Otherwise a
    ``(ok, detail, fix)`` triple: the engine refuses a file written under the other policy with a
    sentence naming the file and the policy, and that sentence is the detail here.
    """
    from .db.engine import Database, _on_disk_format

    path = Path(st.db_file)
    on_disk = _on_disk_format(path)
    if on_disk in ("missing", "empty", "unknown"):
        return None
    try:
        db = Database(st)
    except RuntimeError as exc:
        return (False, str(exc), "set ADAPT_DB_ENCRYPTION to the policy that wrote the file, or point ADAPT_DATA_DIR at a new directory")
    try:
        from sqlalchemy import inspect as _inspect

        if not _inspect(db.engine).has_table("students"):
            return (True, f"{path.name}: open, no schema yet (run: alembic upgrade head)", "")
        return (True, f"{path.name}: open, {db.student_count()} learner(s), driver={db.driver.name}", "")
    except Exception as exc:  # a database that opens but cannot be read is the same problem to an operator
        return (False, f"{path.name}: {type(exc).__name__}: {str(exc)[:160]}", "see docs/UNVERIFIED.md on encryption policy changes")
    finally:
        db.dispose()


def _chain_steps(task: str) -> list[dict[str, Any]]:
    """The merged call's steps, or an empty list when the chain is declared elsewhere.

    Lazy and defensive on purpose: ``adapt doctor`` is the screen an operator runs when
    something is wrong, so a chain module that cannot be imported has to *say nothing* there
    rather than take the whole report down with it.
    """
    try:
        from .subjects import placement as PL

        if task == PL.PLACEMENT_CHAIN.purpose:
            return PL.chain_summary()["steps"]
    except Exception:  # pragma: no cover - a broken import is reported by its own check
        return []
    return []


@app.command("doctor")
def doctor(as_json: bool = typer.Option(False, "--json")) -> None:
    """Report what is installed, what is optional, and what will fall back."""
    st = _settings()
    lines: list[str] = []
    data: dict[str, Any] = {}

    def check(name: str, ok: bool, detail: str, fix: str = "") -> None:
        data[name] = {"ok": bool(ok), "detail": detail, "fix": fix}
        mark = "ok  " if ok else "WARN"
        lines.append(f"[{mark}] {name}: {detail}" + (f"  -> {fix}" if (fix and not ok) else ""))

    check("python", sys.version_info >= (3, 11), sys.version.split()[0], "use Python 3.11+ (3.12 recommended; Kokoro needs <3.13)")
    for pkg, why, fix in [
        ("sqlalchemy", "database toolkit", "pip install 'adapt-backend[dev]'"),
        ("alembic", "schema migrations", "pip install alembic"),
        ("httpx", "external API clients + Gemini", "pip install httpx"),
        ("numpy", "learning algorithms", "pip install numpy"),
        ("sqlcipher3", "whole-file database encryption", "pip install sqlcipher3-binary (else field-level only)"),
        ("cryptography", "field-level AES-GCM + HKDF keys", "pip install cryptography"),
        ("argon2", "password hashing", "pip install argon2-cffi"),
        ("faster_whisper", "local speech-to-text", "pip install 'adapt-backend[voice]'"),
        ("kokoro", "local text-to-speech", "pip install kokoro torch soundfile; apt install espeak-ng"),
        ("webrtcvad", "tighter endpointing", "pip install webrtcvad (energy VAD is the fallback)"),
    ]:
        try:
            import importlib.metadata as _meta

            mod = __import__(pkg)
            try:
                ver = _meta.version("argon2-cffi" if pkg == "argon2" else pkg.replace("_", "-"))
            except _meta.PackageNotFoundError:
                ver = getattr(mod, "__version__", "installed")
            check(pkg, True, str(ver))
        except Exception as exc:
            check(pkg, False, f"not importable ({type(exc).__name__})", fix)
    check(
        "gemini key",
        bool(st.gemini_api_key),
        "configured" if st.gemini_api_key else "no ADAPT_GEMINI_API_KEY: AI 1 uses template phrasing, AI 2 uses the local tutor",
        "export ADAPT_GEMINI_API_KEY=... (free tier)",
    )
    check(
        "youtube key",
        bool(st.youtube_api_key),
        "configured" if st.youtube_api_key else "no key: video search is skipped (the only provider with no quota-free alternative)",
        "optional: create a free YouTube Data API v3 key",
    )
    check("keyfile", Path(st.keyfile).exists() if st.keyfile else True, str(st.keyfile or "derived from ADAPT_DB_PASSPHRASE/generated at first run"))
    data_dir_ok = True
    try:
        Path(st.data_dir).mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        data_dir_ok = False
        check("data dir", False, f"{st.data_dir} not writable ({exc})", "set ADAPT_DATA_DIR")
    if data_dir_ok:
        check("data dir", True, str(st.data_dir))
        # A writable directory is not the same as a readable database: changing
        # ADAPT_DB_ENCRYPTION after a file exists (auto -> field-only, or the reverse) leaves a
        # database the configured driver cannot open. This is the screen whose whole job is to
        # say so before anyone meets it as a stack trace, and it used to report the directory as
        # fine while the app could not read a byte of what was in it.
        db_check = _database_check(st)
        if db_check is not None:
            check("database", db_check[0], db_check[1], db_check[2])
    from .voice import stt as STT
    from .voice import tts as TTS

    st_caps = STT.capabilities(st)
    check("stt", st_caps["local_stt"], st_caps["detail"], st_caps["fallback"])
    tts_caps = TTS.capabilities(st)
    check("tts", tts_caps["local_tts"], tts_caps["detail"], tts_caps["fallback"])
    cpu = os.cpu_count() or 1
    check("cpu for local speech", cpu >= 4, f"{cpu} cores; int8 Whisper 'base' is ~1-3x realtime on 4 cores", "set ADAPT_WHISPER_MODEL_SIZE=tiny on a small machine")
    free_ram = None
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.lower().startswith("memavailable"):
                free_ram = int(line.split()[1]) // 1024
    except Exception:
        pass
    if free_ram is not None:
        check("ram available", free_ram > 1500, f"{free_ram} MB free", "whisper tiny + kokoro together want ~1.5 GB free")
    tunnel = None
    for cmd in ("npx", "cloudflared", "lt"):
        from shutil import which

        if which(cmd):
            tunnel = cmd
            break
    check("tunnel for judges", bool(tunnel), tunnel or "neither localtunnel (npx lt) nor cloudflared found", "npm i -g localtunnel  |  brew install cloudflared")

    # -- what the model calls actually cost ---------------------------------- #
    # Four answers an operator needs before a demonstration, and the only place all four
    # appear together. Model *names* are printed here and nowhere on the HTTP surface
    # (brief §3): this is the console of the machine that holds the API key.
    #
    #   1. which model answers which class of work (routing.report);
    #   2. how big the static heads are, and which of them clear the provider's cache floor;
    #   3. which requests used to be two calls and are now one (the chains);
    #   4. how many requests may be in flight at once before the server says wait.
    from .ai import routing
    from .ai import static_context as SC
    from .security.ratelimit import build_realtime_guard

    routing_report = routing.report(st)
    for tier, row in routing_report["classes"].items():
        names_in_tier = row["tasks"]
        check(
            f"model tier: {tier}",
            True,
            f"{row['model']} -- {len(names_in_tier)} task(s)",
            "",
        )
    blocks = SC.sizes()
    floor = int(getattr(st, "gemini_context_cache_min_tokens", 1024) or 1024)
    cacheable = {name: row for name, row in blocks.items() if row["est_tokens"] >= floor}
    check(
        "static context",
        bool(getattr(st, "ai_context_cache", True)),
        (
            f"{len(blocks)} block(s); {len(cacheable)} above the {floor}-token cache floor "
            f"({', '.join(sorted(cacheable)) or 'none'})"
        ),
        "lower ADAPT_GEMINI_CONTEXT_CACHE_MIN_TOKENS if the provider allows smaller caches",
    )
    joins = {task: pol.joins for task, pol in routing.TASKS.items() if pol.joins}
    check(
        "merged calls",
        bool(getattr(st, "ai_single_call", True)),
        (
            "on: "
            + "; ".join(
                f"{task} replaces {len(purposes)} calls "
                f"({', '.join(purposes)}) with one"
                for task, purposes in joins.items()
            )
            if joins
            else "on: no table entry merges two purposes yet"
        ),
        "ADAPT_AI_SINGLE_CALL=0 restores the sequential path, for measurement",
    )
    guard = build_realtime_guard(st)
    check(
        "concurrency guard",
        True,
        (
            f"{guard.max_inflight} request(s) in flight per key, "
            f"{guard.burst_max} inside any {int(guard.window_s * 1000)} ms before it is called a loop"
        ),
        "raise ADAPT_REALTIME_MAX_INFLIGHT for a load test, not for a demo",
    )
    data["cost"] = {
        "routing": routing_report,
        "static_context": blocks,
        "cache_floor_tokens": floor,
        "chains": {
            # The table says *that* a purpose merges others; the chain module says what the
            # merged prompt actually asks for, step by step. Both, because a claim about
            # call counts that cannot be read back is a claim nobody can check.
            task: {"one_call": True, "replaces": list(purposes), "steps": _chain_steps(task)}
            for task, purposes in joins.items()
        },
        "realtime": guard.snapshot(),
    }
    if as_json:
        _echo(data)
    else:
        typer.echo("\n".join(lines))
        typer.echo(
            "\nADAPT runs on what is present: every WARN above has a fallback that keeps the demo "
            "going, it just changes what the status screen is able to claim."
        )


# --------------------------------------------------------------------------- #
# the learning loop
# --------------------------------------------------------------------------- #


@app.command("rebuild-model")
def rebuild_model(
    student_id: str | None = typer.Option(None, "--for", help="learner id (or use --all)"),
    all_learners: bool = typer.Option(False, "--all", help="rebuild every learner with evidence"),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="recompute and report the comparison, but leave the stored model alone",
    ),
) -> None:
    """Recompute a learner's model from the evidence log, and say whether it changed.

    This is the check the brief asks for made usable: ``equal: true`` means the stored model is
    exactly what a from-scratch fold of the log produces, which is what makes rebuilding safe to
    run after a bug fix, a parameter change or a restored backup. It is also the only thing that
    would *notice* a model that drifted from its own log.

    ``--dry-run`` reports the diff without storing the rebuilt model, for the case where the answer
    is "yes it drifted" and somebody wants to look before the version pointer moves.
    """
    from .services import build

    if not all_learners and not student_id:
        _echo({"error": "give --for <learner id>, or --all to rebuild every learner with evidence"})
        raise typer.Exit(code=2)
    svcs = build()
    store = svcs.state
    ids = [student_id] if not all_learners and student_id else []
    if all_learners:
        from .db.models import StateEvidence

        with svcs.db.session() as s:
            ids = sorted({row for (row,) in s.execute(select(StateEvidence.student_id)).all()})
    if not ids:
        _echo({"learners": [], "note": "nothing to rebuild: no evidence rows and no --for given"})
        raise typer.Exit(code=0)
    out: list[dict[str, Any]] = []
    for sid in ids:
        titles = store.title_map()
        if dry_run:
            from .state.model import build_model

            rows = store.evidence_rows(sid)
            rebuilt = build_model(rows, titles=titles)
            current = store.latest_version_row(sid)
            same = bool(current is not None and current.model == rebuilt)
            out.append(
                {
                    "learner_id": sid,
                    "version": None if current is None else current.version,
                    "evidence_count": len(rows),
                    "equal": same or current is None,
                    "note": (
                        "the stored model already equals a rebuild"
                        if same or current is None
                        else "the stored model differs from a rebuild; re-run without --dry-run to store the rebuild"
                    ),
                }
            )
        else:
            out.append(store.rebuild_model(sid, titles=titles))
    _echo({"learners": out})


@app.command("matrix")
def matrix_cmd() -> None:
    """Print the write-permission matrix: which module may write which domain.

    Printed rather than only asserted in tests because the matrix is the brief's "the AI proposes,
    code decides" rule as data, and an operator asking "could this component have written that row?"
    should be able to answer it from a shell without reading the source.
    """
    from .state.permissions import DOMAINS, MODULES, matrix, violations

    # Not a hard-coded list: the same generated grid the tests assert over, plus every (module,
    # domain) pair the matrix refuses, so the output cannot disagree with the enforcement.
    _echo({"matrix": matrix(), "refused": violations((m, d) for m in MODULES for d in DOMAINS)})


@app.command()
def analyze(
    student_id: str = typer.Option(..., "--for", help="learner id"),
    session_id: str | None = typer.Option(None, "--session"),
    no_llm: bool = typer.Option(False, "--no-llm", help="skip Gemini phrasing entirely"),
) -> None:
    """Run one Test -> Observe -> Understand -> Personalize cycle."""
    from .services import build

    svcs = build()

    async def go() -> dict[str, Any]:
        res = await svcs.ai1.analyze(student_id, session_id=session_id, use_llm=not no_llm)
        return res.as_dict()

    _echo(_run(go()))


@app.command()
def proposals(student_id: str = typer.Option(..., "--for"), include_decided: bool = typer.Option(False, "--all")) -> None:
    """List adaptation proposals (what AI 1 wants to change, and why)."""
    from .db.engine import get_db
    from .services import adaptations as AD

    with get_db().session() as s:
        out = {"pending": AD.pending(s, student_id)}
        if include_decided:
            out["decided"] = AD.history(s, student_id)
    _echo(out)


@app.command()
def decide(
    student_id: str = typer.Option(..., "--for"),
    proposal_id: str = typer.Option(..., "--proposal"),
    decision: str = typer.Option(..., "--decision", help="accept|reject|modify|later"),
    note: str = typer.Option("", "--note"),
    modify: list[str] = typer.Option([], "--modify", help="key=value knob override, repeatable"),
    learner_answered: bool = typer.Option(
        False, "--learner-answered", help="assert the learner gave this answer in front of you (for scripts, where nobody can be asked)"
    ),
) -> None:
    """Answer a proposal as the learner. This is the only way to turn a change on.

    A terminal is not a learner, so this refuses to invent an answer: at a keyboard it
    asks you to confirm the person was there, and with no terminal to ask it requires
    ``--learner-answered``. Either way the channel is written into the audit row, so a
    decision typed by an operator can never later be read as a learner's consent.
    """
    import sys

    from .db.engine import get_db
    from .services import adaptations as AD

    channel = "cli_attested"
    if not learner_answered:
        if not sys.stdin.isatty():
            typer.secho(
                "refused: no terminal to ask in, and a persistent change needs the learner's own answer. "
                "Re-run with --learner-answered only if the learner answered in front of you.",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(2)
        if not typer.confirm("Did the learner give this answer themselves, in front of you?"):
            typer.secho(
                "refused: the proposal stays pending, which is the honest state for an unanswered question.",
                fg=typer.colors.RED,
            )
            raise typer.Exit(2)
        channel = "cli_confirmed"

    mods: dict[str, Any] = {}
    for pair in modify:
        if "=" in pair:
            k, v = pair.split("=", 1)
            try:
                mods[k.strip()] = json.loads(v)
            except json.JSONDecodeError:
                mods[k.strip()] = v
    with get_db().session() as s:
        out = AD.decide(
            s,
            student_id=student_id,
            proposal_id=proposal_id,
            student_decision={
                "decision": decision,
                "proposal_id": proposal_id,
                "answered_by": "student",
                "answered_at": datetime.now(timezone.utc).isoformat(),
                "channel": channel,
            },
            note=note or None,
            modifications=mods,
        )
    _echo(out)


@app.command("profile")
def profile_cmd(student_id: str = typer.Option(..., "--for")) -> None:
    """Print the dynamic learner profile (the same payload AI 2 sees)."""
    from .db.engine import get_db
    from .services import build

    svcs = build()
    with get_db().session() as s:
        _echo(svcs.ai1.snapshot(s, student_id))


@app.command("journey")
def journey_cmd(student_id: str = typer.Option(..., "--for"), days: float = 90, tail: int = 30) -> None:
    """Print the learning journey as a timeline."""
    from .db.engine import get_db
    from .services import journey as J

    with get_db().session() as s:
        data = J.build(s, student_id, days=days, limit=10_000)
    for node in data["nodes"][-tail:]:
        typer.echo(f"{node['ts'][:19]}  {node['kind']:<11} {node['title']}")
        if node["detail"]:
            typer.echo(f"{'':<21}{node['detail']}")
    typer.echo(f"\n{data['summary']}")


@app.command("train-bkt")
def train_bkt(scope: str = typer.Option("global", "--scope"), min_learners: int = typer.Option(3, "--min-learners")) -> None:
    """Refit BKT parameters by EM on every recorded sequence (cohort prior)."""
    from .services import build

    svcs = build()
    _echo(svcs.ai1.train_params(scope=scope, min_learners=min_learners))


@app.command("review")
def review_cmd(student_id: str = typer.Option(..., "--for"), within_hours: float = 24.0) -> None:
    """What is due for spaced review right now."""
    from .db.engine import get_db
    from .services import sessions as S

    with get_db().session() as s:
        _echo({"due": S.due_reviews(s, student_id, within_hours=within_hours)})


@app.command("plan")
def plan_cmd(student_id: str = typer.Option(..., "--for")) -> None:
    from .services import build

    _graph, _mastery, out = build().ai1.plan(student_id)
    _echo(out)


@app.command("explain")
def explain_cmd(question: str, student_id: str = typer.Option(..., "--for"), topic_id: str | None = typer.Option(None, "--topic")) -> None:
    """Ask AI 2 for an adapted explanation (terminal version of the chat panel)."""
    from .services import build

    svcs = build()

    async def go() -> dict[str, Any]:
        reply = await svcs.ai2.explain(student_id, question, topic_id=topic_id)
        return reply.as_dict()

    _echo(_run(go()))


@app.command("numbas-export")
def numbas_export_cmd(out: Path = typer.Option(None, "--out")) -> None:
    from .db.engine import get_db
    from .db.init_db import export_numbas

    st = _settings()
    target = out or (st.data_dir / "numbas" / "adapt_bank.numbas")
    with get_db().session() as s:
        _echo(export_numbas(s, Path(target)))


@app.command("numbas-import")
def numbas_import_cmd(path: Path = typer.Argument(..., exists=True)) -> None:
    from .db.engine import get_db
    from .db.init_db import import_numbas_file

    with get_db().session() as s:
        _echo(import_numbas_file(path, s))


@app.command()
def demo(turns: int = typer.Option(3, "--turns", help="practice items to run through"), use_llm: bool = typer.Option(False, "--llm")) -> None:
    """The whole loop, end to end, on a freshly seeded learner. Prints a story."""
    from .db.engine import Database
    from .db.init_db import init
    from .services import adaptations as AD
    from .services import attempts as A
    from .services import build
    from .services import journey as J
    from .services import sessions as S

    st = _settings()
    db = Database(st)
    svcs = build(st, db=db)
    report = init(st, seed_demo=True, db=db)
    student_id = (report.demo or {}).get("student_id")
    if not student_id:
        typer.secho("seeding produced no learner; check `adapt doctor`", fg=typer.colors.RED)
        raise typer.Exit(code=1)
    with db.session() as s:
        from .db.models import Student

        student = s.get(Student, student_id)
        if student is not None and not student.diagnostic_complete:
            # the seeded attempt history stands in for the diagnostic
            student.diagnostic_complete = True
            s.commit()
    typer.secho(f"\n== learner {student_id} ==\n", fg=typer.colors.CYAN)

    async def go() -> None:
        with db.session() as s:
            sess = S.start_session(s, student_id, mode="quiz", device_hint="cli")
            session_id = sess.id
        with db.session() as s:
            picks = svcs.ai2.next_items(student_id, count=turns)
        for i, pick in enumerate(picks):
            item = pick["item"]
            answer = {"text": "I am not sure, I think you add across"}
            result = await A.submit(
                svcs,
                student_id=student_id,
                session_id=session_id,
                item_id=item["id"],
                answer=answer,
                teachback_text=answer["text"] if item.get("kind") == "teachback" else None,
            )
            typer.echo(
                f"  Q{i + 1} [{item.get('kind')}] {item.get('stem', '')[:58]:<58} -> "
                f"{'right' if result.correct else 'wrong'}  p_know {result.trace.get('p_know_before')}->{result.trace.get('p_know')}"
            )
            with db.session() as s:
                from .services import telemetry as TEL

                TEL.append_event(
                    s,
                    session_id=session_id,
                    student_id=student_id,
                    type="answer_changed",
                    payload={"item_id": item["id"], "count": 1},
                    item_id=item["id"],
                )
                s.commit()
        res = await svcs.ai1.analyze(student_id, session_id=session_id, use_llm=use_llm)
        d = res.as_dict()
        typer.secho("\n-- Observe --", fg=typer.colors.CYAN)
        for e in (d.get("evidence") or [])[:4]:
            typer.echo(f"  {e.get('detector')}: {e.get('metric')}={round(float(e.get('value') or 0), 3)} n={e.get('n')} strength={round(float(e.get('strength') or 0), 2)}")
        typer.secho("-- Understand (hypotheses, not facts) --", fg=typer.colors.CYAN)
        for h in (d.get("hypotheses") or [])[:4]:
            typer.echo(f"  [hypothesis, confidence {h.get('confidence')}] {h.get('statement')[:150]}")
            typer.echo(f"      why: {str(h.get('mechanism'))[:120]}")
            if h.get("falsification"):
                typer.echo(f"      disproof: {h.get('falsification')[:120]}")
            if h.get("citations"):
                typer.echo(f"      basis: {'; '.join(str(c) for c in h['citations'][:2])[:130]}")
        typer.secho("-- Personalize (asks first, never applies) --", fg=typer.colors.CYAN)
        pending = [p for p in (d.get("proposals") or []) if isinstance(p, dict) and p.get("id")]
        for pr in pending[:2]:
            typer.echo(f"  {pr.get('strategy_label')} ({pr.get('strategy')})")
            typer.echo(f"    facts:      {str(pr.get('facts'))[:150]}")
            typer.echo(f"    pattern:    {str(pr.get('pattern'))[:130]}")
            typer.echo(f"    hypothesis: {str(pr.get('hypothesis'))[:150]}")
            typer.echo(f"    change:     {str(pr.get('change'))[:150]}")
            typer.echo(f"    ask:        {str(pr.get('ask'))[:150]}")
            typer.echo(
                f"    watch:      {pr.get('review_metric')} over {pr.get('review_window_text') or 'its own window'}"
            )
            if pr.get("caveat"):
                typer.echo(f"    caveat:     {str(pr.get('caveat'))[:130]}")
        if pending:
            with db.session() as s:
                out = AD.decide(
                    s,
                    student_id=student_id,
                    proposal_id=pending[0]["id"],
                    student_decision={
                        "decision": "accept",
                        "proposal_id": pending[0]["id"],
                        "answered_by": "student",
                        "answered_at": datetime.now(timezone.utc).isoformat(),
                        # The demo learner is a script, so this is not a real consent --
                        # and the audit row says so, rather than letting a synthetic "yes"
                        # read later like a learner's.
                        "channel": "synthetic_demo",
                    },
                )
            typer.echo(
                f"  synthetic learner answered -> {out.get('status')}; active adaptation "
                f"{out.get('active_adaptation_id')} (scripted consent, recorded as such)"
            )
        else:
            typer.echo("  (no proposal this round -- the evidence was not consistent enough yet, which is a valid outcome)")
        typer.secho("-- Learn --", fg=typer.colors.CYAN)
        reply = await svcs.ai2.explain(student_id, "how do I add two fractions with different bottoms?", session_id=None)
        typer.echo(f"  AI 2 provider={reply.provider} level={reply.level} adaptations={reply.adaptations_applied}")
        for sec in reply.sections[:3]:
            typer.echo(f"    [{sec.get('label')}] {str(sec.get('text'))[:150]}")
        with db.session() as s:
            data = J.build(s, student_id, days=7, limit=200)
        typer.secho(f"-- Journey -- {len(data['nodes'])} recorded steps", fg=typer.colors.CYAN)
        for node in data["nodes"][-6:]:
            typer.echo(f"  {node['ts'][11:19]} {node['kind']:<11} {node['title'][:96]}")
        stt_status = svcs.status()["voice"]["stt"]
        typer.echo(
            f"\n  engines: stt={stt_status['detail'][:40]} tts={svcs.status()['voice']['tts']['detail'][:40]} "
            f"model={svcs.status()['llm'].get('configured') and 'gemini' or 'local'}"
        )

    _run(go())
    typer.echo("\ndone.")


@app.command("pilot")
def pilot(
    student_id: str = typer.Option(..., "--student-id", help="the learner the pilot rows belong to"),
    seed: bool = typer.Option(False, "--seed", help="write the deterministic pre-demo rows (default: report only)"),
    remove: bool = typer.Option(False, "--remove", help="delete the pilot rows again"),
) -> None:
    """Pre-demo pilot data: seeded on request, labelled everywhere it appears.

    A live demo used to have two bad options: announce "insufficient evidence" (honest,
    and useless on stage) or lower the bar until it did not (dishonest). The pilot is the
    third: real rows, produced before the demo, flagged ``pilot`` so every number derived
    from them carries the label. ``--seed`` writes them; ``--remove`` takes them back out.
    """
    from sqlalchemy import select

    from .db.models import Attempt
    from .learning import pilot as P
    from .services import build
    from .services import pilot as PS

    svcs = build()
    with svcs.scope() as s:
        if remove:
            _echo(PS.remove(s, student_id))
            return
        if seed:
            _echo(PS.seed(s, student_id))
            return
        rows = (
            s.execute(select(Attempt).where(Attempt.student_id == student_id, Attempt.pilot.is_(True)))
            .scalars()
            .all()
        )
        _echo(
            {
                "student_id": student_id,
                "pilot_rows": len(rows),
                "label": P.PILOT_LABEL,
                "note": P.PILOT_NOTE,
                "next": "adapt pilot --student-id <id> --seed   # write them before a demo",
            }
        )


@app.command()
def serve(
    host: str = typer.Option(None, "--host"),
    port: int = typer.Option(None, "--port"),
    reload: bool = typer.Option(False, "--reload"),
) -> None:
    """Run the API (uvicorn)."""
    import uvicorn

    st = _settings()
    # No ``ws="wsproto"``: ``uvicorn[standard]`` does not install wsproto, so that pinned the
    # API to a websocket implementation a clean install does not have, and the process died at
    # startup with ModuleNotFoundError. ``websockets`` is a declared dependency and is what
    # scripts/probe-fresh.sh and scripts/journey_check.py have been starting all along, so the
    # autoselect resolves to the implementation the voice socket is actually tested against.
    uvicorn.run(
        "adapt.api.app:app",
        host=host or st.host,
        port=int(port or st.port),
        reload=reload,
        log_level=str(st.log_level).lower(),
        # Preserve the socket peer. Uvicorn otherwise trusts forwarding headers from
        # localhost before ADAPT can apply its explicit trusted-proxy allowlist.
        proxy_headers=False,
    )




@app.command("host")
def host_cmd(
    dev: bool = typer.Option(False, "--dev", help="use Next development mode (no production build)"),
    port: int = typer.Option(None, "--port", help="the address people open: the web app's port (default 3000)"),
    data_dir: Path = typer.Option(None, "--data-dir", help="data directory for the API (default: ADAPT_DATA_DIR from your environment, else var/)"),
    tunnel: bool = typer.Option(False, "--tunnel", help="also open an https tunnel (cloudflared, else npx localtunnel): the microphone needs https, and it works off-wifi"),
    ip: str = typer.Option(None, "--ip", help="the wifi address to advertise and allow (default: detected; override it on a machine with a VPN or Docker)"),
    rate_limit_multiplier: float = typer.Option(None, "--rate-limit-multiplier", help="scale the room's shared buckets (default x10); this only ever raises the number"),
) -> None:
    """Host for a room: start the API and the web app, print the address.

    For a showcase on one wifi. Both servers bind 0.0.0.0. The machine's own
    LAN address is detected and added to the API's host allowlist as that
    exact address -- never a wildcard, never a subnet.

    The two things a room will run into are printed before anyone joins:

    * the microphone is unavailable on a plain-http address (browsers expose
      it only in a secure context; --tunnel opens the https link for it), and
    * everyone on the wifi is one connection to the API, so the per-connection
      buckets are the room's shared budget -- reported, and raised for the
      servers this command starts, never for a deployment.

    Nothing is written to .env and no value of a key is read or printed. The
    children inherit your environment with the allowlist, the origin and the
    rate-limit multiplier merged in.
    """
    import shutil

    from . import hosting as H

    st = _settings()
    frontend = H.Frontend(H.repo_root() / "frontend")
    if not frontend.script.exists():
        typer.secho(f"cannot find the web app at {frontend.script}", fg=typer.colors.RED)
        raise typer.Exit(code=2)
    if not shutil.which("node"):
        typer.secho("node is not on PATH: the web app cannot start (and `npm ci` in frontend/ needs it too)", fg=typer.colors.RED)
        raise typer.Exit(code=2)

    web_port = int(port or 3000)
    if not H.port_is_free(web_port):
        typer.secho(
            f"port {web_port} is already in use -- stop whatever is on it (a `npm run dev` from an earlier"
            " session is the usual one), or open a different address: adapt host --port 3001",
            fg=typer.colors.RED,
        )
        raise typer.Exit(code=2)

    api_host = str(st.host or "0.0.0.0")
    api_port = int(st.port or 8000)
    api_note = ""
    if not H.port_is_free(api_port, api_host):
        api_port = H.free_port(api_host)
        api_note = f"port {st.port} was busy, so the API is on {api_port} for this session (your other server is untouched)"

    lan_ip, considered = H.detect_lan_ip(ip)
    multiplier = H.raised_multiplier(st.rate_limit_multiplier, rate_limit_multiplier or H.ROOM_RATE_LIMIT_MULTIPLIER)

    # Merged, never replaced: whatever .env already allows stays allowed, and this is the only
    # place the LAN address enters the API's trust set -- as an exact host.
    api_env = H.build_api_env(
        os.environ,
        lan_ip=lan_ip,
        web_port=web_port,
        api_port=api_port,
        extra_hosts=st.extra_allowed_hosts,
        origins=st.allowed_origins,
        data_dir=str(data_dir) if data_dir else None,
        configured_multiplier=st.rate_limit_multiplier,
        room_multiplier=multiplier,
    )
    web_env = H.build_web_env(
        os.environ, api_port=api_port, web_port=web_port, production=not dev, lan_ip=lan_ip,
    )

    if dev and not frontend.installed:
        typer.secho("frontend dependencies are missing: run `cd frontend && npm ci`", fg=typer.colors.RED)
        raise typer.Exit(code=2)

    if not dev and not frontend.built:
        if not frontend.installed:
            typer.secho(f"{frontend.directory}/node_modules is missing: run `cd frontend && npm ci` first", fg=typer.colors.RED)
            raise typer.Exit(code=2)
        typer.echo("[host] no production build in frontend/.next -- building once (npm run build) ...")
        if subprocess.run(["npm", "run", "build"], cwd=str(frontend.directory)).returncode != 0:
            typer.secho("the web app did not build; the output above says what broke", fg=typer.colors.RED)
            raise typer.Exit(code=2)

    plan = H.HostPlan(
        web_port=web_port,
        api_port=api_port,
        api_host=api_host,
        lan_ip=lan_ip,
        ips_considered=tuple(considered),
        allowed_hosts=tuple(h for h in api_env["ADAPT_EXTRA_ALLOWED_HOSTS"].split(",") if h),
        multiplier=multiplier,
        configured_multiplier=float(st.rate_limit_multiplier or 1.0),
        mode=str(st.mode),
        tunnel_requested=bool(tunnel),
        extra_notes=(api_note,) if api_note else (),
    )

    session = H.HostSession(echo=typer.echo)
    if lan_ip:
        typer.echo(f"[host] trusting exactly this host at the API: {lan_ip} (no wildcard, no subnet)")
    # A terminal Ctrl-C raises KeyboardInterrupt on its own; SIGTERM does not, and a process
    # manager (or `kill`) that stops the presenter would otherwise leave the API and the web
    # server running -- on ports the next run then refuses. Same shutdown path for both.
    try:
        signal.signal(signal.SIGTERM, _stop_on_signal)
    except ValueError:  # pragma: no cover - not the main thread
        pass
    dead: tuple[str, int] | None = None
    try:
        session.start("api", H.uvicorn_command(st, host=api_host, port=api_port), env=api_env)
        session.start("web", ["node", "server.mjs"], env=web_env, cwd=frontend.directory)
        typer.echo(f"[host] starting the API on {api_host}:{api_port} and the web app on 0.0.0.0:{web_port} ...")
        api_ready = session.wait_http(f"http://127.0.0.1:{api_port}/healthz", timeout=120)
        web_ready = api_ready and session.wait_http(f"http://127.0.0.1:{web_port}/", timeout=120)
        dead = session.exited()
        if dead is not None or not (api_ready and web_ready):
            if dead is None:
                dead = ("api" if not api_ready else "web", 1)
        else:
            if tunnel:
                plan = _start_tunnel(session, plan, web_port)
            typer.echo("")
            # Round 3 (Track T): one line on whether the model key is set, so
            # the operator can tell at a glance whether lessons run in
            # "written for you" mode or in the honest "simple mode" fallback.
            # The key itself is never printed here; the boolean is enough.
            try:
                from .config_secrets import status_rows
                for row in status_rows(st):
                    if row["service"] == "gemini":
                        typer.echo(
                            f"[host] Model key: {'configured' if row['configured'] else 'not configured (lessons in simple mode)'}"
                        )
                        break
            except Exception:  # pragma: no cover - never let status print break startup
                pass
            for line in H.host_report(plan):
                typer.echo(line)
            while dead is None:
                time.sleep(0.3)
                dead = session.exited()
    except KeyboardInterrupt:
        typer.echo("\n[host] stopping ...")
    finally:
        session.stop()

    if dead is not None:
        name, code = dead
        typer.secho(f"\n[host] the {name} server exited (code {code}); last lines it printed:", fg=typer.colors.RED)
        for line in session.tail(name, 15):
            typer.echo(f"    {line}")
        raise typer.Exit(code=1)
    typer.echo("[host] stopped.")


def _stop_on_signal(signum: int, frame: Any) -> None:  # pragma: no cover - raised by a signal
    raise KeyboardInterrupt


def _start_tunnel(session: Any, plan: Any, web_port: int) -> Any:
    """Open the opt-in https tunnel, or say why not and keep the LAN address as the answer."""
    from . import hosting as H

    hint = f"        install: {H.TUNNEL_INSTALL_HINT}"
    chosen = H.tunnel_command(web_port)
    if chosen is None:
        note = "neither cloudflared nor npx is installed"
        typer.secho(f"\n[host] --tunnel needs a tunnel runner, and {note}.", fg=typer.colors.YELLOW)
        typer.secho(hint, fg=typer.colors.YELLOW)
        return replace(plan, tunnel_note=note)
    argv, provider, caveat = chosen
    typer.echo(f"[host] opening an https tunnel with {provider} ({caveat}) ...")
    session.start("tunnel", argv, env=os.environ)
    url = session.await_url("tunnel", timeout=H.TUNNEL_START_TIMEOUT_S)
    if url:
        return replace(plan, tunnel_url=url, tunnel_note=caveat)
    # No URL: the phones still get the LAN address, and the reason is printed rather than
    # hidden behind a hanging command. A tunnel that half-works is worse than no tunnel.
    note = f"{provider} printed no https address in {H.TUNNEL_START_TIMEOUT_S:.0f}s"
    typer.secho(f"\n[host] {note}.", fg=typer.colors.YELLOW)
    tail = session.tail("tunnel", 6)
    if tail:
        typer.echo("        the tunnel said:")
        for line in tail:
            typer.echo(f"          {line}")
    else:
        typer.secho(
            f"        it printed nothing at all -- check that `{argv[0]}` can reach the internet.",
            fg=typer.colors.YELLOW,
        )
    typer.secho(hint, fg=typer.colors.YELLOW)
    session.stop_child("tunnel")
    return replace(plan, tunnel_note=note)

@voice_app.command("capabilities")
def voice_caps() -> None:
    from .voice import stt as STT
    from .voice import tts as TTS

    _echo({"stt": STT.capabilities(_settings()), "tts": TTS.capabilities(_settings())})


@voice_app.command("selftest")
def voice_selftest(seconds: float = 2.0) -> None:
    """Synthetic speech through VAD + transcript path: no microphone needed."""
    import numpy as np

    from .voice.audio import silence, tone
    from .voice.vad import build_vad, segment_summary

    vad = build_vad(_settings())
    rng = np.random.default_rng(0)
    burst = tone(300, sample_rate=16000, seconds=0.45).astype(np.float32) * 0.3 + rng.normal(0, 0.02, 7200).astype(np.float32)
    frames = np.concatenate([silence(0.2, sample_rate=16000), burst, silence(seconds, sample_rate=16000)])
    decisions = vad.feed(frames, sample_rate=16000) if hasattr(vad, "feed") else vad.accept(frames)
    summary = segment_summary(decisions)
    _echo({"frames_in": int(frames.size / 320), **{k: v for k, v in summary.items()}})


@app.command("token")
def token_cmd(student_id: str = typer.Option(..., "--for"), hours: int = typer.Option(6, "--hours")) -> None:
    """Mint a session token for the CLI/curl demo flow (printed once, hashed in DB)."""
    from .db.engine import get_db
    from .security import auth

    with get_db().session() as s:
        issued = auth.create_session(s, student_id, hours=hours)
        s.commit()
        typer.echo(f"token: {issued.token}")
        typer.echo(f"csrf:  {issued.csrf}")
        typer.echo(f"expires: {issued.session.expires_at.isoformat()}")
        typer.echo('use:  curl -H "Cookie: adapt_session=<token>" -H "X-CSRF-Token: <csrf>" ...')

_SUPPORTED_KEY_SERVICES = ("gemini", "ai1", "ai2", "ai3", "oak", "pubmed", "youtube")
_SUPPORTED_SLOTS = ("ai1", "ai2", "ai3")


@config_app.command("set-key")
def config_set_key(
    service: str = typer.Option(
        "gemini",
        "--service",
        help=(
            "Credential to store (ai1, ai2, ai3, gemini, oak, pubmed, youtube). "
            "Enter it at the hidden prompt, never as an argument; shell history records arguments."
        ),
        case_sensitive=False,
    ),
) -> None:
    """Store a key at a hidden prompt; never pass it on the command line or in shell history."""
    from .config_secrets import ConfigSecretError, set_key

    st = _settings()
    chosen = service.strip().lower()
    if chosen not in _SUPPORTED_KEY_SERVICES:
        typer.secho(
            f"Unsupported service. Choose one of: {', '.join(_SUPPORTED_KEY_SERVICES)}.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    secret = typer.prompt("API key (input is hidden)", hide_input=True, confirmation_prompt=True)
    try:
        set_key(st.data_dir, chosen, secret)
    except ConfigSecretError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from None
    typer.echo(f"Stored the {chosen} credential in the owner-only console settings file.")
    typer.echo("Restart the application process to load the new configuration.")


@config_app.command("set-url")
def config_set_url(
    slot: str = typer.Option(
        ...,
        "--slot",
        help="Target AI slot: ai1 (Psychology), ai2 (Education), or ai3 (Adaptation).",
        case_sensitive=False,
    ),
    url: str | None = typer.Option(
        None,
        "--url",
        help="HTTPS endpoint URL for the slot (prompted interactively when omitted).",
    ),
    header: str | None = typer.Option(
        None,
        "--header",
        help="HTTP header used to send the slot API key (default: Authorization, e.g. X-API-Key).",
    ),
) -> None:
    """Configure an external HTTPS site API endpoint for Slot 1 (ai1), Slot 2 (ai2), or Slot 3 (ai3)."""
    from .config_secrets import ConfigSecretError, set_url

    st = _settings()
    chosen = slot.strip().lower()
    if chosen not in _SUPPORTED_SLOTS:
        typer.secho(
            f"Unsupported slot. Choose one of: {', '.join(_SUPPORTED_SLOTS)}.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    endpoint = url if url is not None else typer.prompt(f"HTTPS API URL for {chosen}")
    try:
        set_url(st.data_dir, chosen, endpoint, header=header)
    except ConfigSecretError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from None
    typer.echo(f"Stored the {chosen} API endpoint in the owner-only console settings file.")
    typer.echo("Restart the application process to load the new configuration.")


@config_app.command("remove-url")
def config_remove_url(
    slot: str = typer.Option(
        ...,
        "--slot",
        help="Target AI slot whose custom URL should be removed (ai1, ai2, ai3).",
        case_sensitive=False,
    ),
) -> None:
    """Remove a custom HTTPS site API URL from console settings."""
    from .config_secrets import ConfigSecretError, remove_url

    chosen = slot.strip().lower()
    if chosen not in _SUPPORTED_SLOTS:
        typer.secho(
            f"Unsupported slot. Choose one of: {', '.join(_SUPPORTED_SLOTS)}.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    if not typer.confirm(f"Remove the stored {chosen} endpoint URL?", default=False):
        typer.echo("No changes made.")
        return
    try:
        remove_url(_settings().data_dir, chosen)
    except ConfigSecretError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from None
    typer.echo(f"Removed the stored {chosen} endpoint URL. An environment override may still be active.")


@config_app.command("status")
def config_status(
    check: bool = typer.Option(
        False, "--check", help="Probe each enabled slot and connector once (one small request each)."
    ),
) -> None:
    """Show credential and 3-slot API state (when set, at most the final four characters)."""
    from .config_secrets import ConfigSecretError, settings_with_managed_values, status_rows
    from .connectors.slots import check_slot, slot_status_rows

    base_settings = _settings()
    try:
        for row in status_rows(base_settings):
            if row["configured"]:
                # Where the value came from matters: an exported variable or a .env entry
                # overrides the console file, so "I just stored a new key" can otherwise be
                # false while the command says "configured".
                source = f" · from {row['source']}" if row.get("source") else ""
                typer.echo(f"{row['service']}: configured · {row['masked']}{source}")
            else:
                typer.echo(
                    f"{row['service']}: not configured (set it with `adapt config set-key`)"
                )
        effective_settings = settings_with_managed_values(base_settings)
    except ConfigSecretError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from None

    for srow in slot_status_rows(effective_settings):
        state_word = "enabled" if srow["enabled"] else "off"
        url_word = "custom-url" if srow["custom_url_set"] else "builtin-routes"
        providers = ", ".join(srow["providers"])
        probe = ""
        if check and srow["enabled"]:
            verdict = check_slot(srow["slot"], effective_settings)
            probe = f" · health: {'ok' if verdict.get('ok') else 'not answered'}"
        typer.echo(
            f"slot {srow['slot']} ({srow['function']}): {state_word} · {url_word} · [{providers}]{probe}"
        )

    # Round 9 connectors: state only (never a key, never learner data), and
    # an opt-in one-call health probe per connector with --check.
    from .connectors.registry import connector_status, health_check

    for row in connector_status(effective_settings):
        probe = ""
        if check and row["enabled"]:
            verdict = health_check(effective_settings, row["id"])
            probe = f" · health: {'ok' if verdict.get('ok') else 'not answered'}"
        typer.echo(
            f"connector {row['id']}: {row['state']} · {row['note']} · {row['licence']}{probe}"
        )


#: Coarse causes for a failed one-call probe, keyed by the client's own status.
_CONNECTION_FAILURES = {
    "quota": "the provider refused the call for rate or quota reasons",
    "throttled": "a local pacing limit refused the probe before it was sent",
    "disabled": "no credential reached the client",
    "consent_required": "a learner-consent check refused the probe",
    "blocked": "the provider's safety filter refused the probe",
    "empty": "the provider answered, but with no usable text",
    "parse_error": "the provider's reply could not be read",
}


def _without_secret(text: str, settings: Any, limit: int = 600) -> str:
    """One line, bounded, with any configured key or URL removed if it appears."""
    out = str(text or "")
    for attr in (
        "gemini_api_key",
        "ai1_api_key",
        "ai2_api_key",
        "ai3_api_key",
        "oak_api_key",
        "pubmed_api_key",
        "youtube_api_key",
        "ai1_api_url",
        "ai2_api_url",
        "ai3_api_url",
    ):
        val = str(getattr(settings, attr, "") or "").strip()
        if val:
            out = out.replace(val, "[redacted]")
    return " ".join(out.split())[:limit]


def _connection_failure_reason(
    result: Any, settings: Any, *, candidates: Sequence[str] = ()
) -> tuple[str, str]:
    """``(plain-language cause, provider's own words)`` for a failed probe.

    The old command printed one generic sentence for a 404, a timeout, a rejected key and
    an empty answer alike, which left the operator with nothing to act on. The provider's
    message is printed because this is the console, not a learner surface: model names are
    operator configuration here (``adapt config models`` prints them too), while the key
    itself is still stripped from the text.
    """
    error = _without_secret(str(getattr(result, "error", "") or ""), settings)
    status = str(getattr(result, "status", "") or "")
    lowered = error.lower()
    if error.startswith("404"):
        hint = "the configured model ID does not exist for this key"
        if candidates:
            hint += (
                ". The provider retires IDs on its own schedule and leaves them on the "
                "model list, so that list is not a promise: the IDs below are the ones "
                "this key can actually call"
            )
        else:
            hint += (
                ", and no replacement could be found: either this key reaches no model "
                "list, or nothing on the one it reaches can answer with text. Check the "
                "key's model access, or name an ID explicitly with "
                "ADAPT_GEMINI_MODEL_STANDARD"
            )
    elif error.startswith(("auth 401", "auth 403")):
        hint = (
            "the key was rejected. Check `adapt config status`: an exported "
            "ADAPT_GEMINI_API_KEY or a .env entry overrides the console file, so storing "
            "the key again only helps when the source is the console file"
        )
    elif "api key not valid" in lowered or "api key expired" in lowered:
        hint = (
            "the provider rejected the key itself (a copy/paste error or an expired key); "
            "store it again with `adapt config set-key`"
        )
    elif error.startswith("429"):
        hint = "the key is rate-limited or its quota is spent"
    elif "timeout" in lowered:
        hint = "the provider did not answer before the request timed out"
    elif any(
        marker in error
        for marker in ("ConnectError", "ConnectTimeout", "NameResolution", "SSL", "ProxyError")
    ):
        hint = (
            "the provider could not be reached from this machine "
            "(connectivity, DNS, proxy or firewall)"
        )
    else:
        hint = _CONNECTION_FAILURES.get(status, "the provider returned an error")
    return hint, error


@config_app.command("test")
def config_test(
    slot: str = typer.Option(
        "gemini",
        "--slot",
        help="Target to test: gemini (default LLM probe), ai1, ai2, ai3, or all.",
        case_sensitive=False,
    ),
) -> None:
    """One minimal connection check for Gemini or any of the 3 dedicated AI API slots."""
    from .config_secrets import ConfigSecretError, settings_with_managed_values
    from .connectors.slots import check_slot
    from .security.secrecy import install_log_redaction

    chosen_slot = slot.strip().lower()
    if chosen_slot not in {"gemini", "ai1", "ai2", "ai3", "all"}:
        typer.secho(
            "Unsupported slot. Choose one of: gemini, ai1, ai2, ai3, all.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    st = _settings()
    try:
        st = settings_with_managed_values(st)
    except ConfigSecretError:
        typer.echo("Connection test failed. Check the console settings file.")
        raise typer.Exit(code=1) from None

    install_log_redaction(st)

    if chosen_slot in {"ai1", "ai2", "ai3", "all"}:
        targets = ("ai1", "ai2", "ai3") if chosen_slot == "all" else (chosen_slot,)
        any_failed = False
        for target in targets:
            verdict = check_slot(target, st)
            detail = _without_secret(str(verdict.get("detail") or ""), st)
            if verdict.get("ok"):
                typer.echo(f"[{target}] Connection test passed: {detail}")
            else:
                typer.echo(f"[{target}] Connection test failed: {detail}")
                any_failed = True
        if any_failed:
            raise typer.Exit(code=1)
        return

    if not st.gemini_api_key:
        typer.echo("Connection test failed. No credential is configured.")
        raise typer.Exit(code=1)

    from .ai.llm import GeminiLLM

    # No retry/backoff loop and no pacing delay, and the probe carries no learner text:
    # the only extra requests this can make are the two replacements a retired ID is
    # allowed (one configured, one discovered) plus that one discovery request. None of
    # them is a retry of a call that already answered.
    st = st.model_copy(update={"gemini_max_retries": 0, "gemini_min_interval_ms": 0})
    llm = GeminiLLM(st)

    async def one_minimal_request():
        try:
            result = await llm.complete(
                system="Reply with the single word OK.",
                user="Connection check.",
                purpose="console_connection_test",
                role="ai2",
                temperature=0.0,
                max_output_tokens=8,
            )
            # Only after a 404, and only from what the client already learned: a
            # command that is printing a failure must not add a request of its own.
            # Any 404 that reaches here has already run discovery (that is the only way
            # the client runs out of models), so the answer is cached and this is free.
            # The tier is the class the probe was planned under (see ai/routing.py),
            # so the IDs listed are ranked the way the client ranked them.
            candidates = (
                await llm.known_candidates(result.tier or "standard")
                if str(getattr(result, "error", "") or "").startswith("404")
                else []
            )
            return result, candidates
        finally:
            await llm.aclose()

    try:
        result, candidates = _run(one_minimal_request())
    except Exception as exc:
        detail = _without_secret(f"{type(exc).__name__}: {exc}", st)
        typer.echo(f"Connection test failed: the client raised an error. {detail}".strip())
        raise typer.Exit(code=1) from None
    if result.ok:
        # Naming the model is the point: a deployment that only passes because its
        # configured ID was replaced has a configuration to fix.
        typer.echo(
            f"Connection test passed ({result.model})."
            if result.model
            else "Connection test passed."
        )
    else:
        hint, detail = _connection_failure_reason(result, st, candidates=candidates)
        typer.echo(f"Connection test failed: {hint}.")
        if detail:
            typer.echo(f"  the provider said: {detail}")
        if candidates:
            shown = ", ".join(candidates[:6])
            typer.echo(f"  this key can call: {shown}")
            if len(candidates) > 6:
                typer.echo(f"  ... and {len(candidates) - 6} more; see `adapt config models`")
        raise typer.Exit(code=1)


def _group_models(names: Sequence[str], tier: str = "standard") -> list[tuple[str, list[str]]]:
    """``[(heading, ids)]``, with the IDs worth configuring first.

    Three groups, because the provider's list mixes three things: rolling aliases that
    always point at something current, ordinary text models, and models this build can
    never ask for text (speech, music, images, embeddings, other products).
    """
    from .ai.llm import is_text_model, rank_models

    aliases = [n for n in names if n.lower().endswith(MODEL_ALIAS_SUFFIX)]
    taken = set(aliases)
    text = [n for n in names if n not in taken and is_text_model(n)]
    other = [n for n in names if n not in taken and not is_text_model(n)]
    groups: list[tuple[str, list[str]]] = []
    if aliases:
        groups.append(
            (
                "rolling aliases -- these track whatever the provider serves now, "
                "which is why they are the defaults",
                aliases,
            )
        )
    if text:
        groups.append(
            (
                "text models -- in this build's preference order "
                "(newest first inside each family)",
                rank_models(text, tier),
            )
        )
    if other:
        groups.append(
            (
                "listed but not usable for text here (speech, image, other products)",
                other,
            )
        )
    return groups


@config_app.command("models")
def config_models() -> None:
    """List the model IDs the configured key can see (operator diagnostic).

    "See" and not "call": the provider retires versioned IDs on its own schedule and
    leaves them on this list afterwards, so an ID printed here can still answer 404.
    Prefer a rolling alias; `adapt config test` proves which one actually answers.
    """
    from .ai.llm import GeminiLLM
    from .config_secrets import ConfigSecretError, settings_with_managed_values

    st = _settings()
    try:
        st = settings_with_managed_values(st)
    except ConfigSecretError:
        typer.echo("Could not read the console settings file.")
        raise typer.Exit(code=1) from None
    if not st.gemini_api_key:
        typer.echo("No credential is configured.")
        raise typer.Exit(code=1)

    llm = GeminiLLM(st)

    async def collect() -> list[str]:
        try:
            return await llm.list_models()
        finally:
            await llm.aclose()

    try:
        models = _run(collect())
    except Exception:
        typer.echo("The model list could not be read. Check connectivity and the console settings.")
        raise typer.Exit(code=1) from None
    if not models:
        typer.echo("The model list could not be read. Check connectivity and the console settings.")
        raise typer.Exit(code=1)
    typer.echo(
        f"{len(models)} ID(s) this key can see. A listed ID is not a promise that it "
        "answers: retired IDs stay on this list while returning 404, so confirm with "
        "`adapt config test`."
    )
    # Ranked for the *standard* class: the operator is choosing a default, not
    # reproducing the probe, and standard is where the product's own wording is made.
    for heading, group in _group_models(models, "standard"):
        typer.echo("")
        typer.echo(f"{heading}:")
        for name in group:
            typer.echo(f"  {name}")


@config_app.command("rotate")
def config_rotate(
    service: str = typer.Option(
        "gemini",
        "--service",
        help="Credential to rotate (ai1, ai2, ai3, gemini, oak, pubmed, youtube).",
        case_sensitive=False,
    ),
) -> None:
    """Replace a credential using a hidden prompt; the new value is never a shell argument."""
    from .config_secrets import ConfigSecretError, set_key

    chosen = service.strip().lower()
    if chosen not in _SUPPORTED_KEY_SERVICES:
        typer.secho(
            f"Unsupported service. Choose one of: {', '.join(_SUPPORTED_KEY_SERVICES)}.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    secret = typer.prompt(
        "Replacement API key (input is hidden)",
        hide_input=True,
        confirmation_prompt=True,
    )
    try:
        set_key(_settings().data_dir, chosen, secret)
    except ConfigSecretError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from None
    typer.echo(f"Rotated the {chosen} credential in the owner-only console settings file.")
    typer.echo("Restart the application process to load the new configuration.")


@config_app.command("remove")
def config_remove(
    service: str = typer.Option(
        "gemini",
        "--service",
        help="Credential to remove (ai1, ai2, ai3, gemini, oak, pubmed, youtube).",
        case_sensitive=False,
    ),
) -> None:
    """Remove a stored credential without printing it."""
    from .config_secrets import ConfigSecretError, remove_key

    chosen = service.strip().lower()
    if chosen not in _SUPPORTED_KEY_SERVICES:
        typer.secho(
            f"Unsupported service. Choose one of: {', '.join(_SUPPORTED_KEY_SERVICES)}.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    if not typer.confirm(f"Remove the stored {chosen} credential?", default=False):
        typer.echo("No changes made.")
        return
    try:
        remove_key(_settings().data_dir, chosen)
    except ConfigSecretError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from None
    typer.echo(
        f"Removed the stored {chosen} credential. An environment override may still be active."
    )


@config_app.command("set-budget")
def config_set_budget(
    calls: int | None = typer.Option(
        None, "--calls", min=0, help="Daily call limit; omitted values are requested interactively."
    ),
    tokens: int | None = typer.Option(
        None,
        "--tokens",
        min=0,
        help="Daily token limit; omitted values are requested interactively.",
    ),
) -> None:
    """Set the optional daily service limits in console settings, never in learner records."""
    from .config_secrets import ConfigSecretError, set_budgets, settings_with_managed_values

    st = _settings()
    try:
        current = settings_with_managed_values(st)
    except ConfigSecretError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from None
    calls = calls if calls is not None else typer.prompt(
        "Daily call limit", default=current.gemini_daily_call_budget, type=int
    )
    tokens = tokens if tokens is not None else typer.prompt(
        "Daily token limit", default=current.gemini_daily_token_budget, type=int
    )
    try:
        set_budgets(st.data_dir, calls=calls, tokens=tokens)
    except ConfigSecretError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from None
    typer.echo("Saved the daily service limits in the owner-only console settings file.")
    typer.echo("Restart the application process to load the new configuration.")


@app.command("demo-seed")
def demo_seed_cmd(
    count: int = typer.Option(3, "--count", help="number of demo learners to create"),
    reset: bool = typer.Option(False, "--reset", help="remove existing demo-seeded learners first"),
) -> None:
    """Create labelled pilot/demo learners with realistic synthetic histories.

    Each learner is clearly marked as synthetic in every view. Their histories
    are deterministic (seeded) so the contrast demo and evidence screens always
    show the same shapes.
    """
    from .services import build

    svcs = build()
    result = demo_seed(svcs, count=count, reset=reset)
    _echo(result)


def demo_seed(svcs: Any, *, count: int = 3, reset: bool = False) -> dict[str, Any]:
    """Create synthetic demo learners. Called by the CLI and by tests."""
    import random
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select as sa_select

    from .crypto.fields import blind_index_for
    from .db.models import Attempt, Item, LearningSession, Student, Topic
    from .security.auth import hash_password

    db = svcs.db
    created: list[dict[str, str]] = []

    profiles = [
        {
            "username": "demo_alice",
            "display_name": "Alice (demo, synthetic)",
            "topic_strength": {"fraction": 0.85, "algebra": 0.70},
            "style": "methodical",
        },
        {
            "username": "demo_ben",
            "display_name": "Ben (demo, synthetic)",
            "topic_strength": {"fraction": 0.30, "algebra": 0.60},
            "style": "intuitive",
        },
        {
            "username": "demo_clara",
            "display_name": "Clara (demo, synthetic)",
            "topic_strength": {"fraction": 0.55, "algebra": 0.90},
            "style": "visual",
        },
        {
            "username": "demo_david",
            "display_name": "David (demo, synthetic)",
            "topic_strength": {"fraction": 0.45, "algebra": 0.35},
            "style": "mixed",
        },
        {
            "username": "demo_elena",
            "display_name": "Elena (demo, synthetic)",
            "topic_strength": {"fraction": 0.75, "algebra": 0.80},
            "style": "verbal",
        },
    ]

    rng = random.Random(42)
    chosen = profiles[: min(count, len(profiles))]

    with db.session() as s:
        if reset:
            # Remove existing demo learners by username prefix
            for p in profiles:
                existing = s.execute(
                    sa_select(Student).where(Student.username_index == blind_index_for("student.username", p["username"]))
                ).scalars().first()
                if existing:
                    s.delete(existing)
            s.commit()

        # Ensure we have topics and items to reference
        topics = s.execute(sa_select(Topic)).scalars().all()
        topic_ids = {t.id for t in topics}
        items = s.execute(sa_select(Item)).scalars().all()

        for profile in chosen:
            # Skip if already exists
            existing = s.execute(
                sa_select(Student).where(Student.username_index == blind_index_for("student.username", profile["username"]))
            ).scalars().first()
            if existing:
                created.append({
                    "student_id": existing.id,
                    "username": profile["username"],
                    "display_name": profile["display_name"],
                    "style": profile["style"],
                    "label": "synthetic demo learner (already existed)",
                })
                continue

            student = Student(
                username=profile["username"],
                username_index=blind_index_for("student.username", profile["username"]),
                display_name=profile["display_name"],
                email=f"{profile['username']}@demo.adapt",
                email_index=blind_index_for("student.email", f"{profile['username']}@demo.adapt"),
                password_hash=hash_password("demo_password_123"),
                onboarding_complete=True,
                diagnostic_complete=True,
                consent_analytics=True,
                consent_share_with_llm=True,
            )
            s.add(student)
            s.flush()

            # Create a session with synthetic attempts
            session = LearningSession(
                student_id=student.id,
                mode="practice",
                started_at=datetime.now(timezone.utc) - timedelta(hours=2),
            )
            s.add(session)
            s.flush()

            # Create attempts against real items if available, else skip
            used_items = [it for it in items if it.topic_id in topic_ids][:8]
            if not used_items:
                # No curriculum items; create minimal synthetic records
                created.append({
                    "student_id": student.id,
                    "username": profile["username"],
                    "display_name": profile["display_name"],
                    "style": profile["style"],
                    "label": "synthetic demo learner (no curriculum items for attempts)",
                })
                continue

            for item in used_items:
                topic_key = "fraction" if "fraction" in (item.topic_id or "").lower() else "algebra"
                strength = profile["topic_strength"].get(topic_key, 0.5)
                correct = rng.random() < strength
                attempt = Attempt(
                    student_id=student.id,
                    session_id=session.id,
                    item_id=item.id,
                    topic_id=item.topic_id,
                    first_answer={"text": "demo answer"},
                    final_answer={"text": "demo answer"},
                    correct=correct,
                    score=1.0 if correct else 0.0,
                    time_on_item_ms=int(rng.uniform(20000, 120000)),
                    hints_used=rng.randint(0, 2),
                    pilot=True,  # mark as pilot/demo data
                )
                s.add(attempt)

            created.append({
                "student_id": student.id,
                "username": profile["username"],
                "display_name": profile["display_name"],
                "style": profile["style"],
                "label": "synthetic demo learner",
            })
        s.commit()

    return {
        "created": len(created),
        "learners": created,
        "note": (
            "These learners are clearly marked as synthetic (display name contains "
            "'(demo, synthetic)'). Their histories are deterministic (seeded) for "
            "reproducible demos. Attempts are flagged as pilot data."
        ),
    }

if __name__ == "__main__":  # pragma: no cover
    app()
