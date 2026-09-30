"""Persistent storage and recovery layer for AgentEverywhereFlow dialogue sessions."""

import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from agenteverywhereflow.capturer.base import TargetInfo
from agenteverywhereflow.config import ExecutionMode, PermissionMode

logger = logging.getLogger(__name__)


def get_sessions_dir() -> Path:
    """Resolve default ~/.aef/sessions directory."""
    s_dir = Path.home() / ".aef" / "sessions"
    s_dir.mkdir(parents=True, exist_ok=True)
    return s_dir


class TokenUsageStats(BaseModel):
    """Aggregate token consumption and KV cache reuse statistics."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_prompt_tokens: int = 0  # Tokens served from prefix KV cache

    @property
    def cache_hit_rate(self) -> float:
        """Percentage of prompt tokens served directly from KV cache."""
        if self.prompt_tokens <= 0:
            return 0.0
        return round((self.cached_prompt_tokens / self.prompt_tokens) * 100, 2)


class SessionMetadata(BaseModel):
    """Summary metadata record for a saved dialogue session."""

    session_id: str
    target_id: str
    target_title: str
    target_type: str = "window"
    target_rect: dict[str, int] = Field(default_factory=dict)
    native_handle: int = 0
    process_name: str = ""
    mode: str = "minimal"
    permission_mode: str = "auto"
    state: str = "idle"
    turn_count: int = 0
    total_steps: int = 0
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    token_usage: TokenUsageStats = Field(default_factory=TokenUsageStats)
    targets: list[dict[str, Any]] = Field(default_factory=list)


class SessionStorage:
    """Handles disk serialization, recovery, and indexing of dialogue sessions."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or get_sessions_dir()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _get_session_dir(self, session_id: str) -> Path:
        return self.base_dir / session_id

    def save_session(
        self,
        session_id: str,
        target: TargetInfo,
        mode: ExecutionMode,
        permission_mode: PermissionMode,
        state: str,
        turn_count: int,
        total_steps: int,
        messages: list[dict[str, Any]],
        token_usage: TokenUsageStats,
        created_at: float,
        targets: list[TargetInfo] | None = None,
        recorded_steps: list[dict[str, Any]] | None = None,
    ) -> Path:
        """Atomically persist session metadata and conversational trajectory to disk."""
        s_dir = self._get_session_dir(session_id)
        s_dir.mkdir(parents=True, exist_ok=True)

        all_tgts = targets or [target]
        targets_serialized = [
            {
                "target_id": t.target_id,
                "title": t.title,
                "target_type": t.target_type.value
                if hasattr(t.target_type, "value")
                else str(t.target_type),
                "target_rect": {
                    "x": t.rect.x,
                    "y": t.rect.y,
                    "width": t.rect.width,
                    "height": t.rect.height,
                },
                "native_handle": t.native_handle or 0,
                "process_name": t.process_name or "",
            }
            for t in all_tgts
        ]

        meta = SessionMetadata(
            session_id=session_id,
            target_id=target.target_id,
            target_title=target.title,
            target_type=target.target_type.value
            if hasattr(target.target_type, "value")
            else str(target.target_type),
            target_rect={
                "x": target.rect.x,
                "y": target.rect.y,
                "width": target.rect.width,
                "height": target.rect.height,
            },
            native_handle=target.native_handle or 0,
            process_name=target.process_name or "",
            mode=mode.value if hasattr(mode, "value") else str(mode),
            permission_mode=(
                permission_mode.value if hasattr(permission_mode, "value") else str(permission_mode)
            ),
            state=state,
            turn_count=turn_count,
            total_steps=total_steps,
            created_at=created_at,
            updated_at=time.time(),
            token_usage=token_usage,
            targets=targets_serialized,
        )

        meta_file = s_dir / "meta.json"
        with open(meta_file, "w", encoding="utf-8") as f:
            f.write(meta.model_dump_json(indent=2))

        # Save conversation messages trajectory (omitting massive base64 payloads to save disk)
        sanitized_messages: list[dict[str, Any]] = []
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, list):
                sanitized_content = []
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "image_url":
                        sanitized_content.append(
                            {"type": "text", "text": "[Stored Viewport Screenshot Payload]"}
                        )
                    else:
                        sanitized_content.append(part)
                sanitized_messages.append({"role": msg.get("role"), "content": sanitized_content})
            else:
                sanitized_messages.append(msg)

        msg_file = s_dir / "messages.json"
        with open(msg_file, "w", encoding="utf-8") as f:
            json.dump(sanitized_messages, f, indent=2, ensure_ascii=False)

        if recorded_steps is not None:
            rec_file = s_dir / "recorded_steps.json"
            with open(rec_file, "w", encoding="utf-8") as f:
                json.dump(recorded_steps, f, indent=2, ensure_ascii=False)

        return s_dir

    def load_metadata(self, session_id: str) -> SessionMetadata | None:
        """Load session metadata header from disk."""
        meta_file = self._get_session_dir(session_id) / "meta.json"
        if not meta_file.exists():
            return None
        try:
            with open(meta_file, encoding="utf-8") as f:
                data = json.load(f)
            return SessionMetadata.model_validate(data)
        except Exception as e:
            logger.warning(f"Failed to load session metadata for {session_id}: {e}")
            return None

    def load_messages(self, session_id: str) -> list[dict[str, Any]]:
        """Load conversation message trajectory from disk."""
        msg_file = self._get_session_dir(session_id) / "messages.json"
        if not msg_file.exists():
            return []
        try:
            with open(msg_file, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load messages for {session_id}: {e}")
            return []

    def load_recorded_steps(self, session_id: str) -> list[dict[str, Any]]:
        """Load recorded action steps from disk."""
        rec_file = self._get_session_dir(session_id) / "recorded_steps.json"
        if not rec_file.exists():
            return []
        try:
            with open(rec_file, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"Failed to load recorded steps for {session_id}: {e}")
            return []

    def list_sessions(self) -> list[SessionMetadata]:
        """List all saved sessions sorted by most recently updated."""
        if not self.base_dir.exists():
            return []
        records: list[SessionMetadata] = []
        for item in self.base_dir.iterdir():
            if item.is_dir() and (item / "meta.json").exists():
                meta = self.load_metadata(item.name)
                if meta:
                    records.append(meta)
        records.sort(key=lambda s: s.updated_at, reverse=True)
        return records

    def get_latest_session_id(self) -> str | None:
        """Get the ID of the most recently updated session."""
        sessions = self.list_sessions()
        return sessions[0].session_id if sessions else None

    def delete_session(self, session_id: str) -> bool:
        """Permanently delete session files from disk."""
        s_dir = self._get_session_dir(session_id)
        if s_dir.exists():
            try:
                shutil.rmtree(s_dir)
                return True
            except Exception as e:
                logger.warning(f"Failed to remove session dir {s_dir}: {e}")
                return False
        return False


# Global storage instance
session_storage = SessionStorage()
