"""Session Manager registry for tracking and coordinating active dialogue sessions."""

import threading
from typing import Any

from agenteverywhereflow.capturer.base import TargetInfo
from agenteverywhereflow.config import ExecutionMode
from agenteverywhereflow.security.permission import PermissionMode
from agenteverywhereflow.session.session import ChatSession
from agenteverywhereflow.session.state import SessionState
from agenteverywhereflow.session.storage import SessionMetadata, session_storage


class SessionManager:
    """Thread-safe registry for active ChatSession instances with disk recovery."""

    def __init__(self) -> None:
        self._sessions: dict[str, ChatSession] = {}
        self._lock = threading.Lock()

    def create_session(
        self,
        target: TargetInfo | None = None,
        targets: list[TargetInfo] | None = None,
        session_id: str | None = None,
        mode: ExecutionMode = ExecutionMode.MINIMAL_PYTHON,
        permission_mode: PermissionMode = PermissionMode.AUTO,
        planner_func: Any | None = None,
    ) -> ChatSession:
        """Create, register, and return a new ChatSession."""
        session = ChatSession(
            target=target,
            targets=targets,
            session_id=session_id,
            mode=mode,
            permission_mode=permission_mode,
            planner_func=planner_func,
        )
        with self._lock:
            self._sessions[session.session_id] = session
        try:
            session.save()
        except Exception:
            pass
        return session

    def get_session(self, session_id: str) -> ChatSession | None:
        """Retrieve an active in-memory session by its ID."""
        with self._lock:
            return self._sessions.get(session_id)

    def restore_session(self, session_id: str) -> ChatSession | None:
        """Restore a persisted session from disk into memory."""
        with self._lock:
            if session_id in self._sessions:
                return self._sessions[session_id]

        restored = ChatSession.restore(session_id)
        if restored:
            with self._lock:
                self._sessions[restored.session_id] = restored
            return restored
        return None

    def get_latest_session(self) -> ChatSession | None:
        """Retrieve the most recently active session from memory or disk."""
        with self._lock:
            if self._sessions:
                return list(self._sessions.values())[-1]
        latest_id = session_storage.get_latest_session_id()
        if latest_id:
            return self.restore_session(latest_id)
        return None

    def list_sessions(self) -> list[ChatSession]:
        """List all active in-memory sessions."""
        with self._lock:
            return list(self._sessions.values())

    def list_all_stored(self) -> list[SessionMetadata]:
        """List all stored session metadata records on disk."""
        return session_storage.list_sessions()

    def close_session(self, session_id: str, delete_storage: bool = False) -> bool:
        """Mark session as closed and optionally remove files from disk."""
        with self._lock:
            session = self._sessions.pop(session_id, None)
            if session:
                session.state = SessionState.CLOSED

        if delete_storage:
            session_storage.delete_session(session_id)

        return session is not None

    def clear(self) -> None:
        """Clear all in-memory registered sessions."""
        with self._lock:
            for s in self._sessions.values():
                s.state = SessionState.CLOSED
            self._sessions.clear()


# Global singleton instance
session_manager = SessionManager()
