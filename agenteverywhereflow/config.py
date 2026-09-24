import os
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ExecutionMode(StrEnum):
    """Execution mode for agent actions."""

    MINIMAL_PYTHON = "minimal"  # Python CodeAct REPL toolcall
    CONTROL_GUARDED = "guarded"  # Granular function calls with permission gate


def get_global_config_dir() -> Path:
    """Resolve global ~/.aef directory."""
    return Path.home() / ".aef"


def get_global_env_file() -> Path:
    """Resolve global ~/.aef/.env file."""
    return get_global_config_dir() / ".env"


def get_default_screenshot_dir() -> Path:
    """Default cross-platform user directory for screenshots."""
    return get_global_config_dir() / "screenshots"


class AppConfig(BaseSettings):
    """Global configuration settings for AEFlow."""

    # LLM Provider settings
    model_name: str = Field(default="deepseek-flash", description="Default Vision-LLM model")
    api_key: str = Field(default="", description="API Key for the model provider")
    base_url: str = Field(default="https://api.deepseek.com", description="Provider base URL")

    # Execution settings
    default_mode: ExecutionMode = Field(
        default=ExecutionMode.MINIMAL_PYTHON,
        description="Default execution mode: minimal (Python REPL) or guarded (granular)",
    )
    max_steps: int = Field(default=25, description="Maximum loop steps per task")
    step_timeout_seconds: float = Field(default=30.0, description="Timeout for each execution step")

    # Safety settings
    require_human_confirmation: bool = Field(
        default=False,
        description="Require interactive confirmation for potentially destructive operations",
    )

    # Diagnostic & Debug settings
    debug: bool = Field(
        default=False,
        description="Enable verbose debug logging and execution diagnostics",
    )

    # UI & Hotkey settings
    summon_hotkey: str = Field(default="ctrl+shift+a", description="Global hotkey to summon HUD")
    screenshot_dir: Path = Field(
        default_factory=get_default_screenshot_dir,
        description="Directory for temporary screenshots",
    )
    max_visual_history_images: int = Field(
        default=2,
        description="Maximum recent screenshots to retain in LLM context to prevent token explosion",
    )

    @model_validator(mode="before")
    @classmethod
    def populate_fallbacks(cls, data: Any) -> Any:
        """Fallback to standard OPENAI_* environment variables if AEF_* not set."""
        if isinstance(data, dict):
            # 1. Fallback for api_key
            if not data.get("api_key"):
                env_key = os.environ.get("AEF_API_KEY") or os.environ.get("OPENAI_API_KEY")
                if env_key:
                    data["api_key"] = env_key

            # 2. Fallback for base_url
            if not data.get("base_url") or data.get("base_url") in (
                "https://api.deepseek.com",
                "https://api.openai.com/v1",
            ):
                env_base = os.environ.get("AEF_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
                if env_base:
                    data["base_url"] = env_base

            # 3. Fallback for model_name
            if not data.get("model_name") or data.get("model_name") in ("deepseek-flash", "gpt-4o"):
                env_model = os.environ.get("AEF_MODEL_NAME") or os.environ.get("OPENAI_MODEL_NAME")
                if env_model:
                    data["model_name"] = env_model
        return data

    model_config = SettingsConfigDict(
        env_prefix="AEF_",
        env_file=(str(get_global_env_file()), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


# Global singleton instance
config = AppConfig()
