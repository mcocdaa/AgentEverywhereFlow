"""Unit tests for Workflow recording, exporting (Python script & YAML), and replay runner."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from agenteverywhereflow.actions.driver import driver
from agenteverywhereflow.capturer.base import Rect, TargetInfo, TargetType
from agenteverywhereflow.config import ExecutionMode
from agenteverywhereflow.session import ChatSession
from agenteverywhereflow.workflow import (
    WorkflowDefinition,
    WorkflowExporter,
    WorkflowRunner,
    WorkflowStep,
    WorkflowTarget,
)


def make_dummy_target(target_id: str, title: str, x: int = 100, y: int = 100) -> TargetInfo:
    return TargetInfo(
        target_id=target_id,
        target_type=TargetType.WINDOW,
        title=title,
        process_name=f"{title.lower()}.exe",
        rect=Rect(x=x, y=y, width=800, height=600),
        native_handle=1234,
    )


def test_workflow_exporter_from_session_minimal():
    t1 = make_dummy_target("w1", "Doubao Chat")
    session = ChatSession(target=t1, mode=ExecutionMode.MINIMAL_PYTHON)

    session.recorded_steps = [
        {
            "turn": 1,
            "step": 1,
            "mode": "minimal",
            "code": "click(150, 250)\ntype_text('hello')\npress('enter')\nwait(0.5)",
            "target_id": "w1",
            "target_title": "Doubao Chat",
        }
    ]

    wf = WorkflowExporter.from_session(session, name="test_minimal_wf")
    assert wf.name == "test_minimal_wf"
    assert len(wf.targets) == 1
    assert wf.targets[0].title == "Doubao Chat"

    # Verify atomic step parsing
    assert len(wf.steps) == 4
    assert wf.steps[0].action == "click"
    assert wf.steps[0].params["x"] == 150
    assert wf.steps[0].params["y"] == 250
    assert wf.steps[1].action == "type"
    assert wf.steps[1].params["text"] == "hello"
    assert wf.steps[2].action == "press"
    assert wf.steps[2].params["key"] == "enter"
    assert wf.steps[3].action == "wait"
    assert wf.steps[3].params["seconds"] == 0.5


def test_workflow_exporter_from_session_guarded():
    t1 = make_dummy_target("w1", "Editor")
    t2 = make_dummy_target("w2", "Browser")
    session = ChatSession(targets=[t1, t2], mode=ExecutionMode.CONTROL_GUARDED)

    session.recorded_steps = [
        {
            "turn": 1,
            "step": 1,
            "mode": "guarded",
            "action": {"action": "switch_target", "target": "Browser"},
            "target_id": "w1",
            "target_title": "Editor",
        },
        {
            "turn": 1,
            "step": 2,
            "mode": "guarded",
            "action": {"action": "click", "x": 300, "y": 400},
            "target_id": "w2",
            "target_title": "Browser",
        },
    ]

    wf = WorkflowExporter.from_session(session, name="test_guarded_wf")
    assert len(wf.targets) == 2
    assert len(wf.steps) == 2
    assert wf.steps[0].action == "switch_target"
    assert wf.steps[1].action == "click"
    assert wf.steps[1].params["x"] == 300
    assert wf.steps[1].params["y"] == 400


def test_workflow_export_to_yaml_and_python(tmp_path: Path):
    wf = WorkflowDefinition(
        name="demo_wf",
        description="Demo workflow for test",
        targets=[
            WorkflowTarget(
                target_id="w1",
                title="Doubao",
                rect={"x": 100, "y": 100, "width": 800, "height": 600},
            )
        ],
        steps=[
            WorkflowStep(step_number=1, action="click", target="Doubao", params={"x": 50, "y": 60}),
            WorkflowStep(step_number=2, action="type", target="Doubao", params={"text": "world"}),
            WorkflowStep(step_number=3, action="press", target="Doubao", params={"key": "enter"}),
        ],
    )

    # 1. YAML Export
    yaml_file = tmp_path / "test_wf.yaml"
    saved_yaml = WorkflowExporter.export_to_yaml(wf, yaml_file)
    assert saved_yaml.exists()
    content_yaml = saved_yaml.read_text(encoding="utf-8")
    assert "demo_wf" in content_yaml
    assert "click" in content_yaml

    # 2. Python Script Export
    py_file = tmp_path / "test_wf.py"
    saved_py = WorkflowExporter.export_to_python(wf, py_file)
    assert saved_py.exists()
    content_py = saved_py.read_text(encoding="utf-8")
    assert (
        "import driver" in content_py
        or "from agenteverywhereflow.actions.driver import driver" in content_py
    )
    assert "driver.click" in content_py
    assert "driver.type_text('world'" in content_py
    # Verify Python syntax compiles
    compiled = compile(content_py, str(saved_py), "exec")
    assert compiled is not None


def test_workflow_runner_play(tmp_path: Path):
    t1 = make_dummy_target("w1", "Notepad", x=200, y=150)

    wf = WorkflowDefinition(
        name="replay_demo",
        targets=[
            WorkflowTarget(
                target_id="w1",
                title="Notepad",
                rect={"x": 200, "y": 150, "width": 800, "height": 600},
            )
        ],
        steps=[
            WorkflowStep(step_number=1, action="switch_target", target="Notepad"),
            WorkflowStep(
                step_number=2, action="click", target="Notepad", params={"x": 100, "y": 100}
            ),
            WorkflowStep(step_number=3, action="type", target="Notepad", params={"text": "abc"}),
            WorkflowStep(step_number=4, action="wait", target="Notepad", params={"seconds": 0.01}),
        ],
    )

    runner = WorkflowRunner()
    # Mock capturer to resolve target
    runner.capturer = MagicMock()
    runner.capturer.list_targets.return_value = [t1]
    runner.capturer.focus.return_value = True

    # Test Dry Run
    res_dry = runner.play(wf, dry_run=True, speed=2.0)
    assert res_dry.success is True
    assert res_dry.executed_steps == 4

    # Test Live Execution with driver mocks
    with (
        patch.object(driver, "click") as mock_click,
        patch.object(driver, "type_text") as mock_type,
    ):
        res = runner.play(wf, dry_run=False, speed=10.0)
        assert res.success is True
        assert res.executed_steps == 4
        # Coordinate projection: 200 + 100 = 300, 150 + 100 = 250
        assert mock_click.call_count == 1
        assert mock_click.call_args.kwargs["x"] == 300
        assert mock_click.call_args.kwargs["y"] == 250
        # Text typing
        assert mock_type.call_count == 1
        assert mock_type.call_args.args[0] == "abc"
