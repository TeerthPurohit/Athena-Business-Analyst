import pytest


def test_settings_require_database_only_when_database_capability_requested(monkeypatch):
    from athena.config import Settings

    monkeypatch.delenv("DATABASE_URL", raising=False)
    settings = Settings(_env_file=None)

    assert settings.database_url is None
    with pytest.raises(ValueError, match="DATABASE_URL"):
        settings.require_database()


def test_settings_read_deepseek_flash_model_configuration(monkeypatch):
    from athena.config import Settings

    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.com")
    monkeypatch.setenv("LLM_MODEL", "deepseek-flash")

    settings = Settings(_env_file=None)

    assert settings.openai_base_url == "https://api.deepseek.com"
    assert settings.llm_model == "deepseek-flash"


def test_settings_read_neon_object_storage_options(monkeypatch):
    from athena.config import Settings

    monkeypatch.setenv("BA_OBJECT_STORAGE_REGION", "ap-southeast-1")
    monkeypatch.setenv("BA_OBJECT_STORAGE_FORCE_PATH_STYLE", "true")

    settings = Settings(_env_file=None)

    assert settings.object_storage_region == "ap-southeast-1"
    assert settings.object_storage_force_path_style is True
