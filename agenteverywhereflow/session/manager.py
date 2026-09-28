"""Session Manager registry for tracking and coordinating active dialogue sessions."""

import threading
from typing import Any

from agenteverywhereflow.capturer.base import TargetInfo
from agenteverywhereflow.config import ExecutionMode
from agenteverywhereflow.security.permission import PermissionMode
from agenteverywhereflow.session.session import ChatSession
from agenteverywhereflow.session.state import SessionState


class SessionManager:
    """Thread-safe registry for active ChatSession instances."""

    def __init__(self) -> None:
        self._sessions: dict[str, ChatSession] = {}
        self._lock = threading.Lock()

    def create_session(
        self,
        target: TargetInfo,
        session_id: str | None = None,
        mode: ExecutionMode = ExecutionMode.MINIMAL_PYTHON,
        permission_mode: PermissionMode = PermissionMode.AUTO,
        planner_func: Any | None = None,
    ) -> ChatSession:
        """Create, register, and return a new ChatSession."""
        session = ChatSession(
            target=target,
            session_id=session_id,
            mode=mode,
            permission_mode=permission_mode,
            planner_func=planner_func,
        )
        with self._lock:
            self._sessions[session.session_id] = session
        return session

    def get_session(self, session_id: str) -> ChatSession | None:
        """Retrieve an active session by its ID."""
        with self._lock:
            return self._sessions.get(session_id)

    def list_sessions(self) -> list[ChatSession]:
        """List all active sessions."""
        with self._lock:
            return list(self._sessions.values())

    def close_session(self, session_id: str) -> bool:
        """Mark session as closed and remove it from the registry."""
        with self._lock:
            session = self._sessions.pop(session_id, None)
            if session:
                session.state = SessionState.CLOSED
                return True
            return False

    def clear(self) -> None:
        """Clear all registered sessions."""
        with self._lock:
            for s in self._sessions.values():
                s.state = SessionState.CLOSED
            self._sessions.clear()


# Global singleton instance
session_manager = SessionManager()
