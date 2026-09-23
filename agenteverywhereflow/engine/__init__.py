"""Execution engines module."""

from agenteverywhereflow.config import ExecutionMode
from agenteverywhereflow.engine.base import BaseExecutionEngine, ExecutionResult
from agenteverywhereflow.engine.guarded import GuardedActionEngine
from agenteverywhereflow.engine.python_repl import PythonReplEngine


def get_engine(mode: ExecutionMode) -> BaseExecutionEngine:
    """Factory for obtaining execution engine by mode."""
    if mode == ExecutionMode.MINIMAL_PYTHON:
        return PythonReplEngine()
    elif mode == ExecutionMode.CONTROL_GUARDED:
        return GuardedActionEngine()
    else:
        return PythonReplEngine()


__all__ = [
    "BaseExecutionEngine",
    "ExecutionResult",
    "PythonReplEngine",
    "GuardedActionEngine",
    "get_engine",
]
