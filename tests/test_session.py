"""Tests for ChatSession multi-turn conversational engine and SessionManager."""

from typing import Any
from unittest.mock import MagicMock

from PIL import Image

from agenteverywhereflow.capturer.base import Rect, TargetInfo, TargetType
from agenteverywhereflow.config import ExecutionMode, PermissionMode
from agenteverywhereflow.security.permission import ApprovalDecision, ApprovalRequest
from agenteverywhereflow.session import ChatSession, SessionEventType, SessionManager, SessionState
from agenteverywhereflow.session.session import extract_think_and_talk


def _make_dummy_target(name: str = "Test Window") -> TargetInfo:
    return TargetInfo(
        target_id="hwnd:0x1234",
        target_type=TargetType.WINDOW,
        title=name,
        rect=Rect(x=100, y=100, width=800, height=600),
        native_handle=0x1234,
    )


def test_chat_session_multi_turn_execution() -> None:
    """Verify conversational context, turns, and steps are preserved across multiple instructions."""
    target = _make_dummy_target()

    # Mock planner that simulates actions and task completion across turns
    responses = [
        # Turn 1: Step 1 executes action, finishes
        "I need to click the search bar.\n```python\nwait(0.1)\n```\nTASK_COMPLETED: Opened search bar.",
        # Turn 2: Step 1 enters query, finishes
        "Now I'll enter the search term.\n```python\nwait(0.1)\n```\nTASK_COMPLETED: Query searched.",
    ]
    call_idx = 0

    def mock_planner(messages: list[dict[str, Any]], tgt: TargetInfo, step: int) -> str:
        nonlocal call_idx
        resp = responses[call_idx]
        call_idx += 1
        return resp

    session = ChatSession(
        target=target,
        mode=ExecutionMode.MINIMAL_PYTHON,
        permission_mode=PermissionMode.AUTO,
        planner_func=mock_planner,
    )

    # Mock capturer to return a small blank image
    session.capturer.capture = MagicMock(return_value=Image.new("RGB", (100, 100), color="white"))
    session.capturer.focus = MagicMock()

    # Turn 1
    res1 = session.execute_turn("Open search bar")
    assert res1.success is True
    assert res1.completed is True
    assert "Opened search bar" in res1.response
    assert session.turn_count == 1
    assert session.total_steps == 1
    assert session.state == SessionState.WAITING_INPUT

    # Turn 2
    res2 = session.execute_turn("Type query")
    assert res2.success is True
    assert res2.completed is True
    assert "Query searched" in res2.response
    assert session.turn_count == 2
    assert session.total_steps == 2
    assert session.state == SessionState.WAITING_INPUT

    # Context history must contain user turn 1, assistant turn 1, and user turn 2
    user_turns = [
        m
        for m in session.messages
        if m["role"] == "user" and "User instruction:" in str(m["content"])
    ]
    assert len(user_turns) == 2


def test_chat_session_manual_permission_approved() -> None:
    """In MANUAL permission mode, action executes when approval is granted."""
    target = _make_dummy_target()

    def mock_planner(messages: list[dict[str, Any]], tgt: TargetInfo, step: int) -> str:
        return "I will click save.\n```python\nwait(0.1)\n```\nTASK_COMPLETED: Saved."

    approval_called = False

    def approving_handler(req: ApprovalRequest) -> ApprovalDecision:
        nonlocal approval_called
        approval_called = True
        return ApprovalDecision(approved=True)

    session = ChatSession(
        target=target,
        mode=ExecutionMode.MINIMAL_PYTHON,
        permission_mode=PermissionMode.MANUAL,
        planner_func=mock_planner,
    )
    session.permission_gate.set_handler(approving_handler)
    session.capturer.capture = MagicMock(return_value=Image.new("RGB", (100, 100), color="white"))
    session.capturer.focus = MagicMock()

    res = session.execute_turn("Save document")
    assert approval_called is True
    assert res.success is True
    assert res.completed is True


