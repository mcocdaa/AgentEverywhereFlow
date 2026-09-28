"""Command-line interface for AgentEverywhereFlow (AEFlow)."""

import typer
from rich.console import Console
from rich.panel import Panel

from agenteverywhereflow import __version__
from agenteverywhereflow.config import ExecutionMode, PermissionMode

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}

app = typer.Typer(
    name="aef",
    help="AgentEverywhereFlow: Summon an autonomous GUI agent on any screen or window.",
    add_completion=False,
    context_settings=CONTEXT_SETTINGS,
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


def _resolve_latest_release_tag() -> str | None:
    """Query GitHub releases API for the latest stable release tag."""
    try:
        import httpx

        url = "https://api.github.com/repos/mcocdaa/AgentEverywhereFlow/releases/latest"
        resp = httpx.get(url, timeout=3.0, headers={"User-Agent": "AEFlow-Updater"})
        if resp.status_code == 200:
            tag = resp.json().get("tag_name")
            if tag:
                return str(tag)
        # Fallback to tags endpoint if release hasn't been drafted
        url_tags = "https://api.github.com/repos/mcocdaa/AgentEverywhereFlow/tags"
        resp_tags = httpx.get(url_tags, timeout=3.0, headers={"User-Agent": "AEFlow-Updater"})
        if resp_tags.status_code == 200 and resp_tags.json():
            return str(resp_tags.json()[0].get("name"))
    except Exception:
        pass
    return None


@app.command(name="update")
def update(
    channel: str | None = typer.Option(
        None,
        "--channel",
        "-c",
        help="Specific release tag or branch to update to (default: latest stable release tag)",
    ),
    edge: bool = typer.Option(
        False,
        "--edge",
        "--main",
        "--nightly",
        help="Update to the latest bleeding-edge commit on 'main' branch",
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Force reinstall even if already on the latest version"
    ),
) -> None:
    """Update AgentEverywhereFlow to the latest stable release tag (default) or bleeding-edge main."""
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    target_ref = "main" if edge else (channel or None)
    is_stable = not edge and channel is None

    if is_stable:
        latest_tag = _resolve_latest_release_tag()
        target_ref = latest_tag or "main"
    elif not target_ref:
        target_ref = "main"

    console.print(
        Panel(
            f"[bold cyan]🔄 Updating AgentEverywhereFlow[/bold cyan]\n"
            f"[dim]Current version:[/dim] [bold yellow]v{__version__}[/bold yellow]\n"
            f"[dim]Target release:[/dim]  [bold green]{target_ref}[/bold green] "
            f"({'latest stable tag' if is_stable else 'bleeding-edge/custom'})",
            title="AEFlow Self-Updater",
            border_style="cyan",
        )
    )

    # If already on this version and not forcing:
    norm_curr = f"v{__version__}".lower()
    norm_target = target_ref.lower()
    if is_stable and (norm_curr == norm_target or norm_curr == f"v{norm_target}") and not force:
        console.print(
            f"[bold green]✓ AgentEverywhereFlow is already up to date ({target_ref})![/bold green]\n"
            f"[dim]To test the bleeding-edge development build, use: [cyan]aef update --edge[/cyan]\n"
            f"To force reinstall the current version, use: [cyan]aef update --force[/cyan][/dim]"
        )
        return

    # 1. Check if running in a local Git repository
    repo_dir = Path(__file__).resolve().parent.parent
    if (repo_dir / ".git").exists() and shutil.which("git"):
        try:
            console.print(
                f"[bold yellow]Found local Git repository at {repo_dir}. Checking out '{target_ref}'...[/bold yellow]"
            )
            subprocess.run(
                ["git", "fetch", "--tags", "origin"],
                cwd=repo_dir,
                capture_output=True,
                text=True,
            )
            git_cmd = (
                ["git", "pull", "origin", "main"]
                if target_ref == "main"
                else ["git", "checkout", target_ref]
            )
            res = subprocess.run(git_cmd, cwd=repo_dir, capture_output=True, text=True)
            if res.returncode == 0:
                console.print(
                    f"[bold green]✓ Successfully updated local Git repository to {target_ref}![/bold green]"
                )
                return
        except Exception as e:
            console.print(f"[dim]Git fallback: {e}[/dim]")

    # 2. Check if uv is available
    uv_path = shutil.which("uv")
    if uv_path:
        console.print(f"[bold yellow]Updating via 'uv tool' to {target_ref} ...[/bold yellow]")
        install_target = f"git+https://github.com/mcocdaa/AgentEverywhereFlow.git@{target_ref}"
        cmd = [uv_path, "tool", "install", "--force", install_target]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode == 0:
                msg = res.stdout.strip() or res.stderr.strip()
                console.print(
                    f"[bold green]✓ Successfully updated to {target_ref} via uv tool![/bold green]\n{msg}"
                )
                return
        except Exception as e:
            console.print(f"[dim]uv tool error: {e}[/dim]")

    # 3. Fallback to pip
    try:
        console.print(f"[bold yellow]Updating via pip to {target_ref} ...[/bold yellow]")
        pip_cmd = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--upgrade",
            f"git+https://github.com/mcocdaa/AgentEverywhereFlow.git@{target_ref}",
        ]
        res_pip = subprocess.run(pip_cmd, capture_output=True, text=True)
        if res_pip.returncode == 0:
            console.print(
                f"[bold green]✓ Successfully updated to {target_ref} via pip![/bold green]"
            )
            return
        else:
            console.print(f"[bold red]❌ Pip update failed: {res_pip.stderr.strip()}[/bold red]")
    except Exception as e:
        console.print(f"[bold red]❌ Update failed: {e}[/bold red]")


