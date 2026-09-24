"""Tests for AgentLoop execution flow and debug diagnostics."""

from unittest.mock import patch

from PIL import Image

from agenteverywhereflow.agent.loop import AgentLoop, extract_codeact_blocks
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


def test_extract_codeact_blocks_combines_multiple_blocks() -> None:
    text = """
I will click the input area and type the text:
```python
click(100, 200)
type_text("hello")
```
Then I will submit by pressing enter:
```python
press("enter")
```
"""
    extracted = extract_codeact_blocks(text)
    assert "click(100, 200)" in extracted
    assert 'type_text("hello")' in extracted
    assert 'press("enter")' in extracted
    # Ensure they are combined into executable code
    assert extracted == 'click(100, 200)\ntype_text("hello")\n\npress("enter")'


def test_extract_codeact_blocks_ignores_json_block() -> None:
    text = """
```json
{"action": "finish", "message": "done"}
```
"""
    extracted = extract_codeact_blocks(text)
    assert extracted == ""


def test_loop_executes_multiple_codeact_blocks() -> None:
    target = get_mock_target()
    cfg = AppConfig(debug=True, max_steps=2)

    def mock_planner(messages, target_info, step):
        return """
First step:
```python
wait(0.01)
print("BLOCK_1_EXECUTED")
```
Second step in same turn:
```python
wait(0.01)
print("BLOCK_2_EXECUTED")
```

TASK_COMPLETED: Finished multi-block execution.
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
            target=target, user_task="Execute compound action", mode=ExecutionMode.MINIMAL_PYTHON
        )

    assert success is True
