"""Example: Custom Vision-Language Model Planner Callback.

Demonstrates how to plug in your own local model, mock planner, or specialized prompt strategy.
"""

from typing import Any

from agenteverywhereflow.agent.loop import AgentLoop
from agenteverywhereflow.capturer.base import Rect, TargetInfo, TargetType
from agenteverywhereflow.config import AppConfig, ExecutionMode


def custom_heuristic_planner(
    messages: list[dict[str, Any]], target_info: TargetInfo, step_idx: int
) -> str:
    """A custom planner callback that inspects the viewport context and returns tool actions."""
    print(f"Step {step_idx}: Custom planner received {len(messages)} context messages.")
    print(f"Target geometry: {target_info.rect.width}x{target_info.rect.height}")

    if step_idx == 1:
        # Emit Python code to click the center of the viewport
        cx = target_info.rect.width // 2
        cy = target_info.rect.height // 2
        return f"""Focusing window center:
```python
click(x={cx}, y={cy})
type_text("Hello from Custom Planner!")
```"""
    else:
        return "TASK_COMPLETED: Finished demo sequence."


def main():
    # Mock window target
    target = TargetInfo(
        target_id="demo_target",
        target_type=TargetType.WINDOW,
        title="Custom Planner Demo Window",
        rect=Rect(x=0, y=0, width=800, height=600),
        native_handle=0,
    )

    cfg = AppConfig(max_steps=3, default_mode=ExecutionMode.MINIMAL_PYTHON)
    loop = AgentLoop(app_config=cfg, planner_func=custom_heuristic_planner)

    print("Running AgentLoop with custom planner...")
    # This will demonstrate the flow without requiring external API keys
    loop.run(target=target, user_task="Custom Planner Test")


if __name__ == "__main__":
    main()
