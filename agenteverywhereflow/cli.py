"""Command-line interface for AgentEverywhereFlow (AEFlow)."""

import typer
from rich.console import Console
from rich.panel import Panel

from agenteverywhereflow import __version__
from agenteverywhereflow.config import ExecutionMode

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
    from agenteverywhereflow.capturer import get_capturer
    from agenteverywhereflow.capturer.selector import TargetSelector

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
    debug: bool = typer.Option(
        False, "--debug", "-d", help="Enable verbose debug logging and diagnostics"
    ),
) -> None:
    """Interactive screen-share style target picker and agent summoner."""
    from agenteverywhereflow.agent.loop import AgentLoop
    from agenteverywhereflow.capturer.selector import TargetSelector
    from agenteverywhereflow.config import config

    if debug:
        config.debug = True

    selector = TargetSelector()
    target = selector.interactive_select()
    if not target:
        console.print("[yellow]Summon cancelled.[/yellow]")
        raise typer.Exit(0)

    if not task:
        task = console.input(
            "[bold green]📝 Enter task for Agent to accomplish: [/bold green]"
        ).strip()
        if not task:
            console.print("[yellow]No task provided, aborting.[/yellow]")
            raise typer.Exit(0)

    loop = AgentLoop()
    loop.run(target=target, user_task=task, mode=mode)


@app.command(name="run")
def run(
    target_query: str = typer.Option(
        ...,
        "--target",
        "-t",
        help="Target ID (e.g. hwnd:0x1b0a4, display:1), Table index (e.g. 1), Process (e.g. notepad.exe), or Title substring",
    ),
    task: str = typer.Option(..., "--task", help="Task description"),
    mode: ExecutionMode = typer.Option(
        ExecutionMode.MINIMAL_PYTHON, "--mode", "-m", help="Execution mode"
    ),
    debug: bool = typer.Option(
        False, "--debug", "-d", help="Enable verbose debug logging and diagnostics"
    ),
) -> None:
    """Directly summon agent onto a matching display or window."""
    from rich.panel import Panel

    from agenteverywhereflow.agent.loop import AgentLoop
    from agenteverywhereflow.capturer import get_capturer
    from agenteverywhereflow.capturer.selector import resolve_target
    from agenteverywhereflow.config import config

    if debug:
        config.debug = True

    capturer = get_capturer()
    all_targets = capturer.list_targets()

    selected = resolve_target(all_targets, target_query)

    if not selected:
        console.print(f"[bold red]❌ No target found matching query: '{target_query}'[/bold red]")
        console.print(
            "[dim]Tip: Run 'aef list-targets' to view all available Target IDs, handles, and indices.[/dim]"
        )
        raise typer.Exit(1)

    if debug:
        handle_hex = f"0x{selected.native_handle:x}" if selected.native_handle else "N/A"
        console.print(
            Panel(
                f"[bold cyan]Query:[/bold cyan] {target_query}\n"
                f"[bold cyan]Matched Target:[/bold cyan] {selected.title}\n"
                f"[bold cyan]Target ID:[/bold cyan] {selected.target_id}\n"
                f"[bold cyan]Native Handle:[/bold cyan] {handle_hex} ({selected.native_handle})\n"
                f"[bold cyan]Bounds:[/bold cyan] ({selected.rect.x}, {selected.rect.y}, {selected.rect.width}, {selected.rect.height})\n"
                f"[bold cyan]Process Name:[/bold cyan] {selected.process_name or 'N/A'}\n"
                f"[bold cyan]Is Minimized:[/bold cyan] {selected.is_minimized}",
                title="🔍 Target Diagnostics (Debug Mode)",
                border_style="magenta",
            )
        )

    loop = AgentLoop()
    loop.run(target=selected, user_task=task, mode=mode)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
