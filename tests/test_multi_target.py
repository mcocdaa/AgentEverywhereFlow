"""Unit tests for multi-target window coordination and cross-application switching."""

from unittest.mock import MagicMock, patch

from PIL import Image

from agenteverywhereflow.actions.driver import driver
from agenteverywhereflow.agent.prompts import get_prompt_for_targets
from agenteverywhereflow.capturer.base import Rect, TargetInfo, TargetType
from agenteverywhereflow.config import ExecutionMode
from agenteverywhereflow.engine.guarded import GuardedActionEngine
from agenteverywhereflow.engine.python_repl import PythonReplEngine
from agenteverywhereflow.session import ChatSession, session_manager, session_storage


def make_dummy_target(target_id: str, title: str, x: int = 100, y: int = 100) -> TargetInfo:
    return TargetInfo(
        target_id=target_id,
        target_type=TargetType.WINDOW,
        title=title,
        process_name=f"{title.lower()}.exe",
        rect=Rect(x=x, y=y, width=800, height=600),
        native_handle=1234,
    )


def test_multi_target_prompts():
    t1 = make_dummy_target("w1", "Doubao Chat")
    t2 = make_dummy_target("w2", "Google Chrome")

    # Single target prompt fallback
    p_single = get_prompt_for_targets([t1], active_target=t1)
    assert "Doubao Chat" in p_single
    assert "Bound Multi-Window Targets" not in p_single

    # Multi-target prompt
    p_multi = get_prompt_for_targets([t1, t2], active_target=t1, is_minimal_mode=True)
    assert "Bound Multi-Window Targets:" in p_multi
    assert "Doubao Chat" in p_multi
    assert "Google Chrome" in p_multi
    assert "[ACTIVE FOCUS]" in p_multi
    assert "switch_to" in p_multi


def test_session_multi_target_management(tmp_path):
    t1 = make_dummy_target("w1", "Doubao Chat")
    t2 = make_dummy_target("w2", "Google Chrome")
    t3 = make_dummy_target("w3", "Terminal")

    session = ChatSession(
        targets=[t1, t2],
        mode=ExecutionMode.MINIMAL_PYTHON,
    )

    assert len(session.targets) == 2
    assert session.target.target_id == "w1"

    # Add target
    session.add_target(t3)
    assert len(session.targets) == 3

    # Switch target by title
    switched = session.switch_target("Chrome")
    assert switched.target_id == "w2"
    assert session.target.target_id == "w2"

    # Switch target by ID
    switched_id = session.switch_target("w3")
    assert switched_id.target_id == "w3"
    assert session.target.target_id == "w3"

    # Remove target
    ok = session.remove_target("Chrome")
    assert ok is True
    assert len(session.targets) == 2
    assert not any(t.target_id == "w2" for t in session.targets)

    # Cannot remove down to 0 targets
    session.remove_target("w3")
    assert len(session.targets) == 1
    cannot_remove_last = session.remove_target("w1")
    assert cannot_remove_last is False
    assert len(session.targets) == 1


def test_session_multi_target_persistence(tmp_path):
    storage = session_storage
    storage.base_dir = tmp_path

    t1 = make_dummy_target("w1", "Doubao Chat", x=50, y=50)
    t2 = make_dummy_target("w2", "Google Chrome", x=200, y=200)

    session = session_manager.create_session(
        targets=[t1, t2],
        session_id="test_multi_persist",
        mode=ExecutionMode.MINIMAL_PYTHON,
    )
    session.switch_target("Chrome")
    session.save()

    # Restore session
    restored = session_manager.restore_session("test_multi_persist")
    assert restored is not None
    assert len(restored.targets) == 2
    assert restored.target.target_id == "w2"
    assert restored.targets[0].title == "Doubao Chat"
    assert restored.targets[1].title == "Google Chrome"


def test_python_repl_multi_target_switching():
    t1 = make_dummy_target("w1", "Editor", x=0, y=0)
    t2 = make_dummy_target("w2", "Browser", x=500, y=100)

    engine = PythonReplEngine()
    switched_records = []

    def on_switch(tgt):
        switched_records.append(tgt.target_id)

    with patch.object(driver, "click") as mock_click:
        code = """
switch_to("Browser")
click(50, 60)
click(10, 20, target="Editor")
"""
        res = engine.execute(
            code,
            t1,
            targets=[t1, t2],
            on_switch_target=on_switch,
        )
        assert res.success, f"Error: {res.error}"
        assert switched_records == ["w2", "w1"]
        assert mock_click.call_count == 2
        # First click on Browser (x=500+50=550, y=100+60=160)
        assert mock_click.call_args_list[0].kwargs["x"] == 550
        assert mock_click.call_args_list[0].kwargs["y"] == 160
        # Second click on Editor (x=0+10=10, y=0+20=20)
        assert mock_click.call_args_list[1].kwargs["x"] == 10
        assert mock_click.call_args_list[1].kwargs["y"] == 20


def test_guarded_multi_target_switching():
    t1 = make_dummy_target("w1", "Editor", x=0, y=0)
    t2 = make_dummy_target("w2", "Browser", x=500, y=100)

    engine = GuardedActionEngine()
    switched_records = []

    def on_switch(tgt):
        switched_records.append(tgt.target_id)

    # 1. switch_target action
    payload1 = {"action": "switch_target", "target": "Browser"}
    res1 = engine.execute(payload1, t1, targets=[t1, t2], on_switch_target=on_switch)
    assert res1.success
    assert switched_records == ["w2"]

    # 2. click with target override
    with patch.object(driver, "click") as mock_click:
        payload2 = {"action": "click", "x": 100, "y": 200, "target": "Browser"}
        res2 = engine.execute(payload2, t1, targets=[t1, t2], on_switch_target=on_switch)
        assert res2.success
        assert mock_click.call_count == 1
        assert mock_click.call_args.kwargs["x"] == 600
        assert mock_click.call_args.kwargs["y"] == 300


def test_multi_target_observation_in_chat_session():
    t1 = make_dummy_target("w1", "Window One")
    t2 = make_dummy_target("w2", "Window Two")

    dummy_img = Image.new("RGB", (640, 480), color="blue")
    capturer_mock = MagicMock()
    capturer_mock.capture.return_value = dummy_img
    capturer_mock.focus.return_value = True

    def dummy_planner(messages, target, step):
        return "```python\nclick(10, 20)\n```\nTASK_COMPLETED: Done"

    with patch("agenteverywhereflow.session.session.get_capturer", return_value=capturer_mock):
        session = ChatSession(
            targets=[t1, t2],
            planner_func=dummy_planner,
        )
        session.capturer = capturer_mock

        result = session.execute_turn("Test multi-window observation", max_steps=2)
        assert result.success is True
        assert result.completed is True

        # Verify observation message contains both targets
        obs_msg = None
        for m in session.messages:
            content = m.get("content")
            if isinstance(content, list):
                for part in content:
                    if "Screenshot of target 'Window One'" in part.get("text", ""):
                        obs_msg = content
                        break
        assert obs_msg is not None
        # Check both targets were reported
        texts = [p.get("text", "") for p in obs_msg if p.get("type") == "text"]
        full_text = " ".join(texts)
        assert "Window One" in full_text
        assert "Window Two" in full_text
        assert "[ACTIVE FOCUS]" in full_text
