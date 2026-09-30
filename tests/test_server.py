"""Tests for FastAPI Dialogue Service endpoints."""

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from PIL import Image

from agenteverywhereflow.capturer.base import Rect, TargetInfo, TargetType
from agenteverywhereflow.server.app import create_app
from agenteverywhereflow.session import session_manager


def _mock_target() -> TargetInfo:
    return TargetInfo(
        target_id="display:1",
        target_type=TargetType.DISPLAY,
        title="Primary Screen",
        rect=Rect(x=0, y=0, width=1920, height=1080),
        native_handle=0,
    )


def test_server_health_check() -> None:
    """Verify health check endpoint returns active status and version."""
    app = create_app()
    client = TestClient(app)

    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data
    assert "active_sessions" in data


def test_server_list_targets() -> None:
    """Verify target discovery endpoint returns list of available targets."""
    app = create_app()
    client = TestClient(app)

    with patch("agenteverywhereflow.server.app.get_capturer") as mock_capturer_fn:
        mock_capturer = MagicMock()
        mock_capturer.list_targets.return_value = [_mock_target()]
        mock_capturer_fn.return_value = mock_capturer

        response = client.get("/api/v1/targets")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["target_id"] == "display:1"
        assert data[0]["title"] == "Primary Screen"


def test_server_session_lifecycle() -> None:
    """Verify full session lifecycle: creation, query, permission toggle, message, reset, deletion."""
    app = create_app()
    client = TestClient(app)

    # Clean manager before test
    session_manager.clear()

    with patch("agenteverywhereflow.server.app.get_capturer") as mock_capturer_fn:
        mock_capturer = MagicMock()
        mock_capturer.list_targets.return_value = [_mock_target()]
        mock_capturer_fn.return_value = mock_capturer

        # 1. Create Session
        resp = client.post(
            "/api/v1/sessions",
            json={
                "target_id": "display:1",
                "mode": "minimal",
                "permission_mode": "auto",
                "session_id": "test-srv-session",
            },
        )
        assert resp.status_code == 201
        session_data = resp.json()
        assert session_data["session_id"] == "test-srv-session"
        assert session_data["permission_mode"] == "auto"

        # 2. List Sessions
        list_resp = client.get("/api/v1/sessions")
        assert list_resp.status_code == 200
        assert len(list_resp.json()) == 1

        # 3. Get Session Details
        detail_resp = client.get("/api/v1/sessions/test-srv-session")
        assert detail_resp.status_code == 200
        assert detail_resp.json()["turn_count"] == 0

        # 4. Toggle Permission Mode
        perm_resp = client.post(
            "/api/v1/sessions/test-srv-session/permission",
            json={"mode": "manual"},
        )
        assert perm_resp.status_code == 200
        assert perm_resp.json()["permission_mode"] == "manual"

        # 5. Send Message (with mock planner and capturer)
        session = session_manager.get_session("test-srv-session")
        assert session is not None
        session.planner_func = MagicMock(
            return_value="```python\nwait(0.1)\n```\nTASK_COMPLETED: Done"
        )
        session.capturer.capture = MagicMock(
            return_value=Image.new("RGB", (100, 100), color="white")
        )
        session.capturer.focus = MagicMock()
        # Toggle back to auto so message runs synchronously without blocking on operator approval
        session.set_permission_mode("auto")

        msg_resp = client.post(
            "/api/v1/sessions/test-srv-session/message",
            json={"instruction": "Click somewhere", "max_steps": 2},
        )
        assert msg_resp.status_code == 200
        msg_data = msg_resp.json()
        assert msg_data["success"] is True
        assert msg_data["completed"] is True

        # 6. Abort endpoint
        abort_resp = client.post("/api/v1/sessions/test-srv-session/abort")
        assert abort_resp.status_code == 200
        assert abort_resp.json()["status"] == "ok"

        # 7. Reset History
        reset_resp = client.post("/api/v1/sessions/test-srv-session/reset")
        assert reset_resp.status_code == 200
        assert reset_resp.json()["status"] == "ok"

        # 8. Delete Session
        del_resp = client.delete("/api/v1/sessions/test-srv-session")
        assert del_resp.status_code == 200
        assert len(session_manager.list_sessions()) == 0


def test_server_webui_status() -> None:
    """Verify WebUI status endpoint reports status and asset metadata."""
    app = create_app()
    client = TestClient(app)

    resp = client.get("/api/v1/webui/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "installed" in data
    assert "path" in data


def test_server_webui_serves_root() -> None:
    """Verify root serves index.html or fallback landing page."""
    app = create_app()
    client = TestClient(app)

    resp = client.get("/")
    assert resp.status_code == 200
    assert "AgentEverywhereFlow" in resp.text
