"""Tests for configuration management and fallback loading."""

from agenteverywhereflow.config import AppConfig


def test_config_fallbacks_to_openai_env(monkeypatch):
    monkeypatch.delenv("AEF_API_KEY", raising=False)
    monkeypatch.delenv("AEF_BASE_URL", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-fallback-1234")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://custom.openai.api/v1")

    cfg = AppConfig()
    assert cfg.api_key == "sk-openai-fallback-1234"
    assert cfg.base_url == "https://custom.openai.api/v1"


def test_config_aef_takes_precedence_over_openai(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-fallback")
    monkeypatch.setenv("AEF_API_KEY", "sk-aef-priority")

    cfg = AppConfig()
    assert cfg.api_key == "sk-aef-priority"
