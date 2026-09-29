"""End-to-end benchmark verifying Multi-Target Cross-Window Coordination and Workflow Recording/Replay Engine."""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image
from rich.console import Console
from typer.testing import CliRunner

from agenteverywhereflow.actions.driver import driver
from agenteverywhereflow.capturer.base import Rect, TargetInfo, TargetType
from agenteverywhereflow.cli import app
from agenteverywhereflow.config import ExecutionMode
from agenteverywhereflow.session import ChatSession, session_manager, session_storage
from agenteverywhereflow.workflow import (
    WorkflowExporter,
    WorkflowRunner,
)


def create_mock_target(target_id: str, title: str, x: int, y: int) -> TargetInfo:
    return TargetInfo(
        target_id=target_id,
        target_type=TargetType.WINDOW,
        title=title,
        process_name=f"{title.lower().replace(' ', '_')}.exe",
        rect=Rect(x=x, y=y, width=800, height=600),
        native_handle=1000 + abs(hash(target_id)) % 10000,
    )


def run_benchmark() -> None:
    console = Console()
    console.rule("[bold cyan]Benchmarking Direction 3: Multi-Target & Workflow Engine[/bold cyan]")

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        session_storage.base_dir = tmp_path

        # -------------------------------------------------------------
        # Part 1: Multi-Target Session Initialization & Perception
        # -------------------------------------------------------------
        console.print(
            "\n[bold yellow]Step 1: Multi-Target Window Binding & Observation[/bold yellow]"
        )
        t_chat = create_mock_target("hwnd:001", "Doubao Assistant", x=100, y=100)
        t_form = create_mock_target("hwnd:002", "Customer Registration Form", x=950, y=100)

        mock_capturer = MagicMock()
        mock_img1 = Image.new("RGB", (800, 600), color=(30, 144, 255))
        mock_img2 = Image.new("RGB", (800, 600), color=(46, 139, 87))
        mock_capturer.capture.side_effect = lambda t: (
            mock_img1 if t.target_id == "hwnd:001" else mock_img2
        )
        mock_capturer.list_targets.return_value = [t_chat, t_form]
        mock_capturer.focus.return_value = True

        session = ChatSession(
            targets=[t_chat, t_form],
            mode=ExecutionMode.MINIMAL_PYTHON,
            session_id="bench_multi_workflow_sess",
        )
        session.capturer = mock_capturer

        assert len(session.targets) == 2
        assert session.target.target_id == "hwnd:001"
        console.print(f"  ✓ Session initialized with {len(session.targets)} targets.")
        console.print(f"  ✓ Active Target: {session.target.title} ({session.target.target_id})")

        # -------------------------------------------------------------
        # Part 2: Multi-Window Dialogue Execution & Recording
        # -------------------------------------------------------------
        console.print(
            "\n[bold yellow]Step 2: Cross-Window CodeAct Execution & Action Recording[/bold yellow]"
        )

        # Turn 1: Switch to Form, click input, type text, press enter
        planner_turn1_response = (
            "I will switch to Customer Registration Form, click input, type customer name, and press Enter.\n"
            "```python\n"
            "switch_to('Customer Registration Form')\n"
            "click(150, 80)\n"
            "type_text('Jane Doe')\n"
            "press('enter')\n"
            "wait(0.2)\n"
            "```\n"
            "TASK_COMPLETED: Customer form filled."
        )

        with (
            patch.object(driver, "click") as mock_click,
            patch.object(driver, "type_text") as mock_type,
            patch.object(driver, "press_key") as mock_press,
        ):
            session.planner_func = lambda msgs, tgt, step: planner_turn1_response
            res1 = session.execute_turn("Fill the customer form", max_steps=2)

            assert res1.success is True
            assert res1.completed is True
            console.print("  ✓ Turn 1 executed successfully.")
            # Verify coordinates projected against Customer Registration Form (x=950+150=1100, y=100+80=180)
            assert mock_click.call_count == 1
            assert mock_click.call_args.kwargs["x"] == 1100
            assert mock_click.call_args.kwargs["y"] == 180
            assert mock_type.call_count == 1
            assert mock_type.call_args.args[0] == "Jane Doe"
            assert mock_press.call_count == 1
            assert mock_press.call_args.args[0] == "enter"
            console.print(
                f"  ✓ Screen click accurately projected to: ({mock_click.call_args.kwargs['x']}, {mock_click.call_args.kwargs['y']})"
            )

        # Verify active target followed the switch_to call
        assert session.target.target_id == "hwnd:002"
        console.print(
            f"  ✓ Session active target dynamically synchronized to: {session.target.title}"
        )

        # Check recorded steps
        assert len(session.recorded_steps) >= 1
        console.print(f"  ✓ Recorded {len(session.recorded_steps)} execution block(s) in session.")

        # -------------------------------------------------------------
        # Part 3: Session Storage & Disk Persistence Verification
        # -------------------------------------------------------------
        console.print("\n[bold yellow]Step 3: Session Persistence & Full Recovery[/bold yellow]")
        session.save()
        console.print("  ✓ Session saved to disk.")

        restored = session_manager.restore_session("bench_multi_workflow_sess")
        assert restored is not None
        assert len(restored.targets) == 2
        assert restored.target.target_id == "hwnd:002"
        assert len(restored.recorded_steps) >= 1
        console.print(
            f"  ✓ Restored session '{restored.session_id}': {len(restored.targets)} targets, {len(restored.recorded_steps)} recorded blocks."
        )

        # -------------------------------------------------------------
        # Part 4: Workflow Exporter (Declarative YAML & Standalone Python)
        # -------------------------------------------------------------
        console.print(
            "\n[bold yellow]Step 4: Workflow Exporter (YAML & Standalone Python)[/bold yellow]"
        )
        wf = WorkflowExporter.from_session(session, name="customer_registration_workflow")
        assert wf.name == "customer_registration_workflow"
        assert len(wf.targets) == 2
        assert len(wf.steps) == 5  # switch_to, click, type, press, wait
        console.print(
            f"  ✓ Extracted Workflow: '{wf.name}' with {len(wf.steps)} discrete atomic steps."
        )

        # Export to YAML
        yaml_out = tmp_path / "customer_registration.yaml"
        WorkflowExporter.export_to_yaml(wf, yaml_out)
        assert yaml_out.exists()
        console.print(
            f"  ✓ YAML Workflow exported: {yaml_out.name} ({yaml_out.stat().st_size} bytes)"
        )

        # Export to Standalone Python Script
        py_out = tmp_path / "customer_registration.py"
        WorkflowExporter.export_to_python(wf, py_out)
        assert py_out.exists()
        console.print(
            f"  ✓ Standalone Zero-LLM Python Script exported: {py_out.name} ({py_out.stat().st_size} bytes)"
        )

        # Verify Python compiles and contains zero LLM calls
        py_code = py_out.read_text(encoding="utf-8")
        compiled = compile(py_code, str(py_out), "exec")
        assert compiled is not None
        assert "openai" not in py_code.lower()
        assert "anthropic" not in py_code.lower()
        assert "CoordinateProjector.to_screen_coords" in py_code
        console.print(
            "  ✓ Standalone Python script successfully syntax-verified & zero-LLM contract confirmed."
        )

        # -------------------------------------------------------------
        # Part 5: Headless Workflow Replay Execution (Runner)
        # -------------------------------------------------------------
        console.print(
            "\n[bold yellow]Step 5: Headless Replay Execution (WorkflowRunner)[/bold yellow]"
        )
        runner = WorkflowRunner(console=console)
        runner.capturer = mock_capturer

        # 5.1 Dry Run Replay
        dry_res = runner.play(yaml_out, speed=5.0, dry_run=True)
        assert dry_res.success is True
        assert dry_res.executed_steps == 5
        console.print(f"  ✓ Dry-run replay completed in {dry_res.elapsed_seconds}s.")

        # 5.2 High-Speed Deterministic Live Replay (with mocked hardware drivers)
        with (
            patch.object(driver, "click") as replay_click,
            patch.object(driver, "type_text") as replay_type,
            patch.object(driver, "press_key") as replay_press,
        ):
            live_res = runner.play(yaml_out, speed=10.0, dry_run=False)
            assert live_res.success is True
            assert live_res.executed_steps == 5
            assert replay_click.call_count == 1
            assert replay_click.call_args.kwargs["x"] == 1100
            assert replay_click.call_args.kwargs["y"] == 180
            assert replay_type.call_count == 1
            assert replay_type.call_args.args[0] == "Jane Doe"
            assert replay_press.call_count == 1
            assert replay_press.call_args.args[0] == "enter"
            console.print(
                f"  ✓ High-speed live replay completed: {live_res.executed_steps} steps verified in {live_res.elapsed_seconds}s."
            )

        # -------------------------------------------------------------
        # Part 6: CLI Subcommand Integration Test
        # -------------------------------------------------------------
        console.print(
            "\n[bold yellow]Step 6: CLI Subcommands Verification (`aef workflow`)[/bold yellow]"
        )
        cli_runner = CliRunner()

        # Test `aef workflow --help`
        res_help = cli_runner.invoke(app, ["workflow", "--help"])
        assert res_help.exit_code == 0
        assert "Record, export, and replay" in res_help.output

        # Test `aef workflow list`
        old_home = os.environ.get("HOME")
        os.environ["HOME"] = str(tmp_path)
        try:
            wf_sub = tmp_path / ".aef" / "workflows"
            wf_sub.mkdir(parents=True, exist_ok=True)
            import shutil

            shutil.copy(yaml_out, wf_sub / "test_flow.yaml")
            shutil.copy(py_out, wf_sub / "test_flow.py")

            res_list = cli_runner.invoke(app, ["workflow", "list"])
            assert res_list.exit_code == 0
            assert "test_flow" in res_list.output
            assert "YAML" in res_list.output
            assert "PY" in res_list.output
            console.print("  ✓ `aef workflow list` displayed saved workflows.")

            # Test `aef workflow play --dry-run`
            with patch(
                "agenteverywhereflow.workflow.runner.get_capturer", return_value=mock_capturer
            ):
                res_play = cli_runner.invoke(
                    app, ["workflow", "play", str(wf_sub / "test_flow.yaml"), "--dry-run"]
                )
                assert res_play.exit_code == 0
                assert "Workflow Replay Finished Successfully" in res_play.output
                console.print("  ✓ `aef workflow play` dry-run replay succeeded.")
        finally:
            if old_home is not None:
                os.environ["HOME"] = old_home

    console.rule("[bold green]All Direction 3 Benchmarks & Functional Tests PASSED![/bold green]")


if __name__ == "__main__":
    run_benchmark()
