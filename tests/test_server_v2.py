"""Tests for Next-Gen FastAPI endpoints, visual picker APIs, config API, global WS bus & streaming."""

from __future__ import annotations

import io
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import agenteverywhereflow.config as cfg_mod
from agenteverywhereflow.actions.cdp_backend import CDPTargetInfo
from agenteverywhereflow.capturer.base import Rect, TargetInfo, TargetType
from agenteverywhereflow.config import ExecutionMode, PermissionMode
from agenteverywhereflow.server.app import create_app
from agenteverywhereflow.session import ChatSession, SessionEventType, session_manager


def _make_targets() -> list[TargetInfo]:
    return [
        TargetInfo(
            target_id="display:1",
            target_type=TargetType.DISPLAY,
            title="Primary Display",
            rect=Rect(x=0, y=0, width=1920, height=1080),
            native_handle=1,
        ),
        TargetInfo(
            target_id="hwnd:101",
            target_type=TargetType.WINDOW,
            title="Chrome - Docs",
            process_name="chrome.exe",
            rect=Rect(x=100, y=100, width=800, height=600),
            native_handle=101,
        ),
        TargetInfo(
            target_id="hwnd:202",
            target_type=TargetType.WINDOW,
            title="VS Code",
            process_name="code.exe",
            rect=Rect(x=950, y=100, width=900, height=700),
            native_handle=202,
        ),
    ]


def test_create_session_multi_target_region_and_cdp() -> None:
    """Verify POST /api/v1/sessions supports target_ids, region crops, and headless CDP targets."""
    app = create_app()
    client = TestClient(app)
    session_manager.clear()

    targets = _make_targets()
    mock_capturer = MagicMock()
    mock_capturer.list_targets.return_value = targets

    with patch("agenteverywhereflow.server.app.get_capturer", return_value=mock_capturer):
        # 1. Multi-target creation via target_ids
        resp_multi = client.post(
            "/api/v1/sessions",
            json={
                "target_ids": ["hwnd:101", "hwnd:202"],
                "session_id": "multi-sess-1",
                "mode": "minimal",
            },
        )
        assert resp_multi.status_code == 201
        data_multi = resp_multi.json()
        assert data_multi["session_id"] == "multi-sess-1"
        assert data_multi["target"]["target_id"] == "hwnd:101"
        assert len(data_multi["targets"]) == 2
        assert [t["target_id"] for t in data_multi["targets"]] == ["hwnd:101", "hwnd:202"]
        assert data_multi["backend"] == "os"

        sess_multi = session_manager.get_session("multi-sess-1")
        assert sess_multi is not None
        assert len(sess_multi.targets) == 2

        # 2. Custom Region creation
        resp_region = client.post(
            "/api/v1/sessions",
            json={
                "region": {
                    "x": 120,
                    "y": 240,
                    "width": 640,
                    "height": 480,
                    "title": "Sales Table Crop",
                    "parent_target_id": "hwnd:101",
                },
                "session_id": "region-sess-1",
            },
        )
        assert resp_region.status_code == 201
        data_region = resp_region.json()
        assert data_region["target"]["target_id"] == "region:120,240,640,480"
        assert data_region["target"]["title"] == "Sales Table Crop"
        assert data_region["target"]["rect"] == {"x": 120, "y": 240, "width": 640, "height": 480}
        sess_reg = session_manager.get_session("region-sess-1")
        assert sess_reg is not None
        assert sess_reg.target.native_handle == 0

        # 3. Direct CDP session via cdp_ws_url
        resp_cdp = client.post(
            "/api/v1/sessions",
            json={
                "cdp_ws_url": "ws://127.0.0.1:9222/devtools/page/DIRECT1",
                "title": "Headless Tab",
                "session_id": "cdp-sess-direct",
            },
        )
        assert resp_cdp.status_code == 201
        data_cdp = resp_cdp.json()
        assert data_cdp["backend"] == "cdp"
        assert data_cdp["target"]["target_id"] == "cdp:DIRECT1"
        sess_cdp = session_manager.get_session("cdp-sess-direct")
        assert sess_cdp is not None
        assert sess_cdp.cdp_backend is not None
        assert sess_cdp.cdp_backend.ws_url == "ws://127.0.0.1:9222/devtools/page/DIRECT1"

        # 4. CDP session via target_id="cdp:..." resolved via discovery
        fake_cdp = CDPTargetInfo(
            id="PAGE42",
            title="GitHub",
            url="https://github.com",
            ws_url="ws://127.0.0.1:9222/devtools/page/PAGE42",
        )
        with patch("agenteverywhereflow.server.app.resolve_cdp_target", return_value=fake_cdp):
            resp_cdp2 = client.post(
                "/api/v1/sessions",
                json={"target_id": "cdp:PAGE42", "session_id": "cdp-sess-discovered"},
            )
            assert resp_cdp2.status_code == 201
            assert resp_cdp2.json()["target"]["title"] == "GitHub"
            assert resp_cdp2.json()["backend"] == "cdp"

        # 5. Missing target spec returns 400
        resp_bad = client.post("/api/v1/sessions", json={})
        assert resp_bad.status_code == 400

    session_manager.clear()


