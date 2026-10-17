"""Accounts, login and onboarding.

Deliberately small: identity here exists to keep one learner's history away from
another learner's browser, and to make "delete me" mean something. There is no
teacher role, no class roster, no attendance -- the blueprint's scope is one
learner and their own data.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import AliasChoices, BaseModel, Field
from sqlalchemy.orm import Session

from ..deps import clear_login, db_session, issue_login, require_csrf, student_from_request
from ...config import get_settings
from ...db.models import Student
from ...security import auth
from ...security import consent
from ...security.middleware import audit_event, client_ip
from ...services import current
from ...services import sessions as S

router = APIRouter(prefix="/api/v1", tags=["auth"])


def guest_requested(body: RegisterBody) -> bool:
    """A creation is a guest when it asked to be one or gave no identifier at all."""
    return bool(body.as_guest) or not (body.username or body.email)


def _spend(scope: str, key: str) -> None:
    """Take one token from a bucket ``DEFAULT_LIMITS`` declares."""
    current().limiter.allow(scope, key)


def _check_bucket(request: Request, scope: str, key: str, *, note: str) -> None:
    """Refuse while a failure/creation budget is empty -- before any credential is read.

    Why this is here at all: ``auth_fail`` and ``guest_create`` were configured in
    :data:`DEFAULT_LIMITS` for the entire build with no caller, while ``/api/v1/quotas``
    listed both as if they protected something. In practice login was limited only by
    the shared per-IP bucket (120/min), i.e. roughly two password guesses per second
    indefinitely, and account creation was unbounded. The buckets are now consulted for
    real; :data:`DEFAULT_LIMITS` and ``ADAPT_RATE_LIMIT_MULTIPLIER`` remain the knobs.
    """
    blocked, retry = current().limiter.blocked(scope, key)
    if not blocked:
        return
    seconds = max(1, int(retry) + 1)
    audit_event(request, action=f"{scope}_throttled", outcome="denied", detail={"retry_after_s": seconds})
    raise HTTPException(
        status_code=429,
        detail={"error": "too_many_attempts", "scope": scope, "retry_after_s": seconds, "note": note},
        headers={"retry-after": str(seconds)},
    )



class RegisterBody(BaseModel):
    display_name: str | None = Field(default=None, max_length=80)
    # The identifier this build asks for. `email` is still accepted so accounts made before
    # the username field existed, and the tunnel demo, keep working.
    username: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=160)
    password: str | None = Field(default=None, min_length=10, max_length=200)
    age_band: str | None = Field(default=None, max_length=16)
    as_guest: bool = False
    # Older API clients omit these; the web door sends the explicit choice at creation.
    consent_analytics: bool | None = None
    consent_share_with_llm: Annotated[
        bool | None, Field(validation_alias=AliasChoices(*consent.WRITE_FIELDS))
    ] = None


class LoginBody(BaseModel):
    """Either identifier signs in. Both fields are optional in the schema and the route
    refuses an empty pair, so the failure is one readable sentence rather than a 422 that
    names a field the learner never saw."""

    username: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=160)
    password: str = Field(max_length=200)


class OnboardingBody(BaseModel):
    preferences: dict[str, Any] = Field(default_factory=dict)
    consent_analytics: bool = True
    # Both spellings, for the reason in :mod:`adapt.security.consent`: the learner-facing name
    # is the one a client can read back out of `/me`, and a body that wrote it used to be
    # accepted with `extra="ignore"` and change nothing. A silently ignored consent answer is
    # the failure this field cannot be allowed to have.
    consent_share_with_llm: Annotated[
        bool, Field(validation_alias=AliasChoices(*consent.WRITE_FIELDS))
    ] = True
    voice_enabled: bool = False
    voice_gender: str = "female"
    course_id: str | None = None


class PreferencesBody(BaseModel):
    preferences: dict[str, Any] = Field(default_factory=dict)
    source: str = "settings"


class VoiceBody(BaseModel):
    enabled: bool
    voice_gender: str | None = Field(default=None, max_length=8)


@router.post("/students")
def create_student(body: RegisterBody, request: Request, response: Response, s: Session = Depends(db_session)) -> dict[str, Any]:
    if body.as_guest and auth.demo_passphrase_required() and not auth.check_demo_passphrase(
        request.headers.get("x-adapt-demo-passphrase")
    ):
        # The tunnel is world-readable; a passphrase keeps a judge's demo from
        # becoming a public "create a learner" endpoint.
        raise HTTPException(
            status_code=403,
            detail={"error": "demo_passphrase_required", "hint": "set ADAPT_DEMO_PASSPHRASE and send X-ADAPT-Demo-Passphrase"},
        )
    # Kept as typed here; the validation and lookup helpers below normalise for themselves, and
    # the service keeps the typed form as the display fallback so "Rowan.7" is not greeted as
    # "rowan.7".
    username = (body.username or "").strip()
    if not guest_requested(body) and username:
        problem = auth.username_problem(username)
        if problem:
            raise HTTPException(status_code=422, detail=problem)
    if (body.email or username) and not body.password:
        raise HTTPException(status_code=422, detail="an account needs a password of at least 10 characters")
    guest = body.as_guest or not (body.email or username)
    if not guest and username and auth.student_by_username(s, username) is not None:
        audit_event(
            request,
            action="register_existing_username",
            outcome="denied",
            detail={"reason": "username already taken"},
        )
        raise HTTPException(
            status_code=409,
            detail={"error": "username_taken", "next": "pick another username, or sign in if it is yours"},
        )
    if not guest and body.email and auth.student_by_email(s, body.email) is not None:
        # Registration is not a login path. `get_or_create_student` returns the
        # existing row for a known address, so without this guard posting someone's
        # email to /students would hand back a signed-in session for their account.
        audit_event(
            request,
            action="register_existing_email",
            outcome="denied",
            detail={"reason": "address already has an account"},
        )
        raise HTTPException(
            status_code=409,
            detail={
                "error": "email_registered",
                "next": "sign in instead (POST /api/v1/login) - a lost password is reset by whoever runs this instance",
            },
        )
    # Every creation costs one token of the per-connection budget, checked before the
    # row is written so a refused request leaves nothing behind.
    create_key = f"ip:{client_ip(request)}"
    _check_bucket(
        request,
        "guest_create",
        create_key,
        note=(
            "learner creation is capped per connection so a public tunnel cannot be turned into "
            "a learner factory; raise ADAPT_RATE_LIMIT_MULTIPLIER for a rehearsal or a load test"
        ),
    )
    _spend("guest_create", create_key)
    student = S.get_or_create_student(
        s,
        display_name=body.display_name,
        email=None if guest else body.email,
        username=None if guest else (username or None),
        password=body.password,
        age_band=body.age_band,
    )
    if body.consent_analytics is not None:
        student.consent_analytics = body.consent_analytics
    if body.consent_share_with_llm is not None:
        student.consent_share_with_llm = body.consent_share_with_llm
    s.flush()
    login = issue_login(response, s, student, request=request)
    audit_event(
        request,
        action="student_created",
        actor=student.id,
        detail={"guest": guest, "email_set": bool(body.email), "username_set": bool(username)},
    )
    return {**S.profile_summary(s, student.id), **login, "created": True, "onboarding_complete": student.onboarding_complete, "mode": get_settings().mode}


@router.post("/login")
def login(body: LoginBody, request: Request, response: Response, s: Session = Depends(db_session)) -> dict[str, Any]:
    # Two budgets: the address being guessed and the connection doing the guessing. A
    # successful sign-in spends nothing, so a learner is never locked out by their own
    # activity; only failures drain the bucket, and while it is empty the password is
    # not even read.
    acct_key = f"account:{auth.normalise_username(body.username) or (body.email or '').strip().lower()}"
    ip_key = f"ip:{client_ip(request)}"
    for key in (acct_key, ip_key):
        _check_bucket(
            request,
            "auth_fail",
            key,
            note="too many failed attempts at signing in for now. A forgotten password is reset by whoever runs this instance",
        )
    if not (body.username or body.email):
        raise HTTPException(status_code=422, detail="a username and a password are required")
    student = S.authenticate(
        s,
        username=body.username,
        email=body.email,
        password=body.password or "",
    )
    if student is None:
        _spend("auth_fail", acct_key)
        _spend("auth_fail", ip_key)
        audit_event(request, action="login_failed", outcome="denied", detail={"reason": "bad credentials"})
        raise HTTPException(status_code=401, detail="wrong username or password")
    return {**S.profile_summary(s, student.id), **issue_login(response, s, student, request=request), "created": False}


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    s: Session = Depends(db_session),
    student: Student = Depends(require_csrf),
) -> dict[str, Any]:
    row = getattr(request.state, "auth_session", None)
    if row is not None:
        auth.revoke(s, row)
    clear_login(response, request)
    return {"ok": True}


@router.get("/me")
def me(s: Session = Depends(db_session), student: Student = Depends(student_from_request)) -> dict[str, Any]:
    return S.profile_summary(s, student.id)


@router.post("/me/onboarding")
def onboarding(
    body: OnboardingBody,
    s: Session = Depends(db_session),
    student: Student = Depends(require_csrf),
) -> dict[str, Any]:
    return S.complete_onboarding(
        s,
        student.id,
        body.preferences,
        consent_analytics=body.consent_analytics,
        consent_share_with_llm=body.consent_share_with_llm,
        voice_enabled=body.voice_enabled,
        voice_gender=body.voice_gender,
        course_id=body.course_id,
    )


@router.get("/me/preferences")
def get_prefs(s: Session = Depends(db_session), student: Student = Depends(student_from_request)) -> dict[str, Any]:
    return {"preferences": S.get_preferences(s, student.id), "allowed_keys": sorted(S.PREFERENCE_KEYS)}


@router.post("/me/voice")
def set_voice(
    body: VoiceBody,
    s: Session = Depends(db_session),
    student: Student = Depends(require_csrf),
) -> dict[str, Any]:
    """Persist the app-wide voice switch without storing spoken answers."""
    student.voice_enabled = bool(body.enabled)
    if body.voice_gender:
        student.voice_gender = "male" if body.voice_gender.lower().startswith("m") else "female"
    s.commit()
    return {"enabled": student.voice_enabled, "voice_gender": student.voice_gender}


@router.post("/me/preferences")
def set_prefs(
    body: PreferencesBody,
    s: Session = Depends(db_session),
    student: Student = Depends(require_csrf),
) -> dict[str, Any]:
    return S.set_preferences(s, student.id, body.preferences, source=body.source)


@router.delete("/me")
def forget_me(
    response: Response,
    request: Request,
    s: Session = Depends(db_session),
    student: Student = Depends(require_csrf),
) -> dict[str, Any]:
    out = S.soft_delete(s, student.id)
    row = getattr(request.state, "auth_session", None)
    if row is not None:
        auth.revoke(s, row)
    clear_login(response, request)
    audit_event(request, action="student_soft_deleted", actor=student.id, outcome="ok" if out.get("deleted") else "denied")
    return out