# Register 'upgrade' as alias for 'update'
app.command(name="upgrade")(update)


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


@app.command(name="chat")
def chat(
    target_query: str | None = typer.Option(
        None,
        "--target",
        "-t",
        help="Target ID, native handle, table index, or process/title substring. If omitted, opens interactive picker.",
    ),
    resume: str | None = typer.Option(
        None,
        "--resume",
        "-r",
        help="Resume a saved session by ID or 'latest' to restore dialogue memory and KV cache.",
    ),
    mode: ExecutionMode = typer.Option(
        ExecutionMode.MINIMAL_PYTHON,
        "--mode",
        "-m",
        help="Execution mode (minimal: Python REPL / guarded: atomic JSON)",
    ),
    permission: PermissionMode = typer.Option(
        PermissionMode.AUTO,
        "--permission",
        "-p",
        help="Permission mode: 'auto' (fully autonomous) or 'manual' (operator approval required before every action)",
    ),
    debug: bool = typer.Option(
        False, "--debug", "-d", help="Enable verbose debug logging and diagnostics"
    ),
    max_steps: int | None = typer.Option(
        None,
        "--max-steps",
        "-s",
        help="Max agent action steps allowed per dialogue turn (default: from config)",
    ),
) -> None:
    """Launch an interactive multi-turn dialogue session with the agent bound to a window or display."""
    from rich.markup import escape

    from agenteverywhereflow.capturer import get_capturer
    from agenteverywhereflow.capturer.selector import TargetSelector, resolve_target
    from agenteverywhereflow.config import config
    from agenteverywhereflow.session import (
        ChatSession,
        SessionEvent,
        SessionEventType,
        session_manager,
    )

    if debug:
        config.debug = True

    capturer = get_capturer()
    all_targets = capturer.list_targets()

    session: ChatSession
    selected = None

    if resume:
        if resume.lower() in ("latest", "last"):
            found_session = session_manager.get_latest_session()
            if not found_session:
                console.print("[bold red]❌ No existing saved session found to resume.[/bold red]")
                raise typer.Exit(1)
            session = found_session
        else:
            found_session = session_manager.get_session(resume) or session_manager.restore_session(
                resume
            )
            if not found_session:
                console.print(f"[bold red]❌ Saved session '{resume}' not found.[/bold red]")
                raise typer.Exit(1)
            session = found_session

        selected = session.target
        console.print(
            f"[bold green]✓ Successfully resumed session '{session.session_id}'! "
            f"(Turns: {session.turn_count}, Steps: {session.total_steps}, "
            f"KV Cache Hit Rate: {session.token_usage.cache_hit_rate}%)[/bold green]"
        )
    else:
        # Resolve target
        if target_query:
            selected = resolve_target(all_targets, target_query)
            if not selected:
                console.print(
                    f"[bold red]❌ No target found matching query: '{target_query}'[/bold red]"
                )
                console.print(
                    "[dim]Run 'aef list-targets' to view all available Target IDs and indices.[/dim]"
                )
                raise typer.Exit(1)
        else:
            selector = TargetSelector()
            selected = selector.interactive_select()
            if not selected:
                console.print("[yellow]Dialogue session cancelled.[/yellow]")
                raise typer.Exit(0)

        # Initialize conversational session
        session = session_manager.create_session(
            target=selected, mode=mode, permission_mode=permission
        )

    # Attach live Rich terminal event listener
    def on_session_event(event: SessionEvent) -> None:
        if event.event_type == SessionEventType.REASONING:
            content = event.payload.get("content", "")
            if content.strip():
                console.print(Panel(content, title="🤖 Agent Reasoning", style="cyan"))
        elif event.event_type == SessionEventType.ACTION_PROPOSED:
            action_type = event.payload.get("type", "")
            if action_type == "codeact":
                code = event.payload.get("code", "")
                console.print(
                    Panel(
                        code,
                        title=f"⚡ Executing CodeAct Block (Step {event.step})",
                        border_style="yellow",
                    )
                )
            elif action_type == "guarded":
                import json

                payload_data = event.payload.get("payload", {})
                console.print(
                    Panel(
                        json.dumps(payload_data, indent=2, ensure_ascii=False),
                        title=f"⚡ Executing Guarded Action (Step {event.step})",
                        border_style="yellow",
                    )
                )
        elif event.event_type == SessionEventType.ACTION_EXECUTED:
            if event.payload.get("rejected"):
                reason = event.payload.get("reason", "Operator rejected")
                console.print(
                    f"  [bold yellow]⛔ Action Rejected by Operator:[/bold yellow] {reason}"
                )
            else:
                success = event.payload.get("success", False)
                out = event.payload.get("output", "")
                err = event.payload.get("error", "")
                if success:
                    console.print("  [bold green]✅ Actions Executed Successfully[/bold green]")
                else:
                    console.print(f"  [bold red]❌ Execution Failed:[/bold red] {err}")
                if out and out.strip():
                    console.print(f"  [dim]Output: {escape(out.strip())}[/dim]")
        elif event.event_type == SessionEventType.TASK_COMPLETED:
            summary = event.payload.get("summary", "Task completed.")
            console.print(
                Panel(
                    f"[bold green]{summary}[/bold green]",
                    title="🎉 Instruction Completed",
                    border_style="bold green",
                )
            )
        elif event.event_type == SessionEventType.ERROR:
            err = event.payload.get("error", "Unknown error")
            console.print(
                Panel(f"[bold red]{err}[/bold red]", title="❌ Session Error", border_style="red")
            )

    session.add_listener(on_session_event)

    # Print session welcome banner
    console.print(
        Panel(
            f"[bold cyan]🎯 Target:[/bold cyan] {selected.title} [dim]({selected.rect.width}x{selected.rect.height})[/dim]\n"
            f"[bold cyan]🆔 Target ID:[/bold cyan] {selected.target_id}\n"
            f"[bold cyan]🛡️ Permission:[/bold cyan] [bold magenta]{session.permission_gate.mode.value.upper()}[/bold magenta] [dim](Use /perm to toggle manual approval)[/dim]\n"
            f"[bold cyan]⚙️ Mode:[/bold cyan] {mode.value.upper()}\n\n"
            f"[dim]Type your instructions to drive the target, or use slash commands:[/dim]\n"
            f"[dim]  /help           - View commands guide[/dim]\n"
            f"[dim]  /resume [id]    - Resume a saved session or list available sessions[/dim]\n"
            f"[dim]  /steps [n]      - View or adjust max steps per turn[/dim]\n"
            f"[dim]  /perm [mode]    - Switch between 'auto' and 'manual'[/dim]\n"
            f"[dim]  /target [query] - Switch to another window or screen[/dim]\n"
            f"[dim]  /clear          - Reset conversation memory[/dim]\n"
            f"[dim]  /status         - Show session state and diagnostics[/dim]\n"
            f"[dim]  /exit           - Exit session[/dim]",
            title="💬 AgentEverywhereFlow Dialogue Mode",
            border_style="bold blue",
        )
    )

    # Interactive REPL loop
    while True:
        try:
            target_label = selected.title[:12] if selected.title else selected.target_id
            user_input = console.input(f"[bold green]aef [{target_label}] ❯ [/bold green]").strip()
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]Dialogue session closed.[/yellow]")
            break

        if not user_input:
            continue

        if user_input.startswith("/"):
            parts = user_input.split(maxsplit=1)
            cmd = parts[0].lower()
            arg = parts[1].strip() if len(parts) > 1 else ""

            if cmd in ("/exit", "/quit", "/q"):
                console.print("[yellow]Dialogue session ended. Bye![/yellow]")
                break

            elif cmd == "/help":
                console.print(
                    Panel(
                        "[bold cyan]/help[/bold cyan]                - Display this help message\n"
                        "[bold cyan]/resume [id|latest][/bold cyan] - Resume a saved session or list available\n"
                        "[bold cyan]/steps [number][/bold cyan]     - View or adjust max step budget per turn\n"
                        "[bold cyan]/perm [auto|manual][/bold cyan] - Toggle or display permission level\n"
                        "[bold cyan]/target <query>[/bold cyan]     - Switch target window/screen\n"
                        "[bold cyan]/status[/bold cyan]              - Show session stats and viewport info\n"
                        "[bold cyan]/clear[/bold cyan]               - Clear multi-turn history while keeping target\n"
                        "[bold cyan]/exit[/bold cyan]                - Exit dialogue session",
                        title="💡 Available Slash Commands",
                        border_style="cyan",
                    )
                )

            elif cmd == "/resume":
                import datetime

                from rich.table import Table

                from agenteverywhereflow.session import session_storage

                if not arg:
                    sessions = session_storage.list_sessions()
                    if not sessions:
                        console.print("[dim]No saved sessions found in ~/.aef/sessions/[/dim]")
                    else:
                        table = Table(title="📁 Available Sessions to Resume")
                        table.add_column("Session ID", style="bold cyan")
                        table.add_column("Target Title", style="green")
                        table.add_column("Turns", justify="right")
                        table.add_column("Steps", justify="right")
                        table.add_column("KV Cache Hit", style="magenta", justify="right")
                        table.add_column("Last Updated", style="dim")

                        for s in sessions[:8]:
                            upd = datetime.datetime.fromtimestamp(s.updated_at).strftime(
                                "%Y-%m-%d %H:%M"
                            )
                            hit_str = (
                                f"{s.token_usage.cache_hit_rate}%"
                                if s.token_usage.prompt_tokens > 0
                                else "N/A"
                            )
                            table.add_row(
                                s.session_id,
                                s.target_title[:24],
                                str(s.turn_count),
                                str(s.total_steps),
                                hit_str,
                                upd,
                            )
                        console.print(table)
                        console.print(
                            "[dim]Usage: [bold cyan]/resume latest[/bold cyan] or [bold cyan]/resume <session_id>[/bold cyan][/dim]"
                        )
                    continue

                target_sess_id = arg.strip()
                resumed_sess: ChatSession | None = None
                if target_sess_id.lower() in ("latest", "last"):
                    resumed_sess = session_manager.get_latest_session()
                else:
                    resumed_sess = session_manager.get_session(
                        target_sess_id
                    ) or session_manager.restore_session(target_sess_id)
                    if not resumed_sess:
                        # Prefix match
                        all_saved = session_storage.list_sessions()
                        for s_meta in all_saved:
                            if s_meta.session_id.startswith(target_sess_id):
                                resumed_sess = session_manager.restore_session(s_meta.session_id)
                                break

                if not resumed_sess:
                    console.print(
                        f"[bold red]❌ Saved session '{target_sess_id}' not found.[/bold red]"
                    )
                    continue

                if resumed_sess.session_id == session.session_id:
                    console.print(
                        f"[yellow]Already interacting with session '{session.session_id}'.[/yellow]"
                    )
                    continue

                # Auto-save current session before switching
                session.save()
                session.remove_listener(on_session_event)

                session = resumed_sess
                selected = session.target
                session.add_listener(on_session_event)
                session.capturer.focus(selected)

                console.print(
                    Panel(
                        f"[bold green]✓ Successfully switched to session '{session.session_id}'![/bold green]\n"
                        f"[bold cyan]🎯 Target:[/bold cyan] {selected.title} ({selected.rect.width}x{selected.rect.height})\n"
                        f"[bold cyan]📊 Memory:[/bold cyan] Turns={session.turn_count}, Steps={session.total_steps}, "
                        f"KV Cache Hit Rate=[bold green]{session.token_usage.cache_hit_rate}%[/bold green]",
                        title="🔄 Session Resumed",
                        border_style="green",
                    )
                )

            elif cmd == "/perm":
                if arg:
                    try:
                        new_mode = PermissionMode(arg.lower())
                        session.set_permission_mode(new_mode)
                        console.print(
                            f"[bold green]✓ Permission mode updated to: {new_mode.value.upper()}[/bold green]"
                        )
                    except ValueError:
                        console.print(
                            f"[bold red]Invalid mode: '{arg}'. Choose 'auto' or 'manual'.[/bold red]"
                        )
                else:
                    console.print(
                        f"Current permission mode: [bold magenta]{session.permission_gate.mode.value.upper()}[/bold magenta]"
                    )

            elif cmd == "/target":
                if arg:
                    new_t = resolve_target(capturer.list_targets(), arg)
                    if new_t:
                        session.switch_target(new_t)
                        selected = new_t
                        console.print(
                            f"[bold green]✓ Active target switched to: {new_t.title} ({new_t.target_id})[/bold green]"
                        )
                    else:
                        console.print(
                            f"[bold red]❌ Target query '{arg}' could not be resolved.[/bold red]"
                        )
                else:
                    new_t = TargetSelector().interactive_select()
                    if new_t:
                        session.switch_target(new_t)
                        selected = new_t
                        console.print(
                            f"[bold green]✓ Active target switched to: {new_t.title} ({new_t.target_id})[/bold green]"
                        )

            elif cmd in ("/clear", "/reset"):
                session.reset_history()
                console.print(
                    "[bold green]✓ Conversational history and visual memory reset.[/bold green]"
                )

            elif cmd == "/status":
                curr_steps = max_steps or config.max_steps
                console.print(
                    Panel(
                        f"[bold]Session ID:[/bold] {session.session_id}\n"
                        f"[bold]Target:[/bold] {session.target.title} ({session.target.target_id})\n"
                        f"[bold]Resolution:[/bold] {session.target.rect.width}x{session.target.rect.height} at ({session.target.rect.x}, {session.target.rect.y})\n"
                        f"[bold]Process:[/bold] {session.target.process_name or 'N/A'}\n"
                        f"[bold]Turns Completed:[/bold] {session.turn_count} | [bold]Total Steps:[/bold] {session.total_steps}\n"
                        f"[bold]Step Budget per Turn:[/bold] {curr_steps} steps [dim](Change with /steps <n>)[/dim]\n"
                        f"[bold]Permission:[/bold] {session.permission_gate.mode.value.upper()}\n"
                        f"[bold]Execution Mode:[/bold] {session.mode.value.upper()}\n"
                        f"[bold]KV Cache Hit Rate:[/bold] [bold green]{session.token_usage.cache_hit_rate}%[/bold green] ({session.token_usage.cached_prompt_tokens} / {session.token_usage.prompt_tokens} cached prompt tokens)\n"
                        f"[bold]Total Tokens:[/bold] {session.token_usage.total_tokens} (Prompt: {session.token_usage.prompt_tokens}, Completion: {session.token_usage.completion_tokens})\n"
                        f"[bold]State:[/bold] {session.state.value.upper()}\n"
                        f"[bold]Persisted Storage:[/bold] ~/.aef/sessions/{session.session_id}/",
                        title="📊 Session Diagnostics & Token Metrics",
                        border_style="cyan",
                    )
                )

            elif cmd in ("/steps", "/step"):
                if arg:
                    try:
                        new_steps = int(arg)
                        if new_steps <= 0:
                            raise ValueError
                        max_steps = new_steps
                        console.print(
                            f"[bold green]✓ Step budget per turn set to {max_steps} steps.[/bold green]"
                        )
                    except ValueError:
                        console.print(
                            "[bold red]❌ Invalid step count. Usage: /steps <positive_integer>[/bold red]"
                        )
                else:
                    curr_steps = max_steps or config.max_steps
                    console.print(
                        f"[bold cyan]Current step limit per turn:[/bold cyan] {curr_steps} steps [dim](Change with: /steps <number>)[/dim]"
                    )

            else:
                console.print(
                    f"[yellow]Unknown command '{cmd}'. Type /help for assistance.[/yellow]"
                )
            continue

        # Regular user instruction: execute turn
        console.rule(f"[bold blue]Turn {session.turn_count + 1}[/bold blue]")
        turn_res = session.execute_turn(user_input, max_steps=max_steps)
        if not turn_res.success and turn_res.error:
            console.print(f"[bold red]Turn failed: {turn_res.error}[/bold red]")
        elif not turn_res.completed:
            console.print(
                Panel(
                    f"[bold yellow]⚠️ Reached turn step limit ({turn_res.steps_executed} steps) without conclusion.[/bold yellow]\n"
                    f"[dim]The agent executed {turn_res.steps_executed} steps in this turn (e.g. repeated scrolling, waiting, or inspecting).\n"
                    f"Conversation context and memory are preserved. You can enter your next prompt directly to continue, e.g.:\n"
                    f"  - '总结刚才讨论的核心内容，给出结论'\n"
                    f"  - '继续向豆包提问并推进讨论'[/dim]",
                    title="⏸️ Step Limit Reached (Waiting for User Input)",
                    border_style="yellow",
                )
            )


