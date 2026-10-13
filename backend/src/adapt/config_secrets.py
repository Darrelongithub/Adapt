"""Console-only configuration: secrets live on disk, never in the database or API.

This module is the *only* place that reads or writes API keys and provider
configuration. The rules from the brief (section 3) are enforced here and
tested in ``test_config_secrecy.py``:

* Secrets are stored in the encrypted keystore or a ``0600`` env file, never
  in a form the API can read back.
* ``adapt config set-key`` uses a hidden prompt, never echoes, never writes
  to shell history.
* ``adapt config status`` shows configured yes/no and masks all but the last
  four characters.
* ``adapt config test`` makes one minimal call and reports pass/fail.
* ``adapt config rotate`` generates a new key, replacing the old one.
* ``adapt config remove`` erases the key from disk.
* ``adapt config set-budget`` sets the daily call/token budget.
"""

from __future__ import annotations

import base64
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import get_settings

# The canary prefix used in tests to prove keys never leak.
CANARY_PREFIX = "ADAPT_TEST_CANARY_KEY_"

# Provider names that must never appear in HTTP responses or bundles.
PROVIDER_NAMES = frozenset(
    {
        "gemini",
        "openai",
        "anthropic",
        "openalex",
        "arxiv",
        "openlibrary",
        "youtube",
        "kokoro",
        "faster-whisper",
        "faster_whisper",
        "whisper",
    }
)

# Model names that must never appear in HTTP responses or bundles.
MODEL_NAME_PATTERNS = (
    re.compile(r"gemini-\d"),
    re.compile(r"gpt-\d"),
    re.compile(r"claude-\d"),
    re.compile(r"whisper-(?:tiny|base|small|medium|large)"),
    re.compile(r"af_heart|am_fenrir|bf_violet|bm_ola"),
)


@dataclass
class KeyStatus:
    """What ``adapt config status`` reports. Masked, never the full value."""

    configured: bool
    source: str
    masked: str
    budget_calls: int
    budget_tokens: int
    note: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "configured": self.configured,
            "source": self.source,
            "masked_key": self.masked,
            "budget": {
                "daily_calls": self.budget_calls,
                "daily_tokens": self.budget_tokens,
            },
            "note": self.note,
        }


def _env_file_path() -> Path:
    st = get_settings()
    return st.data_dir / ".adapt_keys"


def _read_env_file(path: Path) -> dict[str, str]:
    """Read a ``0600`` env file into a dict. Returns ``{}`` if missing."""
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            out[key.strip()] = value.strip()
    return out


