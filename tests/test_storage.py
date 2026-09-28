"""Tests for session persistence, disk storage, and recovery."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from PIL import Image

from agenteverywhereflow.capturer.base import Rect, TargetInfo, TargetType
from agenteverywhereflow.config import ExecutionMode, PermissionMode
from agenteverywhereflow.session import (
    ChatSession,
    SessionStorage,
    TokenUsageStats,
)


def _sample_target() -> TargetInfo:
    return TargetInfo(
        target_id="hwnd:0x9999",
        target_type=TargetType.WINDOW,
        title="Persist Window Test",
        rect=Rect(x=10, y=20, width=800, height=600),
        native_handle=0x9999,
        process_name="test.exe",
    )


def test_token_usage_stats_cache_hit_rate() -> None:
    """Verify cache hit rate computation."""
    stats = TokenUsageStats(
        prompt_tokens=1000,
        completion_tokens=200,
        total_tokens=1200,
        cached_prompt_tokens=800,
    )
    assert stats.cache_hit_rate == 80.0

    empty_stats = TokenUsageStats()
    assert empty_stats.cache_hit_rate == 0.0


def test_session_storage_roundtrip() -> None:
    """Verify saving to disk and restoring metadata & messages."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        storage = SessionStorage(base_dir=Path(tmp_dir))
        target = _sample_target()
        stats = TokenUsageStats(prompt_tokens=500, cached_prompt_tokens=400)
        messages = [
            {"role": "system", "content": "System prompt text."},
            {"role": "user", "content": [{"type": "text", "text": "Turn 1 request"}]},
        ]

        # Save session
        s_path = storage.save_session(
            session_id="sess-001",
            target=target,
            mode=ExecutionMode.MINIMAL_PYTHON,
            permission_mode=PermissionMode.MANUAL,
            state="idle",
            turn_count=1,
            total_steps=2,
            messages=messages,
            token_usage=stats,
            created_at=1700000000.0,
        )
        assert s_path.exists()
        assert (s_path / "meta.json").exists()
        assert (s_path / "messages.json").exists()

        # Load metadata
        meta = storage.load_metadata("sess-001")
        assert meta is not None
        assert meta.session_id == "sess-001"
        assert meta.target_title == "Persist Window Test"
        assert meta.turn_count == 1
        assert meta.token_usage.cache_hit_rate == 80.0

        # Load messages
        loaded_msgs = storage.load_messages("sess-001")
        assert len(loaded_msgs) == 2
        assert loaded_msgs[0]["content"] == "System prompt text."

        # List sessions
        all_s = storage.list_sessions()
        assert len(all_s) == 1
        assert all_s[0].session_id == "sess-001"

        # Delete session
        deleted = storage.delete_session("sess-001")
        assert deleted is True
        assert storage.load_metadata("sess-001") is None
        assert len(storage.list_sessions()) == 0


def test_chat_session_auto_persistence() -> None:
    """Verify that ChatSession automatically persists to disk upon turn completion and can be restored."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        # Patch session_storage base_dir for testing
        from agenteverywhereflow.session import session_storage

        orig_dir = session_storage.base_dir
        session_storage.base_dir = Path(tmp_dir)

        try:
            target = _sample_target()
            planner = MagicMock(return_value="```python\nwait(0.1)\n```\nTASK_COMPLETED: Done.")
            session = ChatSession(
                target=target,
                session_id="sess-auto-01",
                planner_func=planner,
            )
            session.capturer.capture = MagicMock(return_value=Image.new("RGB", (100, 100), "white"))
            session.capturer.focus = MagicMock()

            # Execute turn
            res = session.execute_turn("Test instruction")
            assert res.success is True

            # Verify it was auto-persisted to disk
            meta = session_storage.load_metadata("sess-auto-01")
            assert meta is not None
            assert meta.turn_count == 1
            assert meta.total_steps == 1

            # Restore into a brand new ChatSession object
            restored = ChatSession.restore("sess-auto-01")
            assert restored is not None
            assert restored.session_id == "sess-auto-01"
            assert restored.target.title == "Persist Window Test"
            assert restored.turn_count == 1
            assert restored.total_steps == 1
            assert len(restored.messages) >= 2

        finally:
            session_storage.base_dir = orig_dir
