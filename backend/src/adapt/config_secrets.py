"""Console-only storage for model-provider credentials and configured budgets.

Secrets live in one owner-only environment file below ``ADAPT_DATA_DIR``.  The file
is intentionally separate from the learner database: no API query, learner export,
or database dump can read it.  Only the names in :data:`MANAGED_KEYS` are accepted;
this is not a general-purpose environment-file editor.

The settings loader integration is deliberately a small public helper so the runtime
can merge this file without duplicating parsing rules.  Explicit process environment
variables always win over values in this file.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import stat
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

SECRET_FIELDS: dict[str, tuple[str, str]] = {
    "gemini": ("ADAPT_GEMINI_API_KEY", "gemini_api_key"),
    "youtube": ("ADAPT_YOUTUBE_API_KEY", "youtube_api_key"),
}
BUDGET_FIELDS: dict[str, str] = {
    "ADAPT_GEMINI_DAILY_CALL_BUDGET": "gemini_daily_call_budget",
    "ADAPT_GEMINI_DAILY_TOKEN_BUDGET": "gemini_daily_token_budget",
}
MANAGED_KEYS = frozenset(
    [env_name for env_name, _field in SECRET_FIELDS.values()] + list(BUDGET_FIELDS)
)
_SERVICE_NAMES = frozenset(SECRET_FIELDS)
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")


class ConfigSecretError(ValueError):
    """Safe configuration error; messages never contain a submitted secret."""


def config_file(data_dir: str | os.PathLike[str]) -> Path:
    """Return the private, non-database settings file for this installation."""
    return Path(data_dir).expanduser() / "keys" / "console.env"


def _ensure_private_parent(path: Path) -> None:
    parent = path.parent
    if parent.is_symlink():
        raise ConfigSecretError("configuration directory must not be a symbolic link")
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if parent.is_symlink():
        raise ConfigSecretError("configuration directory must not be a symbolic link")
    try:
        os.chmod(parent, 0o700)
    except OSError as exc:
        raise ConfigSecretError("could not secure the configuration directory") from exc


def _unquote(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return ""
    if raw.startswith('"'):
        try:
            value = json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise ConfigSecretError(
                "managed settings file contains an invalid quoted value"
            ) from exc
        if not isinstance(value, str):
            raise ConfigSecretError("managed settings file contains an invalid value")
        return value
    if raw.startswith("'"):
        if len(raw) < 2 or not raw.endswith("'"):
            raise ConfigSecretError("managed settings file contains an invalid quoted value")
        return raw[1:-1]
    # Unquoted values are accepted only for numeric budget settings. Do not
    # implement shell expansion, command substitution, or inline comments.
    return raw


def read_managed_values(path: str | os.PathLike[str]) -> dict[str, str]:
    """Read the allowlisted environment file and repair it to mode ``0600``.

    Unknown keys are rejected instead of silently turning this file into a way to
    inject arbitrary runtime configuration.  The CLI never prints parser errors
    that include a line value.
    """
    target = Path(path).expanduser()
    if target.parent.is_symlink():
        raise ConfigSecretError("configuration directory must not be a symbolic link")
    if target.is_symlink():
        raise ConfigSecretError("managed settings file must not be a symbolic link")
    if not target.exists():
        return {}
    try:
        info = target.stat()
        if not stat.S_ISREG(info.st_mode):
            raise ConfigSecretError("managed settings path is not a regular file")
        if stat.S_IMODE(info.st_mode) != 0o600:
            os.chmod(target, 0o600)
        text = target.read_text(encoding="utf-8")
    except ConfigSecretError:
        raise
    except OSError as exc:
        raise ConfigSecretError("could not read managed settings file") from exc

    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            raise ConfigSecretError("managed settings file has an invalid entry")
        name, raw = stripped.split("=", 1)
        name = name.strip()
        if not _ENV_NAME.fullmatch(name) or name not in MANAGED_KEYS:
            raise ConfigSecretError("managed settings file contains an unsupported setting")
        values[name] = _validate_value(name, _unquote(raw))
    return values


def _validate_value(name: str, value: str) -> str:
    if name not in MANAGED_KEYS:
        raise ConfigSecretError("unsupported console setting")
    text = str(value)
    if "\x00" in text or "\n" in text or "\r" in text:
        raise ConfigSecretError("value must be a single line")
    if name in BUDGET_FIELDS:
        try:
            number = int(text)
        except ValueError as exc:
            raise ConfigSecretError("budget must be a whole number") from exc
        if number < 0 or number > 1_000_000_000:
            raise ConfigSecretError("budget is outside the supported range")
        return str(number)
    if not text.strip():
        raise ConfigSecretError("key cannot be empty")
    if len(text) > 4096:
        raise ConfigSecretError("key is too long")
    # A key pasted from a password manager, notes app or browser can arrive with leading
    # or trailing whitespace; sending that to the provider is a *rejected key*, which is
    # indistinguishable from a wrong one at the console. The value has no meaningful
    # surrounding space, so trim it once, at the boundary.
    return text.strip()


def write_managed_values(path: str | os.PathLike[str], values: Mapping[str, Any]) -> Path:
    """Atomically replace the allowlisted settings file with owner-only permissions."""
    target = Path(path).expanduser()
    _ensure_private_parent(target)
    if target.is_symlink():
        raise ConfigSecretError("managed settings file must not be a symbolic link")
    current = read_managed_values(target)
    merged = dict(current)
    for name, value in values.items():
        if value is None:
            merged.pop(name, None)
        else:
            merged[name] = _validate_value(name, str(value))

    lines = [f"{name}={json.dumps(merged[name], ensure_ascii=False)}" for name in sorted(merged)]
    payload = ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")
    fd = -1
    temporary: str | None = None
    try:
        fd, temporary = tempfile.mkstemp(prefix=".console-", suffix=".tmp", dir=target.parent)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            fd = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        temporary = None
        os.chmod(target, 0o600)
        # Persist the directory entry where the platform supports it.
        try:
            dir_fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    except OSError as exc:
        raise ConfigSecretError("could not safely write managed settings file") from exc
    finally:
        if fd >= 0:
            os.close(fd)
        if temporary:
            with contextlib.suppress(OSError):
                os.unlink(temporary)
    return target


def set_key(data_dir: str | os.PathLike[str], service: str, secret: str) -> Path:
    """Store one API key without returning or logging its value."""
    service = service.lower().strip()
    if service not in _SERVICE_NAMES:
        raise ConfigSecretError("unsupported service")
    env_name, _field = SECRET_FIELDS[service]
    value = _validate_value(env_name, secret)
    return write_managed_values(config_file(data_dir), {env_name: value})


def remove_key(data_dir: str | os.PathLike[str], service: str) -> Path:
    service = service.lower().strip()
    if service not in _SERVICE_NAMES:
        raise ConfigSecretError("unsupported service")
    env_name, _field = SECRET_FIELDS[service]
    return write_managed_values(config_file(data_dir), {env_name: None})


def set_budgets(data_dir: str | os.PathLike[str], *, calls: int, tokens: int) -> Path:
    return write_managed_values(
        config_file(data_dir),
        {
            "ADAPT_GEMINI_DAILY_CALL_BUDGET": calls,
            "ADAPT_GEMINI_DAILY_TOKEN_BUDGET": tokens,
        },
    )


def masked_last_four(value: str | None) -> str | None:
    """Return no prefix or length information, and never reveal a <=4 character key."""
    if not value:
        return None
    return "****" + value[-4:] if len(value) > 4 else "****"


def effective_values(settings: Any, path: str | os.PathLike[str] | None = None) -> dict[str, str]:
    """Resolve process env > managed file > existing settings/env-file values."""
    values = read_managed_values(path or config_file(settings.data_dir))
    out: dict[str, str] = {}
    for env_name, field_name in [*SECRET_FIELDS.values(), *BUDGET_FIELDS.items()]:
        explicit = os.environ.get(env_name)
        if explicit is not None and str(explicit).strip():
            out[env_name] = _validate_value(env_name, str(explicit))
        elif env_name in values:
            out[env_name] = values[env_name]
        else:
            existing = getattr(settings, field_name, None)
            if existing is not None:
                out[env_name] = str(existing)
    return out


def settings_with_managed_values(settings: Any, path: str | os.PathLike[str] | None = None) -> Any:
    """Copy a Settings object with managed values applied (process env still wins).

    ``get_settings`` calls this in the runtime integration.  The CLI also uses it
    directly for the one-call connection test, avoiding a write to the process
    environment and keeping the stored secret out of unrelated commands.
    """
    effective = effective_values(settings, path)
    updates: dict[str, Any] = {}
    for env_name, field_name in [*SECRET_FIELDS.values(), *BUDGET_FIELDS.items()]:
        if env_name not in effective:
            continue
        value: Any = effective[env_name]
        if field_name.endswith("_budget"):
            try:
                value = int(value)
            except ValueError as exc:
                raise ConfigSecretError("managed budget value is invalid") from exc
        updates[field_name] = value
    return settings.model_copy(update=updates)


def value_sources(settings: Any, path: str | os.PathLike[str] | None = None) -> dict[str, str]:
    """Where each managed value came from, so a shadowed console file is visible.

    ``effective_values`` resolves process env > managed file > settings/.env, but until
    this existed the console only said "configured": an ``ADAPT_GEMINI_API_KEY`` exported
    in the shell or left in a ``.env`` silently won over the key just typed into
    ``adapt config set-key``, and re-entering the key appeared to do nothing.
    """
    values = read_managed_values(path or config_file(settings.data_dir))
    out: dict[str, str] = {}
    for env_name, field_name in [*SECRET_FIELDS.values(), *BUDGET_FIELDS.items()]:
        explicit = os.environ.get(env_name)
        if explicit is not None and str(explicit).strip():
            out[env_name] = "the environment"
        elif env_name in values:
            out[env_name] = "the console file"
        elif getattr(settings, field_name, None) is not None:
            out[env_name] = "the environment file (.env)"
    return out


def status_rows(settings: Any, path: str | os.PathLike[str] | None = None) -> list[dict[str, Any]]:
    effective = effective_values(settings, path)
    sources = value_sources(settings, path)
    rows: list[dict[str, Any]] = []
    for service, (env_name, _field_name) in SECRET_FIELDS.items():
        secret = effective.get(env_name)
        rows.append(
            {
                "service": service,
                "configured": bool(secret),
                "masked": masked_last_four(secret),
                "source": sources.get(env_name, ""),
            }
        )
    return rows
