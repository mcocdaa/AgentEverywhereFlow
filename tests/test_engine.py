"""Tests for dual-mode execution engines."""

from agenteverywhereflow.capturer.base import Rect, TargetInfo, TargetType
from agenteverywhereflow.engine.guarded import GuardedActionEngine
from agenteverywhereflow.engine.python_repl import PythonReplEngine


def get_mock_target() -> TargetInfo:
    return TargetInfo(
        target_id="mock:1",
        target_type=TargetType.WINDOW,
        title="Mock Test Window",
        rect=Rect(x=0, y=0, width=1280, height=720),
    )


def test_python_repl_engine_calculation() -> None:
    engine = PythonReplEngine()
    target = get_mock_target()

    code = """
x = 10 + 20
print(f"Calculated: {x}")
"""
    result = engine.execute(code, target)
    assert result.success is True
    assert "Calculated: 30" in result.output


def test_python_repl_engine_exception_handling() -> None:
    engine = PythonReplEngine()
    target = get_mock_target()

    code = """
raise ValueError("Intentional test error")
"""
    result = engine.execute(code, target)
    assert result.success is False
    assert "Intentional test error" in result.error


def test_guarded_action_engine_wait_and_finish() -> None:
    engine = GuardedActionEngine()
    target = get_mock_target()

    # Wait action
    res_wait = engine.execute({"action": "wait", "seconds": 0.01}, target)
    assert res_wait.success is True

    # Finish action
    res_finish = engine.execute({"action": "finish", "message": "All done!"}, target)
    assert res_finish.success is True
    assert res_finish.data.get("finished") is True


def test_python_repl_engine_tool_calls_do_not_leak_into_output() -> None:
    engine = PythonReplEngine()
    target = get_mock_target()

    # Tool calls like wait(), click() should print real-time events without polluting result.output
    code = """
wait(0.001)
"""
    result = engine.execute(code, target)
    assert result.success is True
    assert result.output == ""
