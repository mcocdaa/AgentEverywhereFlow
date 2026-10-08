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
                encoding="utf-8",
                errors="replace",
            )
            git_cmd = (
                ["git", "pull", "origin", "main"]
                if target_ref == "main"
                else ["git", "checkout", target_ref]
            )
            res = subprocess.run(
                git_cmd,
                cwd=repo_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
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

        # On Windows, ~/.local/bin/aef.exe is actively executing this process and locked by Windows kernel (os error 32).
        # Upgrading the uv tool's isolated Python environment directly via `uv pip install --python sys.executable`
        # updates all package code, wheels, and dependencies smoothly without attempting to overwrite the locked launcher shim.
        if sys.platform == "win32":
            res_pip = subprocess.run(
                [
                    uv_path,
                    "pip",
                    "install",
                    "--python",
                    sys.executable,
                    "--upgrade",
                    "--refresh",
                    install_target,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if res_pip.returncode == 0:
                console.print(
                    f"[bold green]✓ Successfully updated to {target_ref} via uv![/bold green]"
                )
                return

        cmd = [uv_path, "tool", "install", "--force", "--reinstall", install_target]
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if res.returncode == 0:
                msg = res.stdout.strip() or res.stderr.strip()
                console.print(
                    f"[bold green]✓ Successfully updated to {target_ref} via uv tool![/bold green]\n{msg}"
                )
                return
            else:
                err_msg = res.stderr.strip() or res.stdout.strip()
                # On Windows, if packages were upgraded and only the locked entrypoint copy failed (os error 32)
                if sys.platform == "win32" and (
                    "os error 32" in err_msg or "进程无法访问" in err_msg
                ):
                    combined_output = f"{res.stdout}\n{res.stderr}"
                    if "Installed" in combined_output or "+ agenteverywhereflow" in combined_output:
                        console.print(
                            f"[bold green]✓ Successfully updated to {target_ref} via uv tool![/bold green]\n"
                            "[dim](Package libraries upgraded; active entrypoint shim preserved)[/dim]"
                        )
                        return

                console.print(f"[yellow]⚠️ uv tool install failed: {err_msg}[/yellow]")

                # Try 'uv tool upgrade agenteverywhereflow' as an alternative
                console.print("[dim]Attempting alternative 'uv tool upgrade' ...[/dim]")
                res_upg = subprocess.run(
                    [uv_path, "tool", "upgrade", "agenteverywhereflow"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                if res_upg.returncode == 0:
                    console.print(
                        f"[bold green]✓ Successfully upgraded to {target_ref} via uv tool upgrade![/bold green]"
                    )
                    return

                if sys.platform == "win32" and (
                    "os error 32" in err_msg or "进程无法访问" in err_msg
                ):
                    console.print(
                        "[bold yellow]💡 Windows Tip: If running inside an active 'aef' process, run this in PowerShell after exiting:[/bold yellow]\n"
                        f"   [bold cyan]uv tool install --force --reinstall {install_target}[/bold cyan]\n"
                        "   [dim]or[/dim]\n"
                        "   [bold cyan]uv tool upgrade agenteverywhereflow[/bold cyan]"
                    )
        except Exception as e:
            console.print(f"[dim]uv tool error: {e}[/dim]")

    # 3. Fallback to pip or uv pip
    pip_exe = shutil.which("pip") or shutil.which("pip3")
    has_pip = bool(pip_exe)
    if not has_pip:
        try:
            chk = subprocess.run(
                [sys.executable, "-m", "pip", "--version"],
                capture_output=True,
                text=True,
            )
            has_pip = chk.returncode == 0
        except Exception:
            has_pip = False

    if has_pip:
        pip_cmd = (
            [
                pip_exe,
                "install",
                "--upgrade",
                f"git+https://github.com/mcocdaa/AgentEverywhereFlow.git@{target_ref}",
            ]
            if pip_exe
            else [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--upgrade",
                f"git+https://github.com/mcocdaa/AgentEverywhereFlow.git@{target_ref}",
            ]
        )
        try:
            console.print(f"[bold yellow]Updating via pip to {target_ref} ...[/bold yellow]")
            res_pip = subprocess.run(
                pip_cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if res_pip.returncode == 0:
                console.print(
                    f"[bold green]✓ Successfully updated to {target_ref} via pip![/bold green]"
                )
                return
            else:
                console.print(
                    f"[bold red]❌ Pip update failed: {res_pip.stderr.strip()}[/bold red]"
                )
        except Exception as e:
            console.print(f"[bold red]❌ Pip error: {e}[/bold red]")
    elif uv_path:
        try:
            console.print(f"[bold yellow]Updating via uv pip to {target_ref} ...[/bold yellow]")
            res_uv_pip = subprocess.run(
                [
                    uv_path,
                    "pip",
                    "install",
                    "--upgrade",
                    f"git+https://github.com/mcocdaa/AgentEverywhereFlow.git@{target_ref}",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if res_uv_pip.returncode == 0:
                console.print(
                    f"[bold green]✓ Successfully updated to {target_ref} via uv pip![/bold green]"
                )
                return
        except Exception as e:
            console.print(f"[dim]uv pip fallback error: {e}[/dim]")


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


@app.command(name="move")
def move(
    target_query: str = typer.Argument(
        ...,
        help="Target window ID, title substring, or process name (e.g. '微信' or 'hwnd:0x40960')",
    ),
    to_display: str = typer.Option(
        ...,
        "--to-display",
        "-d",
        help="Destination display ID, number, or title substring (e.g. '2' or 'display:2')",
    ),
) -> None:
    """Move an application window to a specific physical or virtual display."""
    from agenteverywhereflow.capturer import get_capturer
    from agenteverywhereflow.capturer.base import TargetType
    from agenteverywhereflow.capturer.selector import resolve_display, resolve_target

    capturer = get_capturer()
    all_targets = capturer.list_targets()
    tgt = resolve_target(all_targets, target_query)
    if not tgt:
        console.print(f"[bold red]❌ Target window '{target_query}' not found.[/bold red]")
        raise typer.Exit(1)
    if tgt.target_type != TargetType.WINDOW:
        console.print("[bold red]❌ Only window targets can be relocated to a display.[/bold red]")
        raise typer.Exit(1)

    disp = resolve_display(all_targets, to_display)
    if not disp:
        console.print(f"[bold red]❌ Target display '{to_display}' not found.[/bold red]")
        raise typer.Exit(1)

    success = capturer.move_window_to_display(tgt, disp)
    if success:
        console.print(
            f"[bold green]✅ Relocated window '{tgt.title}' to {disp.title} at ({tgt.rect.x}, {tgt.rect.y}).[/bold green]"
        )
    else:
        console.print(
            f"[bold red]❌ Failed to move window '{tgt.title}' to {disp.title}.[/bold red]"
        )
        raise typer.Exit(1)


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
    to_display: str | None = typer.Option(
        None,
        "--to-display",
        "-d",
        help="Optionally relocate the target window to a specific display (e.g. '2' or 'display:2') before running",
    ),
    debug: bool = typer.Option(
        False, "--debug", "-d", help="Enable verbose debug logging and diagnostics"
    ),
) -> None:
    """Directly summon agent onto a matching display or window."""
    from rich.panel import Panel

    from agenteverywhereflow.agent.loop import AgentLoop
    from agenteverywhereflow.capturer import get_capturer
    from agenteverywhereflow.capturer.base import TargetType
    from agenteverywhereflow.capturer.selector import resolve_display, resolve_target
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

    if to_display and selected.target_type == TargetType.WINDOW:
        disp = resolve_display(all_targets, to_display)
        if disp:
            if capturer.move_window_to_display(selected, disp):
                console.print(
                    f"[bold green]🖥️ Relocated '{selected.title}' to {disp.title} ({selected.rect.x}, {selected.rect.y})[/bold green]"
                )
        else:
            console.print(
                f"[yellow]⚠️ Target display '{to_display}' not found, skipping relocation.[/yellow]"
            )

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
    target_queries: list[str] | None = typer.Option(
        None,
        "--target",
        "-t",
        help="Target ID, native handle, table index, or process/title substring. Can be specified multiple times or comma-separated.",
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
    from agenteverywhereflow.capturer.base import TargetInfo
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
        # Resolve target(s)
        resolved_targets: list[TargetInfo] = []
        if target_queries:
            raw_queries: list[str] = []
            for item in target_queries:
                for q in item.split(","):
                    clean_q = q.strip()
                    if clean_q:
                        raw_queries.append(clean_q)
            for q in raw_queries:
                tgt = resolve_target(all_targets, q)
                if not tgt:
                    console.print(f"[bold red]❌ No target found matching query: '{q}'[/bold red]")
                    console.print(
                        "[dim]Run 'aef list-targets' to view all available Target IDs and indices.[/dim]"
                    )
                    raise typer.Exit(1)
                resolved_targets.append(tgt)
        else:
            selector = TargetSelector()
            selected_single = selector.interactive_select()
            if not selected_single:
                console.print("[yellow]Dialogue session cancelled.[/yellow]")
                raise typer.Exit(0)
            resolved_targets = [selected_single]

        # Initialize conversational session
        session = session_manager.create_session(
            targets=resolved_targets, mode=mode, permission_mode=permission
        )
        selected = session.target

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
    targets_info = ""
    if len(session.targets) > 1:
        targets_info = (
            f"\n[bold cyan]🪟 Bound Targets ({len(session.targets)}):[/bold cyan]\n"
            + "\n".join(
                f"  {i}. {t.title} [dim]({t.rect.width}x{t.rect.height}, ID: {t.target_id}){' [ACTIVE]' if t.target_id == selected.target_id else ''}[/dim]"
                for i, t in enumerate(session.targets, 1)
            )
            + "\n"
        )

    console.print(
        Panel(
            f"[bold cyan]🎯 Active Target:[/bold cyan] {selected.title} [dim]({selected.rect.width}x{selected.rect.height})[/dim]\n"
            f"[bold cyan]🆔 Target ID:[/bold cyan] {selected.target_id}"
            f"{targets_info}\n"
            f"[bold cyan]🛡️ Permission:[/bold cyan] [bold magenta]{session.permission_gate.mode.value.upper()}[/bold magenta] [dim](Use /perm to toggle manual approval)[/dim]\n"
            f"[bold cyan]⚙️ Mode:[/bold cyan] {mode.value.upper()}\n\n"
            f"[dim]Type your instructions to drive the target, or use slash commands:[/dim]\n"
            f"[dim]  /help           - View commands guide[/dim]\n"
            f"[dim]  /target [args]  - Manage multi-window target pool (list, add, rm, switch)[/dim]\n"
            f"[dim]  /export [path]  - Export replayable workflow (.py script or .yaml)[/dim]\n"
            f"[dim]  /resume [id]    - Resume a saved session or list available sessions[/dim]\n"
            f"[dim]  /steps [n]      - View or adjust max steps per turn[/dim]\n"
            f"[dim]  /perm [mode]    - Switch between 'auto' and 'manual'[/dim]\n"
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
                        "[bold cyan]/target [query][/bold cyan]     - View, switch, add, or remove target windows\n"
                        "[bold cyan]/export [file][/bold cyan]      - Export workflow (.py zero-LLM script or .yaml)\n"
                        "[bold cyan]/resume [id|latest][/bold cyan] - Resume a saved session or list available\n"
                        "[bold cyan]/steps [number][/bold cyan]     - View or adjust max step budget per turn\n"
                        "[bold cyan]/perm [auto|manual][/bold cyan] - Toggle or display permission level\n"
                        "[bold cyan]/status[/bold cyan]              - Show session stats, targets, and viewport info\n"
                        "[bold cyan]/clear[/bold cyan]               - Clear multi-turn history while keeping targets\n"
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

            elif cmd in ("/target", "/targets"):
                sub_parts = arg.split(maxsplit=1)
                sub_cmd = sub_parts[0].lower() if sub_parts else ""
                sub_arg = sub_parts[1].strip() if len(sub_parts) > 1 else ""

                if not arg or sub_cmd == "list":
                    from rich.table import Table

                    table = Table(title="🪟 Session Multi-Window Target Pool")
                    table.add_column("No.", justify="right", style="dim")
                    table.add_column("Status", justify="center")
                    table.add_column("Target ID", style="bold cyan")
                    table.add_column("Title", style="green")
                    table.add_column("Resolution")
                    table.add_column("Process", style="dim")

                    for i, t in enumerate(session.targets, 1):
                        is_active = t.target_id == session.target.target_id
                        status_str = (
                            "[bold green]▶ ACTIVE[/bold green]"
                            if is_active
                            else "[dim]STANDBY[/dim]"
                        )
                        table.add_row(
                            str(i),
                            status_str,
                            t.target_id,
                            t.title[:30],
                            f"{t.rect.width}x{t.rect.height}",
                            t.process_name or "N/A",
                        )
                    console.print(table)
                    console.print(
                        "[dim]Commands: [cyan]/target <query>[/cyan], [cyan]/target switch <query>[/cyan], "
                        "[cyan]/target add <query>[/cyan], [cyan]/target rm <query>[/cyan][/dim]"
                    )

                elif sub_cmd == "add":
                    if not sub_arg:
                        console.print(
                            "[bold red]Usage: /target add <window_title_or_id>[/bold red]"
                        )
                    else:
                        new_t = resolve_target(capturer.list_targets(), sub_arg)
                        if new_t:
                            session.add_target(new_t)
                            console.print(
                                f"[bold green]✓ Target added to session pool: '{new_t.title}' ({new_t.target_id})[/bold green]"
                            )
                        else:
                            console.print(
                                f"[bold red]❌ Target '{sub_arg}' could not be resolved from open windows.[/bold red]"
                            )

                elif sub_cmd in ("rm", "remove", "delete"):
                    if not sub_arg:
                        console.print(
                            "[bold red]Usage: /target remove <window_title_or_id>[/bold red]"
                        )
                    else:
                        ok = session.remove_target(sub_arg)
                        if ok:
                            selected = session.target
                            console.print(
                                f"[bold green]✓ Target removed. Current active: '{session.target.title}'[/bold green]"
                            )
                        else:
                            console.print(
                                f"[bold red]❌ Cannot remove '{sub_arg}' (target not found or is the only window in session).[/bold red]"
                            )

                else:
                    query = sub_arg if sub_cmd == "switch" else arg
                    new_t = session.switch_target(query)
                    selected = session.target
                    capturer.focus(selected)
                    console.print(
                        f"[bold green]✓ Active target switched to: '{selected.title}' ({selected.target_id})[/bold green]"
                    )

            elif cmd == "/export":
                from pathlib import Path

                from agenteverywhereflow.workflow import WorkflowExporter

                wf_dir = Path.home() / ".aef" / "workflows"
                wf_dir.mkdir(parents=True, exist_ok=True)
                default_name = f"workflow_{session.session_id}"

                wf = WorkflowExporter.from_session(session, name=default_name)
                if not wf.steps:
                    console.print(
                        "[yellow]⚠️ No actions recorded in this session yet to export.[/yellow]"
                    )
                    continue

                if arg:
                    out_path = Path(arg.strip())
                else:
                    out_path = wf_dir / f"{default_name}.yaml"

                if out_path.suffix.lower() == ".py":
                    saved = WorkflowExporter.export_to_python(wf, out_path)
                    console.print(
                        f"[bold green]✓ Exported standalone zero-LLM Python script to:[/bold green] [cyan]{saved}[/cyan]"
                    )
                    console.print(f"[dim]Run directly with: [bold]python {saved}[/bold][/dim]")
                else:
                    saved = WorkflowExporter.export_to_yaml(wf, out_path)
                    console.print(
                        f"[bold green]✓ Exported declarative YAML workflow to:[/bold green] [cyan]{saved}[/cyan]"
                    )
                    console.print(f"[dim]Replay with: [bold]aef workflow play {saved}[/bold][/dim]")

            elif cmd in ("/clear", "/reset"):
                session.reset_history()
                console.print(
                    "[bold green]✓ Conversational history and visual memory reset.[/bold green]"
                )

            elif cmd == "/status":
                curr_steps = max_steps or config.max_steps
                targets_status_str = f"{len(session.targets)} bound window(s):\n" + "\n".join(
                    f"    - {t.title} [dim]({t.rect.width}x{t.rect.height}, {t.target_id}){' [ACTIVE]' if t.target_id == session.target.target_id else ''}[/dim]"
                    for t in session.targets
                )
                console.print(
                    Panel(
                        f"[bold]Session ID:[/bold] {session.session_id}\n"
                        f"[bold]Active Target:[/bold] {session.target.title} ({session.target.target_id})\n"
                        f"[bold]Resolution:[/bold] {session.target.rect.width}x{session.target.rect.height} at ({session.target.rect.x}, {session.target.rect.y})\n"
                        f"[bold]Targets Pool:[/bold] {targets_status_str}\n"
                        f"[bold]Process:[/bold] {session.target.process_name or 'N/A'}\n"
                        f"[bold]Turns Completed:[/bold] {session.turn_count} | [bold]Total Steps:[/bold] {session.total_steps}\n"
                        f"[bold]Recorded Replay Steps:[/bold] {len(session.recorded_steps)}\n"
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


def _check_port_available(host: str, port: int) -> tuple[bool, str]:
    """Check if (host, port) is free to bind. Returns (is_free, occupant_description)."""
    import socket

    test_host = "127.0.0.1" if host in ("0.0.0.0", "", "localhost") else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((test_host, port))
            return True, ""
        except OSError:
            pass

    occupant_info = ""
    try:
        import psutil

        for conn in psutil.net_connections(kind="inet"):
            if conn.laddr and conn.laddr.port == port and conn.status == psutil.CONN_LISTEN:
                if conn.pid:
                    try:
                        proc = psutil.Process(conn.pid)
                        occupant_info = f"PID {conn.pid} ({proc.name()})"
                    except Exception:
                        occupant_info = f"PID {conn.pid}"
                break
    except Exception:
        pass

    return False, occupant_info


@app.command(name="serve")
def serve(
    host: str = typer.Option(
        None, "--host", "-H", help="Bind host address (default: 127.0.0.1 or from config)"
    ),
    port: int = typer.Option(
        None, "--port", "-p", help="Bind port number (default: 8000 or from config)"
    ),
    open_browser: bool = typer.Option(
        False, "--open", help="Open WebUI Studio in default browser upon startup"
    ),
    reload: bool = typer.Option(False, "--reload", "-r", help="Enable auto-reload for development"),
) -> None:
    """Launch the AgentEverywhereFlow REST & WebSocket dialogue service daemon."""
    import threading
    import time
    import webbrowser

    import uvicorn

    from agenteverywhereflow.config import config
    from agenteverywhereflow.server.webui import is_webui_available

    bind_host = host or config.server_host
    bind_port = port or config.server_port

    is_free, occupant = _check_port_available(bind_host, bind_port)
    if not is_free:
        occ_str = f" by [bold cyan]{occupant}[/bold cyan]" if occupant else ""
        console.print(
            Panel(
                f"[bold red]❌ Port {bind_port} is already in use{occ_str}![/bold red]\n\n"
                f"[bold yellow]Common Causes & Solutions:[/bold yellow]\n"
                f"• A previous 'aef serve' daemon or another web server is already running on port {bind_port}.\n\n"
                f"[bold green]Option 1: Launch on a different port[/bold green]\n"
                f"   [cyan]aef serve --port {bind_port + 1}[/cyan]\n\n"
                f"[bold green]Option 2: Terminate the occupying process on Windows (PowerShell)[/bold green]\n"
                f"   [dim]# Find process:[/dim] [cyan]Get-NetTCPConnection -LocalPort {bind_port} | Select-Object LocalPort, OwningProcess, State[/cyan]\n"
                f"   [dim]# Stop by PID:[/dim] [cyan]Stop-Process -Id <PID> -Force[/cyan]\n"
                f"   [dim]# Or kill lingering python daemons:[/dim] [cyan]taskkill /F /IM python.exe[/cyan]\n\n"
                f"[bold green]Option 3: Terminate the occupying process on Linux / macOS[/bold green]\n"
                f"   [cyan]lsof -ti :{bind_port} | xargs kill -9[/cyan]",
                title="⚠️ Port Conflict (WinError 10048 / Address Already in Use)",
                border_style="red",
            )
        )
        raise typer.Exit(code=1)

    webui_line = (
        f"[bold cyan]🖥️ WebUI Console:[/bold cyan]  http://{bind_host}:{bind_port}\n"
        if is_webui_available()
        else ""
    )

    console.print(
        Panel(
            f"[bold green]🚀 AgentEverywhereFlow Dialogue Service Daemon[/bold green]\n\n"
            f"[bold cyan]🌐 Server Endpoint:[/bold cyan] http://{bind_host}:{bind_port}\n"
            f"{webui_line}"
            f"[bold cyan]📖 Swagger Docs:[/bold cyan]    http://{bind_host}:{bind_port}/docs\n"
            f"[bold cyan]🔌 REST Base:[/bold cyan]       http://{bind_host}:{bind_port}/api/v1\n"
            f"[bold cyan]⚡ WebSocket:[/bold cyan]       ws://{bind_host}:{bind_port}/api/v1/sessions/{{id}}/ws",
            title="🌐 AEFlow Daemon Online",
            border_style="green",
        )
    )

    if open_browser:

        def _open():
            time.sleep(0.8)
            try:
                webbrowser.open(f"http://{bind_host}:{bind_port}")
            except Exception:
                pass

        threading.Thread(target=_open, daemon=True).start()

    import os
    import signal

    def _sigint_handler(signum: int, frame: object) -> None:
        console.print("\n[dim]Server stopped by user (Ctrl+C).[/dim]")
        os._exit(0)

    try:
        signal.signal(signal.SIGINT, _sigint_handler)
    except Exception:
        pass

    try:
        uvicorn.run(
            "agenteverywhereflow.server.app:create_app",
            host=bind_host,
            port=bind_port,
            factory=True,
            reload=reload,
            timeout_graceful_shutdown=2,
        )
    except (KeyboardInterrupt, SystemExit):
        os._exit(0)
    except OSError as e:
        if "10048" in str(e) or "already in use" in str(e).lower():
            console.print(
                f"[bold red]❌ Error binding to {bind_host}:{bind_port}: Port already in use.[/bold red]\n"
                f"[yellow]Try specifying another port: [cyan]aef serve --port {bind_port + 1}[/cyan][/yellow]"
            )
            raise typer.Exit(code=1) from None
        raise


# ---------------------------------------------------------------------------
# WebUI Studio Commands (First-class UI Experience)
# ---------------------------------------------------------------------------
ui_app = typer.Typer(
    name="ui",
    help="Launch, install, or update the embedded WebUI Studio console.",
    invoke_without_command=True,
    add_completion=False,
    context_settings=CONTEXT_SETTINGS,
)
app.add_typer(ui_app, name="ui")


def _run_ui_server(
    host: str | None = None,
    port: int | None = None,
    open_browser: bool = True,
    update: bool = False,
) -> None:
    import threading
    import time
    import webbrowser

    import uvicorn

    from agenteverywhereflow.config import config
    from agenteverywhereflow.server.webui import (
        get_webui_dist_path,
        install_webui,
        is_webui_available,
    )

    if update or not is_webui_available():
        with console.status("[bold cyan]Preparing WebUI studio assets...[/bold cyan]"):
            try:
                install_webui(force=update)
                console.print("[bold green]✓ WebUI assets ready.[/bold green]")
            except Exception as e:
                console.print(f"[yellow]⚠️ WebUI auto-install note: {e}[/yellow]")

    bind_host = host or config.server_host
    base_port = port or config.server_port

    target_port = base_port
    if port is None:
        # Dynamically find next available port so user doesn't hit port collision
        for candidate in range(base_port, base_port + 20):
            is_free, _ = _check_port_available(bind_host, candidate)
            if is_free:
                target_port = candidate
                break
    else:
        is_free, occupant = _check_port_available(bind_host, target_port)
        if not is_free:
            occ_str = f" by [bold cyan]{occupant}[/bold cyan]" if occupant else ""
            console.print(
                f"[bold red]❌ Port {target_port} is already in use{occ_str}![/bold red]\n"
                f"[yellow]Try specifying another port: [cyan]aef ui --port {target_port + 1}[/cyan][/yellow]"
            )
            raise typer.Exit(code=1)

    url = f"http://{bind_host}:{target_port}"

    if open_browser:

        def _open():
            time.sleep(0.8)
            try:
                webbrowser.open(url)
            except Exception:
                pass

        threading.Thread(target=_open, daemon=True).start()

    dist_path = get_webui_dist_path()
    dist_info = (
        f"[dim]Assets:[/dim] [cyan]{dist_path}[/cyan]"
        if dist_path
        else "[dim]Assets: Default HTML Landing[/dim]"
    )

    console.print(
        Panel(
            f"[bold magenta]🖥️ AgentEverywhereFlow WebUI Studio[/bold magenta]\n\n"
            f"[bold cyan]🌐 WebUI Console:[/bold cyan]  [bold underline]{url}[/bold underline]\n"
            f"[bold cyan]📖 Swagger Docs:[/bold cyan]   http://{bind_host}:{target_port}/docs\n"
            f"[bold cyan]🔌 REST Base:[/bold cyan]      http://{bind_host}:{target_port}/api/v1\n"
            f"[bold cyan]⚡ WebSocket:[/bold cyan]      ws://{bind_host}:{target_port}/api/v1/sessions/{{id}}/ws\n"
            f"{dist_info}",
            title="✨ Studio Online",
            border_style="magenta",
        )
    )

    import os
    import signal

    def _sigint_handler(signum: int, frame: object) -> None:
        console.print("\n[dim]WebUI Studio stopped by user (Ctrl+C).[/dim]")
        os._exit(0)

    try:
        signal.signal(signal.SIGINT, _sigint_handler)
    except Exception:
        pass

    try:
        uvicorn.run(
            "agenteverywhereflow.server.app:create_app",
            host=bind_host,
            port=target_port,
            factory=True,
            timeout_graceful_shutdown=2,
        )
    except (KeyboardInterrupt, SystemExit):
        os._exit(0)
    except OSError as e:
        if "10048" in str(e) or "already in use" in str(e).lower():
            console.print(
                f"[bold red]❌ Error binding to {bind_host}:{target_port}: Port already in use.[/bold red]\n"
                f"[yellow]Try specifying another port: [cyan]aef ui --port {target_port + 1}[/cyan][/yellow]"
            )
            raise typer.Exit(code=1) from None
        raise


@ui_app.callback(invoke_without_command=True)
def ui_default(
    ctx: typer.Context,
    host: str | None = typer.Option(
        None, "--host", "-H", help="Bind host address (default: 127.0.0.1)"
    ),
    port: int | None = typer.Option(
        None,
        "--port",
        "-p",
        help="Bind port number (default: auto-detect free port starting from 8000)",
    ),
    open_browser: bool = typer.Option(True, "--open/--no-open", help="Automatically open browser"),
    update: bool = typer.Option(
        False, "--update", "-u", help="Check and update WebUI assets before launching"
    ),
) -> None:
    """Launch the WebUI Studio console in your browser (zero setup required)."""
    if ctx.invoked_subcommand is not None:
        return
    _run_ui_server(host=host, port=port, open_browser=open_browser, update=update)


@ui_app.command(name="open")
def ui_open(
    host: str | None = typer.Option(None, "--host", "-H", help="Bind host address"),
    port: int | None = typer.Option(None, "--port", "-p", help="Bind port number"),
    no_open: bool = typer.Option(False, "--no-open", help="Do not open browser"),
) -> None:
    """Open the WebUI Studio console."""
    _run_ui_server(host=host, port=port, open_browser=not no_open, update=False)


@ui_app.command(name="install")
def ui_install(
    version: str | None = typer.Option(
        None, "--version", "-v", help="Specific release version tag"
    ),
    force: bool = typer.Option(True, "--force", "-f", help="Force reinstall"),
) -> None:
    """Download and install the latest prebuilt WebUI static bundle."""
    from agenteverywhereflow.server.webui import install_webui

    with console.status(
        "[bold cyan]Downloading and installing prebuilt WebUI assets...[/bold cyan]"
    ):
        try:
            path = install_webui(version=version, force=force)
            console.print(
                f"[bold green]✓ WebUI assets installed successfully to:[/bold green] [cyan]{path}[/cyan]"
            )
            console.print("[dim]Run [bold]aef ui[/bold] to launch the studio console.[/dim]")
        except Exception as e:
            console.print(f"[bold red]❌ Failed to install WebUI assets:[/bold red] {e}")
            raise typer.Exit(code=1) from None


@ui_app.command(name="update")
def ui_update() -> None:
    """Update prebuilt WebUI static bundle to the latest release."""
    from agenteverywhereflow.server.webui import install_webui

    with console.status("[bold cyan]Updating WebUI assets to latest release...[/bold cyan]"):
        try:
            path = install_webui(force=True)
            console.print(
                f"[bold green]✓ WebUI assets updated successfully at:[/bold green] [cyan]{path}[/cyan]"
            )
        except Exception as e:
            console.print(f"[bold red]❌ Failed to update WebUI assets:[/bold red] {e}")
            raise typer.Exit(code=1) from None


@ui_app.command(name="status")
def ui_status() -> None:
    """Show WebUI installation status and asset path."""
    from agenteverywhereflow.server.webui import get_webui_status

    st = get_webui_status()
    installed = st["installed"]
    status_str = (
        "[bold green]Installed & Available[/bold green]"
        if installed
        else "[bold red]Not Installed[/bold red]"
    )
    embed_str = " (Package-Embedded)" if st.get("is_embedded") else " (User Directory)"

    console.print(
        Panel(
            f"[bold cyan]Status:[/bold cyan]       {status_str}{embed_str if installed else ''}\n"
            f"[bold cyan]Path:[/bold cyan]         {st.get('path') or 'N/A'}\n"
            f"[bold cyan]HTML Index:[/bold cyan]   {'✓ Yes' if st.get('has_index') else '❌ No'}\n"
            f"[bold cyan]Asset Files:[/bold cyan]  {st.get('asset_count', 0)} files",
            title="🖥️ WebUI Installation Status",
            border_style="cyan",
        )
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


# ---------------------------------------------------------------------------
# Workflow Commands (zero-LLM replay and script generation)
# ---------------------------------------------------------------------------
workflow_app = typer.Typer(
    name="workflow",
    help="Record, export, and replay automated GUI workflows without LLM tokens.",
)
app.add_typer(workflow_app, name="workflow")


@workflow_app.command(name="list")
def list_workflows() -> None:
    """List all saved workflows in ~/.aef/workflows/."""
    from datetime import datetime
    from pathlib import Path

    import yaml
    from rich.table import Table

    wf_dir = Path.home() / ".aef" / "workflows"
    if not wf_dir.exists():
        console.print("[dim]No saved workflows found in ~/.aef/workflows/[/dim]")
        return

    files = sorted(
        [f for f in wf_dir.iterdir() if f.is_file() and f.suffix in (".yaml", ".yml", ".py")],
        key=lambda x: x.stat().st_mtime,
        reverse=True,
    )
    if not files:
        console.print("[dim]No saved workflows found in ~/.aef/workflows/[/dim]")
        return

    table = Table(title="⚡ AEFlow Automated Workflows (~/.aef/workflows/)")
    table.add_column("Filename", style="bold cyan", no_wrap=True)
    table.add_column("Format", style="magenta")
    table.add_column("Workflow Name", style="green")
    table.add_column("Steps", justify="right")
    table.add_column("Targets", style="yellow")
    table.add_column("Last Modified", style="dim")

    for f in files:
        mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        fmt = f.suffix.lstrip(".").upper()
        wf_name = f.stem
        steps_count = "N/A"
        targets_str = "N/A"

        if f.suffix in (".yaml", ".yml"):
            try:
                with open(f, encoding="utf-8") as yf:
                    data = yaml.safe_load(yf)
                wf_name = data.get("name", f.stem)
                steps_count = str(len(data.get("steps", [])))
                tgts = [t.get("title", "") for t in data.get("targets", [])]
                targets_str = ", ".join(t[:15] for t in tgts) if tgts else "None"
            except Exception:
                pass
        elif f.suffix == ".py":
            steps_count = "Script"
            targets_str = "Python Replay"

        table.add_row(f.name, fmt, wf_name[:24], steps_count, targets_str[:30], mtime)

    console.print(table)
    console.print("[dim]Replay with: [bold cyan]aef workflow play <filename>[/bold cyan][/dim]")


@workflow_app.command(name="export")
def export_workflow_cmd(
    session_id: str = typer.Argument(..., help="Session ID to export"),
    output: str | None = typer.Option(
        None, "--output", "-o", help="Target output file path (.yaml or .py)"
    ),
    format: str = typer.Option(
        "yaml",
        "--format",
        "-f",
        help="Export format: 'yaml' (declarative) or 'py' (standalone script)",
    ),
) -> None:
    """Export a saved session trajectory to a standalone Python script or YAML workflow."""
    from pathlib import Path

    from agenteverywhereflow.session import session_manager
    from agenteverywhereflow.workflow import WorkflowExporter

    session = session_manager.get_session(session_id) or session_manager.restore_session(session_id)
    if not session:
        console.print(f"[bold red]❌ Session '{session_id}' not found.[/bold red]")
        raise typer.Exit(1)

    wf = WorkflowExporter.from_session(session)
    if not wf.steps:
        console.print(f"[yellow]⚠️ No action steps recorded in session '{session_id}'.[/yellow]")
        raise typer.Exit(0)

    wf_dir = Path.home() / ".aef" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)

    fmt = format.lower().strip()
    if output:
        out_path = Path(output)
    else:
        ext = ".py" if fmt == "py" else ".yaml"
        out_path = wf_dir / f"workflow_{session_id}{ext}"

    if out_path.suffix.lower() == ".py" or fmt == "py":
        saved = WorkflowExporter.export_to_python(wf, out_path)
        console.print(
            f"[bold green]✓ Standalone zero-LLM Python script exported to:[/bold green] [cyan]{saved.resolve()}[/cyan]"
        )
        console.print(f"[dim]Run directly with: [bold]python {saved}[/bold][/dim]")
    else:
        saved = WorkflowExporter.export_to_yaml(wf, out_path)
        console.print(
            f"[bold green]✓ Declarative YAML workflow exported to:[/bold green] [cyan]{saved.resolve()}[/cyan]"
        )
        console.print(f"[dim]Replay with: [bold]aef workflow play {saved}[/bold][/dim]")


@workflow_app.command(name="play")
def play_workflow_cmd(
    workflow: str = typer.Argument(
        ..., help="Path to workflow YAML file or name in ~/.aef/workflows/"
    ),
    speed: float = typer.Option(
        1.0, "--speed", "-s", help="Playback speed multiplier (e.g. 1.5, 2.0)"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Simulate replay without injecting actual input events"
    ),
) -> None:
    """Replay an automated workflow deterministically with coordinate projection."""
    from pathlib import Path

    from agenteverywhereflow.workflow import WorkflowRunner

    wf_path = Path(workflow)
    if not wf_path.exists():
        candidate = Path.home() / ".aef" / "workflows" / workflow
        if candidate.exists():
            wf_path = candidate
        elif candidate.with_suffix(".yaml").exists():
            wf_path = candidate.with_suffix(".yaml")
        elif candidate.with_suffix(".yml").exists():
            wf_path = candidate.with_suffix(".yml")

    if not wf_path.exists():
        console.print(f"[bold red]❌ Workflow file '{workflow}' not found.[/bold red]")
        raise typer.Exit(1)

    runner = WorkflowRunner(console=console)
    res = runner.play(wf_path, speed=speed, dry_run=dry_run)
    if not res.success:
        raise typer.Exit(1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
