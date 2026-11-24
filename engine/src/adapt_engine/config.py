"""Typed startup configuration with ADAPT_ prefix.

Fail closed: a missing or unsafe value raises ConfigError naming the
variable, and the entrypoint exits non-zero. Secrets are held as
SecretStr and are never logged or returned by any endpoint.
"""

from __future__ import annotations

import os
import re
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

#: Master key ids appear inside every ciphertext header, so they are boring by
#: construction: short, lowercase, no punctuation.
_KID_PATTERN = re.compile(r"^[a-z0-9]{1,16}$")


class Settings(BaseModel):
    """Runtime settings. Required fields have no defaults."""

    env: str = Field(description="ADAPT_ENV")
    database_url: str = Field(description="ADAPT_DATABASE_URL")
    master_key: SecretStr = Field(description="ADAPT_MASTER_KEY")
    master_key_id: str = Field(default="k1", description="ADAPT_MASTER_KEY_ID")
    #: The key `master_key` replaced. Kept so ciphertext written before a
    #: rotation still opens; the engine refuses to start when a kid in use is
    #: missing from the ring.
    master_key_previous: SecretStr | None = Field(
        default=None, description="ADAPT_MASTER_KEY_PREVIOUS"
    )
    master_key_previous_id: str | None = Field(
        default=None, description="ADAPT_MASTER_KEY_PREVIOUS_ID"
    )
    key_pepper: SecretStr = Field(description="ADAPT_KEY_PEPPER")
    redis_url: str = Field(default="redis://localhost:6379/0", description="ADAPT_REDIS_URL")
    commit: str = Field(default="unknown", description="ADAPT_COMMIT")
    build_time: str = Field(default="unknown", description="ADAPT_BUILD_TIME")
    version: str = Field(default="0.1.0", description="ADAPT_VERSION")
    #: Outbound webhook delivery. Off by default: AGENT_M1 §0 forbids network
    #: egress and §1 requires webhooks, so the switch is how both are true at
    #: once — deliveries are recorded either way, attempted only when enabled.
    webhook_delivery: str = Field(default="disabled", description="ADAPT_WEBHOOK_DELIVERY")

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

        master_key_id = (source.get("ADAPT_MASTER_KEY_ID") or "k1").strip() or "k1"
        if not _KID_PATTERN.match(master_key_id):
            raise ConfigError(
                "ADAPT_MASTER_KEY_ID",
                "must be 1-16 lowercase letters or digits (it appears in every header)",
            )

        master_key_previous: str | None = None
        master_key_previous_id: str | None = None
        raw_previous = (source.get("ADAPT_MASTER_KEY_PREVIOUS") or "").strip()
        if raw_previous:
            if len(raw_previous.encode("utf-8")) < 32:
                raise ConfigError(
                    "ADAPT_MASTER_KEY_PREVIOUS",
                    "must be at least 32 bytes",
                )
            master_key_previous = raw_previous
            master_key_previous_id = (
                source.get("ADAPT_MASTER_KEY_PREVIOUS_ID") or "k0"
            ).strip() or "k0"
            if not _KID_PATTERN.match(master_key_previous_id):
                raise ConfigError(
                    "ADAPT_MASTER_KEY_PREVIOUS_ID",
                    "must be 1-16 lowercase letters or digits",
                )
            if master_key_previous_id == master_key_id:
                raise ConfigError(
                    "ADAPT_MASTER_KEY_PREVIOUS_ID",
                    "must differ from ADAPT_MASTER_KEY_ID: two keys under one id "
                    "would make rotation a coin flip",
                )
        elif (source.get("ADAPT_MASTER_KEY_PREVIOUS_ID") or "").strip():
            raise ConfigError(
                "ADAPT_MASTER_KEY_PREVIOUS",
                "is required when ADAPT_MASTER_KEY_PREVIOUS_ID is set",
            )

        key_pepper = _required("ADAPT_KEY_PEPPER")

        redis_url = (source.get("ADAPT_REDIS_URL") or "redis://localhost:6379/0").strip()

        webhook_delivery = (source.get("ADAPT_WEBHOOK_DELIVERY") or "disabled").strip().lower()
        if webhook_delivery not in ("disabled", "enabled"):
            raise ConfigError(
                "ADAPT_WEBHOOK_DELIVERY",
                "must be 'disabled' or 'enabled'",
            )

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
            master_key_id=master_key_id,
            master_key_previous=(SecretStr(master_key_previous) if master_key_previous else None),
            master_key_previous_id=master_key_previous_id,
            key_pepper=SecretStr(key_pepper),
            redis_url=redis_url,
            webhook_delivery=webhook_delivery,
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
        for secret in (self.master_key, self.master_key_previous):
            if secret is not None and secret.get_secret_value() in _DEV_MASTER_KEYS:
                name = (
                    "ADAPT_MASTER_KEY" if secret is self.master_key else "ADAPT_MASTER_KEY_PREVIOUS"
                )
                raise ConfigError(
                    name,
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
