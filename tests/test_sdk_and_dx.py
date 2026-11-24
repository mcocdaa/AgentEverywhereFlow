"""Tests for 3-line Python SDK, CLI onboarding (init/doctor/update), packaging, and P0 fixes."""

import io
import os
import re
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image
from typer.testing import CliRunner

import agenteverywhereflow as aef
import agenteverywhereflow.config as config_mod
from agenteverywhereflow.actions import CoordinateProjector, InputDriver, driver
from agenteverywhereflow.agent.loop import AgentLoop
from agenteverywhereflow.capturer.base import Rect, TargetInfo, TargetType
from agenteverywhereflow.cli import (
    app,
    clear_daemon_info,
    read_daemon_info,
    write_daemon_info,
)
from agenteverywhereflow.config import (
    AppConfig,
    ConfigurationError,
    ExecutionMode,
)
from agenteverywhereflow.sdk import Agent, RunResult
from agenteverywhereflow.server.webui import (
    VERSION_MARKER_NAME,
    get_webui_dist_path,
    get_webui_status,
    install_webui,
)
from agenteverywhereflow.session import ChatSession
from agenteverywhereflow.workflow import WorkflowDefinition, WorkflowExporter, WorkflowStep

cli_runner = CliRunner()
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def _strip_ansi(text: str) -> str:
    """Strip ANSI SGR escape sequences so Rich/Typer help checks are terminal-agnostic."""
    return _ANSI_ESCAPE_RE.sub("", text)


def _isolate_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Isolate Path.home() / USERPROFILE / HOME across POSIX and Windows runners."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))


def _make_target(title: str = "Chrome - Docs", target_id: str = "xwin:0x101") -> TargetInfo:
    return TargetInfo(
        target_id=target_id,
        target_type=TargetType.WINDOW,
        title=title,
        process_name="chrome",
        rect=Rect(x=10, y=20, width=1280, height=720),
        native_handle=0x101,
    )


@pytest.fixture
def restore_live_config():
    """Restore live config singleton after tests that run CLI config commands."""
    snapshot = {field: getattr(config_mod.config, field) for field in AppConfig.model_fields}
    try:
        yield
    finally:
        for field, value in snapshot.items():
            setattr(config_mod.config, field, value)


# ---------------------------------------------------------------------------
# 1. Packaging & Top-level SDK Exports
# ---------------------------------------------------------------------------


def test_top_level_exports_and_mode_aliases() -> None:
    expected_exports = (
        "run",
        "Agent",
        "RunResult",
        "ChatSession",
        "AppConfig",
        "ConfigurationError",
        "ExecutionMode",
        "PermissionMode",
        "list_targets",
        "serve",
        "__version__",
    )
    for symbol in expected_exports:
        assert hasattr(aef, symbol), f"Missing top-level export: {symbol}"

    assert ExecutionMode.MINIMAL is ExecutionMode.MINIMAL_PYTHON
    assert ExecutionMode.GUARDED is ExecutionMode.CONTROL_GUARDED


def test_pyproject_uv_and_macos_packaging_metadata() -> None:
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    project = data["project"]

    assert project["scripts"]["aef"] == "agenteverywhereflow.cli:app"
    assert project["scripts"]["agenteverywhereflow"] == "agenteverywhereflow.cli:app"
    assert "Operating System :: MacOS :: MacOS X" in project["classifiers"]

    deps = project["dependencies"]
    assert any("pyobjc-core" in d and "darwin" in d for d in deps)
    assert any("pyobjc-framework-Cocoa" in d and "darwin" in d for d in deps)
    assert any("pyobjc-framework-Quartz" in d and "darwin" in d for d in deps)
    assert not any(d.startswith("anthropic") for d in deps)
    assert not any(d.startswith("python-dotenv") for d in deps)

    optional = project.get("optional-dependencies", {})
    assert "windows" not in optional
    assert "linux" not in optional
    assert "dev" in optional


def test_python_dash_m_entrypoint() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "agenteverywhereflow", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    assert proc.returncode == 0
    assert "AgentEverywhereFlow" in proc.stdout


# ---------------------------------------------------------------------------
# 2. Ergonomic Python SDK & ConfigurationError
# ---------------------------------------------------------------------------


