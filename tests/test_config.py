"""Tests for configuration management, provider env fallbacks, and safe `.env` persistence."""

from pathlib import Path

import pytest

import agenteverywhereflow.config as config_mod
from agenteverywhereflow.config import (
    AppConfig,
    ExecutionMode,
    PermissionMode,
    get_masked_config,
    mask_api_key,
    update_env_config,
)

PROVIDER_ENV_VARS = (
    "AEF_API_KEY",
    "AEF_BASE_URL",
    "AEF_MODEL_NAME",
    "AEF_DEFAULT_MODE",
    "AEF_DEFAULT_PERMISSION_MODE",
    "AEF_MAX_STEPS",
    "AEF_SUMMON_HOTKEY",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_MODEL_NAME",
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_MODEL_NAME",
    "OPENROUTER_API_KEY",
    "OPENROUTER_BASE_URL",
    "OPENROUTER_MODEL_NAME",
    "OLLAMA_HOST",
    "OLLAMA_MODEL",
)


@pytest.fixture
def clean_provider_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Isolate tests from host env vars and host ~/.aef/.env."""
    for var in PROVIDER_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


@pytest.fixture
def restore_live_config():
    """Snapshot and restore module-level config singleton around tests that mutate it."""
    snapshot = {field: getattr(config_mod.config, field) for field in AppConfig.model_fields}
    try:
        yield
    finally:
        for field, value in snapshot.items():
            setattr(config_mod.config, field, value)


def test_config_defaults(clean_provider_env: Path) -> None:
    cfg = AppConfig(_env_file=None)
    assert cfg.model_name == "gpt-4o"
    assert cfg.base_url == "https://api.openai.com/v1"
    assert cfg.api_key == ""
    assert cfg.max_steps == 100


def test_config_fallbacks_to_openai_env(
    monkeypatch: pytest.MonkeyPatch, clean_provider_env: Path
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-fallback-1234")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://custom.openai.api/v1")

    cfg = AppConfig(_env_file=None)
    assert cfg.api_key == "sk-openai-fallback-1234"
    assert cfg.base_url == "https://custom.openai.api/v1"
    assert cfg.model_name == "gpt-4o"


def test_config_openai_key_alone_never_routes_to_deepseek(
    monkeypatch: pytest.MonkeyPatch, clean_provider_env: Path
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-only-9999")

    cfg = AppConfig(_env_file=None)
    assert cfg.api_key == "sk-openai-only-9999"
    assert cfg.base_url == "https://api.openai.com/v1"
    assert cfg.model_name == "gpt-4o"


def test_config_deepseek_env_fallback(
    monkeypatch: pytest.MonkeyPatch, clean_provider_env: Path
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-key-1234")

    cfg = AppConfig(_env_file=None)
    assert cfg.api_key == "sk-deepseek-key-1234"
    assert cfg.base_url == "https://api.deepseek.com"
    assert cfg.model_name == "deepseek-chat"


def test_config_openrouter_env_fallback(
    monkeypatch: pytest.MonkeyPatch, clean_provider_env: Path
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-abc123")

    cfg = AppConfig(_env_file=None)
    assert cfg.api_key == "sk-or-v1-abc123"
    assert cfg.base_url == "https://openrouter.ai/api/v1"


def test_config_ollama_host_fallback(
    monkeypatch: pytest.MonkeyPatch, clean_provider_env: Path
) -> None:
    monkeypatch.setenv("OLLAMA_HOST", "127.0.0.1:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.2-vision")

    cfg = AppConfig(_env_file=None)
    assert cfg.api_key == "ollama"
    assert cfg.base_url == "http://127.0.0.1:11434/v1"
    assert cfg.model_name == "llama3.2-vision"


def test_config_aef_takes_precedence_over_openai(
    monkeypatch: pytest.MonkeyPatch, clean_provider_env: Path
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-fallback")
    monkeypatch.setenv("AEF_API_KEY", "sk-aef-priority")

    cfg = AppConfig(_env_file=None)
    assert cfg.api_key == "sk-aef-priority"


def test_explicit_kwargs_never_overwritten_by_env_fallbacks(
    monkeypatch: pytest.MonkeyPatch, clean_provider_env: Path
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-openai")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://override.example/v1")
    monkeypatch.setenv("OPENAI_MODEL_NAME", "override-model")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-env-deepseek")

    cfg = AppConfig(
        _env_file=None,
        api_key="sk-explicit-key",
        base_url="https://api.openai.com/v1",
        model_name="gpt-4o",
    )
    assert cfg.api_key == "sk-explicit-key"
    assert cfg.base_url == "https://api.openai.com/v1"
    assert cfg.model_name == "gpt-4o"


def test_get_masked_config_and_mask_api_key(clean_provider_env: Path) -> None:
    assert mask_api_key("") == ""
    assert mask_api_key("abc") == "***"
    assert mask_api_key("sk-123456") == "sk...56"
    assert mask_api_key("sk-proj-1234567890abcd") == "sk-pro...abcd"

    cfg = AppConfig(
        _env_file=None,
        api_key="sk-proj-1234567890abcd",
        base_url="https://api.openai.com/v1",
        model_name="gpt-4o",
        default_mode=ExecutionMode.CONTROL_GUARDED,
        default_permission_mode=PermissionMode.MANUAL,
        summon_hotkey="alt+space",
    )
    masked = get_masked_config(cfg)
    assert masked["model_name"] == "gpt-4o"
    assert masked["base_url"] == "https://api.openai.com/v1"
    assert masked["api_key_set"] is True
    assert masked["api_key_preview"] == "sk-pro...abcd"
    assert masked["default_mode"] == "guarded"
    assert masked["default_permission"] == "manual"
    assert masked["summon_hotkey"] == "alt+space"


def test_update_env_config_persists_preserves_and_refreshes_singleton(
    clean_provider_env: Path,
    restore_live_config: None,
) -> None:
    env_file = clean_provider_env / ".aef" / ".env"
    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.write_text(
        "# Existing comment\nUNRELATED_KEY=keep_me\nAEF_MODEL_NAME=old-model\n",
        encoding="utf-8",
    )

    updated = update_env_config(
        {
            "api_key": "sk-new-secret-987654",
            "base_url": "https://api.deepseek.com",
            "model_name": "deepseek-chat",
            "default_mode": "guarded",
            "default_permission": "manual",
            "summon_hotkey": "ctrl+alt+a",
            "debug": None,  # ignored partial field
        },
        env_path=env_file,
    )

    assert updated is config_mod.config
    assert config_mod.config.api_key == "sk-new-secret-987654"
    assert config_mod.config.base_url == "https://api.deepseek.com"
    assert config_mod.config.model_name == "deepseek-chat"
    assert config_mod.config.default_mode == ExecutionMode.CONTROL_GUARDED
    assert config_mod.config.default_permission_mode == PermissionMode.MANUAL
    assert config_mod.config.summon_hotkey == "ctrl+alt+a"

    saved_text = env_file.read_text(encoding="utf-8")
    assert "# Existing comment" in saved_text
    assert "UNRELATED_KEY=keep_me" in saved_text
    assert "AEF_API_KEY=sk-new-secret-987654" in saved_text
    assert "AEF_BASE_URL=https://api.deepseek.com" in saved_text
    assert "AEF_MODEL_NAME=deepseek-chat" in saved_text
    assert "AEF_DEFAULT_MODE=guarded" in saved_text
    assert "AEF_DEFAULT_PERMISSION_MODE=manual" in saved_text
