"""Base execution engine interfaces."""

from abc import ABC, abstractmethod
from typing import Any
from pydantic import BaseModel, Field

from agenteverywhereflow.capturer.base import TargetInfo


class ExecutionResult(BaseModel):
    """Result of an action or script execution."""
    success: bool
    output: str = ""
    error: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


class BaseExecutionEngine(ABC):
    """Abstract interface for executing agent actions."""

    @abstractmethod
    def execute(self, payload: Any, target: TargetInfo, **kwargs: Any) -> ExecutionResult:
        """Execute the action or code payload within the context of the target."""
        pass