def test_target_thumbnail_jpeg_resize_and_ttl_cache() -> None:
    """Verify GET /api/v1/targets/{id}/thumbnail returns resized JPEG and caches for 500ms."""
    app = create_app()
    client = TestClient(app)
    targets = _make_targets()

    mock_capturer = MagicMock()
    mock_capturer.list_targets.return_value = targets
    # Explicitly disable capture_thumbnail so it uses capture() + resize path
    mock_capturer.capture_thumbnail = None
    mock_capturer.capture.return_value = Image.new("RGBA", (1600, 800), color=(20, 40, 80, 255))

    with patch("agenteverywhereflow.server.app.get_capturer", return_value=mock_capturer):
        # First request: cache miss, resized to width=320 -> height=160
        r1 = client.get("/api/v1/targets/hwnd:101/thumbnail?width=320&quality=75")
        assert r1.status_code == 200
        assert r1.headers["content-type"] == "image/jpeg"
        assert r1.headers.get("x-thumbnail-cache") == "miss"
        img1 = Image.open(io.BytesIO(r1.content))
        assert img1.format == "JPEG"
        assert img1.size == (320, 160)
        assert mock_capturer.capture.call_count == 1

        # Immediate second request: cache hit -> capturer.capture NOT called again
        r2 = client.get("/api/v1/targets/hwnd:101/thumbnail?width=320&quality=75")
        assert r2.status_code == 200
        assert r2.headers.get("x-thumbnail-cache") == "hit"
        assert r2.content == r1.content
        assert mock_capturer.capture.call_count == 1

        # Wait past 500ms TTL -> cache miss again
        time.sleep(0.55)
        r3 = client.get("/api/v1/targets/hwnd:101/thumbnail?width=320&quality=75")
        assert r3.status_code == 200
        assert r3.headers.get("x-thumbnail-cache") == "miss"
        assert mock_capturer.capture.call_count == 2

        # Region target thumbnail works too
        r_reg = client.get("/api/v1/targets/region:10,20,400,200/thumbnail?width=200")
        assert r_reg.status_code == 200
        assert r_reg.headers["content-type"] == "image/jpeg"

        # Unknown target -> 404
        r_404 = client.get("/api/v1/targets/nonexistent_xyz/thumbnail")
        assert r_404.status_code == 404


