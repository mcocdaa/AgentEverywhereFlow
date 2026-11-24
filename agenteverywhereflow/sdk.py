"""Ergonomic high-level Python SDK for AgentEverywhereFlow (AEFlow)."""

from __future__ import annotations

import atexit
import os
import threading
import time
import webbrowser
from typing import Any, overload

from PIL import Image
from pydantic import BaseModel

from agenteverywhereflow.agent.loop import AgentLoop
from agenteverywhereflow.capturer import get_capturer
from agenteverywhereflow.capturer.base import TargetInfo
from agenteverywhereflow.capturer.selector import resolve_target
from agenteverywhereflow.config import (
    AppConfig,
    ConfigurationError,
    ExecutionMode,
    PermissionMode,
)
from agenteverywhereflow.session.session import ChatSession, TurnResult


class RunResult(BaseModel):
    """Structured outcome returned by SDK `run()` and `Agent.do()` calls."""

    success: bool
    target: TargetInfo
    task: str
    steps: int = 0
    message: str = ""


def _coerce_execution_mode(mode: ExecutionMode | str) -> ExecutionMode:
    """Normalize an ExecutionMode enum or string alias."""
    if isinstance(mode, ExecutionMode):
        return mode
    normalized = mode.strip().lower()
    if normalized in ("minimal", "minimal_python", "codeact"):
        return ExecutionMode.MINIMAL_PYTHON
    if normalized in ("guarded", "control_guarded"):
        return ExecutionMode.CONTROL_GUARDED
    raise ValueError(f"Unsupported execution mode: {mode!r}. Expected 'minimal' or 'guarded'.")


def _coerce_permission_mode(mode: PermissionMode | str) -> PermissionMode:
    """Normalize a PermissionMode enum or string alias."""
    if isinstance(mode, PermissionMode):
        return mode
    normalized = mode.strip().lower()
    if normalized in ("auto", "autonomous"):
        return PermissionMode.AUTO
    if normalized in ("manual", "approval"):
        return PermissionMode.MANUAL
    raise ValueError(f"Unsupported permission mode: {mode!r}. Expected 'auto' or 'manual'.")


def _build_sdk_config(
    *,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    mode: ExecutionMode | None = None,
    permission_mode: PermissionMode | None = None,
    max_steps: int | None = None,
    debug: bool | None = None,
) -> AppConfig:
    """Construct an AppConfig from explicit SDK kwargs plus current environment/dotenv."""
    overrides: dict[str, Any] = {}
    if model is not None:
        overrides["model_name"] = model
    if api_key is not None:
        overrides["api_key"] = api_key
    if base_url is not None:
        overrides["base_url"] = base_url
    if mode is not None:
        overrides["default_mode"] = mode
    if permission_mode is not None:
        overrides["default_permission_mode"] = permission_mode
    if max_steps is not None:
        overrides["max_steps"] = max_steps
    if debug is not None:
        overrides["debug"] = debug
    return AppConfig(**overrides)


def list_targets(
    include_windows: bool = True,
    *,
    include_displays: bool = True,
) -> list[TargetInfo]:
    """List available display and window targets on the current machine."""
    capturer = get_capturer()
    return capturer.list_targets(
        include_displays=include_displays,
        include_windows=include_windows,
    )


def _resolve_sdk_target(target: str | TargetInfo) -> TargetInfo:
    """Resolve a target string query or pass through an existing TargetInfo."""
    if isinstance(target, TargetInfo):
        return target
    all_targets = list_targets(include_windows=True, include_displays=True)
    resolved = resolve_target(all_targets, target)
    if resolved is None:
        raise ValueError(
            f"No target found matching {target!r}. Call list_targets() to inspect available displays and windows."
        )
    return resolved


def run(
    target: str | TargetInfo,
    task: str,
    *,
    model: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    mode: ExecutionMode | str = ExecutionMode.MINIMAL,
    max_steps: int = 15,
    debug: bool = False,
    planner_func: Any | None = None,
) -> RunResult:
    """Run an autonomous GUI task against a target display or window in one call.

    Example:
        >>> import agenteverywhereflow as aef
        >>> result = aef.run("Chrome", "Search for uv docs and press Enter")
    """
    exec_mode = _coerce_execution_mode(mode)
    resolved_target = _resolve_sdk_target(target)
    app_cfg = _build_sdk_config(
        model=model,
        api_key=api_key,
        base_url=base_url,
        mode=exec_mode,
        max_steps=max_steps,
        debug=debug,
    )
    loop = AgentLoop(app_config=app_cfg, planner_func=planner_func)
    success = loop.run(
        target=resolved_target,
        user_task=task,
        mode=exec_mode,
        max_steps=max_steps,
    )
    return RunResult(
        success=success,
        target=resolved_target,
        task=task,
        steps=loop.last_steps,
        message=loop.last_message,
    )


