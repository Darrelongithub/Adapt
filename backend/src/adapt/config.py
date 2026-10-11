"""Runtime configuration.

Everything is environment driven so the same code runs in three modes:

1. ``local``  -- ``http://localhost:3000`` on the demo machine.
2. ``tunnel`` -- behind localtunnel / Cloudflare Tunnel for judges (HTTPS).
3. ``test``   -- ephemeral SQLite file, no network, no models.

Every external dependency (Gemini, OpenAlex, arXiv, Open Library, YouTube,
faster-whisper, Kokoro) is optional at runtime and degrades to a local
implementation instead of failing the demo -- the blueprint's "a free-tier limit
must not kill the demo" rule.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, computed_field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Mode = Literal["local", "tunnel", "test"]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ADAPT_",
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # -- general ---------------------------------------------------------
    app_name: str = "ADAPT"
    mode: Mode = "local"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000
    data_dir: Path = _repo_root() / "var"
    log_level: str = "INFO"

    # -- frontend / transport ------------------------------------------
    # The browser only ever talks to the Next.js origin; Next proxies /api and
    # /ws to us. That keeps the demo working when the sandbox/browser and the
    # backend are different machines (tunnel, e2b preview, ...).
    frontend_origin: str = "http://localhost:3000"
    allowed_origins: list[str] = Field(default_factory=list)
    # Extra hosts accepted for WebSocket / redirect safety (tunnel hostnames).
    extra_allowed_hosts: list[str] = Field(default_factory=list)
    require_https: bool = False  # set true when serving the judge tunnel with TLS
    tls_certfile: Path | None = None
    tls_keyfile: Path | None = None
    max_body_bytes: int = 8 * 1024 * 1024  # 8 MB: enough for a voice clip upload

    # -- database + encryption at rest ---------------------------------
    db_path: Path | None = None
    # auto = SQLCipher when the driver is importable and a key is available,
    #         field-level encryption only otherwise (never silently unencrypted
    #         for learner text).
    db_encryption: Literal["auto", "sqlcipher", "field-only", "off"] = "auto"
    db_key: str | None = None  # base64 32 bytes; prefer ADAPT_KEYFILE
    keyfile: Path | None = None
    db_key_passphrase_env: str = "ADAPT_DB_PASSPHRASE"
    sqlcipher_kdf_iter: int = 256000
    sqlcipher_memory: int = 128 * 1024 * 1024  # bytes, PRAGMA cipher_memory_capacity
    enforce_field_encryption: bool = True
    # Scales every token bucket (see security/ratelimit.py). Keep 1.0 on a tunnel;
    # raise it for load tests or a demo rehearsed behind a shared NAT.
    rate_limit_multiplier: float = 1.0

    # -- auth ------------------------------------------------------------
    session_ttl_hours: int = 12
    cookie_name: str = "adapt_session"
    csrf_cookie_name: str = "adapt_csrf"
    argon2_time_cost: int = 3
    argon2_memory_kib: int = 64 * 1024

    # -- Gemini (AI 1 + AI 2) -------------------------------------------
    gemini_api_key: str | None = None
    gemini_model_ai1: str = "gemini-2.5-flash"
    gemini_model_ai2: str = "gemini-2.5-flash"
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    gemini_timeout_s: float = 45.0
    # Free-tier guard: stop calling before the daily quota is exhausted and let
    # the local reasoner carry the demo instead.
    gemini_daily_call_budget: int = 120
    gemini_daily_token_budget: int = 2_000_000
    gemini_min_interval_ms: int = 700  # smooths RPM limits
    gemini_max_retries: int = 2

    # -- local reasoner fallback ----------------------------------------
    # Never fail the demo because a network or quota limit tripped.
    allow_local_fallback: bool = True

    # -- external learning resources (all free tier) --------------------
    openalex_enabled: bool = True
    openalex_mailto: str | None = None  # polite pool -> higher free throughput
    arxiv_enabled: bool = True
    openlibrary_enabled: bool = True
    youtube_api_key: str | None = None  # optional; quota-free key required
    resource_cache_ttl_hours: int = 24 * 7
    resource_timeout_s: float = 12.0
    resource_max_results: int = 6

    # -- Numbas ----------------------------------------------------------
    numbas_export_dir: Path | None = None
    numbas_bank_path: Path | None = None  # JSON/.numbas package(s) to import

    # -- voice -----------------------------------------------------------
    voice_enabled: bool = True
    whisper_model_size: Literal["tiny", "base", "small", "medium", "large-v3"] = "base"
    whisper_compute_type: Literal["int8", "float16", "float32"] = "int8"
    whisper_device: Literal["auto", "cpu", "cuda"] = "auto"
    tts_engine: Literal["auto", "kokoro", "off"] = "auto"
    kokoro_lang: str = "a"  # 'a' American, 'b' British (verified in kokoro 0.9.4)
    kokoro_voice_female: str = "af_heart"
    kokoro_voice_male: str = "am_fenrir"
    sample_rate: int = 16_000  # STT input
    tts_sample_rate: int = 24_000  # Kokoro output rate (verified)
    vad_backend: Literal["auto", "energy", "webrtc"] = "auto"
    vad_hangover_ms: int = 600
    vad_min_speech_ms: int = 240
    voice_language: str = "en"  # STT language hint + TTS phonemiser language
    barge_in_enabled: bool = True
    barge_in_speech_ms: int = 220  # sustained speech needed to interrupt TTS

    # -- learning science knobs ----------------------------------------
    bkt_em_enabled: bool = True
    mastery_proficient: float = 0.80
    mastery_developing: float = 0.55
    gap_threshold: float = 0.45
    min_evidence_for_proposal: int = 6  # events/attempts before proposing
    proposal_cooldown_minutes: int = 20
    retention_target: float = 0.85  # desired expected-recall before scheduling out
    enable_learning_styles_labels: bool = False  # guarded off; see profile.py

    # -- misc ------------------------------------------------------------
    seed_demo_students: bool = False
    audit_log_enabled: bool = True

    @field_validator(
        "keyfile",
        "db_path",
        "db_key",
        "tls_certfile",
        "tls_keyfile",
        "numbas_export_dir",
        "numbas_bank_path",
        "gemini_api_key",
        "youtube_api_key",
        "openalex_mailto",
        "mode",
        mode="before",
    )
    @classmethod
    def _blank_is_unset(cls, v: object) -> object:
        """``ADAPT_KEYFILE=`` means "not set", not "the empty path".

        A deployer writes ``Environment=ADAPT_KEYFILE=`` in systemd or leaves the value out
        of a compose file, and pydantic hands us ``""`` -- which became ``PosixPath('.')``,
        and the app died with ``ValueError: PosixPath('.') has an empty name`` from inside
        ``with_suffix`` while building the keyring. An unusable path with a confusing crash
        is worse than an absent one with a default, so the blank collapses to the default.
        """
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("data_dir", mode="before")
    @classmethod
    def _data_dir_never_blank(cls, v: object) -> object:
        if v is None or (isinstance(v, str) and not v.strip()):
            return _repo_root() / "var"
        return v

    @field_validator("allowed_origins", "extra_allowed_hosts", mode="before")
    @classmethod
    def _split_list(cls, v: object) -> object:
        if isinstance(v, str):
            return [x.strip() for x in v.split(",") if x.strip()]
        return v

    @computed_field  # type: ignore[prop-decorator]
    @property
    def origins(self) -> list[str]:
        base = [self.frontend_origin]
        if self.debug:
            base += ["http://localhost:3000", "http://127.0.0.1:3000"]
        base += self.allowed_origins
        # Deduplicate, preserve order.
        return list(dict.fromkeys(o for o in base if o))

    @property
    def db_file(self) -> Path:
        return self.db_path or (self.data_dir / "adapt.db")

    @property
    def keyfile_path(self) -> Path:
        return self.keyfile or (self.data_dir / "keys" / "adapt.key")

    @property
    def numbas_dir(self) -> Path:
        return self.numbas_export_dir or (self.data_dir / "numbas")

    @property
    def has_gemini(self) -> bool:
        return bool(self.gemini_api_key)

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.keyfile_path.parent, self.numbas_dir):
            Path(d).mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    if os.environ.get("ADAPT_DISABLE_DIR_CREATE"):
        pass
    return s


def reset_settings_cache() -> None:
    get_settings.cache_clear()
