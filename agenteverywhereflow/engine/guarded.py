"""Control Execution Mode: Guarded atomic tool calls with safety intercepts."""

from typing import Any

from rich.console import Console
from rich.prompt import Confirm

from agenteverywhereflow.actions.coords import CoordinateProjector
from agenteverywhereflow.actions.driver import driver
from agenteverywhereflow.capturer.base import TargetInfo
from agenteverywhereflow.config import config
from agenteverywhereflow.engine.base import BaseExecutionEngine, ExecutionResult


class GuardedActionEngine(BaseExecutionEngine):
    """Executes atomic, strictly validated tool calls with permission gates."""

    def __init__(self) -> None:
        self.console = Console()

    def execute(
        self, payload: dict[str, Any], target: TargetInfo, **kwargs: Any
    ) -> ExecutionResult:
        """Execute a structured action object.

        Payload format:
            {"action": "click", "x": 100, "y": 200, "button": "left"}
            {"action": "type", "text": "hello world"}
            {"action": "press", "key": "enter"}
            {"action": "hotkey", "keys": ["ctrl", "c"]}
            {"action": "scroll", "amount": -5}
            {"action": "wait", "seconds": 1.0}
        """
        action = payload.get("action", "").lower()
        if not action:
            return ExecutionResult(success=False, error="No action specified in payload")

        # 1. Safety Guard Check
        if config.require_human_confirmation or self._is_sensitive_action(payload):
            self.console.print(
                f"[bold yellow]⚠️ Safety Gate:[/bold yellow] Agent requests action: [cyan]{payload}[/cyan]"
            )
            confirmed = Confirm.ask("Allow this action to execute?", default=True)
            if not confirmed:
                return ExecutionResult(
                    success=False,
                    error="Action cancelled by user safety gate.",
                    output="Action rejected by operator.",
                )

        # 2. Dispatch Action
        try:
            if action == "click":
                x = float(payload.get("x", 0))
                y = float(payload.get("y", 0))
                button = payload.get("button", "left")
                clicks = int(payload.get("clicks", 1))
                sx, sy = CoordinateProjector.to_screen_coords(
                    target, x, y, img_width=target.rect.width, img_height=target.rect.height
                )
                driver.click(
                    x=sx,
                    y=sy,
                    button=button,  # type: ignore
                    clicks=clicks,
                    window_handle=target.native_handle,
                    window_rel_x=int(x),
                    window_rel_y=int(y),
                )
                return ExecutionResult(success=True, output=f"Clicked at ({sx}, {sy})")

            elif action == "move":
                x = float(payload.get("x", 0))
                y = float(payload.get("y", 0))
                sx, sy = CoordinateProjector.to_screen_coords(
                    target, x, y, img_width=target.rect.width, img_height=target.rect.height
                )
                driver.move_to(sx, sy)
                return ExecutionResult(success=True, output=f"Moved to ({sx}, {sy})")

            elif action == "type":
                text = str(payload.get("text", ""))
                driver.type_text(text, window_handle=target.native_handle)
                return ExecutionResult(success=True, output=f"Typed text: {text}")

            elif action == "press":
                key = str(payload.get("key", ""))
                driver.press_key(key, window_handle=target.native_handle)
                return ExecutionResult(success=True, output=f"Pressed key: {key}")

            elif action == "hotkey":
                keys = payload.get("keys", [])
                driver.hotkey(*keys, window_handle=target.native_handle)
                return ExecutionResult(success=True, output=f"Sent hotkey: {keys}")

            elif action == "scroll":
                amount = int(payload.get("amount", 0))
                driver.scroll(amount)
                return ExecutionResult(success=True, output=f"Scrolled {amount}")

            elif action == "wait":
                seconds = float(payload.get("seconds", 1.0))
                driver.wait(seconds)
                return ExecutionResult(success=True, output=f"Waited {seconds}s")

            elif action == "finish":
                return ExecutionResult(
                    success=True,
                    output="Task marked as completed.",
                    data={"finished": True, "message": payload.get("message", "")},
                )

            else:
                return ExecutionResult(success=False, error=f"Unknown action: '{action}'")

        except Exception as e:
            return ExecutionResult(success=False, error=str(e))

    def _is_sensitive_action(self, payload: dict[str, Any]) -> bool:
        """Heuristic check for sensitive key combos or destructive patterns."""
        action = payload.get("action", "").lower()
        if action == "hotkey":
            keys = [str(k).lower() for k in payload.get("keys", [])]
            if "delete" in keys or "d" in keys and "win" in keys:
                return True
        return False
