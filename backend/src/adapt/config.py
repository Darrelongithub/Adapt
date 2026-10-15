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

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, computed_field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

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
    # ``NoDecode`` keeps pydantic-settings from json.loads()-ing these two before the
    # validator below can see them. Without it, the plain comma separated value the API's own
    # 400 hint ("set ADAPT_EXTRA_ALLOWED_HOSTS") tells an operator to type raised
    # ``SettingsError`` at import time and killed the process -- while ``["a","b"]`` worked.
    # Both forms are accepted now: see ``_split_list``. (NoDecode is in pydantic-settings
    # since 2.4; the floor here is 2.7.)
    allowed_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    # Extra hosts accepted for WebSocket / redirect safety (tunnel hostnames).
    extra_allowed_hosts: Annotated[list[str], NoDecode] = Field(default_factory=list)
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
    # Model *names* live here and nowhere else. Which task gets which class is decided in
    # `ai/routing.py`; these three say what a class is on this deployment. `flash-lite`
    # for the light class is not a downgrade of the product: every light task is checked
    # against a rule, a list or a validator, so its failure mode is a fallback nobody sees,
    # while the deep class is where a weak answer costs a learner a rewrite.
    gemini_model_light: str = "gemini-2.5-flash-lite"
    gemini_model_standard: str = "gemini-2.5-flash"
    gemini_model_deep: str = "gemini-2.5-pro"
    # Blank means "follow the tiers". Set one of these to pin every task a role performs
    # to a single model (a deployment with one allowed model, or a comparison run).
    gemini_model_ai1: str | None = None
    gemini_model_ai2: str | None = None
    # Per-task pins, most specific override: {"ai2_explain": "<model>"}. Accepted as JSON
    # or as `task=model,task=model`, because the docs tell an operator to write the
    # comma-separated form for lists and the JSON form would then be the odd one out.
    gemini_task_models: Annotated[dict[str, str], NoDecode] = Field(default_factory=dict)
    gemini_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    gemini_timeout_s: float = 45.0
    # Free-tier guard: stop calling before the daily quota is exhausted and let
    # the local reasoner carry the demo instead.
    gemini_daily_call_budget: int = 120
    gemini_daily_token_budget: int = 2_000_000
    gemini_min_interval_ms: int = 700  # smooths RPM limits
    gemini_max_retries: int = 2

    # -- context caching (item 2 of the cost round) ----------------------
    # The static head of a prompt -- system instructions, rubric, subject catalogue -- is
    # the same for every learner, and passing it again on every call is what turns a
    # handful of prompts into a quota problem. When enabled, the client caches that head
    # server-side and sends only the dynamic tail. Three things keep this safe:
    #   * only *static* text is ever cached (a test forbids learner text in a prefix),
    #   * a cache that cannot be created is a warning, never a failed answer,
    #   * prompts are built static-head-first anyway, so the provider's own implicit
    #     prefix caching helps even when explicit caching is unavailable.
    ai_context_cache: bool = True
    gemini_context_cache_ttl_s: int = 1800  # 30 min: a demo sitting, not a day
    gemini_context_cache_min_tokens: int = 1024  # the documented floor for a cacheable prompt
    gemini_context_cache_max_entries: int = 8  # bounded: an unbounded cache is a leak
    gemini_context_cache_max_tokens: int = 32_000  # refuse to cache a prompt bigger than this

    # -- single-call chains (item 1) -------------------------------------
    # Where two prompts read the same content and ask two questions about it, one call
    # answers both. Off means "use the old two-call path", which exists so the change can
    # be measured rather than believed.
    ai_single_call: bool = True

    # -- concurrency guards (item 4) -------------------------------------
    # Real-time routes: how many requests one learner/IP may have in flight at once, and
    # how fast a burst has to arrive before it is treated as a loop rather than a person.
    # A person clicking cannot reach these; a runaway tab hits them in under a second.
    realtime_max_inflight: int = 4
    realtime_burst_window_ms: int = 1000
    realtime_burst_max: int = 20
    # Identical text frames closer together than this are one turn: a double-click, a
    # key repeat, or a reconnect that replays the last message. The frame *rate* limits
    # live in security/ratelimit.py with every other token bucket, so there is one table
    # to read when asking what the server will accept.
    ws_duplicate_window_ms: int = 900

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
        "gemini_model_ai1",
        "gemini_model_ai2",
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
        """Accept both spellings of a host/origin list: ``a,b`` and ``["a","b"]``.

        The comma separated form is what the docs and the API's own untrusted-host hint tell
        an operator to write; the JSON array is what pydantic-settings decodes and what any
        existing ``.env`` may already hold, so both keep working.
        """
        if isinstance(v, str):
            text = v.strip()
            if text.startswith("["):
                try:
                    decoded = json.loads(text)
                except ValueError:
                    decoded = None
                if isinstance(decoded, list):
                    return [str(x).strip() for x in decoded if str(x).strip()]
            return [chunk.strip() for chunk in text.split(",") if chunk.strip()]
        return v

    @field_validator("gemini_task_models", mode="before")
    @classmethod
    def _task_model_map(cls, v: object) -> object:
        """Accept ``{"ai2_explain": "<model>"}`` and ``ai2_explain=<model>,...``.

        A typo'd pin used to be a silent no-op in the obvious implementation (parse,
        fail, return ``{}``), and a silent no-op is the worst outcome for a setting whose
        whole purpose is *overriding* something: the operator would believe a task was
        pinned while it followed the tier. So a value that is neither shape raises, at
        import time, where the operator is looking.
        """
        if not isinstance(v, str):
            return v
        text = v.strip()
        if not text:
            return {}
        if text.startswith("{"):
            try:
                decoded = json.loads(text)
            except ValueError as exc:
                raise ValueError(f"ADAPT_GEMINI_TASK_MODELS is not valid JSON: {exc}") from None
            if not isinstance(decoded, dict):
                raise ValueError("ADAPT_GEMINI_TASK_MODELS must be a JSON object of task names")
            return decoded
        out: dict[str, str] = {}
        for chunk in text.split(","):
            if not chunk.strip():
                continue
            if "=" not in chunk:
                raise ValueError(
                    "ADAPT_GEMINI_TASK_MODELS wants `task=model` pairs or a JSON object; "
                    f"`{chunk.strip()}` is neither"
                )
            task, _, name = chunk.partition("=")
            out[task.strip()] = name.strip()
        return out

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

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.keyfile_path.parent, self.numbas_dir):
            Path(d).mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    s = Settings()
    if not os.environ.get("ADAPT_DISABLE_DIR_CREATE"):
        s.ensure_dirs()
    return s


def reset_settings_cache() -> None:
    get_settings.cache_clear()
