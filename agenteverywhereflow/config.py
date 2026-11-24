"""Global configuration, provider env fallbacks, and safe persistence helpers."""

import os
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigurationError(RuntimeError):
    """Raised when required AEFlow configuration (such as an LLM API key) is missing or invalid."""


class ExecutionMode(StrEnum):
    """Execution mode for agent actions."""

    MINIMAL_PYTHON = "minimal"  # Python CodeAct REPL toolcall
    CONTROL_GUARDED = "guarded"  # Granular function calls with permission gate
    # Ergonomic aliases
    MINIMAL = "minimal"
    GUARDED = "guarded"


class PermissionMode(StrEnum):
    """Permission mode for agent action execution."""

    AUTO = "auto"  # Full autonomy
    MANUAL = "manual"  # Require manual operator approval before every action


DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL_NAME = "gpt-4o"

PROVIDER_PRESETS: dict[str, dict[str, str]] = {
    "openai": {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model_name": "gpt-4o",
        "env_key": "OPENAI_API_KEY",
    },
    "deepseek": {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "model_name": "deepseek-chat",
        "env_key": "DEEPSEEK_API_KEY",
    },
    "openrouter": {
        "label": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "model_name": "openai/gpt-4o",
        "env_key": "OPENROUTER_API_KEY",
    },
    "ollama": {
        "label": "Ollama (Local)",
        "base_url": "http://localhost:11434/v1",
        "model_name": "llama3.2-vision",
        "env_key": "OLLAMA_HOST",
    },
    "custom": {
        "label": "Custom OpenAI-Compatible Endpoint",
        "base_url": "",
        "model_name": "",
        "env_key": "AEF_API_KEY",
    },
}

_FIELD_TO_ENV_KEY: dict[str, str] = {
    "api_key": "AEF_API_KEY",
    "model_name": "AEF_MODEL_NAME",
    "base_url": "AEF_BASE_URL",
    "default_mode": "AEF_DEFAULT_MODE",
    "default_permission": "AEF_DEFAULT_PERMISSION_MODE",
    "default_permission_mode": "AEF_DEFAULT_PERMISSION_MODE",
    "max_steps": "AEF_MAX_STEPS",
    "step_timeout_seconds": "AEF_STEP_TIMEOUT_SECONDS",
    "require_human_confirmation": "AEF_REQUIRE_HUMAN_CONFIRMATION",
    "server_host": "AEF_SERVER_HOST",
    "server_port": "AEF_SERVER_PORT",
    "debug": "AEF_DEBUG",
    "summon_hotkey": "AEF_SUMMON_HOTKEY",
    "screenshot_dir": "AEF_SCREENSHOT_DIR",
    "max_visual_history_images": "AEF_MAX_VISUAL_HISTORY_IMAGES",
}

_ENV_KEY_TO_FIELD: dict[str, str] = {
    "AEF_API_KEY": "api_key",
    "AEF_MODEL_NAME": "model_name",
    "AEF_BASE_URL": "base_url",
    "AEF_DEFAULT_MODE": "default_mode",
    "AEF_DEFAULT_PERMISSION_MODE": "default_permission_mode",
    "AEF_MAX_STEPS": "max_steps",
    "AEF_STEP_TIMEOUT_SECONDS": "step_timeout_seconds",
    "AEF_REQUIRE_HUMAN_CONFIRMATION": "require_human_confirmation",
    "AEF_SERVER_HOST": "server_host",
    "AEF_SERVER_PORT": "server_port",
    "AEF_DEBUG": "debug",
    "AEF_SUMMON_HOTKEY": "summon_hotkey",
    "AEF_SCREENSHOT_DIR": "screenshot_dir",
    "AEF_MAX_VISUAL_HISTORY_IMAGES": "max_visual_history_images",
}


def get_global_config_dir() -> Path:
    """Resolve global ~/.aef directory."""
    return Path.home() / ".aef"


def get_global_env_file() -> Path:
    """Resolve global ~/.aef/.env file."""
    return get_global_config_dir() / ".env"


def get_default_screenshot_dir() -> Path:
    """Default cross-platform user directory for screenshots."""
    return get_global_config_dir() / "screenshots"


def _normalize_ollama_base_url(host: str) -> str:
    """Convert an OLLAMA_HOST value into an OpenAI-compatible `/v1` base URL."""
    cleaned = host.strip().rstrip("/")
    if not cleaned:
        return "http://localhost:11434/v1"
    if "://" not in cleaned:
        cleaned = f"http://{cleaned}"
    if not cleaned.endswith("/v1"):
        cleaned = f"{cleaned}/v1"
    return cleaned


