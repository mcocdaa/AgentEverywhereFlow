"""Screen and window selector (simulating video conference screen-sharing picker)."""

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from agenteverywhereflow.capturer import get_capturer
from agenteverywhereflow.capturer.base import TargetInfo, TargetType


class TargetSelector:
    """Provides interactive selection for displays and application windows."""

    def __init__(self) -> None:
        self.capturer = get_capturer()
        self.console = Console()

    def list_all(self) -> list[TargetInfo]:
        """Fetch all available targets."""
        return self.capturer.list_targets()

    def display_selection_menu(self, targets: list[TargetInfo]) -> None:
        """Render a rich table categorized like Zoom/Tencent Meeting share picker."""
        table = Table(title="🎯 Agent Everywhere - Select Target to Summon", show_lines=True)
        table.add_column("No.", justify="center", style="cyan", no_wrap=True)
        table.add_column("Type", justify="center", style="magenta")
        table.add_column("Title / Window Name", style="bold green")
        table.add_column("Process", style="yellow")
        table.add_column("Resolution / Bounds", style="white")

        displays = [t for t in targets if t.target_type == TargetType.DISPLAY]
        windows = [t for t in targets if t.target_type == TargetType.WINDOW]

        current_idx = 1

        for d in displays:
            table.add_row(
                str(current_idx),
                "🖥️ Display",
                d.title,
                "-",
                f"{d.rect.width}x{d.rect.height} (at {d.rect.x},{d.rect.y})",
            )
            current_idx += 1

        for w in windows:
            proc = w.process_name or "-"
            table.add_row(
                str(current_idx),
                "🪟 Window",
                w.title[:45] + ("..." if len(w.title) > 45 else ""),
                proc,
                f"{d.rect.width}x{d.rect.height}" if False else f"{w.rect.width}x{w.rect.height}",
            )
            current_idx += 1

        self.console.print(table)

    def interactive_select(self) -> TargetInfo | None:
        """Prompt user in terminal to pick a display or window target."""
        targets = self.list_all()
        if not targets:
            self.console.print(Panel("[red]No displays or windows detected![/red]", title="Error"))
            return None

        self.display_selection_menu(targets)

        while True:
            choice = self.console.input(
                f"[bold yellow]👉 Select target [1-{len(targets)}] (or 'q' to quit): [/bold yellow]"
            ).strip()

            if choice.lower() == "q":
                return None

            if choice.isdigit():
                idx = int(choice)
                if 1 <= idx <= len(targets):
                    selected = targets[idx - 1]
                    self.console.print(f"[bold green]✓ Target selected:[/bold green] {selected}")
                    return selected

            self.console.print(
                f"[red]Invalid input. Please enter a number between 1 and {len(targets)}.[/red]"
            )
