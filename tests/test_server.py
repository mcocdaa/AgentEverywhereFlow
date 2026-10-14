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


def test_server_session_export_and_workflow_run() -> None:
    """Verify session export (python, yaml, markdown) and workflow dry-run / replay APIs."""
    app = create_app()
    client = TestClient(app)
    session_manager.clear()

    with patch("agenteverywhereflow.server.app.get_capturer") as mock_capturer_fn:
        mock_capturer = MagicMock()
        mock_capturer.list_targets.return_value = [_mock_target()]
        mock_capturer_fn.return_value = mock_capturer

        # Create session and populate recorded steps
        resp = client.post(
            "/api/v1/sessions",
            json={
                "target_id": "display:1",
                "mode": "minimal",
                "permission_mode": "auto",
                "session_id": "export-test-session",
            },
        )
        assert resp.status_code == 201

        session = session_manager.get_session("export-test-session")
        assert session is not None
        session.recorded_steps = [
            {
                "turn": 1,
                "step": 1,
                "mode": "minimal",
                "code": "click(120, 240)\ntype_text('aef-api')\nwait(0.2)",
                "target_id": "display:1",
                "target_title": "Primary Screen",
            }
        ]

        # 1. Export as Python
        py_resp = client.get(
            "/api/v1/sessions/export-test-session/export?format=python&workflow_name=custom_wf"
        )
        assert py_resp.status_code == 200
        py_data = py_resp.json()
        assert py_data["session_id"] == "export-test-session"
        assert py_data["format"] == "python"
        assert py_data["step_count"] == 3
        assert "driver.click" in py_data["content"]
        assert py_data["workflow"]["name"] == "custom_wf"

        # 2. Export as YAML
        yaml_resp = client.get("/api/v1/sessions/export-test-session/export?format=yaml")
        assert yaml_resp.status_code == 200
        yaml_data = yaml_resp.json()
        assert yaml_data["format"] == "yaml"
        assert yaml_data["step_count"] == 3
        assert "steps:" in yaml_data["content"]

        # 3. Export as Markdown
        md_resp = client.get("/api/v1/sessions/export-test-session/export?format=markdown")
        assert md_resp.status_code == 200
        md_data = md_resp.json()
        assert md_data["format"] == "markdown"
        assert "# Workflow Report:" in md_data["content"]

        # 4. Run workflow dry-run via session_id
        run_sess_resp = client.post(
            "/api/v1/workflows/run",
            json={"session_id": "export-test-session", "dry_run": True},
        )
        assert run_sess_resp.status_code == 200
        run_sess_data = run_sess_resp.json()
        assert run_sess_data["status"] == "dry_run"
        assert run_sess_data["steps"] == 3
        assert len(run_sess_data["workflow"]["steps"]) == 3

        # 5. Run workflow dry-run via workflow_yaml
        run_yaml_resp = client.post(
            "/api/v1/workflows/run",
            json={"workflow_yaml": yaml_data["content"], "dry_run": True},
        )
        assert run_yaml_resp.status_code == 200
        run_yaml_data = run_yaml_resp.json()
        assert run_yaml_data["status"] == "dry_run"
        assert run_yaml_data["steps"] == 3

        # Cleanup
        client.delete("/api/v1/sessions/export-test-session")
