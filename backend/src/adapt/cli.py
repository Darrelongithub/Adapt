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
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import typer

from .config import get_settings

app = typer.Typer(
    name="adapt",
    help="ADAPT: adaptive learning with a Learning Intelligence and an Educational Intelligence.",
    no_args_is_help=True,
    add_completion=False,
)
db_app = typer.Typer(help="database lifecycle and inspection", no_args_is_help=True)
voice_app = typer.Typer(help="speech engine checks", no_args_is_help=True)
app.add_typer(db_app, name="db")
app.add_typer(voice_app, name="voice")


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
            mod = __import__(pkg)
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
    from .services import adaptations as AD
    from .db.engine import get_db

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
    from .services import build
    from .services import adaptations as AD
    from .services import attempts as A
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
    )




@app.command("host")
def host_cmd(
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
    web_env = H.build_web_env(os.environ, api_port=api_port, web_port=web_port, production=True)

    if not frontend.built:
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
    decisions = vad.feed(frames, sample_rate=16000) if hasattr(vad, "feed") else vad.accept(np.array_split(frames, 24))
    summary = segment_summary(decisions)
    _echo({"frames_in": int(frames.size / 320), **{k: v for k, v in summary.items()}})


@app.command("token")
def token_cmd(student_id: str = typer.Option(..., "--for"), hours: int = typer.Option(6, "--hours")) -> None:
    """Mint a session token for the CLI/curl demo flow (printed once, hashed in DB)."""
    from .db.engine import get_db
    from .security import auth

    with get_db().session() as s:
        issued = auth.create_session(s, student_id)
        s.commit()
        typer.echo(f"token: {issued.token}")
        typer.echo(f"csrf:  {issued.csrf}")
        typer.echo(f"expires: {issued.session.expires_at.isoformat()}")
        typer.echo('use:  curl -H "Cookie: adapt_session=<token>" -H "X-CSRF-Token: <csrf>" ...')

if __name__ == "__main__":  # pragma: no cover
    app()
