"""Configuration management for AgentEverywhereFlow."""

from enum import Enum
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ExecutionMode(str, Enum):
    """Execution mode for agent actions."""
    MINIMAL_PYTHON = "minimal"  # Python CodeAct REPL toolcall
    CONTROL_GUARDED = "guarded"  # Granular function calls with permission gate


class AppConfig(BaseSettings):
    """Global configuration settings for AEFlow."""

    # LLM Provider settings
    model_name: str = Field(default="gpt-4o", description="Default Vision-LLM model")
    api_key: str = Field(default="", description="API Key for the model provider")
    base_url: str = Field(default="https://api.openai.com/v1", description="Provider base URL")

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

    # UI & Hotkey settings
    summon_hotkey: str = Field(default="ctrl+shift+a", description="Global hotkey to summon HUD")
    screenshot_dir: Path = Field(
        default=Path("./.aef_cache/screenshots"),
        description="Directory for temporary screenshots",
    )

    model_config = SettingsConfigDict(
        env_prefix="AEF_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


# Global singleton instance
config = AppConfig()
