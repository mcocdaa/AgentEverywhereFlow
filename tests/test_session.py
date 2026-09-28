"""Tests for ChatSession multi-turn conversational engine and SessionManager."""

from typing import Any
from unittest.mock import MagicMock

from PIL import Image

from agenteverywhereflow.capturer.base import Rect, TargetInfo, TargetType
from agenteverywhereflow.config import ExecutionMode, PermissionMode
from agenteverywhereflow.security.permission import ApprovalDecision, ApprovalRequest
from agenteverywhereflow.session import ChatSession, SessionManager, SessionState


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