class Agent:
    """Stateful high-level GUI agent bound to a specific screen or application window."""

    def __init__(
        self,
        target: str | TargetInfo,
        *,
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        mode: ExecutionMode | str = ExecutionMode.MINIMAL,
        permission_mode: PermissionMode | str = PermissionMode.AUTO,
        max_steps: int = 15,
        debug: bool = False,
        planner_func: Any | None = None,
    ) -> None:
        self._target: TargetInfo = _resolve_sdk_target(target)
        self.mode = _coerce_execution_mode(mode)
        self.permission_mode = _coerce_permission_mode(permission_mode)
        self.max_steps = max_steps
        self.planner_func = planner_func
        self.config = _build_sdk_config(
            model=model,
            api_key=api_key,
            base_url=base_url,
            mode=self.mode,
            permission_mode=self.permission_mode,
            max_steps=max_steps,
            debug=debug,
        )
        self._session: ChatSession | None = None

    @property
    def target(self) -> TargetInfo:
        """Return the bound viewport target."""
        return self._target

    def do(
        self,
        task: str,
        *,
        max_steps: int | None = None,
        mode: ExecutionMode | str | None = None,
    ) -> RunResult:
        """Execute a single autonomous task on the bound target."""
        exec_mode = _coerce_execution_mode(mode) if mode is not None else self.mode
        step_budget = max_steps if max_steps is not None else self.max_steps
        loop = AgentLoop(app_config=self.config, planner_func=self.planner_func)
        success = loop.run(
            target=self._target,
            user_task=task,
            mode=exec_mode,
            max_steps=step_budget,
        )
        return RunResult(
            success=success,
            target=self._target,
            task=task,
            steps=loop.last_steps,
            message=loop.last_message,
        )

    @overload
    def chat(
        self,
        instruction: None = None,
        *,
        max_steps: int | None = None,
        permission_mode: PermissionMode | str | None = None,
    ) -> ChatSession: ...

    @overload
    def chat(
        self,
        instruction: str,
        *,
        max_steps: int | None = None,
        permission_mode: PermissionMode | str | None = None,
    ) -> TurnResult: ...

    def chat(
        self,
        instruction: str | None = None,
        *,
        max_steps: int | None = None,
        permission_mode: PermissionMode | str | None = None,
    ) -> ChatSession | TurnResult:
        """Return the persistent ChatSession or execute a conversational turn on it."""
        perm = (
            _coerce_permission_mode(permission_mode)
            if permission_mode is not None
            else self.permission_mode
        )
        if self._session is None:
            self._session = ChatSession(
                target=self._target,
                mode=self.mode,
                permission_mode=perm,
                app_config=self.config,
                planner_func=self.planner_func,
            )
        elif permission_mode is not None:
            self._session.set_permission_mode(perm)

        if instruction is None:
            return self._session

        if self.planner_func is None and not self.config.api_key:
            raise ConfigurationError(
                "No LLM API key configured. Run `aef init` to set up a provider, "
                "set AEF_API_KEY (or OPENAI_API_KEY / DEEPSEEK_API_KEY / OPENROUTER_API_KEY), "
                "or pass `api_key=...` to Agent(...)."
            )

        return self._session.execute_turn(
            instruction,
            max_steps=max_steps if max_steps is not None else self.max_steps,
        )

    def screenshot(self) -> Image.Image:
        """Capture an isolated screenshot of the bound target viewport."""
        return get_capturer().capture(self._target)


def serve(
    host: str = "127.0.0.1",
    port: int = 8000,
    open_browser: bool = False,
) -> None:
    """Launch the AEFlow FastAPI + WebSocket service daemon programmatically."""
    import uvicorn

    from agenteverywhereflow.cli import clear_daemon_info, write_daemon_info

    if open_browser:

        def _open() -> None:
            time.sleep(0.8)
            try:
                connect_host = "127.0.0.1" if host in ("0.0.0.0", "", "::") else host
                webbrowser.open(f"http://{connect_host}:{port}")
            except Exception:
                pass

        threading.Thread(target=_open, daemon=True).start()

    pid = os.getpid()
    write_daemon_info(host=host, port=port, pid=pid)
    atexit.register(clear_daemon_info, pid)
    try:
        uvicorn.run(
            "agenteverywhereflow.server.app:create_app",
            host=host,
            port=port,
            factory=True,
            timeout_graceful_shutdown=2,
        )
    finally:
        clear_daemon_info(expected_pid=pid)


__all__ = [
    "Agent",
    "RunResult",
    "list_targets",
    "run",
    "serve",
]