@app.command(name="serve")
def serve(
    host: str = typer.Option(
        None, "--host", "-H", help="Bind host address (default: 127.0.0.1 or from config)"
    ),
    port: int = typer.Option(
        None, "--port", "-p", help="Bind port number (default: 8000 or from config)"
    ),
    reload: bool = typer.Option(False, "--reload", "-r", help="Enable auto-reload for development"),
) -> None:
    """Launch the AgentEverywhereFlow REST & WebSocket dialogue service daemon."""
    import uvicorn

    from agenteverywhereflow.config import config

    bind_host = host or config.server_host
    bind_port = port or config.server_port

    console.print(
        Panel(
            f"[bold green]🚀 AgentEverywhereFlow Dialogue Service Daemon[/bold green]\n\n"
            f"[bold cyan]🌐 Server Endpoint:[/bold cyan] http://{bind_host}:{bind_port}\n"
            f"[bold cyan]📖 Swagger Docs:[/bold cyan]    http://{bind_host}:{bind_port}/docs\n"
            f"[bold cyan]🔌 REST Base:[/bold cyan]       http://{bind_host}:{bind_port}/api/v1\n"
            f"[bold cyan]⚡ WebSocket:[/bold cyan]       ws://{bind_host}:{bind_port}/api/v1/sessions/{{id}}/ws",
            title="🌐 AEFlow Daemon Online",
            border_style="green",
        )
    )

    uvicorn.run(
        "agenteverywhereflow.server.app:create_app",
        host=bind_host,
        port=bind_port,
        factory=True,
        reload=reload,
    )