def test_agent_loop_raises_configuration_error_not_typer_exit() -> None:
    cfg = AppConfig(_env_file=None, api_key="")
    loop = AgentLoop(app_config=cfg)
    with pytest.raises(ConfigurationError, match="aef init"):
        _ = loop.client

    target = _make_target()
    with (
        patch.object(loop.capturer, "focus", return_value=True),
        patch.object(
            loop.capturer,
            "capture",
            return_value=Image.new("RGB", (64, 64), color="white"),
        ),
        pytest.raises(ConfigurationError),
    ):
        loop.run(target=target, user_task="Test auth error", max_steps=1)


def test_sdk_run_resolves_target_and_returns_run_result() -> None:
    target = _make_target("Chrome - Search")
    mock_capturer = MagicMock()
    mock_capturer.list_targets.return_value = [target]
    mock_capturer.focus.return_value = True
    mock_capturer.capture.return_value = Image.new("RGB", (100, 100), color="white")

    def fake_planner(messages, tgt, step):
        return "```python\nwait(0.01)\n```\nTASK_COMPLETED: Clicked search bar."

    with (
        patch("agenteverywhereflow.sdk.get_capturer", return_value=mock_capturer),
        patch("agenteverywhereflow.agent.loop.get_capturer", return_value=mock_capturer),
    ):
        result = aef.run(
            "Chrome",
            "Click search bar",
            api_key="sk-test",
            model="gpt-4o",
            max_steps=3,
            planner_func=fake_planner,
        )

    assert isinstance(result, RunResult)
    assert result.success is True
    assert result.target.target_id == target.target_id
    assert result.task == "Click search bar"
    assert result.steps == 1
    assert "Clicked search bar" in result.message


def test_sdk_run_raises_value_error_for_unknown_target() -> None:
    mock_capturer = MagicMock()
    mock_capturer.list_targets.return_value = [_make_target("Notes")]

    with (
        patch("agenteverywhereflow.sdk.get_capturer", return_value=mock_capturer),
        pytest.raises(ValueError, match="No target found"),
    ):
        aef.run("DoesNotExist", "Click anything")


def test_sdk_agent_do_screenshot_and_chat() -> None:
    target = _make_target("VS Code")
    mock_img = Image.new("RGB", (120, 80), color="black")
    mock_capturer = MagicMock()
    mock_capturer.list_targets.return_value = [target]
    mock_capturer.focus.return_value = True
    mock_capturer.capture.return_value = mock_img

    def fake_planner(messages, tgt, step):
        return "TASK_COMPLETED: Done via Agent."

    with (
        patch("agenteverywhereflow.sdk.get_capturer", return_value=mock_capturer),
        patch("agenteverywhereflow.agent.loop.get_capturer", return_value=mock_capturer),
        patch("agenteverywhereflow.session.session.get_capturer", return_value=mock_capturer),
    ):
        targets = aef.list_targets()
        assert len(targets) == 1

        agent = Agent(
            "VS Code",
            model="gpt-4o",
            api_key="sk-test",
            mode="minimal",
            planner_func=fake_planner,
        )
        assert agent.target.title == "VS Code"

        shot = agent.screenshot()
        assert shot.size == (120, 80)

        res = agent.do("Format file")
        assert res.success is True
        assert res.steps == 1

        sess = agent.chat()
        assert isinstance(sess, ChatSession)

        turn = agent.chat("Explain what is on screen")
        assert turn.success is True

    # Agent without API key or planner_func raises ConfigurationError on chat turn
    agent_no_key = Agent(target, api_key="", model="gpt-4o")
    with pytest.raises(ConfigurationError):
        agent_no_key.chat("Hello")


# ---------------------------------------------------------------------------
# 3. P0 #2 CLI Flags, ConfigurationError Handling, init, doctor, update, daemon.json
# ---------------------------------------------------------------------------