def test_chat_session_manual_permission_rejection() -> None:
    """In MANUAL permission mode, rejected action feeds guidance back to agent."""
    target = _make_dummy_target()

    step_counter = 0

    def mock_planner(messages: list[dict[str, Any]], tgt: TargetInfo, step: int) -> str:
        nonlocal step_counter
        step_counter += 1
        if step_counter == 1:
            return "I will click delete.\n```python\nwait(0.1)\n```"
        else:
            # Second step after rejection
            return "Understood, operator refused deletion. Concluding without deleting.\nTASK_COMPLETED: Aborted."

    def rejecting_handler(req: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision(approved=False, reason="Deletion is forbidden.")

    session = ChatSession(
        target=target,
        mode=ExecutionMode.MINIMAL_PYTHON,
        permission_mode=PermissionMode.MANUAL,
        planner_func=mock_planner,
    )
    session.permission_gate.set_handler(rejecting_handler)
    session.capturer.capture = MagicMock(return_value=Image.new("RGB", (100, 100), color="white"))
    session.capturer.focus = MagicMock()

    res = session.execute_turn("Clean up files")
    assert res.success is True
    assert res.completed is True
    assert "Aborted" in res.response

    # Verify feedback was appended into conversation messages
    has_rejection_notice = any(
        "REJECTED by operator: Deletion is forbidden." in str(m.get("content", ""))
        for m in session.messages
    )
    assert has_rejection_notice is True


def test_chat_session_switch_target_and_reset() -> None:
    """Test dynamic switching of active target and session reset."""
    t1 = _make_dummy_target("Window 1")
    t2 = _make_dummy_target("Window 2")

    session = ChatSession(target=t1)
    assert session.target.title == "Window 1"

    session.switch_target(t2)
    assert session.target.title == "Window 2"

    session.reset_history()
    assert session.turn_count == 0
    assert session.total_steps == 0
    assert len(session.messages) == 1  # Only system prompt remains


def test_session_manager_registry() -> None:
    """Verify SessionManager create, get, list, and close operations."""
    mgr = SessionManager()
    t = _make_dummy_target()

    s1 = mgr.create_session(t, session_id="test-1")
    assert s1.session_id == "test-1"
    assert mgr.get_session("test-1") is s1

    assert len(mgr.list_sessions()) == 1

    closed = mgr.close_session("test-1")
    assert closed is True
    assert mgr.get_session("test-1") is None
    assert len(mgr.list_sessions()) == 0


def test_session_manager_restore_and_latest() -> None:
    """Verify SessionManager restore_session and get_latest_session."""
    mgr = SessionManager()
    t = _make_dummy_target("Notepad Test")

    s = mgr.create_session(t, session_id="test-restore-01")
    s.turn_count = 3
    s.total_steps = 5
    s.save()

    # Clear in-memory
    mgr.clear()
    assert mgr.get_session("test-restore-01") is None

    # Restore from disk
    restored = mgr.restore_session("test-restore-01")
    assert restored is not None
    assert restored.session_id == "test-restore-01"
    assert restored.target.title == "Notepad Test"
    assert restored.turn_count == 3
    assert restored.total_steps == 5

    # Get latest
    latest = mgr.get_latest_session()
    assert latest is not None
    assert latest.session_id == "test-restore-01"


def test_chat_session_max_steps_limit() -> None:
    """Verify that execute_turn respects max_steps and stops gracefully when reaching step limit."""
    target = _make_dummy_target("Loop Target")

    # Planner that keeps taking intermediate actions without completing
    def infinite_scroller(messages: list[dict[str, Any]], tgt: TargetInfo, step: int) -> str:
        return "Still reading, let me scroll.\n```python\nwait(0.01)\n```"

    session = ChatSession(
        target=target,
        mode=ExecutionMode.MINIMAL_PYTHON,
        permission_mode=PermissionMode.AUTO,
        planner_func=infinite_scroller,
    )
    session.capturer.capture = MagicMock(return_value=Image.new("RGB", (100, 100), color="white"))
    session.capturer.focus = MagicMock()

    # Test with max_steps=3
    res = session.execute_turn("Keep scrolling", max_steps=3)
    assert res.success is True
    assert res.completed is False
    assert res.steps_executed == 3
    assert "Step limit reached" in res.response
    assert session.state == SessionState.WAITING_INPUT


def test_chat_session_abort_execution() -> None:
    """Verify that abort() stops an in-progress execution turn immediately."""
    target = _make_dummy_target("Abort Target")

    events_received = []

    def abort_triggering_planner(messages: list[dict[str, Any]], tgt: TargetInfo, step: int) -> str:
        session.abort()
        return "Executing action before abort.\n```python\nwait(0.01)\n```"

    session = ChatSession(
        target=target,
        mode=ExecutionMode.MINIMAL_PYTHON,
        permission_mode=PermissionMode.AUTO,
        planner_func=abort_triggering_planner,
    )
    session.capturer.capture = MagicMock(return_value=Image.new("RGB", (100, 100), color="white"))
    session.capturer.focus = MagicMock()
    session.add_listener(events_received.append)

    res = session.execute_turn("Test instruction", max_steps=10)
    assert res.success is False
    assert res.completed is False
    assert "aborted" in (res.error or "").lower()
    assert session.state == SessionState.IDLE

    has_aborted_event = any(e.event_type == SessionEventType.ABORTED for e in events_received)
    assert has_aborted_event is True


def test_extract_think_and_talk_decoupling() -> None:
    """Verify clean decoupling of internal Think, user-facing Talk, and CodeAct Act."""
    # Case 1: Model with <think> tag and code
    text1 = (
        "<think>The user wants to open the search bar. I need to click (100, 200).</think>\n"
        "Opening search bar now...\n"
        "```python\nclick(100, 200)\n```"
    )
    th1, ta1 = extract_think_and_talk(text1)
    assert th1 == "The user wants to open the search bar. I need to click (100, 200)."
    assert ta1 == "Opening search bar now..."

    # Case 2: Model asking for clarification (pure dialogue, no code, no think tags)
    text2 = "已为您定位到微信窗口。请问您希望发送什么文字内容给文件传输助手？"
    th2, ta2 = extract_think_and_talk(text2)
    assert th2 == ""
    assert ta2 == text2

    # Case 3: API structured reasoning (DeepSeek-R1 / o1)
    text3 = "分析当前布局：左侧导航栏，右侧聊天区。"
    th3, ta3 = extract_think_and_talk(
        text3, raw_thinking="Examining window layout in visual observation."
    )
    assert th3 == "Examining window layout in visual observation."
    assert ta3 == "分析当前布局：左侧导航栏，右侧聊天区。"

    # Case 4: TASK_COMPLETED extraction
    text4 = "点击完成。\n```python\nclick(50, 50)\n```\nTASK_COMPLETED: 任务已完成。"
    th4, ta4 = extract_think_and_talk(text4)
    assert th4 == ""
    assert ta4 == "点击完成。"


def test_chat_session_conversational_turn_without_code() -> None:
    """Verify that a conversational turn without code executes gracefully without false retry or scolding."""
    target = _make_dummy_target("WeChat Test")

    # Planner simulates answering an inquiry about layout
    def layout_planner(messages: list[dict[str, Any]], tgt: TargetInfo, step: int) -> str:
        return (
            "<think>Analyzing window structure: 3 vertical panels detected.</think>\n"
            "当前微信窗口为三栏式布局：①左侧导航栏 ②中间会话列表 ③右侧聊天区。"
        )

    events_emitted = []
    session = ChatSession(
        target=target,
        mode=ExecutionMode.MINIMAL_PYTHON,
        permission_mode=PermissionMode.AUTO,
        planner_func=layout_planner,
    )
    session.capturer.capture = MagicMock(return_value=Image.new("RGB", (100, 100), color="white"))
    session.capturer.focus = MagicMock()
    session.add_listener(events_emitted.append)

    res = session.execute_turn("分析当前窗口界面布局与主要元素")
    assert res.success is True
    assert res.completed is True
    assert "三栏式布局" in res.response
    assert session.state == SessionState.WAITING_INPUT

    # Verify event types emitted
    event_types = [e.event_type for e in events_emitted]
    assert SessionEventType.REASONING in event_types
    assert SessionEventType.MESSAGE in event_types
    assert SessionEventType.TURN_FINISHED in event_types

    # Ensure REASONING has thinking and MESSAGE has user-facing text
    reasoning_ev = next(e for e in events_emitted if e.event_type == SessionEventType.REASONING)
    assert "3 vertical panels" in reasoning_ev.payload["thinking"]

    message_ev = next(e for e in events_emitted if e.event_type == SessionEventType.MESSAGE)
    assert "三栏式布局" in message_ev.payload["text"]

    # Verify conversational TASK_COMPLETED payload has conversational=True
    tc_ev = next(e for e in events_emitted if e.event_type == SessionEventType.TASK_COMPLETED)
    assert tc_ev.payload.get("conversational") is True

    # Crucial: verify that NO retry error or scolding prompt was appended to session.messages
    retry_prompts = [
        m for m in session.messages if "No executable code block" in str(m.get("content", ""))
    ]
    assert len(retry_prompts) == 0


def test_chat_session_conversational_clarification_flow() -> None:
    """Verify multi-turn flow where model first asks for clarification, then executes on user reply."""
    target = _make_dummy_target("WeChat Test")

    responses = [
        # Turn 1: Model asks clarification
        "请问您想给文件传输助手发送什么文字内容？",
        # Turn 2: User provides content -> Model executes code and finishes
        "正在输入消息...\n```python\nwait(0.1)\n```\nTASK_COMPLETED: 已向「文件传输助手」发送消息「你好呀」。",
    ]
    call_idx = 0

    def mock_agent(messages: list[dict[str, Any]], tgt: TargetInfo, step: int) -> str:
        nonlocal call_idx
        resp = responses[call_idx]
        call_idx += 1
        return resp

    session = ChatSession(
        target=target,
        mode=ExecutionMode.MINIMAL_PYTHON,
        permission_mode=PermissionMode.AUTO,
        planner_func=mock_agent,
    )
    session.capturer.capture = MagicMock(return_value=Image.new("RGB", (100, 100), color="white"))
    session.capturer.focus = MagicMock()

    # Turn 1: User gives vague instruction
    turn1_events = []
    session.add_listener(turn1_events.append)
    res1 = session.execute_turn("帮我给文件传输助手发个消息")

    assert res1.success is True
    assert res1.completed is True
    assert "请问您想给文件传输助手发送什么文字内容？" in res1.response
    assert session.state == SessionState.WAITING_INPUT
    assert any(e.event_type == SessionEventType.MESSAGE for e in turn1_events)

    # Turn 2: User specifies content
    turn2_events = []
    session.add_listener(turn2_events.append)
    res2 = session.execute_turn("发送'你好呀'")

    assert res2.success is True
    assert res2.completed is True
    assert "你好呀" in res2.response
    assert session.state == SessionState.WAITING_INPUT

    # In turn 2, an action was executed and finished with real TASK_COMPLETED
    t2_completed_ev = next(
        e for e in turn2_events if e.event_type == SessionEventType.TASK_COMPLETED
    )
    assert t2_completed_ev.payload.get("conversational") is False
    assert "你好呀" in t2_completed_ev.payload["summary"]
