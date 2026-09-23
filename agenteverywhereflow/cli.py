"""Command-line interface for AgentEverywhereFlow (AEFlow)."""

import typer
from rich.console import Console
from rich.panel import Panel

from agenteverywhereflow import __version__
from agenteverywhereflow.agent.loop import AgentLoop
from agenteverywhereflow.capturer import get_capturer
from agenteverywhereflow.capturer.selector import TargetSelector
from agenteverywhereflow.config import ExecutionMode, config

app = typer.Typer(
    name="aef",
    help="AgentEverywhereFlow: Summon an autonomous GUI agent on any screen or window.",
    add_completion=False,
)
console = Console()


@app.command(name="version")
def version() -> None:
    """Show version and information."""
    console.print(
        Panel(
            f"[bold magenta]AgentEverywhereFlow (AEFlow)[/bold magenta] v{__version__}\n"
            f"[cyan]Family: *Flow | Agent on Everywhere[/cyan]",
            title="System Info",
        )
    )


@app.command(name="list-targets")
def list_targets(
    displays_only: bool = typer.Option(False, "--displays-only", "-d", help="Only list displays"),
    windows_only: bool = typer.Option(False, "--windows-only", "-w", help="Only list windows"),
) -> None:
    """List all available physical displays and active application windows."""
    capturer = get_capturer()
    inc_disp = not windows_only
    inc_win = not displays_only
    targets = capturer.list_targets(include_displays=inc_disp, include_windows=inc_win)
    selector = TargetSelector()
    selector.display_selection_menu(targets)


@app.command(name="summon")
def summon(
    task: str = typer.Option(None, "--task", "-t", help="Task description to execute"),
    mode: ExecutionMode = typer.Option(
        ExecutionMode.MINIMAL_PYTHON,
        "--mode",
        "-m",
        help="Execution mode (minimal: Python REPL / guarded: atomic calls)",
    ),
) -> None:
    """Interactive screen-share style target picker and agent summoner."""
    selector = TargetSelector()
    target = selector.interactive_select()
    if not target:
        console.print("[yellow]Summon cancelled.[/yellow]")
        raise typer.Exit(0)

    if not task:
        task = console.input("[bold green]📝 Enter task for Agent to accomplish: [/bold green]").strip()
        if not task:
            console.print("[yellow]No task provided, aborting.[/yellow]")
            raise typer.Exit(0)

    loop = AgentLoop()
    loop.run(target=target, user_task=task, mode=mode)


@app.command(name="run")
def run(
    target_query: str = typer.Option(
        ..., "--target", help="Display index (e.g. 1) or Window title substring"
    ),
    task: str = typer.Option(..., "--task", "-t", help="Task description"),
    mode: ExecutionMode = typer.Option(
        ExecutionMode.MINIMAL_PYTHON, "--mode", "-m", help="Execution mode"
    ),
) -> None:
    """Directly summon agent onto a matching display or window."""
    capturer = get_capturer()
    all_targets = capturer.list_targets()

    selected = None
    # 1. Match display index
    if target_query.isdigit():
        idx = int(target_query)
        if 1 <= idx <= len(all_targets):
            selected = all_targets[idx - 1]

    # 2. Match window title or process substring
    if not selected:
        for t in all_targets:
            if target_query.lower() in t.title.lower() or (
                t.process_name and target_query.lower() in t.process_name.lower()
            ):
                selected = t
                break

    if not selected:
        console.print(f"[bold red]❌ No target found matching query: '{target_query}'[/bold red]")
        raise typer.Exit(1)

    loop = AgentLoop()
    loop.run(target=selected, user_task=task, mode=mode)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