session_app = typer.Typer(
    name="session",
    help="Manage persistent dialogue sessions, trajectory history, and audit exports.",
    add_completion=False,
    context_settings=CONTEXT_SETTINGS,
)
app.add_typer(session_app, name="session")


@session_app.command(name="list")
def list_saved_sessions() -> None:
    """List all persisted sessions with turns, steps, and KV cache statistics."""
    import datetime

    from rich.table import Table

    from agenteverywhereflow.session import session_storage

    sessions = session_storage.list_sessions()
    if not sessions:
        console.print("[dim]No saved sessions found in ~/.aef/sessions/[/dim]")
        return

    table = Table(title="📁 Persisted AEFlow Dialogue Sessions")
    table.add_column("Session ID", style="bold cyan")
    table.add_column("Target Title", style="green")
    table.add_column("Turns", justify="right")
    table.add_column("Steps", justify="right")
    table.add_column("KV Cache Hit", style="magenta", justify="right")
    table.add_column("Permission", style="yellow")
    table.add_column("Last Updated", style="dim")

    for s in sessions:
        upd = datetime.datetime.fromtimestamp(s.updated_at).strftime("%Y-%m-%d %H:%M")
        hit_str = f"{s.token_usage.cache_hit_rate}%" if s.token_usage.prompt_tokens > 0 else "N/A"
        table.add_row(
            s.session_id,
            s.target_title[:24],
            str(s.turn_count),
            str(s.total_steps),
            hit_str,
            s.permission_mode.upper(),
            upd,
        )
    console.print(table)