def mask_api_key(api_key: str) -> str:
    """Return a masked preview of an API key suitable for UI/CLI display."""
    key = (api_key or "").strip()
    if not key:
        return ""
    if len(key) > 10:
        return f"{key[:6]}...{key[-4:]}"
    if len(key) >= 4:
        return f"{key[:2]}...{key[-2:]}"
    return "***"


class AppConfig(BaseSettings):
    """Global configuration settings for AEFlow."""

    # LLM Provider settings
    model_name: str = Field(default=DEFAULT_MODEL_NAME, description="Default Vision-LLM model")
    api_key: str = Field(default="", description="API Key for the model provider")
    base_url: str = Field(default=DEFAULT_BASE_URL, description="Provider base URL")

    # Execution settings
    default_mode: ExecutionMode = Field(
        default=ExecutionMode.MINIMAL_PYTHON,
        description="Default execution mode: minimal (Python REPL) or guarded (granular)",
    )
    max_steps: int = Field(default=100, description="Maximum loop steps per task")
    step_timeout_seconds: float = Field(default=30.0, description="Timeout for each execution step")

    # Safety & Permission settings
    default_permission_mode: PermissionMode = Field(
        default=PermissionMode.AUTO,
        description="Default permission mode: auto (full autonomy) or manual (human-in-the-loop approval)",
    )
    require_human_confirmation: bool = Field(
        default=False,
        description="Require interactive confirmation for potentially destructive operations",
    )

    # Server settings
    server_host: str = Field(default="127.0.0.1", description="Default host for aef serve")
    server_port: int = Field(default=8000, description="Default port for aef serve")

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

    @model_validator(mode="after")
    def populate_provider_fallbacks(self) -> "AppConfig":
        """Apply provider-aware env fallbacks without ever overwriting explicit inputs.

        Uses `self.model_fields_set` so explicit constructor kwargs and `AEF_*` settings
        always take strict precedence over third-party provider env vars.
        """
        fields_set = self.model_fields_set
        api_key_set = "api_key" in fields_set
        base_url_set = "base_url" in fields_set
        model_name_set = "model_name" in fields_set

        if not api_key_set:
            if openai_key := os.environ.get("OPENAI_API_KEY", "").strip():
                self.api_key = openai_key
                if not base_url_set:
                    self.base_url = (
                        os.environ.get("OPENAI_BASE_URL", "").strip() or DEFAULT_BASE_URL
                    )
                if not model_name_set:
                    self.model_name = (
                        os.environ.get("OPENAI_MODEL_NAME", "").strip() or DEFAULT_MODEL_NAME
                    )
                return self

            if deepseek_key := os.environ.get("DEEPSEEK_API_KEY", "").strip():
                self.api_key = deepseek_key
                if not base_url_set:
                    self.base_url = (
                        os.environ.get("DEEPSEEK_BASE_URL", "").strip()
                        or "https://api.deepseek.com"
                    )
                if not model_name_set:
                    self.model_name = (
                        os.environ.get("DEEPSEEK_MODEL_NAME", "").strip() or "deepseek-chat"
                    )
                return self

            if openrouter_key := os.environ.get("OPENROUTER_API_KEY", "").strip():
                self.api_key = openrouter_key
                if not base_url_set:
                    self.base_url = (
                        os.environ.get("OPENROUTER_BASE_URL", "").strip()
                        or "https://openrouter.ai/api/v1"
                    )
                if not model_name_set:
                    if or_model := os.environ.get("OPENROUTER_MODEL_NAME", "").strip():
                        self.model_name = or_model
                return self

            if ollama_host := os.environ.get("OLLAMA_HOST", "").strip():
                self.api_key = "ollama"
                if not base_url_set:
                    self.base_url = _normalize_ollama_base_url(ollama_host)
                if not model_name_set:
                    if ollama_model := os.environ.get("OLLAMA_MODEL", "").strip():
                        self.model_name = ollama_model
                return self

        # Fallback for standalone OPENAI_BASE_URL / OPENAI_MODEL_NAME when neither
        # explicit kwargs nor AEF_* env vars were set.
        if not base_url_set:
            if env_base := os.environ.get("OPENAI_BASE_URL", "").strip():
                self.base_url = env_base
        if not model_name_set:
            if env_model := os.environ.get("OPENAI_MODEL_NAME", "").strip():
                self.model_name = env_model

        return self

    model_config = SettingsConfigDict(
        env_prefix="AEF_",
        env_file=(str(get_global_env_file()), ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


def get_masked_config(cfg: AppConfig | None = None) -> dict[str, Any]:
    """Return a JSON-safe configuration dictionary with the API key masked."""
    current = cfg or config
    return {
        "model_name": current.model_name,
        "base_url": current.base_url,
        "api_key_set": bool(current.api_key),
        "api_key_preview": mask_api_key(current.api_key),
        "default_mode": current.default_mode.value,
        "default_permission": current.default_permission_mode.value,
        "default_permission_mode": current.default_permission_mode.value,
        "summon_hotkey": current.summon_hotkey,
        "max_steps": current.max_steps,
        "debug": current.debug,
        "server_host": current.server_host,
        "server_port": current.server_port,
    }


def _normalize_update_value(field_name: str, value: Any) -> tuple[Any, str]:
    """Normalize an update value into both a typed Python value and a `.env` string."""
    if field_name == "default_mode" and isinstance(value, str):
        lower = value.strip().lower()
        if lower in ("minimal", "minimal_python", "codeact"):
            enum_mode = ExecutionMode.MINIMAL_PYTHON
            return enum_mode, enum_mode.value
        if lower in ("guarded", "control_guarded"):
            enum_mode = ExecutionMode.CONTROL_GUARDED
            return enum_mode, enum_mode.value
    if field_name == "default_permission_mode" and isinstance(value, str):
        lower = value.strip().lower()
        if lower in ("auto", "autonomous"):
            enum_perm = PermissionMode.AUTO
            return enum_perm, enum_perm.value
        if lower in ("manual", "approval"):
            enum_perm = PermissionMode.MANUAL
            return enum_perm, enum_perm.value

    if isinstance(value, StrEnum):
        return value, value.value
    if isinstance(value, bool):
        return value, "true" if value else "false"
    if isinstance(value, Path):
        return value, str(value)
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned, cleaned
    return value, str(value)


def _format_env_line(key: str, value: str) -> str:
    """Format a single `KEY=VALUE` line safely for `.env` files."""
    sanitized = value.replace("\r", "").replace("\n", "")
    if any(ch in sanitized for ch in (" ", "#", '"', "'")) and not (
        sanitized.startswith('"') and sanitized.endswith('"')
    ):
        escaped = sanitized.replace("\\", "\\\\").replace('"', '\\"')
        return f'{key}="{escaped}"'
    return f"{key}={sanitized}"


def update_env_config(
    updates: dict[str, Any],
    env_path: Path | None = None,
) -> AppConfig:
    """Persist partial config updates into `~/.aef/.env` (or `env_path`) and refresh `config` in place.

    Preserves unrelated keys and comments in the target `.env` file.
    """
    target_path = env_path if env_path is not None else get_global_env_file()
    target_path.parent.mkdir(parents=True, exist_ok=True)

    env_updates: dict[str, str] = {}
    field_overrides: dict[str, Any] = {}

    for raw_key, raw_val in updates.items():
        if raw_val is None:
            continue
        if raw_key in _FIELD_TO_ENV_KEY:
            env_key = _FIELD_TO_ENV_KEY[raw_key]
            canonical_field = _ENV_KEY_TO_FIELD[env_key]
        elif raw_key.upper() in _ENV_KEY_TO_FIELD:
            env_key = raw_key.upper()
            canonical_field = _ENV_KEY_TO_FIELD[env_key]
        elif raw_key.upper().startswith("AEF_"):
            env_key = raw_key.upper()
            canonical_field = ""
        else:
            raise ValueError(f"Unsupported configuration key: {raw_key!r}")

        typed_val, str_val = _normalize_update_value(canonical_field, raw_val)
        env_updates[env_key] = str_val
        if canonical_field:
            field_overrides[canonical_field] = typed_val

    existing_lines: list[str] = []
    if target_path.exists():
        existing_lines = target_path.read_text(encoding="utf-8").splitlines()

    written_keys: set[str] = set()
    new_lines: list[str] = []
    for line in existing_lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key_part = stripped.split("=", 1)[0].strip()
            if key_part in env_updates:
                if key_part not in written_keys:
                    new_lines.append(_format_env_line(key_part, env_updates[key_part]))
                    written_keys.add(key_part)
                continue
        new_lines.append(line)

    for key_part, str_val in env_updates.items():
        if key_part not in written_keys:
            new_lines.append(_format_env_line(key_part, str_val))
            written_keys.add(key_part)

    content = "\n".join(new_lines) + ("\n" if new_lines else "")
    target_path.write_text(content, encoding="utf-8")
    if os.name == "posix":
        try:
            target_path.chmod(0o600)
        except OSError:
            pass

    # Reload from the target env file while applying explicit updated overrides so callers
    # see the updated values immediately even if older env vars exist in the shell.
    env_files: Any = str(target_path) if env_path is not None else (str(target_path), ".env")
    refreshed = AppConfig(_env_file=env_files, **field_overrides)  # type: ignore[call-arg]

    # Refresh the module-level singleton in place so `from ... import config` references stay live.
    for field_name in AppConfig.model_fields:
        setattr(config, field_name, getattr(refreshed, field_name))

    return config


# Global singleton instance
config = AppConfig()