def test_cli_run_no_dash_d_flag_collision() -> None:
    help_res = cli_runner.invoke(app, ["run", "--help"], env={"NO_COLOR": "1", "TERM": "dumb"})
    assert help_res.exit_code == 0
    plain_help = _strip_ansi(help_res.stdout)
    assert "-D" in plain_help
    assert "--to-display" in plain_help
    assert "-d" in plain_help
    assert "--debug" in plain_help

    target = _make_target("Chrome")
    display = TargetInfo(
        target_id="display:2",
        target_type=TargetType.DISPLAY,
        title="Display 2",
        rect=Rect(x=1920, y=0, width=1920, height=1080),
        native_handle=2,
    )
    mock_capturer = MagicMock()
    mock_capturer.list_targets.return_value = [display, target]
    mock_capturer.move_window_to_display.return_value = True

    with (
        patch("agenteverywhereflow.capturer.get_capturer", return_value=mock_capturer),
        patch("agenteverywhereflow.agent.loop.AgentLoop.run", return_value=True) as mock_run,
    ):
        res = cli_runner.invoke(
            app,
            ["run", "-t", "Chrome", "--task", "Say hi", "-D", "2", "-d"],
        )
    assert res.exit_code == 0, res.stdout
    mock_capturer.move_window_to_display.assert_called_once()
    mock_run.assert_called_once()


def test_cli_run_handles_configuration_error_gracefully() -> None:
    target = _make_target("Chrome")
    mock_capturer = MagicMock()
    mock_capturer.list_targets.return_value = [target]

    with (
        patch("agenteverywhereflow.capturer.get_capturer", return_value=mock_capturer),
        patch(
            "agenteverywhereflow.agent.loop.AgentLoop.run",
            side_effect=ConfigurationError("No LLM API key configured."),
        ),
    ):
        res = cli_runner.invoke(app, ["run", "-t", "Chrome", "--task", "Click"])

    assert res.exit_code == 1
    assert "aef init" in res.stdout


def test_cli_init_non_interactive_and_interactive(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    restore_live_config: None,
) -> None:
    _isolate_home(monkeypatch, tmp_path)

    # Non-interactive flags
    res = cli_runner.invoke(
        app,
        [
            "init",
            "--provider",
            "deepseek",
            "--api-key",
            "sk-deepseek-test-12345",
            "--model",
            "deepseek-chat",
        ],
    )
    assert res.exit_code == 0, res.stdout
    env_file = tmp_path / ".aef" / ".env"
    assert env_file.is_file()
    content = env_file.read_text(encoding="utf-8")
    assert "AEF_API_KEY=sk-deepseek-test-12345" in content
    assert "AEF_BASE_URL=https://api.deepseek.com" in content
    assert "AEF_MODEL_NAME=deepseek-chat" in content

    # Interactive prompt flow (OpenAI preset)
    res_interactive = cli_runner.invoke(
        app,
        ["init"],
        input="1\nsk-openai-interactive-9999\ngpt-4o\n",
    )
    assert res_interactive.exit_code == 0, res_interactive.stdout
    content2 = env_file.read_text(encoding="utf-8")
    assert "AEF_API_KEY=sk-openai-interactive-9999" in content2
    assert "AEF_BASE_URL=https://api.openai.com/v1" in content2
    assert "AEF_MODEL_NAME=gpt-4o" in content2