@session_app.command(name="show")
def show_session(session_id: str) -> None:
    """Display detailed trajectory and token statistics for a session."""
    import datetime

    from agenteverywhereflow.session import session_storage

    meta = session_storage.load_metadata(session_id)
    if not meta:
        console.print(f"[bold red]Session '{session_id}' not found.[/bold red]")
        raise typer.Exit(1)

    created = datetime.datetime.fromtimestamp(meta.created_at).strftime("%Y-%m-%d %H:%M:%S")
    updated = datetime.datetime.fromtimestamp(meta.updated_at).strftime("%Y-%m-%d %H:%M:%S")

    console.print(
        Panel(
            f"[bold cyan]Session ID:[/bold cyan] {meta.session_id}\n"
            f"[bold cyan]Target:[/bold cyan] {meta.target_title} ({meta.target_id})\n"
            f"[bold cyan]Permission:[/bold cyan] {meta.permission_mode.upper()} | [bold cyan]Mode:[/bold cyan] {meta.mode.upper()}\n"
            f"[bold cyan]Turns Completed:[/bold cyan] {meta.turn_count} | [bold cyan]Total Steps:[/bold cyan] {meta.total_steps}\n"
            f"[bold cyan]Tokens:[/bold cyan] Prompt={meta.token_usage.prompt_tokens}, Cached={meta.token_usage.cached_prompt_tokens} (Hit Rate: [bold green]{meta.token_usage.cache_hit_rate}%[/bold green]), Completion={meta.token_usage.completion_tokens}\n"
            f"[bold cyan]Timeline:[/bold cyan] Created={created} | Updated={updated}\n"
            f"[bold cyan]Status:[/bold cyan] {meta.state.upper()}",
            title=f"📋 Session Audit Record: {session_id}",
            border_style="cyan",
        )
    )


