"""Interactive ChatSession engine for multi-turn GUI agent dialogues."""

import base64
import io
import json
import logging
import re
import threading
import uuid
from collections.abc import Callable
from typing import Any

from PIL import Image
from pydantic import BaseModel

from agenteverywhereflow.agent.loop import extract_codeact_blocks
from agenteverywhereflow.agent.prompts import get_prompt_for_target
from agenteverywhereflow.capturer import get_capturer
from agenteverywhereflow.capturer.base import TargetInfo
from agenteverywhereflow.config import AppConfig, ExecutionMode, config
from agenteverywhereflow.engine import get_engine
from agenteverywhereflow.security.permission import (
    ApprovalDecision,
    ApprovalRequest,
    PermissionGate,
    PermissionMode,
)
from agenteverywhereflow.session.state import SessionEvent, SessionEventType, SessionState

logger = logging.getLogger(__name__)


class TurnResult(BaseModel):
    """Outcome of a single dialogue turn."""

    success: bool
    completed: bool
    response: str
    steps_executed: int
    error: str | None = None


class ChatSession:
    """Stateful multi-turn conversational session bound to a designated target window or screen."""

    def __init__(
        self,
        target: TargetInfo,
        session_id: str | None = None,
        mode: ExecutionMode = ExecutionMode.MINIMAL_PYTHON,
        permission_mode: PermissionMode = PermissionMode.AUTO,
        app_config: AppConfig | None = None,
        planner_func: Any | None = None,
        approval_timeout: float = 300.0,
    ) -> None:
        self.session_id = session_id or uuid.uuid4().hex[:10]
        self.target = target
        self.mode = mode
        self.config = app_config or config
        self.planner_func = planner_func
        self.approval_timeout = approval_timeout

        self.capturer = get_capturer()
        self.engine = get_engine(mode)
        self.permission_gate = PermissionGate(mode=permission_mode)

        self.state = SessionState.IDLE
        self.turn_count = 0
        self.total_steps = 0

        self.messages: list[dict[str, Any]] = []
        self._init_system_prompt()

        # Approval coordination
        self.pending_approval: ApprovalRequest | None = None
        self._approval_event = threading.Event()
        self._approval_decision: ApprovalDecision | None = None
        self._approval_lock = threading.Lock()

        # Event listeners for streaming / UI updates
        self.listeners: list[Callable[[SessionEvent], None]] = []
        self._client: Any = None

    def _init_system_prompt(self) -> None:
        """Initialize or refresh system prompt based on active target and execution mode."""
        is_minimal = self.mode == ExecutionMode.MINIMAL_PYTHON
        base_prompt = get_prompt_for_target(self.target, is_minimal_mode=is_minimal)
        dialogue_instruction = (
            "\nInteractive Dialogue Mode Active:\n"
            "- You are participating in a multi-turn conversation with the operator.\n"
            "- Focus solely on executing the user's latest instruction.\n"
            "- When you finish the current user instruction, conclude with 'TASK_COMPLETED: <summary>' "
            "so the operator can inspect the viewport and provide follow-up commands.\n"
        )
        sys_content = base_prompt + dialogue_instruction
        if not self.messages:
            self.messages.append({"role": "system", "content": sys_content})
        else:
            self.messages[0] = {"role": "system", "content": sys_content}

    @property
    def client(self) -> Any:
        """Lazily initialize LLM client."""
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(
                api_key=self.config.api_key,
                base_url=self.config.base_url,
            )
        return self._client

    def add_listener(self, listener: Callable[[SessionEvent], None]) -> None:
        """Register an event listener for session notifications."""
        self.listeners.append(listener)

    def remove_listener(self, listener: Callable[[SessionEvent], None]) -> None:
        """Remove a previously registered event listener."""
        if listener in self.listeners:
            self.listeners.remove(listener)

    def emit_event(
        self,
        event_type: SessionEventType,
        step: int = 0,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Broadcast session lifecycle event to all subscribed listeners."""
        event = SessionEvent(
            session_id=self.session_id,
            event_type=event_type,
            step=step,
            payload=payload or {},
        )
        for listener in list(self.listeners):
            try:
                listener(event)
            except Exception as e:
                logger.warning(f"Error notifying session listener: {e}")

    def set_permission_mode(self, mode: PermissionMode) -> None:
        """Update permission mode (auto or manual) dynamically."""
        self.permission_gate.mode = mode

    def switch_target(self, new_target: TargetInfo) -> None:
        """Dynamically switch current target window or display."""
        self.target = new_target
        self._init_system_prompt()
        self.messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"[Target Switch] Current active target has been switched to: {new_target}",
                    }
                ],
            }
        )

    def reset_history(self) -> None:
        """Reset conversation context while retaining system prompt and current target."""
        self.messages.clear()
        self._init_system_prompt()
        self.state = SessionState.IDLE
        self.turn_count = 0
        self.total_steps = 0

    def capture_frame(self) -> Image.Image:
        """Capture live viewport frame of the target."""
        self.capturer.focus(self.target)
        return self.capturer.capture(self.target)

    def _encode_image(self, img: Image.Image) -> str:
        """Encode PIL Image to base64 JPEG."""
        buffer = io.BytesIO()
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.save(buffer, format="JPEG", quality=85)
        return base64.b64encode(buffer.getvalue()).decode("utf-8")

    def _prune_visual_history(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Keep only the most recent N screenshots across conversational turns."""
        keep_n = self.config.max_visual_history_images
        img_indices: list[int] = []

        for i, msg in enumerate(messages):
            content = msg.get("content")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "image_url":
                        img_indices.append(i)
                        break

        if len(img_indices) > keep_n:
            prune_indices = set(img_indices[:-keep_n])
            for idx in prune_indices:
                new_content = []
                for part in messages[idx]["content"]:
                    if isinstance(part, dict) and part.get("type") == "image_url":
                        new_content.append(
                            {
                                "type": "text",
                                "text": "[Historical screenshot omitted to conserve context tokens]",
                            }
                        )
                    else:
                        new_content.append(part)
                messages[idx]["content"] = new_content
        return messages

    def wait_for_server_approval(self, request: ApprovalRequest) -> ApprovalDecision:
        """Block and wait for external approval via submit_approval() or timeout."""
        with self._approval_lock:
            self.pending_approval = request
            self._approval_decision = None
            self._approval_event.clear()
            self.state = SessionState.WAITING_APPROVAL

        self.emit_event(
            SessionEventType.APPROVAL_REQUIRED,
            step=request.step,
            payload=request.model_dump(),
        )

        ok = self._approval_event.wait(timeout=self.approval_timeout)
        with self._approval_lock:
            self.state = SessionState.RUNNING
            self.pending_approval = None
            if not ok or self._approval_decision is None:
                decision = ApprovalDecision(
                    approved=False,
                    reason=f"Approval request timed out after {self.approval_timeout}s.",
                )
            else:
                decision = self._approval_decision

        self.emit_event(
            SessionEventType.APPROVAL_RESOLVED,
            step=request.step,
            payload=decision.model_dump(),
        )
        return decision

    def submit_approval(self, decision: ApprovalDecision) -> bool:
        """Resolve a pending approval request (called by API or external controller)."""
        with self._approval_lock:
            if self.pending_approval is None:
                return False
            self._approval_decision = decision
            self._approval_event.set()
            return True

    def execute_turn(
        self,
        user_instruction: str,
        max_steps: int = 15,
    ) -> TurnResult:
        """Execute a conversational dialogue turn against the target."""
        self.turn_count += 1
        self.state = SessionState.RUNNING
        self.emit_event(
            SessionEventType.TURN_START,
            payload={"turn": self.turn_count, "instruction": user_instruction},
        )

        # Append user instruction to messages
        self.messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"User instruction: {user_instruction}",
                    }
                ],
            }
        )

        is_minimal = self.mode == ExecutionMode.MINIMAL_PYTHON
        turn_steps = 0

        while turn_steps < max_steps:
            turn_steps += 1
            self.total_steps += 1

            self.emit_event(
                SessionEventType.STEP_FINISHED,
                step=turn_steps,
                payload={"status": "step_start", "total_steps": self.total_steps},
            )

            # 1. Bring target window into focus
            self.capturer.focus(self.target)

            # 2. Capture screenshot
            try:
                img = self.capturer.capture(self.target)
                img_b64 = self._encode_image(img)
            except Exception as e:
                err_msg = f"Failed to capture target viewport: {e}"
                self.state = SessionState.ERROR
                self.emit_event(SessionEventType.ERROR, step=turn_steps, payload={"error": err_msg})
                return TurnResult(
                    success=False,
                    completed=False,
                    response="",
                    steps_executed=turn_steps,
                    error=err_msg,
                )

            # 3. Add visual observation
            step_message = {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"Current screenshot of {self.target.title} ({img.width}x{img.height}). What is your next action?",
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{img_b64}"},
                    },
                ],
            }
            self.messages.append(step_message)
            self.messages = self._prune_visual_history(self.messages)

            self.emit_event(
                SessionEventType.OBSERVE,
                step=turn_steps,
                payload={"width": img.width, "height": img.height},
            )

            # 4. Reason with LLM or custom planner
            if self.planner_func:
                try:
                    assistant_text = self.planner_func(self.messages, self.target, turn_steps)
                except Exception as e:
                    err_msg = f"Planner Error: {e}"
                    self.state = SessionState.ERROR
                    return TurnResult(
                        success=False,
                        completed=False,
                        response="",
                        steps_executed=turn_steps,
                        error=err_msg,
                    )
            else:
                try:
                    response = self.client.chat.completions.create(
                        model=self.config.model_name,
                        messages=self.messages,  # type: ignore[arg-type]
                        temperature=0.2,
                    )
                    assistant_text = response.choices[0].message.content or ""
                except Exception as e:
                    err_msg = f"LLM API Error: {e}"
                    self.state = SessionState.ERROR
                    self.emit_event(
                        SessionEventType.ERROR, step=turn_steps, payload={"error": err_msg}
                    )
                    return TurnResult(
                        success=False,
                        completed=False,
                        response="",
                        steps_executed=turn_steps,
                        error=err_msg,
                    )

            self.messages.append({"role": "assistant", "content": assistant_text})
            self.emit_event(
                SessionEventType.REASONING,
                step=turn_steps,
                payload={"content": assistant_text},
            )

            # 5. Extract and Validate Actions
            executed_action = False
            action_success = False

            if is_minimal:
                code_to_exec = extract_codeact_blocks(assistant_text)
                if code_to_exec:
                    self.emit_event(
                        SessionEventType.ACTION_PROPOSED,
                        step=turn_steps,
                        payload={"type": "codeact", "code": code_to_exec},
                    )

                    # Check permission gate
                    req = ApprovalRequest(
                        session_id=self.session_id,
                        step=turn_steps,
                        action_type="codeact",
                        description=f"Execute Python CodeAct block on target '{self.target.title}'",
                        code_snippet=code_to_exec,
                    )
                    decision = self.permission_gate.request_approval(req)

                    if not decision.approved:
                        # Rejection feedback
                        rej_feedback = (
                            f"Action execution REJECTED by operator: {decision.reason or 'User denied permission'}. "
                            f"Please acknowledge this rejection and choose an alternative action or explain your plan."
                        )
                        self.messages.append(
                            {
                                "role": "user",
                                "content": [{"type": "text", "text": rej_feedback}],
                            }
                        )
                        self.emit_event(
                            SessionEventType.ACTION_EXECUTED,
                            step=turn_steps,
                            payload={
                                "approved": False,
                                "rejected": True,
                                "reason": decision.reason,
                            },
                        )
                        continue

                    # Executed with permission
                    result = self.engine.execute(code_to_exec, self.target)
                    executed_action = True
                    action_success = result.success

                    self.emit_event(
                        SessionEventType.ACTION_EXECUTED,
                        step=turn_steps,
                        payload={
                            "type": "codeact",
                            "success": result.success,
                            "output": result.output,
                            "error": result.error,
                        },
                    )

                    feedback = (
                        f"Action execution result: success={result.success}\n"
                        f"Output: {result.output.strip() or 'None'}\n"
                        f"Error: {result.error or 'None'}"
                    )
                    self.messages.append(
                        {"role": "user", "content": [{"type": "text", "text": feedback}]}
                    )

            else:
                # Guarded JSON mode
                json_match = re.search(r"```(?:json)?\s*(.*?)\s*```", assistant_text, re.DOTALL)
                payload_data: dict[str, Any] | None = None
                if json_match:
                    try:
                        payload_data = json.loads(json_match.group(1).strip())
                    except Exception:
                        payload_data = None

                if payload_data:
                    action_name = payload_data.get("action", "")
                    if action_name == "finish":
                        msg = payload_data.get("message", "Task finished.")
                        self.state = SessionState.WAITING_INPUT
                        self.emit_event(
                            SessionEventType.TASK_COMPLETED,
                            step=turn_steps,
                            payload={"summary": msg},
                        )
                        return TurnResult(
                            success=True,
                            completed=True,
                            response=msg,
                            steps_executed=turn_steps,
                        )

                    self.emit_event(
                        SessionEventType.ACTION_PROPOSED,
                        step=turn_steps,
                        payload={"type": "guarded", "payload": payload_data},
                    )

                    req = ApprovalRequest(
                        session_id=self.session_id,
                        step=turn_steps,
                        action_type=action_name or "guarded",
                        details=payload_data,
                        description=f"Execute guarded action '{action_name}' on '{self.target.title}'",
                        code_snippet=json.dumps(payload_data, indent=2, ensure_ascii=False),
                    )
                    decision = self.permission_gate.request_approval(req)

                    if not decision.approved:
                        rej_feedback = (
                            f"Action execution REJECTED by operator: {decision.reason or 'User denied permission'}. "
                            f"Please provide an alternative action."
                        )
                        self.messages.append(
                            {
                                "role": "user",
                                "content": [{"type": "text", "text": rej_feedback}],
                            }
                        )
                        self.emit_event(
                            SessionEventType.ACTION_EXECUTED,
                            step=turn_steps,
                            payload={
                                "approved": False,
                                "rejected": True,
                                "reason": decision.reason,
                            },
                        )
                        continue

                    result = self.engine.execute(payload_data, self.target)
                    executed_action = True
                    action_success = result.success

                    self.emit_event(
                        SessionEventType.ACTION_EXECUTED,
                        step=turn_steps,
                        payload={
                            "type": "guarded",
                            "success": result.success,
                            "output": result.output,
                            "error": result.error,
                        },
                    )

                    feedback = (
                        f"Action execution result: success={result.success}\n"
                        f"Output: {result.output.strip() or 'None'}\n"
                        f"Error: {result.error or 'None'}"
                    )
                    self.messages.append(
                        {"role": "user", "content": [{"type": "text", "text": feedback}]}
                    )

            # 6. Check Completion
            if "TASK_COMPLETED" in assistant_text:
                if not executed_action or action_success:
                    summary = assistant_text.split("TASK_COMPLETED")[-1].strip(": \n")
                    self.state = SessionState.WAITING_INPUT
                    self.emit_event(
                        SessionEventType.TASK_COMPLETED,
                        step=turn_steps,
                        payload={"summary": summary or "Instruction completed."},
                    )
                    return TurnResult(
                        success=True,
                        completed=True,
                        response=summary or "Instruction completed successfully.",
                        steps_executed=turn_steps,
                    )

            if not executed_action and "TASK_COMPLETED" not in assistant_text:
                self.messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "No executable code block or TASK_COMPLETED was found. Please provide an executable action or conclude with TASK_COMPLETED.",
                            }
                        ],
                    }
                )

        self.state = SessionState.WAITING_INPUT
        return TurnResult(
            success=True,
            completed=False,
            response="Step limit reached for this turn.",
            steps_executed=turn_steps,
        )
