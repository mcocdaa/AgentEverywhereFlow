"""Headless deterministic replay runner for YAML GUI workflows."""

import time
from collections.abc import Callable
from pathlib import Path

import yaml
from rich.console import Console

from agenteverywhereflow.actions.coords import CoordinateProjector
from agenteverywhereflow.actions.driver import driver
from agenteverywhereflow.capturer import get_capturer
from agenteverywhereflow.capturer.base import TargetInfo
from agenteverywhereflow.workflow.models import WorkflowDefinition, WorkflowStep


class WorkflowReplayResult:
    """Summary of workflow execution run."""

    def __init__(
        self,
        success: bool,
        total_steps: int,
        executed_steps: int,
        elapsed_seconds: float,
        error: str = "",
    ) -> None:
        self.success = success
        self.total_steps = total_steps
        self.executed_steps = executed_steps
        self.elapsed_seconds = elapsed_seconds
        self.error = error


class WorkflowRunner:
    """Replays declarative GUI workflows with live target re-binding and coordinate projection."""

    def __init__(self, console: Console | None = None) -> None:
        self.capturer = get_capturer()
        self.console = console or Console()

    def load_workflow(self, path: Path | str) -> WorkflowDefinition:
        """Load workflow definition from YAML file."""
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"Workflow file '{path}' not found.")
        with open(file_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return WorkflowDefinition.model_validate(data)

    def resolve_target(self, query: str) -> TargetInfo:
        """Locate target window on the desktop matching query."""
        all_targets = self.capturer.list_targets()
        q = query.strip().lower()
        # 1. Exact or prefix ID match
        for t in all_targets:
            if q == t.target_id.lower() or t.target_id.lower().startswith(q):
                return t
        # 2. Substring match on title or process name
        for t in all_targets:
            if q in t.title.lower() or q in (t.process_name or "").lower():
                return t
        raise RuntimeError(f"Target window matching '{query}' not found on desktop.")

    def play(
        self,
        workflow: WorkflowDefinition | Path | str,
        speed: float = 1.0,
        dry_run: bool = False,
        on_step: Callable[[WorkflowStep, TargetInfo | None], None] | None = None,
    ) -> WorkflowReplayResult:
        """Execute all steps of the workflow in sequence."""
        if isinstance(workflow, (str, Path)):
            wf = self.load_workflow(workflow)
        else:
            wf = workflow

        effective_speed = max(0.1, speed)
        delay_scale = 1.0 / effective_speed

        self.console.print(
            f"[bold green]▶ Starting Workflow Replay:[/bold green] [bold cyan]{wf.name}[/bold cyan] "
            f"({len(wf.steps)} steps, Speed: {effective_speed}x{' [DRY RUN]' if dry_run else ''})"
        )

        start_time = time.time()
        active_target: TargetInfo | None = None
        executed_count = 0

        # Pre-resolve initial target if available
        if wf.targets:
            try:
                active_target = self.resolve_target(wf.targets[0].title)
                if not dry_run:
                    self.capturer.focus(active_target)
            except Exception:
                pass

        try:
            for step in wf.steps:
                executed_count += 1
                target_query = step.target or (wf.targets[0].title if wf.targets else "")

                # Target binding
                if target_query and (
                    not active_target or target_query.lower() not in active_target.title.lower()
                ):
                    active_target = self.resolve_target(target_query)
                    if not dry_run:
                        self.capturer.focus(active_target)
                        time.sleep(0.3 * delay_scale)

                if on_step:
                    on_step(step, active_target)

                self.console.print(
                    f"  [dim]Step {step.step_number}/{len(wf.steps)}:[/dim] "
                    f"[bold yellow]{step.action}[/bold yellow] [dim]({step.description or ''})[/dim]"
                )

                if dry_run:
                    time.sleep(0.1 * delay_scale)
                    continue

                # Execute Action
                if step.action == "switch_target":
                    if active_target:
                        self.capturer.focus(active_target)
                    time.sleep(0.3 * delay_scale)

                elif step.action in ("click", "double_click", "right_click"):
                    if not active_target:
                        raise RuntimeError("No active target window bound for click action.")
                    x = float(step.params.get("x", 0))
                    y = float(step.params.get("y", 0))
                    button = step.params.get("button", "left")
                    clicks = int(
                        step.params.get("clicks", 2 if step.action == "double_click" else 1)
                    )
                    if step.action == "right_click":
                        button = "right"
                        clicks = 1

                    sx, sy = CoordinateProjector.to_screen_coords(
                        target=active_target,
                        x=x,
                        y=y,
                        img_width=active_target.rect.width,
                        img_height=active_target.rect.height,
                    )
                    driver.click(
                        x=sx,
                        y=sy,
                        button=button,  # type: ignore
                        clicks=clicks,
                        window_handle=active_target.native_handle,
                    )
                    time.sleep(0.3 * delay_scale)

                elif step.action == "type":
                    if not active_target:
                        raise RuntimeError("No active target window bound for type action.")
                    text = str(step.params.get("text", ""))
                    driver.type_text(text, window_handle=active_target.native_handle)
                    time.sleep(0.3 * delay_scale)

                elif step.action == "press":
                    if not active_target:
                        raise RuntimeError("No active target window bound for press action.")
                    key = str(step.params.get("key", ""))
                    driver.press_key(key, window_handle=active_target.native_handle)
                    time.sleep(0.2 * delay_scale)

                elif step.action == "hotkey":
                    if not active_target:
                        raise RuntimeError("No active target window bound for hotkey action.")
                    keys = step.params.get("keys", [])
                    driver.hotkey(*keys, window_handle=active_target.native_handle)
                    time.sleep(0.3 * delay_scale)

                elif step.action == "scroll":
                    amount = int(step.params.get("amount", 0))
                    x_raw = step.params.get("x")
                    y_raw = step.params.get("y")
                    if x_raw is not None and y_raw is not None and active_target:
                        sx, sy = CoordinateProjector.to_screen_coords(
                            target=active_target,
                            x=float(x_raw),
                            y=float(y_raw),
                            img_width=active_target.rect.width,
                            img_height=active_target.rect.height,
                        )
                        driver.scroll(amount, x=sx, y=sy)
                    else:
                        driver.scroll(amount)
                    time.sleep(0.2 * delay_scale)

                elif step.action == "wait":
                    sec = float(step.params.get("seconds", 1.0))
                    time.sleep(sec * delay_scale)

                elif step.action == "codeact" and step.code:
                    if active_target:
                        from agenteverywhereflow.engine.python_repl import PythonReplEngine

                        repl = PythonReplEngine()
                        repl.execute(step.code, active_target)
                    time.sleep(0.3 * delay_scale)

            elapsed = round(time.time() - start_time, 2)
            self.console.print(
                f"[bold green]✓ Workflow Replay Finished Successfully![/bold green] "
                f"({executed_count} steps executed in {elapsed}s)"
            )
            return WorkflowReplayResult(
                success=True,
                total_steps=len(wf.steps),
                executed_steps=executed_count,
                elapsed_seconds=elapsed,
            )

        except Exception as e:
            elapsed = round(time.time() - start_time, 2)
            self.console.print(
                f"[bold red]❌ Replay Failed at Step {executed_count}:[/bold red] {e}"
            )
            return WorkflowReplayResult(
                success=False,
                total_steps=len(wf.steps),
                executed_steps=executed_count,
                elapsed_seconds=elapsed,
                error=str(e),
            )