@session_app.command(name="delete")
def delete_session(session_id: str) -> None:
    """Delete a saved session and its trajectory from disk."""
    from agenteverywhereflow.session import session_storage

    success = session_storage.delete_session(session_id)
    if success:
        console.print(
            f"[bold green]✓ Session '{session_id}' deleted from ~/.aef/sessions/[/bold green]"
        )
    else:
        console.print(f"[bold red]❌ Failed to delete session '{session_id}'.[/bold red]")


@session_app.command(name="export")
def export_session(
    session_id: str,
    output: str | None = typer.Option(None, "--output", "-o", help="Target output file path"),
) -> None:
    """Export complete session trajectory and metadata to JSON file."""
    import json
    from pathlib import Path

    from agenteverywhereflow.session import session_storage

    meta = session_storage.load_metadata(session_id)
    if not meta:
        console.print(f"[bold red]Session '{session_id}' not found.[/bold red]")
        raise typer.Exit(1)

    messages = session_storage.load_messages(session_id)
    export_payload = {
        "metadata": meta.model_dump(),
        "trajectory": messages,
    }

    out_path = Path(output) if output else Path(f"session_export_{session_id}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(export_payload, f, indent=2, ensure_ascii=False)

    console.print(
        f"[bold green]✓ Session '{session_id}' exported to {out_path.resolve()}[/bold green]"
    )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
