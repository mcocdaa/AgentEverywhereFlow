"""Tests for AgentLoop execution flow and debug diagnostics."""

from unittest.mock import patch

from PIL import Image

from agenteverywhereflow.agent.loop import AgentLoop
from agenteverywhereflow.capturer.base import Rect, TargetInfo, TargetType
from agenteverywhereflow.config import AppConfig, ExecutionMode


def get_mock_target() -> TargetInfo:
    return TargetInfo(
        target_id="hwnd:0x70d6a",
        target_type=TargetType.WINDOW,
        title="1.txt - Notepad",
        rect=Rect(x=100, y=100, width=800, height=600),
        native_handle=0x70D6A,
        process_name="notepad.exe",
    )


def test_loop_executes_action_before_task_completed():
    target = get_mock_target()
    cfg = AppConfig(debug=True, max_steps=3)

    def mock_planner(messages, target_info, step):
        return """
Observation: Notepad is open.
Intent: Click and type text.

```python
wait(0.01)
print("EXECUTED_NOTEPAD_ACTION")
```

TASK_COMPLETED: Clicked the center of the Notepad editing area and typed successfully.
"""

    loop = AgentLoop(app_config=cfg, planner_func=mock_planner)

    with (
        patch.object(loop.capturer, "focus", return_value=True),
        patch.object(
            loop.capturer,
            "capture",
            return_value=Image.new("RGB", (100, 100), color="white"),
        ),
    ):
        success = loop.run(
            target=target, user_task="Type into notepad", mode=ExecutionMode.MINIMAL_PYTHON
        )

    assert success is True


def test_loop_debug_mode_diagnostics():
    target = get_mock_target()
    cfg = AppConfig(debug=True, max_steps=1)

    def mock_planner(messages, target_info, step):
        return "TASK_COMPLETED: Done immediately."

    loop = AgentLoop(app_config=cfg, planner_func=mock_planner)

    with (
        patch.object(loop.capturer, "focus", return_value=True),
        patch.object(
            loop.capturer,
            "capture",
            return_value=Image.new("RGB", (100, 100), color="white"),
        ),
    ):
        success = loop.run(
            target=target, user_task="Quick check", mode=ExecutionMode.MINIMAL_PYTHON
        )

    assert success is True


def test_loop_prunes_older_visual_history():
    cfg = AppConfig(max_visual_history_images=2)
    loop = AgentLoop(app_config=cfg)

    # Construct 4 messages with image_url
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Turn 1"},
                {"type": "image_url", "image_url": {"url": "data:img1"}},
            ],
        },
        {"role": "assistant", "content": "Ok 1"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Turn 2"},
                {"type": "image_url", "image_url": {"url": "data:img2"}},
            ],
        },
        {"role": "assistant", "content": "Ok 2"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Turn 3"},
                {"type": "image_url", "image_url": {"url": "data:img3"}},
            ],
        },
    ]

    pruned = loop._prune_visual_history(messages)

    # First turn's image_url should be replaced with text placeholder
    turn1_content = pruned[0]["content"]
    assert any("omitted" in p.get("text", "") for p in turn1_content)
    assert not any(p.get("type") == "image_url" for p in turn1_content)

    # Turn 2 and 3 should retain their image_url
    assert any(p.get("type") == "image_url" for p in pruned[2]["content"])
    assert any(p.get("type") == "image_url" for p in pruned[4]["content"])