def test_cli_doctor_runs_cleanly(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _isolate_home(monkeypatch, tmp_path)
    res = cli_runner.invoke(app, ["doctor"], env={"NO_COLOR": "1", "TERM": "dumb"})
    assert res.exit_code == 0, res.stdout
    plain_out = _strip_ansi(res.stdout)
    assert "Environment Doctor" in plain_out
    assert "Python" in plain_out
    assert "LLM API Config" in plain_out
    assert "WebUI Studio" in plain_out
    assert "Local Daemon" in plain_out


def test_daemon_info_lifecycle_and_pid_guard(tmp_path: Path) -> None:
    daemon_file = tmp_path / ".aef" / "daemon.json"

    written = write_daemon_info("0.0.0.0", 8123, pid=12345, version="0.1.5", path=daemon_file)
    assert written == daemon_file

    info = read_daemon_info(path=daemon_file)
    assert info is not None
    assert info["pid"] == 12345
    assert info["host"] == "0.0.0.0"
    assert info["port"] == 8123
    assert info["version"] == "0.1.5"
    assert info["url"] == "http://127.0.0.1:8123"

    # Different PID must NOT delete another running daemon's metadata
    assert clear_daemon_info(expected_pid=99999, path=daemon_file) is False
    assert daemon_file.is_file()

    # Matching PID cleans it up
    assert clear_daemon_info(expected_pid=12345, path=daemon_file) is True
    assert not daemon_file.exists()


def test_cli_update_prefers_uv_tool_upgrade_and_supports_edge() -> None:
    fake_ok = SimpleNamespace(returncode=0, stdout="Upgraded agenteverywhereflow", stderr="")

    with (
        patch("agenteverywhereflow.cli._resolve_latest_release_tag", return_value="v9.9.9"),
        patch("pathlib.Path.exists", return_value=False),
        patch("shutil.which", side_effect=lambda name: "/usr/bin/uv" if name == "uv" else None),
        patch("subprocess.run", return_value=fake_ok) as mock_subproc,
    ):
        res_stable = cli_runner.invoke(app, ["update"])
        assert res_stable.exit_code == 0, res_stable.stdout
        called_cmd = mock_subproc.call_args_list[0].args[0]
        assert called_cmd == ["/usr/bin/uv", "tool", "upgrade", "agenteverywhereflow"]

    with (
        patch("pathlib.Path.exists", return_value=False),
        patch("shutil.which", side_effect=lambda name: "/usr/bin/uv" if name == "uv" else None),
        patch("subprocess.run", return_value=fake_ok) as mock_subproc_edge,
    ):
        res_edge = cli_runner.invoke(app, ["update", "--edge"])
        assert res_edge.exit_code == 0, res_edge.stdout
        edge_cmd = mock_subproc_edge.call_args_list[0].args[0]
        assert "git+https://github.com/mcocdaa/AgentEverywhereFlow.git" in edge_cmd


# ---------------------------------------------------------------------------
# 4. P0 #3 Actions Export & Exported Standalone Script PEP 723
# ---------------------------------------------------------------------------


def test_actions_package_exports_driver_instance() -> None:
    assert isinstance(driver, InputDriver)
    assert hasattr(driver, "click")
    assert CoordinateProjector is not None


def test_exported_python_workflow_has_pep723_and_valid_driver_import() -> None:
    wf = WorkflowDefinition(
        name="pep723_demo",
        steps=[
            WorkflowStep(step_number=1, action="click", target="Chrome", params={"x": 42, "y": 84}),
            WorkflowStep(step_number=2, action="wait", target="Chrome", params={"seconds": 0.5}),
        ],
    )
    script = WorkflowExporter.to_python_script(wf)
    assert "# /// script\n" in script
    assert '# requires-python = ">=3.11"\n' in script
    assert '# dependencies = ["agenteverywhereflow"]\n' in script
    assert "# ///\n" in script
    assert "from agenteverywhereflow.actions.driver import driver" in script
    assert compile(script, "pep723_demo.py", "exec") is not None


# ---------------------------------------------------------------------------
# 5. P0 #4 WebUI Precedence & version.json Marker
# ---------------------------------------------------------------------------


def test_webui_precedence_and_install_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _isolate_home(monkeypatch, tmp_path)
    monkeypatch.delenv("AEF_WEBUI_DIR", raising=False)

    # 1. With no user dist, resolves to package-embedded webui_dist
    default_path = get_webui_dist_path()
    assert default_path is not None
    assert "webui_dist" in str(default_path)

    # 2. Simulate `aef ui update` downloading a release zip
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("index.html", "<html><body>Updated WebUI</body></html>")
        zf.writestr("assets/app.js", "console.log('updated');")
    fake_resp = SimpleNamespace(status_code=200, content=buf.getvalue())

    mock_client = MagicMock()
    mock_client.__enter__.return_value.get.return_value = fake_resp

    with patch("httpx.Client", return_value=mock_client):
        installed_path = install_webui(version="v0.2.0", force=True)

    assert installed_path == tmp_path / ".aef" / "webui" / "dist"
    assert (installed_path / VERSION_MARKER_NAME).is_file()

    # Now user-updated dist MUST take precedence over package-embedded webui_dist
    resolved = get_webui_dist_path()
    assert resolved == installed_path
    st = get_webui_status()
    assert st["installed"] is True
    assert st["source"] == "user"
    assert st["version"] == "v0.2.0"

    # 3. AEF_WEBUI_DIR env var overrides everything
    custom_dir = tmp_path / "custom_ui"
    custom_dir.mkdir()
    (custom_dir / "index.html").write_text("<html>Custom</html>", encoding="utf-8")
    monkeypatch.setenv("AEF_WEBUI_DIR", str(custom_dir))
    assert get_webui_dist_path() == custom_dir
    assert get_webui_status()["source"] == "env"
    os.environ.pop("AEF_WEBUI_DIR", None)
