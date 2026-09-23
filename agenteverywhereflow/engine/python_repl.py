"""Minimal Execution Mode: Python REPL (CodeAct Sandbox).

Provides the agent with direct Python execution capability, injecting high-level
target-relative mouse, keyboard, and vision helpers into the evaluation scope.
"""

import io
import sys
import traceback
from typing import Any

from agenteverywhereflow.actions.coords import CoordinateProjector
from agenteverywhereflow.actions.driver import driver
from agenteverywhereflow.capturer import get_capturer
from agenteverywhereflow.capturer.base import TargetInfo
from agenteverywhereflow.engine.base import BaseExecutionEngine, ExecutionResult


class PythonReplEngine(BaseExecutionEngine):
    """Executes Python code blocks with target-aware primitives injected."""

    def __init__(self) -> None:
        self.capturer = get_capturer()

    def _build_context(self, target: TargetInfo) -> dict[str, Any]:
        """Construct the sandbox globals injected into Python code."""

        def _resolve_coords(x: float, y: float) -> tuple[int, int]:
            return CoordinateProjector.to_screen_coords(
                target=target,
                x=x,
                y=y,
                img_width=target.rect.width,
                img_height=target.rect.height,
            )

        def click(x: float, y: float, button: str = "left", clicks: int = 1) -> None:
            sx, sy = _resolve_coords(x, y)
            driver.click(
                x=sx,
                y=sy,
                button=button,  # type: ignore
                clicks=clicks,
                window_handle=target.native_handle,
                window_rel_x=int(x),
                window_rel_y=int(y),
            )

        def move(x: float, y: float) -> None:
            sx, sy = _resolve_coords(x, y)
            driver.move_to(x=sx, y=sy)

        def double_click(x: float, y: float) -> None:
            sx, sy = _resolve_coords(x, y)
            driver.double_click(x=sx, y=sy, window_handle=target.native_handle)

        def right_click(x: float, y: float) -> None:
            sx, sy = _resolve_coords(x, y)
            driver.right_click(x=sx, y=sy, window_handle=target.native_handle)

        def type_text(text: str) -> None:
            driver.type_text(text, window_handle=target.native_handle)

        def press(key: str) -> None:
            driver.press_key(key)

        def hotkey(*keys: str) -> None:
            driver.hotkey(*keys)

        def scroll(amount: int, x: float | None = None, y: float | None = None) -> None:
            if x is not None and y is not None:
                sx, sy = _resolve_coords(x, y)
                driver.scroll(amount, x=sx, y=sy)
            else:
                driver.scroll(amount)

        def wait(seconds: float) -> None:
            driver.wait(seconds)

        def screenshot() -> Any:
            return self.capturer.capture(target)

        return {
            "target": target,
            "click": click,
            "move": move,
            "double_click": double_click,
            "right_click": right_click,
            "type_text": type_text,
            "press": press,
            "hotkey": hotkey,
            "scroll": scroll,
            "wait": wait,
            "screenshot": screenshot,
        }

    def execute(self, payload: str, target: TargetInfo, **kwargs: Any) -> ExecutionResult:
        """Execute a Python code string."""
        code_str = payload.strip()
        # Strip markdown ```python ... ``` fences if present
        if code_str.startswith("```"):
            lines = code_str.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            code_str = "\n".join(lines).strip()

        context = self._build_context(target)

        # Redirect stdout and stderr
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        redirected_out = io.StringIO()
        redirected_err = io.StringIO()

        success = True
        error_msg = ""

        try:
            sys.stdout = redirected_out
            sys.stderr = redirected_err
            # Execute in sandbox context
            exec(code_str, context)
        except Exception:
            success = False
            error_msg = traceback.format_exc()
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

        output_str = redirected_out.getvalue()
        if redirected_err.getvalue():
            output_str += "\n[stderr]\n" + redirected_err.getvalue()

        return ExecutionResult(
            success=success,
            output=output_str.strip(),
            error=error_msg,
            data={"executed_code": code_str},
        )
