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


@app.command(name="config")
def manage_config(
    set_key: str | None = typer.Option(
        None, "--set-key", "-k", help="Set and persist API key into global ~/.aef/.env"
    ),
    set_base: str | None = typer.Option(
        None, "--set-base", "-b", help="Set and persist Base URL into global ~/.aef/.env"
    ),
    set_model: str | None = typer.Option(
        None, "--set-model", "-m", help="Set and persist Model Name into global ~/.aef/.env"
    ),
) -> None:
    """View current configuration or persist global options in ~/.aef/.env."""
    import agenteverywhereflow.config
    from agenteverywhereflow.config import AppConfig, get_global_config_dir, get_global_env_file

    env_file = get_global_env_file()
    modified = False

    # Read existing key-values from ~/.aef/.env if it exists
    env_dict: dict[str, str] = {}
    if env_file.exists():
        try:
            with open(env_file, encoding="utf-8") as f:
                for line in f:
                    line_strip = line.strip()
                    if line_strip and not line_strip.startswith("#") and "=" in line_strip:
                        k, v = line_strip.split("=", 1)
                        env_dict[k.strip()] = v.strip().strip("'\"")
        except Exception:
            pass

    if set_key is not None:
        env_dict["AEF_API_KEY"] = set_key.strip()
        modified = True
    if set_base is not None:
        env_dict["AEF_BASE_URL"] = set_base.strip()
        modified = True
    if set_model is not None:
        env_dict["AEF_MODEL_NAME"] = set_model.strip()
        modified = True

    if modified:
        get_global_config_dir().mkdir(parents=True, exist_ok=True)
        with open(env_file, "w", encoding="utf-8") as f:
            for k, v in env_dict.items():
                f.write(f"{k}={v}\n")
        console.print(
            f"[bold green]✓ Successfully updated global configuration in {env_file}![/bold green]"
        )
        # Reload config instance
        agenteverywhereflow.config.config = AppConfig()

    current_cfg = agenteverywhereflow.config.config
    key_display = (
        f"[green]{current_cfg.api_key[:6]}...{current_cfg.api_key[-4:]}[/green]"
        if len(current_cfg.api_key) > 10
        else (
            "[green]Configured[/green]"
            if current_cfg.api_key
            else "[bold red]Not Configured (Missing)[/bold red]"
        )
    )

    console.print(
        Panel(
            f"[bold cyan]Model Name:[/bold cyan] {current_cfg.model_name}\n"
            f"[bold cyan]Base URL:[/bold cyan] {current_cfg.base_url}\n"
            f"[bold cyan]API Key:[/bold cyan] {key_display}\n"
            f"[bold cyan]Global Config File:[/bold cyan] {env_file}\n"
            f"[bold cyan]Screenshots Dir:[/bold cyan] {current_cfg.screenshot_dir}\n"
            f"[bold cyan]Execution Mode:[/bold cyan] {current_cfg.default_mode}\n"
            f"[bold cyan]Debug Mode:[/bold cyan] {current_cfg.debug}",
            title="⚙️ AgentEverywhereFlow Configuration",
            border_style="cyan",
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