def test_target_at_point_native_and_fallback() -> None:
    """Verify GET /api/v1/targets/at-point uses native hit-test when present and falls back cleanly."""
    app = create_app()
    client = TestClient(app)
    targets = _make_targets()

    # Case 1: Capturer has native target_at_point
    mock_native = MagicMock()
    mock_native.target_at_point.return_value = targets[1]
    with patch("agenteverywhereflow.server.app.get_capturer", return_value=mock_native):
        resp = client.get("/api/v1/targets/at-point?x=250&y=250&exclude_pid=9999")
        assert resp.status_code == 200
        assert resp.json()["target"]["target_id"] == "hwnd:101"
        mock_native.target_at_point.assert_called_once_with(250, 250, exclude_pid=9999)

    # Case 2: Capturer returns None -> {"target": null}
    mock_native.target_at_point.return_value = None
    with patch("agenteverywhereflow.server.app.get_capturer", return_value=mock_native):
        resp_none = client.get("/api/v1/targets/at-point?x=5000&y=5000")
        assert resp_none.status_code == 200
        assert resp_none.json() == {"target": None}

    # Case 3: Fallback capturer without target_at_point method
    class _MinimalCapturer:
        def list_targets(
            self, include_displays: bool = True, include_windows: bool = True
        ) -> list[TargetInfo]:
            return targets

    with patch("agenteverywhereflow.server.app.get_capturer", return_value=_MinimalCapturer()):
        r_win = client.get("/api/v1/targets/at-point?x=1100&y=200")
        assert r_win.status_code == 200
        assert r_win.json()["target"]["target_id"] == "hwnd:202"

        # Point outside windows but inside display:1
        r_disp = client.get("/api/v1/targets/at-point?x=10&y=10")
        assert r_disp.status_code == 200
        assert r_disp.json()["target"]["target_id"] == "display:1"

        # Point outside everything
        r_out = client.get("/api/v1/targets/at-point?x=9999&y=9999")
        assert r_out.status_code == 200
        assert r_out.json() == {"target": None}


def test_list_cdp_targets_endpoint() -> None:
    """Verify GET /api/v1/targets/cdp returns discovered headless tabs."""
    app = create_app()
    client = TestClient(app)

    fake_tabs = [
        CDPTargetInfo(
            id="ABC1",
            title="Local App",
            url="http://localhost:3000",
            ws_url="ws://127.0.0.1:9222/devtools/page/ABC1",
        )
    ]
    with patch("agenteverywhereflow.server.app.discover_cdp_targets", return_value=fake_tabs) as m:
        resp = client.get("/api/v1/targets/cdp?endpoint=http://127.0.0.1:9333")
        assert resp.status_code == 200
        m.assert_called_once_with(endpoint="http://127.0.0.1:9333")
        body = resp.json()
        assert body["endpoint"] == "http://127.0.0.1:9333"
        assert len(body["targets"]) == 1
        assert body["targets"][0]["target_id"] == "cdp:ABC1"


