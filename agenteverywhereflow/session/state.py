"""Session states and lifecycle events for AgentEverywhereFlow."""

import time
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class SessionState(StrEnum):
    """Lifecycle state of an interactive agent session."""

    IDLE = "idle"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_INPUT = "waiting_input"
    ABORTED = "aborted"
    ERROR = "error"
    CLOSED = "closed"


class SessionEventType(StrEnum):
    """Event types emitted during session execution for streaming and logging."""

    SESSION_START = "session_start"
    TURN_START = "turn_start"
    OBSERVE = "observe"
    REASONING = "reasoning"
    ACTION_PROPOSED = "action_proposed"
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_RESOLVED = "approval_resolved"
    ACTION_EXECUTED = "action_executed"
    STEP_FINISHED = "step_finished"
    TASK_COMPLETED = "task_completed"
    ABORTED = "aborted"
    ERROR = "error"


class SessionEvent(BaseModel):
    """Typed event emitted by ChatSession."""

    session_id: str
    event_type: SessionEventType
    step: int = 0
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: float = Field(default_factory=time.time)
