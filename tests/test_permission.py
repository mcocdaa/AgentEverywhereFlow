"""Tests for permission management and operator approval gate."""

from agenteverywhereflow.capturer.base import Rect, TargetInfo, TargetType
from agenteverywhereflow.config import PermissionMode
from agenteverywhereflow.security.permission import (
    ApprovalDecision,
    ApprovalRequest,
    PermissionGate,
)


def _dummy_target() -> TargetInfo:
    return TargetInfo(
        target_id="display:1",
        target_type=TargetType.DISPLAY,
        title="Primary Display",
        rect=Rect(x=0, y=0, width=1920, height=1080),
        native_handle=0,
    )


def test_permission_gate_auto_mode() -> None:
    """In AUTO mode, all approval requests must automatically be approved."""
    gate = PermissionGate(mode=PermissionMode.AUTO)
    req = ApprovalRequest(
        action_type="click",
        description="Click button",
        details={"x": 100, "y": 200},
    )
    decision = gate.request_approval(req)
    assert decision.approved is True
    assert decision.reason is None


def test_permission_gate_manual_mode_approval() -> None:
    """In MANUAL mode, custom handler determines approval."""
    handled_reqs: list[ApprovalRequest] = []

    def mock_handler(req: ApprovalRequest) -> ApprovalDecision:
        handled_reqs.append(req)
        return ApprovalDecision(approved=True)

    gate = PermissionGate(mode=PermissionMode.MANUAL, handler=mock_handler)
    req = ApprovalRequest(
        action_type="type",
        description="Type password",
        details={"text": "secret"},
    )
    decision = gate.request_approval(req)

    assert len(handled_reqs) == 1
    assert handled_reqs[0].action_type == "type"
    assert decision.approved is True


def test_permission_gate_manual_mode_rejection() -> None:
    """In MANUAL mode, operator can reject with specific reason."""

    def rejecting_handler(req: ApprovalRequest) -> ApprovalDecision:
        return ApprovalDecision(
            approved=False,
            reason="Do not enter credentials in plain text.",
        )

    gate = PermissionGate(mode=PermissionMode.MANUAL, handler=rejecting_handler)
    req = ApprovalRequest(
        action_type="codeact",
        description="Run code snippet",
        code_snippet="click(500, 600)",
    )
    decision = gate.request_approval(req)

    assert decision.approved is False
    assert decision.reason == "Do not enter credentials in plain text."


def test_permission_gate_viewport_bounds() -> None:
    """Coordinates outside target dimensions must be flagged."""
    target = _dummy_target()
    gate = PermissionGate()

    assert gate.check_viewport_bounds(target, 0, 0) is True
    assert gate.check_viewport_bounds(target, 1920, 1080) is True
    assert gate.check_viewport_bounds(target, 500, 500) is True

    # Out-of-bounds
    assert gate.check_viewport_bounds(target, -1, 500) is False
    assert gate.check_viewport_bounds(target, 1921, 500) is False
    assert gate.check_viewport_bounds(target, 500, 1081) is False
