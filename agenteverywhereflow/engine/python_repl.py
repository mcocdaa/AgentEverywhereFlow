"""Minimal Execution Mode: Python REPL (CodeAct Sandbox).

Provides the agent with direct Python execution capability, injecting high-level
target-relative mouse, keyboard, and vision helpers into the evaluation scope.
"""

import io
import sys
import traceback
from typing import Any

from rich.console import Console

from agenteverywhereflow.actions.coords import CoordinateProjector
from agenteverywhereflow.actions.driver import driver
from agenteverywhereflow.capturer import get_capturer
from agenteverywhereflow.capturer.base import TargetInfo
from agenteverywhereflow.engine.base import BaseExecutionEngine, ExecutionResult


class PythonReplEngine(BaseExecutionEngine):
    """Executes Python code blocks with target-aware primitives injected."""

    def __init__(self) -> None:
        self.capturer = get_capturer()
        self.console = Console(file=sys.__stdout__ or sys.stdout)

    def _print_action(self, text: str) -> None:
        try:
            self.console.print(text)
        except Exception:
            pass

    def _build_context(self, target: TargetInfo, **kwargs: Any) -> dict[str, Any]:
        """Construct the sandbox globals injected into Python code with multi-target coordination."""
        all_targets: list[TargetInfo] = kwargs.get("targets") or [target]
        active_target_box = [target]
        tool_log_cb = kwargs.get("tool_log_callback")

        def _log(rich_text: str, plain_text: str) -> None:
            self._print_action(rich_text)
            if tool_log_cb:
                tool_log_cb(plain_text)

        def _resolve_target(target_arg: str | TargetInfo | None = None) -> TargetInfo:
            if target_arg is None:
                return active_target_box[0]
            if isinstance(target_arg, TargetInfo):
                return target_arg
            query = str(target_arg).strip().lower()
            # 1. Exact or prefix ID match
            for t in all_targets:
                if query == t.target_id.lower() or t.target_id.lower().startswith(query):
                    return t
            # 2. Substring match on title or process name
            for t in all_targets:
                if query in t.title.lower() or query in (t.process_name or "").lower():
                    return t
            return active_target_box[0]

        def switch_to(target_query: str | TargetInfo) -> TargetInfo:
            tgt = _resolve_target(target_query)
            active_target_box[0] = tgt
            self.capturer.focus(tgt)
            _log(
                f"  [bold yellow]⚡ [Tool Call][/bold yellow] [bold cyan]switch_to[/bold cyan]({repr(str(target_query))}) "
                f"[dim]──▶ Focused: '{tgt.title}' [ID: {tgt.target_id}][/dim]",
                f"⚡ [Tool Call] switch_to({repr(str(target_query))}) ──▶ Focused: '{tgt.title}' [ID: {tgt.target_id}]",
            )
            on_switch = kwargs.get("on_switch_target")
            if on_switch:
                on_switch(tgt)
            return tgt

        def _resolve_coords(x: float, y: float, tgt: TargetInfo) -> tuple[int, int]:
            return CoordinateProjector.to_screen_coords(
                target=tgt,
                x=x,
                y=y,
                img_width=tgt.rect.width,
                img_height=tgt.rect.height,
            )

        def click(
            x: float,
            y: float,
            button: str = "left",
            clicks: int = 1,
            target: str | TargetInfo | None = None,
        ) -> None:
            tgt = switch_to(target) if target else active_target_box[0]
            sx, sy = _resolve_coords(x, y, tgt)
            _log(
                f"  [bold yellow]⚡ [Tool Call][/bold yellow] [bold cyan]click[/bold cyan](x={int(x)}, y={int(y)}) "
                f"[dim]──▶ Screen: ({sx}, {sy}) [button={button}, clicks={clicks}, window='{tgt.title}'][/dim]",
                f"⚡ [Tool Call] click(x={int(x)}, y={int(y)}) ──▶ Screen: ({sx}, {sy}) [button={button}, clicks={clicks}, window='{tgt.title}']",
            )
            driver.click(
                x=sx,
                y=sy,
                button=button,  # type: ignore
                clicks=clicks,
                window_handle=tgt.native_handle,
                window_rel_x=int(x),
                window_rel_y=int(y),
            )

        def move(x: float, y: float, target: str | TargetInfo | None = None) -> None:
            tgt = switch_to(target) if target else active_target_box[0]
            sx, sy = _resolve_coords(x, y, tgt)
            _log(
                f"  [bold yellow]⚡ [Tool Call][/bold yellow] [bold cyan]move[/bold cyan](x={int(x)}, y={int(y)}) "
                f"[dim]──▶ Screen: ({sx}, {sy}) [window='{tgt.title}'][/dim]",
                f"⚡ [Tool Call] move(x={int(x)}, y={int(y)}) ──▶ Screen: ({sx}, {sy}) [window='{tgt.title}']",
            )
            driver.move_to(x=sx, y=sy)

        def double_click(x: float, y: float, target: str | TargetInfo | None = None) -> None:
            tgt = switch_to(target) if target else active_target_box[0]
            sx, sy = _resolve_coords(x, y, tgt)
            _log(
                f"  [bold yellow]⚡ [Tool Call][/bold yellow] [bold cyan]double_click[/bold cyan](x={int(x)}, y={int(y)}) "
                f"[dim]──▶ Screen: ({sx}, {sy}) [window='{tgt.title}'][/dim]",
                f"⚡ [Tool Call] double_click(x={int(x)}, y={int(y)}) ──▶ Screen: ({sx}, {sy}) [window='{tgt.title}']",
            )
            driver.double_click(x=sx, y=sy, window_handle=tgt.native_handle)

        def right_click(x: float, y: float, target: str | TargetInfo | None = None) -> None:
            tgt = switch_to(target) if target else active_target_box[0]
            sx, sy = _resolve_coords(x, y, tgt)
            _log(
                f"  [bold yellow]⚡ [Tool Call][/bold yellow] [bold cyan]right_click[/bold cyan](x={int(x)}, y={int(y)}) "
                f"[dim]──▶ Screen: ({sx}, {sy}) [window='{tgt.title}'][/dim]",
                f"⚡ [Tool Call] right_click(x={int(x)}, y={int(y)}) ──▶ Screen: ({sx}, {sy}) [window='{tgt.title}']",
            )
            driver.right_click(x=sx, y=sy, window_handle=tgt.native_handle)

        def type_text(text: str, target: str | TargetInfo | None = None) -> None:
            tgt = switch_to(target) if target else active_target_box[0]
            method_desc = (
                "Clipboard Injection (Ctrl+V)"
                if (any(ord(c) > 127 for c in text) or sys.platform == "win32")
                else "Keyboard Emulation"
            )
            _log(
                f"  [bold yellow]⚡ [Tool Call][/bold yellow] [bold cyan]type_text[/bold cyan]({repr(text)}) "
                f"[dim]──▶ Method: {method_desc} [window='{tgt.title}'][/dim]",
                f"⚡ [Tool Call] type_text({repr(text)}) ──▶ Method: {method_desc} [window='{tgt.title}']",
            )
            driver.type_text(text, window_handle=tgt.native_handle)

        def press(key: str, target: str | TargetInfo | None = None) -> None:
            tgt = switch_to(target) if target else active_target_box[0]
            _log(
                f"  [bold yellow]⚡ [Tool Call][/bold yellow] [bold cyan]press[/bold cyan]({repr(key)}) [dim][window='{tgt.title}'][/dim]",
                f"⚡ [Tool Call] press({repr(key)}) [window='{tgt.title}']",
            )
            driver.press_key(key, window_handle=tgt.native_handle)

        def hotkey(*keys: str, target: str | TargetInfo | None = None) -> None:
            tgt = switch_to(target) if target else active_target_box[0]
            _log(
                f"  [bold yellow]⚡ [Tool Call][/bold yellow] [bold cyan]hotkey[/bold cyan]({', '.join(repr(k) for k in keys)}) [dim][window='{tgt.title}'][/dim]",
                f"⚡ [Tool Call] hotkey({', '.join(repr(k) for k in keys)}) [window='{tgt.title}']",
            )
            driver.hotkey(*keys, window_handle=tgt.native_handle)

        def scroll(
            amount: int,
            x: float | None = None,
            y: float | None = None,
            target: str | TargetInfo | None = None,
        ) -> None:
            tgt = switch_to(target) if target else active_target_box[0]
            pos_info = f" at ({x}, {y})" if x is not None and y is not None else ""
            _log(
                f"  [bold yellow]⚡ [Tool Call][/bold yellow] [bold cyan]scroll[/bold cyan](amount={amount}{pos_info}) [dim][window='{tgt.title}'][/dim]",
                f"⚡ [Tool Call] scroll(amount={amount}{pos_info}) [window='{tgt.title}']",
            )
            if x is not None and y is not None:
                sx, sy = _resolve_coords(x, y, tgt)
                driver.scroll(amount, x=sx, y=sy)
            else:
                driver.scroll(amount)

        def wait(seconds: float) -> None:
            _log(
                f"  [bold yellow]⚡ [Tool Call][/bold yellow] [bold cyan]wait[/bold cyan]({seconds}s)",
                f"⚡ [Tool Call] wait({seconds}s)",
            )
            driver.wait(seconds)

        def screenshot(target: str | TargetInfo | None = None) -> Any:
            tgt = switch_to(target) if target else active_target_box[0]
            _log(
                f"  [bold yellow]⚡ [Tool Call][/bold yellow] [bold cyan]screenshot[/bold cyan]() [dim][window='{tgt.title}'][/dim]",
                f"⚡ [Tool Call] screenshot() [window='{tgt.title}']",
            )
            return self.capturer.capture(tgt)

        return {
            "target": active_target_box[0],
            "targets": all_targets,
            "switch_to": switch_to,
            "focus": switch_to,
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

        tool_logs: list[str] = []
        context = self._build_context(target, tool_log_callback=tool_logs.append, **kwargs)

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
            data={"executed_code": code_str, "tool_calls": tool_logs},
        )
