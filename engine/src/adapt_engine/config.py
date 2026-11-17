"""Typed startup configuration with ADAPT_ prefix.

Fail closed: a missing or unsafe value raises ConfigError naming the
variable, and the entrypoint exits non-zero. Secrets are held as
SecretStr and are never logged or returned by any endpoint.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from pydantic import BaseModel, Field, SecretStr

from adapt_engine.errors import ConfigError

_ALLOWED_ENVS = ("development", "test", "staging", "production")

# Known weak values that must never be used in production.
_DEV_MASTER_KEYS = frozenset(
    {
        "dev-master-key",
        "development-master-key",
        "test-master-key",
        "change-me",
        "changeme",
        "secret",
        "password",
    }
)
_DEV_PEPPERS = frozenset(
    {
        "dev-pepper",
        "development-pepper",
        "test-pepper",
        "change-me",
        "changeme",
        "secret",
        "password",
    }
)


# Build identities that are placeholders, never a production release.
_DEV_BUILD_IDENTITIES = frozenset({"unknown", "local", "dev", "test", "development"})


class Settings(BaseModel):
    """Runtime settings. Required fields have no defaults."""

    env: str = Field(description="ADAPT_ENV")
    database_url: str = Field(description="ADAPT_DATABASE_URL")
    master_key: SecretStr = Field(description="ADAPT_MASTER_KEY")
    key_pepper: SecretStr = Field(description="ADAPT_KEY_PEPPER")
    redis_url: str = Field(default="redis://localhost:6379/0", description="ADAPT_REDIS_URL")
    commit: str = Field(default="unknown", description="ADAPT_COMMIT")
    build_time: str = Field(default="unknown", description="ADAPT_BUILD_TIME")
    version: str = Field(default="0.1.0", description="ADAPT_VERSION")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        """Load and check settings from the environment.

        Raises ConfigError naming the first offending variable.
        """
        source: Mapping[str, str] = os.environ if env is None else env

        def _required(name: str) -> str:
            value = source.get(name)
            if value is None or not value.strip():
                raise ConfigError(name, "is required but is missing or empty")
            return value.strip()

        env_value = _required("ADAPT_ENV")
        if env_value not in _ALLOWED_ENVS:
            raise ConfigError(
                "ADAPT_ENV",
                "must be one of development, test, staging, production",
            )

        database_url = _required("ADAPT_DATABASE_URL")
        if not (
            database_url.startswith("postgresql://")
            or database_url.startswith("postgres://")
            or database_url.startswith("postgresql+")
            or database_url.startswith("sqlite://")
        ):
            raise ConfigError(
                "ADAPT_DATABASE_URL",
                "must be a postgresql:// or sqlite:// URL",
            )

        master_key = _required("ADAPT_MASTER_KEY")
        if len(master_key.encode("utf-8")) < 32:
            raise ConfigError(
                "ADAPT_MASTER_KEY",
                "must be at least 32 bytes",
            )

        key_pepper = _required("ADAPT_KEY_PEPPER")

        redis_url = (source.get("ADAPT_REDIS_URL") or "redis://localhost:6379/0").strip()

        # Build identity is required in production: a health check that cannot
        # say which build is running is not an incident tool.
        if env_value == "production":
            commit = _required("ADAPT_COMMIT")
            version = _required("ADAPT_VERSION")
        else:
            commit = (source.get("ADAPT_COMMIT") or "unknown").strip() or "unknown"
            version = (source.get("ADAPT_VERSION") or "0.1.0").strip() or "0.1.0"
        build_time = (source.get("ADAPT_BUILD_TIME") or "unknown").strip() or "unknown"

        settings = cls(
            env=env_value,
            database_url=database_url,
            master_key=SecretStr(master_key),
            key_pepper=SecretStr(key_pepper),
            redis_url=redis_url,
            commit=commit,
            build_time=build_time,
            version=version,
        )
        settings._check_production_defaults()
        return settings

    def _check_production_defaults(self) -> None:
        """Refuse production with any development default."""
        if self.env != "production":
            return
        if "localhost" in self.database_url or "127.0.0.1" in self.database_url:
            raise ConfigError(
                "ADAPT_DATABASE_URL",
                "must not use a development default (localhost) when ADAPT_ENV=production",
            )
        if "localhost" in self.redis_url or "127.0.0.1" in self.redis_url:
            raise ConfigError(
                "ADAPT_REDIS_URL",
                "must not use a development default (localhost) when ADAPT_ENV=production",
            )
        if self.master_key.get_secret_value() in _DEV_MASTER_KEYS:
            raise ConfigError(
                "ADAPT_MASTER_KEY",
                "must not use a development default when ADAPT_ENV=production",
            )
        if self.key_pepper.get_secret_value() in _DEV_PEPPERS:
            raise ConfigError(
                "ADAPT_KEY_PEPPER",
                "must not use a development default when ADAPT_ENV=production",
            )
        if self.commit in _DEV_BUILD_IDENTITIES:
            raise ConfigError(
                "ADAPT_COMMIT",
                "must be a real commit identifier, not a development placeholder, "
                "when ADAPT_ENV=production",
            )
        if self.version in _DEV_BUILD_IDENTITIES:
            raise ConfigError(
                "ADAPT_VERSION",
                "must be a real version, not a development placeholder, when ADAPT_ENV=production",
            )
