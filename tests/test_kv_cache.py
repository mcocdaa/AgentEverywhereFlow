"""Tests for KV Cache prefix stabilization, token extraction, and prefix invariance."""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

from PIL import Image

from agenteverywhereflow.capturer.base import Rect, TargetInfo, TargetType
from agenteverywhereflow.config import ExecutionMode
from agenteverywhereflow.session import ChatSession, session_storage


def _sample_target() -> TargetInfo:
    return TargetInfo(
        target_id="test_window_01",
        target_type=TargetType.WINDOW,
        title="KV Cache Target Test",
        rect=Rect(x=0, y=0, width=640, height=480),
        native_handle=0x1234,
        process_name="test_app",
    )


def test_freeze_completed_turn_prefix_stability() -> None:
    """Verify that completed turns freeze historical screenshot blobs into immutable markers.

    This ensures that when Turn 2 executes, the messages of Turn 1 are an exact byte-for-byte
    prefix of the prompt, maximizing KV cache hit rate across LLM providers.
    """
    target = _sample_target()
    session = ChatSession(
        target=target,
        session_id="kv-test-01",
        mode=ExecutionMode.MINIMAL_PYTHON,
    )

    # Simulate messages in Turn 1
    session.messages.append(
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Screenshot 1"},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}},
            ],
        }
    )
    session.messages.append(
        {
            "role": "assistant",
            "content": "```python\nclick(50, 50)\n```",
        }
    )

    # Freeze completed turn
    session.freeze_completed_turn()

    # Verify image_url is replaced by immutable marker
    obs_content = session.messages[1]["content"]
    assert any(
        isinstance(p, dict)
        and p.get("type") == "text"
        and "Historical viewport observation screenshot frozen in KV cache" in p.get("text", "")
        for p in obs_content
    )
    assert not any(isinstance(p, dict) and p.get("type") == "image_url" for p in obs_content)

    # Capture snapshot of Turn 1 frozen prefix
    frozen_prefix = [dict(m) for m in session.messages]

    # Now simulate starting Turn 2
    session.messages.append(
        {
            "role": "user",
            "content": [{"type": "text", "text": "Next instruction"}],
        }
    )
    session.messages.append(
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Screenshot 2"},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,BBBB"}},
            ],
        }
    )

    # The prefix corresponding to Turn 1 must match the frozen prefix exactly
    assert session.messages[: len(frozen_prefix)] == frozen_prefix


def test_token_usage_parsing_deepseek_and_openai() -> None:
    """Verify extraction of prompt_tokens and cached_prompt_tokens for DeepSeek and OpenAI."""
    target = _sample_target()
    session = ChatSession(
        target=target,
        session_id="kv-test-usage",
        mode=ExecutionMode.MINIMAL_PYTHON,
    )
    session.capturer.capture = MagicMock(return_value=Image.new("RGB", (100, 100), "white"))
    session.capturer.focus = MagicMock()

    mock_client = MagicMock()
    session._client = mock_client

    # Turn 1: DeepSeek format (prompt_cache_hit_tokens)
    mock_resp_1 = MagicMock()
    mock_resp_1.choices = [
        MagicMock(message=MagicMock(content="```python\nwait(0.1)\n```\nTASK_COMPLETED: Done."))
    ]
    usage_1 = MagicMock()
    usage_1.prompt_tokens = 1000
    usage_1.completion_tokens = 50
    usage_1.prompt_cache_hit_tokens = 800  # 80% hit
    usage_1.prompt_tokens_details = None
    mock_resp_1.usage = usage_1

    mock_client.chat.completions.create.return_value = mock_resp_1

    res1 = session.execute_turn("Turn 1 with DeepSeek")
    assert res1.success is True
    assert session.token_usage.prompt_tokens == 1000
    assert session.token_usage.completion_tokens == 50
    assert session.token_usage.cached_prompt_tokens == 800
    assert session.token_usage.cache_hit_rate == 80.0

    # Turn 2: OpenAI format (prompt_tokens_details.cached_tokens)
    mock_resp_2 = MagicMock()
    mock_resp_2.choices = [
        MagicMock(message=MagicMock(content="```python\nwait(0.1)\n```\nTASK_COMPLETED: Step 2."))
    ]
    usage_2 = MagicMock()
    usage_2.prompt_tokens = 2000
    usage_2.completion_tokens = 100
    usage_2.prompt_cache_hit_tokens = 0
    usage_2.prompt_tokens_details = MagicMock(cached_tokens=1800)  # 90% hit
    mock_resp_2.usage = usage_2

    mock_client.chat.completions.create.return_value = mock_resp_2

    res2 = session.execute_turn("Turn 2 with OpenAI")
    assert res2.success is True
    assert session.token_usage.prompt_tokens == 3000
    assert session.token_usage.completion_tokens == 150
    assert session.token_usage.cached_prompt_tokens == 2600  # 800 + 1800
    # 2600 / 3000 * 100 = 86.67%
    assert round(session.token_usage.cache_hit_rate, 2) == 86.67


def test_guarded_mode_completion_freezes_and_saves() -> None:
    """Verify guarded mode finish action also freezes images and persists to disk."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        orig_dir = session_storage.base_dir
        session_storage.base_dir = Path(tmp_dir)

        try:
            target = _sample_target()
            session = ChatSession(
                target=target,
                session_id="guarded-freeze-test",
                mode=ExecutionMode.CONTROL_GUARDED,
            )
            session.capturer.capture = MagicMock(return_value=Image.new("RGB", (100, 100), "white"))
            session.capturer.focus = MagicMock()

            # Planner returning guarded finish
            session.planner_func = MagicMock(
                return_value='```json\n{"action": "finish", "message": "Guarded task completed."}\n```'
            )

            res = session.execute_turn("Guarded turn instruction")
            assert res.success is True
            assert res.completed is True
            assert res.response == "Guarded task completed."

            # Verify historical screenshots are frozen (no image_url remaining)
            for msg in session.messages:
                content = msg.get("content")
                if isinstance(content, list):
                    for part in content:
                        assert not (isinstance(part, dict) and part.get("type") == "image_url")

            # Verify persisted to disk
            meta = session_storage.load_metadata("guarded-freeze-test")
            assert meta is not None
            assert meta.turn_count == 1
        finally:
            session_storage.base_dir = orig_dir
