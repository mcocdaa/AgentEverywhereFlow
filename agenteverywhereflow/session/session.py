"""Interactive ChatSession engine for multi-turn GUI agent dialogues."""

import base64
import io
import json
import logging
import re
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PIL import Image
from pydantic import BaseModel

from agenteverywhereflow.agent.loop import extract_codeact_blocks
from agenteverywhereflow.agent.prompts import get_prompt_for_targets
from agenteverywhereflow.capturer import get_capturer
from agenteverywhereflow.capturer.base import Rect, TargetInfo, TargetType
from agenteverywhereflow.config import AppConfig, ExecutionMode, config
from agenteverywhereflow.engine import get_engine
from agenteverywhereflow.security.permission import (
    ApprovalDecision,
    ApprovalRequest,
    PermissionGate,
    PermissionMode,
)
from agenteverywhereflow.session.state import SessionEvent, SessionEventType, SessionState
from agenteverywhereflow.session.storage import TokenUsageStats, session_storage

logger = logging.getLogger(__name__)


class TurnResult(BaseModel):
    """Outcome of a single dialogue turn."""

    success: bool
    completed: bool
    response: str
    steps_executed: int
    error: str | None = None


class ChatSession:
    """Stateful multi-turn conversational session bound to designated target window(s) or screen."""

    def __init__(
        self,
        target: TargetInfo | None = None,
        targets: list[TargetInfo] | None = None,
        session_id: str | None = None,
        mode: ExecutionMode = ExecutionMode.MINIMAL_PYTHON,
        permission_mode: PermissionMode = PermissionMode.AUTO,
        app_config: AppConfig | None = None,
        planner_func: Any | None = None,
        approval_timeout: float = 300.0,
    ) -> None:
        self.session_id = session_id or uuid.uuid4().hex[:10]
        if targets:
            self.targets = list(targets)
            self.target = target or self.targets[0]
        elif target:
            self.target = target
            self.targets = [target]
        else:
            raise ValueError("Either target or targets must be specified.")

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
        self.created_at = time.time()
        self.token_usage = TokenUsageStats()

        self.messages: list[dict[str, Any]] = []
        self.recorded_steps: list[dict[str, Any]] = []
        self._init_system_prompt()

        # Approval coordination
        self.pending_approval: ApprovalRequest | None = None
        self._approval_event = threading.Event()
        self._approval_decision: ApprovalDecision | None = None
        self._approval_lock = threading.Lock()
        self._abort_requested = threading.Event()

        # Event listeners for streaming / UI updates
        self.listeners: list[Callable[[SessionEvent], None]] = []
        self._client: Any = None

    def _init_system_prompt(self) -> None:
        """Initialize or refresh system prompt based on active target(s) and execution mode."""
        is_minimal = self.mode == ExecutionMode.MINIMAL_PYTHON
        base_prompt = get_prompt_for_targets(
            self.targets, active_target=self.target, is_minimal_mode=is_minimal
        )
        dialogue_instruction = (
            "\nInteractive Dialogue Mode Active:\n"
            "- You are participating in a multi-turn conversation with the operator.\n"
            "- Focus solely on executing the user's latest instruction.\n"
            "- TASK_COMPLETED is the terminal signal: emit 'TASK_COMPLETED: <summary_or_answer>' ONLY when the entire user instruction has been 100% fulfilled.\n"
            "- Never emit TASK_COMPLETED during intermediate action steps or if you still need to observe the UI response.\n"
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

    def add_target(self, new_target: TargetInfo) -> None:
        """Add a new target window or display to the multi-window session pool."""
        for t in self.targets:
            if t.target_id == new_target.target_id:
                return
        self.targets.append(new_target)
        self._init_system_prompt()
        self.messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"[Target Added] Added target to multi-window session: '{new_target.title}' (ID: {new_target.target_id})",
                    }
                ],
            }
        )

    def remove_target(self, target_id_or_title: str) -> bool:
        """Remove a target from the session pool."""
        query = target_id_or_title.strip().lower()
        found = None
        for t in self.targets:
            if query == t.target_id.lower() or query in t.title.lower():
                found = t
                break
        if not found or len(self.targets) <= 1:
            return False
        self.targets.remove(found)
        if self.target.target_id == found.target_id:
            self.target = self.targets[0]
        self._init_system_prompt()
        return True

    def switch_target(self, target_or_query: TargetInfo | str) -> TargetInfo:
        """Dynamically switch current active target window or display."""
        if isinstance(target_or_query, TargetInfo):
            new_target: TargetInfo = target_or_query
            if not any(t.target_id == new_target.target_id for t in self.targets):
                self.targets.append(new_target)
        else:
            query = str(target_or_query).strip().lower()
            found_target: TargetInfo | None = None
            for t in self.targets:
                if (
                    query == t.target_id.lower()
                    or query in t.title.lower()
                    or query in (t.process_name or "").lower()
                ):
                    found_target = t
                    break
            if not found_target:
                # Resolve from OS if not found in current pool
                all_os = self.capturer.list_targets()
                for t in all_os:
                    if (
                        query == t.target_id.lower()
                        or query in t.title.lower()
                        or query in (t.process_name or "").lower()
                    ):
                        found_target = t
                        self.targets.append(t)
                        break
            if not found_target:
                return self.target
            new_target = found_target

        self.target = new_target
        self._init_system_prompt()
        self.messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"[Target Switch] Current active target has been switched to: '{new_target.title}' (ID: {new_target.target_id})",
                    }
                ],
            }
        )
        return self.target

    def _on_engine_switch_target(self, tgt: TargetInfo) -> None:
        """Engine callback when agent code calls switch_to()."""
        if self.target.target_id != tgt.target_id:
            self.target = tgt
            if not any(t.target_id == tgt.target_id for t in self.targets):
                self.targets.append(tgt)
            self._init_system_prompt()

    def reset_history(self) -> None:
        """Reset conversation context while retaining system prompt and current target."""
        self.messages.clear()
        self.recorded_steps.clear()
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

    def freeze_completed_turn(self) -> None:
        """Freeze and compact screenshots of completed turns into immutable text markers.

        In Prefix Caching architectures (DeepSeek Context Caching, OpenAI Prompt Caching,
        vLLM, SGLang), prompt tokens must have an exact byte-for-byte prefix match from token 0.
        By freezing past turns at turn boundaries rather than mutating messages in-flight,
        the historical conversation prefix remains 100% stable and achieves near-perfect KV cache reuse.
        """
        for i in range(len(self.messages)):
            content = self.messages[i].get("content")
            if isinstance(content, list):
                new_content = []
                has_img = False
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "image_url":
                        has_img = True
                        new_content.append(
                            {
                                "type": "text",
                                "text": "[Historical viewport observation screenshot frozen in KV cache]",
                            }
                        )
                    else:
                        new_content.append(part)
                if has_img:
                    self.messages[i]["content"] = new_content

    def save(self) -> Path:
        """Persist current session metadata and trajectory to disk."""
        return session_storage.save_session(
            session_id=self.session_id,
            target=self.target,
            targets=self.targets,
            mode=self.mode,
            permission_mode=self.permission_gate.mode,
            state=self.state.value,
            turn_count=self.turn_count,
            total_steps=self.total_steps,
            messages=self.messages,
            token_usage=self.token_usage,
            created_at=self.created_at,
            recorded_steps=self.recorded_steps,
        )

    @classmethod
    def restore(cls, session_id: str, app_config: AppConfig | None = None) -> "ChatSession | None":
        """Restore a previously saved session from disk storage."""
        meta = session_storage.load_metadata(session_id)
        if not meta:
            return None
        raw_messages = session_storage.load_messages(session_id)

        targets_list: list[TargetInfo] = []
        if getattr(meta, "targets", None):
            for t_dict in meta.targets:
                r_dict = t_dict.get("rect", {})
                rect = Rect(
                    x=r_dict.get("x", 0),
                    y=r_dict.get("y", 0),
                    width=r_dict.get("width", 800),
                    height=r_dict.get("height", 600),
                )
                targets_list.append(
                    TargetInfo(
                        target_id=t_dict.get("target_id", ""),
                        target_type=TargetType(t_dict.get("target_type", "window")),
                        title=t_dict.get("title", ""),
                        process_name=t_dict.get("process_name", ""),
                        rect=rect,
                        native_handle=t_dict.get("native_handle", 0),
                    )
                )

        if not targets_list:
            rect = Rect(
                x=meta.target_rect.get("x", 0),
                y=meta.target_rect.get("y", 0),
                width=meta.target_rect.get("width", 800),
                height=meta.target_rect.get("height", 600),
            )
            target = TargetInfo(
                target_id=meta.target_id,
                target_type=TargetType(meta.target_type),
                title=meta.target_title,
                process_name=meta.process_name,
                rect=rect,
                native_handle=meta.native_handle,
            )
            targets_list = [target]

        active_target = None
        for t in targets_list:
            if t.target_id == meta.target_id:
                active_target = t
                break
        if not active_target:
            active_target = targets_list[0]

        session = cls(
            target=active_target,
            targets=targets_list,
            session_id=meta.session_id,
            mode=ExecutionMode(meta.mode),
            permission_mode=PermissionMode(meta.permission_mode),
            app_config=app_config,
        )
        session.turn_count = meta.turn_count
        session.total_steps = meta.total_steps
        session.token_usage = meta.token_usage
        session.created_at = meta.created_at
        session.state = SessionState(meta.state)
        if raw_messages:
            session.messages = raw_messages
        session.recorded_steps = session_storage.load_recorded_steps(session_id)
        return session

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

    def abort(self) -> bool:
        """Signal the running turn to immediately abort execution."""
        self._abort_requested.set()
        with self._approval_lock:
            if self.pending_approval is not None:
                self._approval_decision = ApprovalDecision(
                    approved=False, reason="Aborted by operator"
                )
                self._approval_event.set()
        return True

    def execute_turn(
        self,
        user_instruction: str,
        max_steps: int | None = None,
    ) -> TurnResult:
        """Execute a conversational dialogue turn against the target."""
        self._abort_requested.clear()
        effective_max_steps = max_steps if max_steps is not None else self.config.max_steps
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

        while turn_steps < effective_max_steps:
            if self._abort_requested.is_set():
                logger.info(
                    "Session %s turn %d aborted by operator before step %d.",
                    self.session_id,
                    self.turn_count,
                    turn_steps + 1,
                )
                self.state = SessionState.IDLE
                self.emit_event(
                    SessionEventType.ABORTED,
                    step=turn_steps,
                    payload={"message": "Execution aborted by operator."},
                )
                return TurnResult(
                    success=False,
                    completed=False,
                    response="Execution aborted by operator.",
                    steps_executed=turn_steps,
                    error="Execution aborted by operator.",
                )

            turn_steps += 1
            self.total_steps += 1

            self.emit_event(
                SessionEventType.STEP_FINISHED,
                step=turn_steps,
                payload={"status": "step_start", "total_steps": self.total_steps},
            )

            # 1. Bring target window into focus
            self.capturer.focus(self.target)

            # 2. Capture screenshot(s) and build visual observation
            if len(self.targets) <= 1:
                try:
                    img = self.capturer.capture(self.target)
                    img_b64 = self._encode_image(img)
                except Exception as e:
                    err_msg = f"Failed to capture target viewport: {e}"
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
                obs_width, obs_height = img.width, img.height
            else:
                obs_content: list[dict[str, Any]] = []
                last_img = None
                for t in self.targets:
                    is_active = t.target_id == self.target.target_id
                    active_tag = " [ACTIVE FOCUS]" if is_active else ""
                    try:
                        t_img = self.capturer.capture(t)
                        last_img = t_img
                        t_b64 = self._encode_image(t_img)
                        obs_content.append(
                            {
                                "type": "text",
                                "text": f"Screenshot of target '{t.title}' (ID: {t.target_id}, Resolution: {t_img.width}x{t_img.height}){active_tag}:",
                            }
                        )
                        obs_content.append(
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{t_b64}"},
                            }
                        )
                    except Exception as e:
                        obs_content.append(
                            {
                                "type": "text",
                                "text": f"[Failed to capture target '{t.title}': {e}]",
                            }
                        )
                obs_content.append(
                    {
                        "type": "text",
                        "text": f"Active target is '{self.target.title}' (ID: {self.target.target_id}). What is your next action?",
                    }
                )
                step_message = {
                    "role": "user",
                    "content": obs_content,
                }
                obs_width = last_img.width if last_img else self.target.rect.width
                obs_height = last_img.height if last_img else self.target.rect.height

            self.messages.append(step_message)
            self.messages = self._prune_visual_history(self.messages)

            self.emit_event(
                SessionEventType.OBSERVE,
                step=turn_steps,
                payload={"width": obs_width, "height": obs_height},
            )

            # 4. Reason with LLM or custom planner
            raw_thinking = ""
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
                    choice = response.choices[0]
                    assistant_text = choice.message.content or ""
                    raw_thinking = (
                        getattr(choice.message, "reasoning_content", None)
                        or getattr(choice.message, "reasoning", None)
                        or ""
                    )

                    # Extract Token usage and KV cache hit metrics
                    usage = getattr(response, "usage", None)
                    if usage:
                        p_tok = getattr(usage, "prompt_tokens", 0)
                        c_tok = getattr(usage, "completion_tokens", 0)
                        # DeepSeek Context Caching hit tokens:
                        cached_tok = getattr(usage, "prompt_cache_hit_tokens", 0)
                        if not cached_tok:
                            # OpenAI / vLLM cached prompt tokens:
                            ptd = getattr(usage, "prompt_tokens_details", None)
                            if ptd:
                                cached_tok = getattr(ptd, "cached_tokens", 0)

                        self.token_usage.prompt_tokens += p_tok
                        self.token_usage.completion_tokens += c_tok
                        self.token_usage.total_tokens += p_tok + c_tok
                        self.token_usage.cached_prompt_tokens += cached_tok
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

            # Determine structured reasoning / CoT
            thinking_text = ""
            if raw_thinking:
                thinking_text = str(raw_thinking).strip()
            elif "<think>" in assistant_text and "</think>" in assistant_text:
                think_match = re.search(r"<think>(.*?)</think>", assistant_text, re.DOTALL)
                if think_match:
                    thinking_text = think_match.group(1).strip()

            if not thinking_text:
                if is_minimal:
                    parts = re.split(r"```(?:python)?\s*.*?\s*```", assistant_text, flags=re.DOTALL)
                    outside = "\n".join(p.strip() for p in parts if p.strip()).strip()
                    thinking_text = outside or assistant_text
                else:
                    parts = re.split(r"```(?:json)?\s*.*?\s*```", assistant_text, flags=re.DOTALL)
                    outside = "\n".join(p.strip() for p in parts if p.strip()).strip()
                    thinking_text = outside or assistant_text

            self.messages.append({"role": "assistant", "content": assistant_text})
            self.emit_event(
                SessionEventType.REASONING,
                step=turn_steps,
                payload={"thinking": thinking_text, "content": assistant_text},
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
                    result = self.engine.execute(
                        code_to_exec,
                        self.target,
                        targets=self.targets,
                        on_switch_target=self._on_engine_switch_target,
                    )
                    executed_action = True
                    action_success = result.success
                    if result.success:
                        self.recorded_steps.append(
                            {
                                "turn": self.turn_count,
                                "step": turn_steps,
                                "total_step": self.total_steps,
                                "mode": "minimal",
                                "code": code_to_exec,
                                "target_id": self.target.target_id,
                                "target_title": self.target.title,
                            }
                        )

                    self.emit_event(
                        SessionEventType.ACTION_EXECUTED,
                        step=turn_steps,
                        payload={
                            "type": "codeact",
                            "success": result.success,
                            "output": result.output,
                            "error": result.error,
                            "tool_calls": result.data.get("tool_calls", []),
                        },
                    )

                    if self._abort_requested.is_set():
                        logger.info(
                            "Session %s turn %d aborted by operator after action in step %d.",
                            self.session_id,
                            self.turn_count,
                            turn_steps,
                        )
                        self.state = SessionState.IDLE
                        self.emit_event(
                            SessionEventType.ABORTED,
                            step=turn_steps,
                            payload={"message": "Execution aborted by operator."},
                        )
                        return TurnResult(
                            success=False,
                            completed=False,
                            response="Execution aborted by operator.",
                            steps_executed=turn_steps,
                            error="Execution aborted by operator.",
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
                        self.freeze_completed_turn()
                        self.save()
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

                    result = self.engine.execute(
                        payload_data,
                        self.target,
                        targets=self.targets,
                        on_switch_target=self._on_engine_switch_target,
                    )
                    executed_action = True
                    action_success = result.success
                    if result.success:
                        self.recorded_steps.append(
                            {
                                "turn": self.turn_count,
                                "step": turn_steps,
                                "total_step": self.total_steps,
                                "mode": "guarded",
                                "action": payload_data,
                                "target_id": self.target.target_id,
                                "target_title": self.target.title,
                            }
                        )

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

                    if self._abort_requested.is_set():
                        logger.info(
                            "Session %s turn %d aborted by operator after action in step %d.",
                            self.session_id,
                            self.turn_count,
                            turn_steps,
                        )
                        self.state = SessionState.IDLE
                        self.emit_event(
                            SessionEventType.ABORTED,
                            step=turn_steps,
                            payload={"message": "Execution aborted by operator."},
                        )
                        return TurnResult(
                            success=False,
                            completed=False,
                            response="Execution aborted by operator.",
                            steps_executed=turn_steps,
                            error="Execution aborted by operator.",
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
                    self.freeze_completed_turn()
                    self.save()
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
        self.freeze_completed_turn()
        self.save()
        return TurnResult(
            success=True,
            completed=False,
            response="Step limit reached for this turn.",
            steps_executed=turn_steps,
        )
