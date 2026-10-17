"""The copied env templates must feed the same Settings object as shell variables."""
from __future__ import annotations

from pathlib import Path


def test_env_file_loads_prefixed_settings_and_database_passphrase(tmp_path: Path):
    from adapt.config import Settings

    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "ADAPT_MODE=local",
                "ADAPT_DB_ENCRYPTION=field-only",
                "ADAPT_DB_PASSPHRASE='a passphrase from the copied env file'",
                "ADAPT_ALLOWED_ORIGINS=http://localhost:3000,http://127.0.0.1:3000",
                'ADAPT_GEMINI_TASK_MODELS={"ai2_explain":"gemini-test"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file, data_dir=tmp_path / "data")

    assert settings.mode == "local"
    assert settings.db_encryption == "field-only"
    assert settings.db_passphrase == "a passphrase from the copied env file"
    assert settings.allowed_origins == ["http://localhost:3000", "http://127.0.0.1:3000"]
    assert settings.gemini_task_models == {"ai2_explain": "gemini-test"}
    assert "db_passphrase" not in settings.model_dump()
    assert "a passphrase from the copied env file" not in repr(settings)


def test_repository_env_file_is_resolved_independently_of_working_directory():
    from adapt.config import Settings

    configured_files = tuple(str(path) for path in Settings.model_config["env_file"])
    repo_root = Path(__file__).resolve().parents[2]
    assert str(repo_root / ".env") in configured_files
