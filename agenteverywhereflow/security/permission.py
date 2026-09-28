"""Permission management and human-in-the-loop security gate.

Provides dual-tier permission policies:
- AUTO: Full autonomous action execution.
- MANUAL: Manual operator approval required for every action before execution.
"""

import time
import uuid
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from agenteverywhereflow.capturer.base import TargetInfo
from agenteverywhereflow.config import PermissionMode


class ApprovalRequest(BaseModel):
    """Metadata describing a pending action requiring approval."""

    request_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    session_id: str = Field(default="")
    step: int = Field(default=1)
    action_type: str = Field(description="Action type: codeact, click, type, press, etc.")
    details: dict[str, Any] = Field(default_factory=dict)
    description: str = Field(description="Human readable explanation of the planned action")
    code_snippet: str | None = Field(default=None, description="Code or payload snippet")
    created_at: float = Field(default_factory=time.time)


class ApprovalDecision(BaseModel):
    """The outcome of an approval evaluation."""

    approved: bool
    reason: str | None = None


class PermissionGate:
    """Evaluates planned actions against current security policy and operator approvals."""

    def __init__(
        self,
        mode: PermissionMode = PermissionMode.AUTO,
        handler: Callable[[ApprovalRequest], ApprovalDecision] | None = None,
    ) -> None:
        self.mode = mode
        self._handler = handler

    def set_handler(self, handler: Callable[[ApprovalRequest], ApprovalDecision] | None) -> None:
        """Register a custom handler for interactive approval requests."""
        self._handler = handler

    def check_viewport_bounds(self, target: TargetInfo, x: float, y: float) -> bool:
        """Verify if coordinates reside strictly within the target viewport boundaries."""
        if target.rect.width <= 0 or target.rect.height <= 0:
            return True
        return 0 <= x <= target.rect.width and 0 <= y <= target.rect.height

    def request_approval(self, request: ApprovalRequest) -> ApprovalDecision:
        """Evaluate approval for the requested action based on active permission mode."""
        if self.mode == PermissionMode.AUTO:
            return ApprovalDecision(approved=True)

        # MANUAL mode: delegate to handler if configured
        if self._handler is not None:
            return self._handler(request)

        # Fallback to default terminal confirmation if no handler was explicitly set
        return self._default_cli_handler(request)

    def _default_cli_handler(self, request: ApprovalRequest) -> ApprovalDecision:
        """Default interactive CLI confirmation prompt via rich."""
        import sys

        from rich.console import Console
        from rich.panel import Panel
        from rich.prompt import Prompt

        console = Console(file=sys.__stdout__ or sys.stdout)

        content = f"[bold cyan]Action:[/bold cyan] {request.action_type}\n[bold cyan]Details:[/bold cyan] {request.description}"
        if request.code_snippet:
            content += (
                f"\n\n[bold yellow]Code / Payload:[/bold yellow]\n{request.code_snippet.strip()}"
            )

        console.print(
            Panel(
                content,
                title="🛡️ [Permission Required] Operator Approval Gate",
                border_style="bold yellow",
            )
        )

        choice = Prompt.ask(
            "  [bold yellow]Allow execution?[/bold yellow] ([green]y[/green]=approve / [red]n[/red]=reject / [cyan]custom feedback[/cyan])",
            default="y",
        ).strip()

        if choice.lower() in ("y", "yes", "true", "1"):
            return ApprovalDecision(approved=True)

        if choice.lower() in ("n", "no", "false", "0"):
            return ApprovalDecision(
                approved=False,
                reason="Action rejected by operator.",
            )

        # Operator provided custom corrective guidance
        return ApprovalDecision(
            approved=False,
            reason=f"Operator rejected action with guidance: {choice}",
        )
