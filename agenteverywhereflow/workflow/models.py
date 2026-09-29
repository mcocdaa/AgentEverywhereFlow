"""Data models for recorded GUI workflows and steps."""

import time
from typing import Any

from pydantic import BaseModel, Field


class WorkflowTarget(BaseModel):
    """Metadata of an application window or display involved in a workflow."""

    target_id: str = Field(description="Captured Target ID, e.g. hwnd or display index")
    title: str = Field(description="Window or display title at recording time")
    process_name: str | None = Field(default=None, description="Executable process name")
    target_type: str = Field(default="window", description="'window' or 'display'")
    rect: dict[str, int] = Field(
        default_factory=lambda: {"x": 0, "y": 0, "width": 800, "height": 600},
        description="Viewport rectangle at recording time",
    )


class WorkflowStep(BaseModel):
    """A discrete recorded GUI interaction step."""

    step_number: int = Field(description="Sequential step index (1-based)")
    action: str = Field(
        description="Action name: click, double_click, right_click, type, press, hotkey, scroll, wait, switch_target, codeact"
    )
    target: str | None = Field(
        default=None, description="Target window query or ID to focus before executing"
    )
    params: dict[str, Any] = Field(
        default_factory=dict, description="Action parameters (e.g. x, y, text, key, keys, amount)"
    )
    code: str | None = Field(
        default=None, description="Original Python CodeAct block if recorded in minimal mode"
    )
    description: str = Field(default="", description="Human-readable description of this step")


class WorkflowDefinition(BaseModel):
    """Complete declarative definition of an automated GUI workflow."""

    name: str = Field(description="Workflow identifier name")
    description: str = Field(default="", description="Workflow documentation or intent")
    version: str = Field(default="1.0", description="Workflow schema version")
    created_at: float = Field(default_factory=time.time, description="Unix timestamp of creation")
    targets: list[WorkflowTarget] = Field(
        default_factory=list, description="All application windows used in this workflow"
    )
    steps: list[WorkflowStep] = Field(
        default_factory=list, description="Ordered sequence of executed action steps"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Custom metadata, e.g. source session_id, model, token stats",
    )