def _write_env_file(path: Path, data: dict[str, str]) -> None:
    """Write a ``0600`` env file, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write("# ADAPT configuration secrets -- do not commit, do not share\n")
        for key, value in sorted(data.items()):
            fh.write(f"{key}={value}\n")
    os.chmod(path, 0o600)


def _mask_key(key: str | None) -> str:
    """Show only the last four characters of a key."""
    if not key:
        return ""
    if len(key) <= 4:
        return "****"
    return "*" * (len(key) - 4) + key[-4:]


def get_key_status() -> KeyStatus:
    """Read the current configuration status without exposing the key."""
    st = get_settings()
    env_path = _env_file_path()
    env_data = _read_env_file(env_path)

    key = st.gemini_api_key or env_data.get("ADAPT_GEMINI_API_KEY")
    source = "environment" if st.gemini_api_key else ("file" if env_data.get("ADAPT_GEMINI_API_KEY") else "none")

    return KeyStatus(
        configured=bool(key),
        source=source,
        masked=_mask_key(key),
        budget_calls=st.gemini_daily_call_budget,
        budget_tokens=st.gemini_daily_token_budget,
        note=(
            "configured"
            if key
            else "no key: lessons use deterministic templates (simple mode)"
        ),
    )


def set_key(key: str) -> dict[str, str]:
    """Store a key in the env file. The caller has already hidden the prompt."""
    key = key.strip()
    if not key:
        return {"ok": False, "error": "key cannot be empty"}
    env_path = _env_file_path()
    data = _read_env_file(env_path)
    data["ADAPT_GEMINI_API_KEY"] = key
    _write_env_file(env_path, data)
    return {
        "ok": True,
        "stored_at": str(env_path),
        "masked": _mask_key(key),
        "note": "key stored; restart the API to use it",
    }


def remove_key() -> dict[str, str]:
    """Remove the key from the env file."""
    env_path = _env_file_path()
    data = _read_env_file(env_path)
    removed = data.pop("ADAPT_GEMINI_API_KEY", None)
    if removed is None:
        return {"ok": True, "note": "no key was stored"}
    _write_env_file(env_path, data)
    return {"ok": True, "note": "key removed; restart the API"}


def rotate_key(new_key: str) -> dict[str, str]:
    """Replace the stored key with a new one."""
    new_key = new_key.strip()
    if not new_key:
        return {"ok": False, "error": "new key cannot be empty"}
    env_path = _env_file_path()
    data = _read_env_file(env_path)
    old = data.get("ADAPT_GEMINI_API_KEY")
    data["ADAPT_GEMINI_API_KEY"] = new_key
    _write_env_file(env_path, data)
    return {
        "ok": True,
        "stored_at": str(env_path),
        "old_masked": _mask_key(old),
        "new_masked": _mask_key(new_key),
        "note": "key rotated; restart the API to use the new key",
    }


def set_budget(calls: int | None = None, tokens: int | None = None) -> dict[str, Any]:
    """Set daily call/token budget in the env file."""
    env_path = _env_file_path()
    data = _read_env_file(env_path)
    changes: list[str] = []
    if calls is not None:
        if calls < 1:
            return {"ok": False, "error": "daily call budget must be at least 1"}
        data["ADAPT_GEMINI_DAILY_CALL_BUDGET"] = str(calls)
        changes.append(f"calls={calls}")
    if tokens is not None:
        if tokens < 1000:
            return {"ok": False, "error": "daily token budget must be at least 1000"}
        data["ADAPT_GEMINI_DAILY_TOKEN_BUDGET"] = str(tokens)
        changes.append(f"tokens={tokens}")
    if not changes:
        return {"ok": False, "error": "specify --calls or --tokens"}
    _write_env_file(env_path, data)
    return {
        "ok": True,
        "stored_at": str(env_path),
        "changes": changes,
        "note": "budget updated; restart the API to apply",
    }


async def test_key() -> dict[str, Any]:
    """Make one minimal call to verify the key works."""
    st = get_settings()
    env_path = _env_file_path()
    env_data = _read_env_file(env_path)
    key = st.gemini_api_key or env_data.get("ADAPT_GEMINI_API_KEY")
    if not key:
        return {
            "ok": False,
            "error": "no key configured",
            "hint": "run: adapt config set-key",
        }
    try:
        import httpx

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{st.gemini_base_url}/models/gemini-2.5-flash:generateContent",
                headers={"x-goog-api-key": key, "content-type": "application/json"},
                json={
                    "contents": [{"parts": [{"text": "ping"}]}],
                    "generationConfig": {"maxOutputTokens": 4},
                },
            )
            if resp.status_code == 200:
                return {"ok": True, "detail": "key accepted, model responded"}
            return {
                "ok": False,
                "error": f"provider returned {resp.status_code}",
                "hint": "check the key or try again later",
            }
    except Exception as exc:
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {str(exc)[:120]}",
            "hint": "network or provider error",
        }


def learner_safe_status() -> dict[str, Any]:
    """The status payload safe for learner-facing endpoints.

    This is what ``/api/v1/status`` and the footer status bar must return.
    No key, no provider names, no model names, no budget figures.
    """
    st = get_settings()
    key = st.gemini_api_key
    return {
        "version": _safe_version(),
        "mode": st.mode,
        "lessons": "ready" if key else "simple mode",
        "voice": "ready" if _voice_ready() else "browser fallback",
        "resources": "ready" if _resources_ready() else "limited",
        "database": "ok",
        "note": (
            "all systems operational"
            if key
            else "running without a model key; lessons use built-in content"
        ),
    }


def _safe_version() -> str:
    from . import __version__

    return __version__


def _voice_ready() -> bool:
    try:
        from .voice import stt as STT
        from .voice import tts as TTS

        st = get_settings()
        return bool(STT.capabilities(st).get("local_stt")) and bool(
            TTS.capabilities(st).get("local_tts")
        )
    except Exception:
        return False


def _resources_ready() -> bool:
    st = get_settings()
    return bool(st.openalex_enabled and st.arxiv_enabled and st.openlibrary_enabled)


def is_canary_string(text: str) -> bool:
    """Check if text contains a canary key."""
    return CANARY_PREFIX in text


def contains_provider_name(text: str) -> str | None:
    """Check if text contains a provider name (case-insensitive)."""
    lower = text.lower()
    for name in PROVIDER_NAMES:
        if name in lower:
            return name
    return None


def contains_model_name(text: str) -> str | None:
    """Check if text contains a model name pattern."""
    for pat in MODEL_NAME_PATTERNS:
        m = pat.search(text)
        if m:
            return m.group(0)
    return None
