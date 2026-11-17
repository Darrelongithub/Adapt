"""Fail-closed startup tests: missing or weak config exits non-zero."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from adapt_engine.config import Settings
from adapt_engine.errors import ConfigError

ENGINE_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ENGINE_ROOT / "src"

VALID_ENV = {
    "ADAPT_ENV": "test",
    "ADAPT_DATABASE_URL": "postgresql://test:test@localhost:5432/adapt_test",
    "ADAPT_MASTER_KEY": "test-master-key-32-bytes-long-xxxx",
    "ADAPT_KEY_PEPPER": "test-pepper-synthetic-only",
    "ADAPT_REDIS_URL": "redis://localhost:6379/0",
    "ADAPT_COMMIT": "test-commit-synthetic",
    "ADAPT_BUILD_TIME": "2026-11-17T00:00:00Z",
    "ADAPT_VERSION": "0.1.0",
}

REQUIRED_VARS = [
    "ADAPT_ENV",
    "ADAPT_DATABASE_URL",
    "ADAPT_MASTER_KEY",
    "ADAPT_KEY_PEPPER",
]


def _run_entrypoint(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    full_env = dict(os.environ)
    # Clear any ADAPT_ vars inherited from the outer environment.
    for key in list(full_env.keys()):
        if key.startswith("ADAPT_"):
            del full_env[key]
    full_env.update(env)
    full_env["PYTHONPATH"] = str(SRC_DIR)
    return subprocess.run(
        [sys.executable, "-m", "adapt_engine.main"],
        cwd=str(ENGINE_ROOT),
        env=full_env,
        capture_output=True,
        text=True,
        timeout=15,
    )


@pytest.mark.parametrize("missing", REQUIRED_VARS)
def test_missing_required_variable_exits_nonzero(missing: str) -> None:
    env = dict(VALID_ENV)
    del env[missing]
    result = _run_entrypoint(env)
    assert result.returncode != 0
    combined = result.stderr + result.stdout
    assert missing in combined


def test_empty_required_variable_exits_nonzero() -> None:
    env = dict(VALID_ENV)
    env["ADAPT_DATABASE_URL"] = ""
    result = _run_entrypoint(env)
    assert result.returncode != 0
    assert "ADAPT_DATABASE_URL" in (result.stderr + result.stdout)


def test_weak_master_key_exits_nonzero() -> None:
    env = dict(VALID_ENV)
    env["ADAPT_MASTER_KEY"] = "short"
    result = _run_entrypoint(env)
    assert result.returncode != 0
    assert "ADAPT_MASTER_KEY" in (result.stderr + result.stdout)


def test_production_with_dev_default_exits_nonzero() -> None:
    env = dict(VALID_ENV)
    env["ADAPT_ENV"] = "production"
    # database_url still points at localhost (development default).
    result = _run_entrypoint(env)
    assert result.returncode != 0
    combined = result.stderr + result.stdout
    assert "ADAPT_DATABASE_URL" in combined or "ADAPT_ENV" in combined


def test_settings_from_env_names_missing_variable() -> None:
    env = dict(VALID_ENV)
    del env["ADAPT_KEY_PEPPER"]
    with pytest.raises(ConfigError) as excinfo:
        Settings.from_env(env)
    assert "ADAPT_KEY_PEPPER" in str(excinfo.value)


def test_settings_from_env_accepts_valid() -> None:
    settings = Settings.from_env(dict(VALID_ENV))
    assert settings.env == "test"
    assert settings.commit == "test-commit-synthetic"