def test_config_get_and_patch_masking_and_persistence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify GET/PATCH /api/v1/config masks API keys, persists to .env, and keeps unrelated keys."""
    env_file = tmp_path / ".aef" / ".env"
    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.write_text(
        "# User comment\nUNRELATED_TOKEN=keep_me_safe\nAEF_MODEL_NAME=old-model\n", encoding="utf-8"
    )

    # Preserve original runtime config values and restore after test
    orig_cfg = (
        cfg_mod.config.api_key,
        cfg_mod.config.model_name,
        cfg_mod.config.base_url,
        cfg_mod.config.default_mode,
        cfg_mod.config.default_permission_mode,
        cfg_mod.config.summon_hotkey,
    )
    monkeypatch.setattr(cfg_mod, "get_global_env_file", lambda: env_file)
    cfg_mod.config.api_key = ""
    cfg_mod.config.model_name = "old-model"
    cfg_mod.config.base_url = "https://api.example.com/v1"
    cfg_mod.config.default_mode = ExecutionMode.MINIMAL_PYTHON
    cfg_mod.config.default_permission_mode = PermissionMode.AUTO
    cfg_mod.config.summon_hotkey = "ctrl+shift+a"

    try:
        app = create_app()
        client = TestClient(app)

        # 1. GET initial config (no api key)
        r_get = client.get("/api/v1/config")
        assert r_get.status_code == 200
        g = r_get.json()
        assert g["model_name"] == "old-model"
        assert g["api_key_set"] is False
        assert g["api_key_preview"] == ""
        assert g["default_mode"] == "minimal"
        assert g["default_permission"] == "auto"
        assert g["summon_hotkey"] == "ctrl+shift+a"

        # 2. PATCH partial update (API key + model + permission + hotkey)
        r_patch = client.patch(
            "/api/v1/config",
            json={
                "api_key": "sk-proj-secret-abcdef1234",
                "model_name": "gpt-4o",
                "base_url": "https://api.openai.com/v1",
                "default_mode": "guarded",
                "default_permission": "manual",
                "summon_hotkey": "alt+space",
            },
        )
        assert r_patch.status_code == 200
        p = r_patch.json()
        assert p["api_key_set"] is True
        assert p["api_key_preview"] == "sk-...1234"
        assert "secret" not in p["api_key_preview"]
        assert p["model_name"] == "gpt-4o"
        assert p["base_url"] == "https://api.openai.com/v1"
        assert p["default_mode"] == "guarded"
        assert p["default_permission"] == "manual"
        assert p["summon_hotkey"] == "alt+space"

        # Verify file persistence preserved unrelated keys & comments
        written = env_file.read_text(encoding="utf-8")
        assert "# User comment" in written
        assert "UNRELATED_TOKEN=keep_me_safe" in written
        assert "AEF_API_KEY=sk-proj-secret-abcdef1234" in written
        assert "AEF_MODEL_NAME=gpt-4o" in written
        assert "AEF_DEFAULT_MODE=guarded" in written
        assert "AEF_DEFAULT_PERMISSION_MODE=manual" in written
        assert "AEF_SUMMON_HOTKEY=alt+space" in written

        # Verify live runtime config updated
        assert cfg_mod.config.model_name == "gpt-4o"
        assert cfg_mod.config.summon_hotkey == "alt+space"

        # 3. Invalid mode returns 400
        r_err = client.patch("/api/v1/config", json={"default_mode": "invalid_mode"})
        assert r_err.status_code == 400
    finally:
        (
            cfg_mod.config.api_key,
            cfg_mod.config.model_name,
            cfg_mod.config.base_url,
            cfg_mod.config.default_mode,
            cfg_mod.config.default_permission_mode,
            cfg_mod.config.summon_hotkey,
        ) = orig_cfg


def test_global_events_websocket_bus() -> None:
    """Verify WS /api/v1/events broadcasts session_created, approval_required, state changes, and session_deleted."""
    app = create_app()
    client = TestClient(app)
    session_manager.clear()

    targets = _make_targets()
    mock_capturer = MagicMock()
    mock_capturer.list_targets.return_value = targets

    with patch("agenteverywhereflow.server.app.get_capturer", return_value=mock_capturer):
        with client.websocket_connect("/api/v1/events") as ws:
            # 1. Session created
            r_create = client.post(
                "/api/v1/sessions",
                json={"target_id": "display:1", "session_id": "ws-bus-1"},
            )
            assert r_create.status_code == 201
            ev1 = ws.receive_json()
            assert ev1["event_type"] == "session_created"
            assert ev1["session_id"] == "ws-bus-1"

            # 2. Approval required event emitted from session propagates globally
            sess = session_manager.get_session("ws-bus-1")
            assert sess is not None
            sess.emit_event(
                SessionEventType.APPROVAL_REQUIRED,
                step=1,
                payload={"action_type": "codeact", "description": "Click submit"},
            )
            ev2 = ws.receive_json()
            ev3 = ws.receive_json()
            assert ev2["event_type"] == "approval_required"
            assert ev2["session_id"] == "ws-bus-1"
            assert ev2["payload"]["description"] == "Click submit"
            assert ev3["event_type"] == "session_state_changed"

            # 3. Session deleted
            r_del = client.delete("/api/v1/sessions/ws-bus-1")
            assert r_del.status_code == 200
            ev4 = ws.receive_json()
            assert ev4["event_type"] == "session_deleted"
            assert ev4["session_id"] == "ws-bus-1"


def test_session_token_streaming_deltas_and_fallback() -> None:
    """Verify ChatSession streams reasoning_delta / talk_delta while preserving full backward-compatible events."""
    target = _make_targets()[1]

    def _make_chunk(
        content: str | None = None,
        reasoning_content: str | None = None,
        usage: Any = None,
    ) -> Any:
        delta = SimpleNamespace(content=content, reasoning_content=reasoning_content)
        choices = (
            [SimpleNamespace(delta=delta)]
            if (content is not None or reasoning_content is not None)
            else []
        )
        return SimpleNamespace(choices=choices, usage=usage)

    stream_chunks = [
        _make_chunk(reasoning_content="Checking layout... "),
        _make_chunk(reasoning_content="button at (50, 50)."),
        _make_chunk(content="<think>Extra inline thought"),
        _make_chunk(content="</think>All "),
        _make_chunk(content="set!"),
        _make_chunk(
            usage=SimpleNamespace(
                prompt_tokens=120,
                completion_tokens=30,
                prompt_cache_hit_tokens=60,
                prompt_tokens_details=None,
            )
        ),
    ]

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = iter(stream_chunks)

    session = ChatSession(
        target=target,
        session_id="stream-test-01",
        mode=ExecutionMode.MINIMAL_PYTHON,
        enable_streaming=True,
    )
    session._client = mock_client
    session.capturer = MagicMock()
    session.capturer.capture.return_value = Image.new("RGB", (200, 150), "white")

    received_events: list[Any] = []
    session.add_listener(received_events.append)

    res = session.execute_turn("Inspect layout")
    assert res.success is True
    assert res.completed is True
    assert res.response == "All set!"

    # Streaming deltas delivered to listeners
    r_deltas = [e for e in received_events if e.event_type == "reasoning_delta"]
    t_deltas = [e for e in received_events if e.event_type == "talk_delta"]
    assert "".join(e.payload["delta"] for e in r_deltas) == (
        "Checking layout... button at (50, 50).Extra inline thought"
    )
    assert "".join(e.payload["delta"] for e in t_deltas) == "All set!"
    # Streaming events serialize cleanly via model_dump()
    dumped = r_deltas[0].model_dump()
    assert dumped["event_type"] == "reasoning_delta"
    assert dumped["payload"]["delta"] == "Checking layout... "

    # Final canonical events still emitted AND persisted cleanly
    event_types = [e.event_type for e in received_events]
    assert SessionEventType.REASONING in event_types
    assert SessionEventType.MESSAGE in event_types
    assert SessionEventType.TASK_COMPLETED in event_types
    assert SessionEventType.TURN_FINISHED in event_types

    # Token usage extracted from final stream chunk
    assert session.token_usage.prompt_tokens == 120
    assert session.token_usage.completion_tokens == 30
    assert session.token_usage.cached_prompt_tokens == 60

    # Disk restore succeeds cleanly (ephemeral deltas did not break SessionEventType validation)
    restored = ChatSession.restore("stream-test-01")
    assert restored is not None
    assert restored.turn_count == 1
    assert any(e.event_type == SessionEventType.TASK_COMPLETED for e in restored.events)

    # Graceful fallback when client raises TypeError on stream=True
    fallback_client = MagicMock()
    non_stream_resp = MagicMock()
    non_stream_resp.choices = [
        MagicMock(
            message=MagicMock(
                content="Fallback answer.",
                reasoning_content=None,
                reasoning=None,
            )
        )
    ]
    non_stream_resp.usage = None

    def _reject_stream(**kwargs: Any) -> Any:
        if "stream" in kwargs or "stream_options" in kwargs:
            raise TypeError("unexpected keyword argument 'stream'")
        return non_stream_resp

    fallback_client.chat.completions.create.side_effect = _reject_stream
    session2 = ChatSession(
        target=target,
        session_id="stream-fallback-01",
        enable_streaming=True,
    )
    session2._client = fallback_client
    session2.capturer = MagicMock()
    session2.capturer.capture.return_value = Image.new("RGB", (100, 100), "white")
    res2 = session2.execute_turn("Fallback check")
    assert res2.success is True
    assert res2.response == "Fallback answer."


def test_fallback_root_html_suggests_aef_ui_install() -> None:
    """Verify fallback HTML landing page suggests 'aef ui install' instead of pnpm dev."""
    with patch("agenteverywhereflow.server.app.get_webui_dist_path", return_value=None):
        app = create_app()
        client = TestClient(app)
        resp = client.get("/")
        assert resp.status_code == 200
        assert "aef ui install" in resp.text
        assert "pnpm dev" not in resp.text


def test_session_websocket_streams_delta_events() -> None:
    """Verify WS /api/v1/sessions/{id}/ws forwards reasoning_delta & talk_delta frames cleanly."""
    app = create_app()
    client = TestClient(app)
    session_manager.clear()

    targets = _make_targets()
    mock_capturer = MagicMock()
    mock_capturer.list_targets.return_value = targets

    def _chunk(content: str | None = None, reasoning_content: str | None = None) -> Any:
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    delta=SimpleNamespace(content=content, reasoning_content=reasoning_content)
                )
            ],
            usage=None,
        )

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = iter(
        [
            _chunk(reasoning_content="Looking at screen"),
            _chunk(content="Hello "),
            _chunk(content="operator!"),
        ]
    )

    with patch("agenteverywhereflow.server.app.get_capturer", return_value=mock_capturer):
        r = client.post(
            "/api/v1/sessions",
            json={"target_id": "display:1", "session_id": "ws-stream-1"},
        )
        assert r.status_code == 201
        sess = session_manager.get_session("ws-stream-1")
        assert sess is not None
        sess._client = mock_client
        sess.capturer = MagicMock()
        sess.capturer.capture.return_value = Image.new("RGB", (120, 80), "white")

        seen: list[dict[str, Any]] = []
        with client.websocket_connect("/api/v1/sessions/ws-stream-1/ws") as ws:
            ws.send_json({"action": "message", "instruction": "Say hi", "max_steps": 1})
            for _ in range(20):
                msg = ws.receive_json()
                seen.append(msg)
                if msg.get("event_type") == "turn_finished":
                    break

        ev_types = [m["event_type"] for m in seen]
        assert "reasoning_delta" in ev_types
        assert "talk_delta" in ev_types
        assert "reasoning" in ev_types
        assert "message" in ev_types
        assert "turn_finished" in ev_types

    session_manager.clear()


def test_config_delegates_to_pkg_sdk_helpers_when_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify GET/PATCH /api/v1/config delegates cleanly to config.py helpers once merged."""
    env_file = tmp_path / ".env"
    monkeypatch.setattr(cfg_mod, "get_global_env_file", lambda: env_file)

    called_updates: list[dict[str, Any]] = []

    def _fake_get_masked(cfg: Any = None) -> dict[str, Any]:
        return {
            "model_name": "gpt-4o-mini",
            "base_url": "https://api.openai.com/v1",
            "api_key_set": True,
            "api_key_preview": "sk-...9999",
            "default_mode": "minimal",
            "default_permission": "auto",
            "summon_hotkey": "alt+space",
        }

    def _fake_update_env(updates: dict[str, Any], env_path: Path | None = None) -> Any:
        called_updates.append(dict(updates))
        return cfg_mod.config

    monkeypatch.setattr(cfg_mod, "get_masked_config", _fake_get_masked, raising=False)
    monkeypatch.setattr(cfg_mod, "update_env_config", _fake_update_env, raising=False)

    orig_model = cfg_mod.config.model_name
    try:
        app = create_app()
        client = TestClient(app)

        r_get = client.get("/api/v1/config")
        assert r_get.status_code == 200
        assert r_get.json()["api_key_preview"] == "sk-...9999"

        r_patch = client.patch("/api/v1/config", json={"model_name": "gpt-4o-mini"})
        assert r_patch.status_code == 200
        assert called_updates == [{"model_name": "gpt-4o-mini"}]
    finally:
        cfg_mod.config.model_name = orig_model
