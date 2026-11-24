"""Interactive ChatSession engine for multi-turn GUI agent dialogues."""

import base64
import io
import json
import logging
import re
import sys
import threading
import time
import traceback
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
from agenteverywhereflow.engine.base import ExecutionResult
from agenteverywhereflow.security.permission import (
    ApprovalDecision,
    ApprovalRequest,
    PermissionGate,
    PermissionMode,
)
from agenteverywhereflow.session.state import SessionEvent, SessionEventType, SessionState
from agenteverywhereflow.session.storage import TokenUsageStats, session_storage

logger = logging.getLogger(__name__)


class StreamDeltaEvent(SessionEvent):
    """Ephemeral token-level streaming delta event (reasoning_delta / talk_delta)."""

    event_type: str  # type: ignore[assignment]


def _parse_target_type(raw_type: Any) -> TargetType:
    """Safely parse a persisted TargetType string (display, window, or region)."""
    if isinstance(raw_type, TargetType):
        return raw_type
    val = str(raw_type or "window").strip().lower()
    try:
        return TargetType(val)
    except ValueError:
        return TargetType.WINDOW


class _ThinkStreamRouter:
    """Incremental parser splitting streamed content tokens into reasoning vs talk deltas."""

    def __init__(self) -> None:
        self._in_think = False
        self._pending = ""

    def feed(self, text: str) -> list[tuple[str, str]]:
        """Consume a streamed chunk and return ``[(delta_type, piece), ...]``."""
        if not text:
            return []
        buf = self._pending + text
        self._pending = ""
        out: list[tuple[str, str]] = []

        while buf:
            tag = "</think>" if self._in_think else "<think>"
            idx = buf.find(tag)
            if idx != -1:
                if idx > 0:
                    out.append(("reasoning_delta" if self._in_think else "talk_delta", buf[:idx]))
                self._in_think = not self._in_think
                buf = buf[idx + len(tag) :]
                continue

            # Hold back a possible partial tag prefix at the end of the buffer
            hold = 0
            max_check = min(len(tag) - 1, len(buf))
            for k in range(max_check, 0, -1):
                if tag.startswith(buf[-k:]):
                    hold = k
                    break
            if hold:
                emit_part = buf[:-hold]
                self._pending = buf[-hold:]
            else:
                emit_part = buf
            if emit_part:
                out.append(("reasoning_delta" if self._in_think else "talk_delta", emit_part))
            break

        return out

    def flush(self) -> list[tuple[str, str]]:
        """Flush any trailing buffered characters at end of stream."""
        if not self._pending:
            return []
        rem = self._pending
        self._pending = ""
        return [("reasoning_delta" if self._in_think else "talk_delta", rem)]


class TurnResult(BaseModel):
    """Outcome of a single dialogue turn."""

    success: bool
    completed: bool
    response: str
    steps_executed: int
    error: str | None = None


def extract_think_and_talk(
    assistant_text: str,
    raw_thinking: str = "",
) -> tuple[str, str]:
    """Separate Think (internal reasoning/CoT) and Talk (user-facing dialogue/explanation).

    Returns:
        (thinking_text, talk_text)
    """
    thinking_text = ""
    clean_text = assistant_text

    # 1. Structured reasoning_content from API (e.g. DeepSeek-R1, o1)
    if raw_thinking:
        thinking_text = str(raw_thinking).strip()

    # 2. Inline <think>...</think> tags
    if "<think>" in clean_text:
        if "</think>" in clean_text:
            think_matches = re.findall(r"<think>(.*?)</think>", clean_text, re.DOTALL)
            if think_matches and not thinking_text:
                thinking_text = "\n\n".join(m.strip() for m in think_matches if m.strip())
            clean_text = re.sub(r"<think>.*?</think>", "", clean_text, flags=re.DOTALL).strip()
        else:
            parts = clean_text.split("<think>", 1)
            if not thinking_text:
                thinking_text = parts[1].strip()
            clean_text = parts[0].strip()

    # 3. Extract Talk: text outside code blocks and outside TASK_COMPLETED
    code_pattern = r"```[a-zA-Z0-9_-]*\s*.*?\s*```"
    parts = re.split(code_pattern, clean_text, flags=re.DOTALL)
    talk_raw = "\n".join(p.strip() for p in parts if p.strip()).strip()

    if "TASK_COMPLETED" in talk_raw:
        talk_raw = talk_raw.split("TASK_COMPLETED")[0].strip()

    talk_text = talk_raw
    return thinking_text, talk_text


def clean_conversational_text(text: str) -> str:
    """Unwrap robotic meta-reporting phrasing into direct natural speech."""
    s = text.strip()
    # Strip patterns like: 已按你的要求在此对话中向你提问——「...」 or 已向你提问：... or 已向用户提问：“...”
    match = re.search(
        r"(?:向你提问|向您提问|向用户提问|在此对话中提问)[—\-:：\s]*[「\"“](.*?)[」\"”]",
        s,
        re.DOTALL,
    )
    if match:
        return match.group(1).strip()
    return s


def is_conversational_turn(
    executed_action: bool,
    text: str,
) -> bool:
    """Determine if a step conclusion is conversational dialogue rather than GUI action completion."""
    if not executed_action:
        return True
    s = text.strip()
    if s.endswith(("?", "？")):
        return True
    if any(k in s for k in ("请问", "向你提问", "想问你", "请确认", "请告诉我")):
        return True
    return False


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
        title: str = "",
        cdp_backend: Any | None = None,
        enable_streaming: bool = True,
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

        self.title = title or (self.target.title if self.target else "")
        self.mode = mode
        self.config = app_config or config
        self.planner_func = planner_func
        self.approval_timeout = approval_timeout
        self.enable_streaming = enable_streaming
        self.cdp_backend: Any | None = cdp_backend
        self._cdp_backends: dict[str, Any] = {}
        if cdp_backend is not None:
            self._cdp_backends[self.target.target_id] = cdp_backend

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
        self.events: list[SessionEvent] = []
        self._init_system_prompt()

        # Approval coordination
        self.pending_approval: ApprovalRequest | None = None
        self._approval_event = threading.Event()
        self._approval_decision: ApprovalDecision | None = None
        self._approval_lock = threading.Lock()
        self._turn_lock = threading.Lock()
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
        self.events.append(event)
        for listener in list(self.listeners):
            try:
                listener(event)
            except Exception as e:
                logger.warning(f"Error notifying session listener: {e}")

    def emit_stream_delta(
        self,
        delta_type: str,
        step: int,
        delta: str,
    ) -> None:
        """Broadcast an ephemeral token-level streaming delta to active listeners."""
        if not delta:
            return
        payload: dict[str, Any] = {
            "delta": delta,
            "content": delta,
            "text": delta,
        }
        if delta_type == "reasoning_delta":
            payload["thinking"] = delta
        event = StreamDeltaEvent(
            session_id=self.session_id,
            event_type=delta_type,
            step=step,
            payload=payload,
        )
        for listener in list(self.listeners):
            try:
                listener(event)
            except Exception as e:
                logger.warning(f"Error notifying session stream listener: {e}")

    def set_cdp_backend(self, backend: Any | None, target_id: str | None = None) -> None:
        """Bind or clear a headless CDPActionBackend for this session or a specific target."""
        tid = target_id or self.target.target_id
        if backend is None:
            self._cdp_backends.pop(tid, None)
            if tid == self.target.target_id:
                self.cdp_backend = None
        else:
            self._cdp_backends[tid] = backend
            if tid == self.target.target_id or self.cdp_backend is None:
                self.cdp_backend = backend

    def _get_cdp_backend(self, target: TargetInfo | None = None) -> Any | None:
        """Resolve an attached CDPActionBackend for the given target, if any."""
        tgt = target or self.target
        if tgt.target_id in self._cdp_backends:
            return self._cdp_backends[tgt.target_id]
        if self.cdp_backend is not None and (
            tgt.target_id.startswith("cdp:") or tgt.target_id == self.target.target_id
        ):
            return self.cdp_backend
        return None

    def _capture_target(self, target: TargetInfo) -> Image.Image:
        """Capture a target frame via CDP if bound, otherwise via the OS capturer."""
        cdp = self._get_cdp_backend(target)
        if cdp is not None:
            img: Image.Image = cdp.capture()
            if img.width > 0 and img.height > 0:
                target.last_capture_size = (img.width, img.height)
                if target.rect.width != img.width or target.rect.height != img.height:
                    target.rect = Rect(x=0, y=0, width=img.width, height=img.height)
            return img
        img = self.capturer.capture(target)
        if img.width > 0 and img.height > 0:
            target.last_capture_size = (img.width, img.height)
        return img

    def _execute_cdp_codeact(
        self,
        code_str: str,
        cdp_backend: Any,
        target: TargetInfo,
    ) -> ExecutionResult:
        """Execute CodeAct Python actions headlessly through CDP without OS focus/cursor steal."""
        cleaned = code_str.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()

        tool_logs: list[str] = []

        def _click(
            x: float,
            y: float,
            button: str = "left",
            clicks: int = 1,
            target: str | TargetInfo | None = None,
        ) -> None:
            _ = target
            tool_logs.append(
                f"⚡ [CDP] click(x={int(x)}, y={int(y)}, button={button}, clicks={clicks})"
            )
            cdp_backend.click(x, y, button=button, click_count=clicks)

        def _move(x: float, y: float, target: str | TargetInfo | None = None) -> None:
            _ = target
            tool_logs.append(f"⚡ [CDP] move(x={int(x)}, y={int(y)})")
            cdp_backend.move_to(x, y)

        def _double_click(x: float, y: float, target: str | TargetInfo | None = None) -> None:
            _ = target
            tool_logs.append(f"⚡ [CDP] double_click(x={int(x)}, y={int(y)})")
            cdp_backend.double_click(x, y)

        def _right_click(x: float, y: float, target: str | TargetInfo | None = None) -> None:
            _ = target
            tool_logs.append(f"⚡ [CDP] right_click(x={int(x)}, y={int(y)})")
            cdp_backend.right_click(x, y)

        def _drag(
            from_x: float,
            from_y: float,
            to_x: float,
            to_y: float,
            duration: float = 0.5,
            target: str | TargetInfo | None = None,
        ) -> None:
            _ = target
            tool_logs.append(
                f"⚡ [CDP] drag({int(from_x)}, {int(from_y)} -> {int(to_x)}, {int(to_y)})"
            )
            cdp_backend.drag(from_x, from_y, to_x, to_y, duration=duration)

        def _type_text(text: str, target: str | TargetInfo | None = None) -> None:
            _ = target
            tool_logs.append(f"⚡ [CDP] type_text({text!r})")
            cdp_backend.type_text(text)

        def _press(key: str, target: str | TargetInfo | None = None) -> None:
            _ = target
            tool_logs.append(f"⚡ [CDP] press({key!r})")
            cdp_backend.press_key(key)

        def _hotkey(*keys: str, target: str | TargetInfo | None = None) -> None:
            _ = target
            tool_logs.append(f"⚡ [CDP] hotkey({', '.join(repr(k) for k in keys)})")
            cdp_backend.hotkey(*keys)

        def _scroll(
            amount: int,
            x: float | None = None,
            y: float | None = None,
            target: str | TargetInfo | None = None,
        ) -> None:
            _ = target
            sx = float(x) if x is not None else float(target_obj.rect.width // 2)
            sy = float(y) if y is not None else float(target_obj.rect.height // 2)
            tool_logs.append(f"⚡ [CDP] scroll(amount={amount}, x={int(sx)}, y={int(sy)})")
            cdp_backend.scroll(sx, sy, amount)

        def _wait(seconds: float) -> None:
            tool_logs.append(f"⚡ [CDP] wait({seconds}s)")
            time.sleep(seconds)

        def _screenshot(target: str | TargetInfo | None = None) -> Image.Image:
            _ = target
            tool_logs.append("⚡ [CDP] screenshot()")
            return cdp_backend.capture()

        def _get_ax_tree(max_nodes: int = 200) -> list[dict[str, Any]]:
            tool_logs.append(f"⚡ [CDP] get_accessibility_tree(max_nodes={max_nodes})")
            return cdp_backend.get_accessibility_tree(max_nodes=max_nodes)

        target_obj = target

        def _switch_to(target_query: str | TargetInfo) -> TargetInfo:
            switched = self.switch_target(target_query)
            tool_logs.append(f"⚡ [CDP] switch_to({target_query!r}) -> {switched.target_id}")
            return switched

        context: dict[str, Any] = {
            "target": target_obj,
            "targets": self.targets,
            "switch_to": _switch_to,
            "focus": _switch_to,
            "click": _click,
            "move": _move,
            "double_click": _double_click,
            "right_click": _right_click,
            "drag": _drag,
            "type_text": _type_text,
            "press": _press,
            "hotkey": _hotkey,
            "scroll": _scroll,
            "wait": _wait,
            "screenshot": _screenshot,
            "get_accessibility_tree": _get_ax_tree,
        }

        old_stdout, old_stderr = sys.stdout, sys.stderr
        redirected_out, redirected_err = io.StringIO(), io.StringIO()
        success = True
        error_msg = ""
        try:
            sys.stdout = redirected_out
            sys.stderr = redirected_err
            exec(cleaned, context)
        except Exception:
            success = False
            error_msg = traceback.format_exc()
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

        output_str = redirected_out.getvalue()
        if redirected_err.getvalue():
            output_str += "\n[stderr]\n" + redirected_err.getvalue()
        return ExecutionResult(
            success=success,
            output=output_str.strip(),
            error=error_msg,
            data={"executed_code": cleaned, "tool_calls": tool_logs, "backend": "cdp"},
        )

    def _execute_cdp_guarded(
        self,
        payload_data: dict[str, Any],
        cdp_backend: Any,
        target: TargetInfo,
    ) -> ExecutionResult:
        """Execute a structured Guarded JSON action headlessly through CDP."""
        action = str(payload_data.get("action", "")).lower().strip()
        if not action:
            return ExecutionResult(success=False, error="No action specified in payload")
        try:
            if action in ("switch_target", "switch_to"):
                target_override = payload_data.get("target")
                if target_override:
                    switched = self.switch_target(str(target_override))
                    return ExecutionResult(
                        success=True, output=f"Switched active target to: {switched.title}"
                    )
                return ExecutionResult(success=True, output=f"Active target: {target.title}")
            if action == "click":
                x = float(payload_data.get("x", 0))
                y = float(payload_data.get("y", 0))
                button = str(payload_data.get("button", "left"))
                clicks = int(payload_data.get("clicks", 1))
                cdp_backend.click(x, y, button=button, click_count=clicks)
                return ExecutionResult(success=True, output=f"CDP clicked at ({x}, {y})")
            if action == "double_click":
                x = float(payload_data.get("x", 0))
                y = float(payload_data.get("y", 0))
                cdp_backend.double_click(x, y)
                return ExecutionResult(success=True, output=f"CDP double-clicked at ({x}, {y})")
            if action == "right_click":
                x = float(payload_data.get("x", 0))
                y = float(payload_data.get("y", 0))
                cdp_backend.right_click(x, y)
                return ExecutionResult(success=True, output=f"CDP right-clicked at ({x}, {y})")
            if action == "move":
                x = float(payload_data.get("x", 0))
                y = float(payload_data.get("y", 0))
                cdp_backend.move_to(x, y)
                return ExecutionResult(success=True, output=f"CDP moved to ({x}, {y})")
            if action == "drag":
                cdp_backend.drag(
                    float(payload_data.get("from_x", 0)),
                    float(payload_data.get("from_y", 0)),
                    float(payload_data.get("to_x", 0)),
                    float(payload_data.get("to_y", 0)),
                    duration=float(payload_data.get("duration", 0.5)),
                )
                return ExecutionResult(success=True, output="CDP drag completed")
            if action == "type":
                text = str(payload_data.get("text", ""))
                cdp_backend.type_text(text)
                return ExecutionResult(success=True, output=f"CDP typed text: {text}")
            if action == "press":
                key = str(payload_data.get("key", ""))
                cdp_backend.press_key(key)
                return ExecutionResult(success=True, output=f"CDP pressed key: {key}")
            if action == "hotkey":
                keys = payload_data.get("keys", [])
                cdp_backend.hotkey(*[str(k) for k in keys])
                return ExecutionResult(success=True, output=f"CDP sent hotkey: {keys}")
            if action == "scroll":
                amount = int(payload_data.get("amount", 0))
                sx = float(payload_data.get("x", target.rect.width // 2))
                sy = float(payload_data.get("y", target.rect.height // 2))
                cdp_backend.scroll(sx, sy, amount)
                return ExecutionResult(
                    success=True, output=f"CDP scrolled {amount} at ({sx}, {sy})"
                )
            if action == "wait":
                seconds = float(payload_data.get("seconds", 1.0))
                time.sleep(seconds)
                return ExecutionResult(success=True, output=f"Waited {seconds}s")
            return ExecutionResult(success=False, error=f"Unknown CDP action: '{action}'")
        except Exception as exc:
            return ExecutionResult(success=False, error=str(exc))

    def _record_usage(self, usage: Any) -> None:
        """Accumulate prompt, completion, and KV cache hit token metrics."""
        if not usage:
            return
        p_tok = getattr(usage, "prompt_tokens", 0) or 0
        c_tok = getattr(usage, "completion_tokens", 0) or 0
        cached_tok = getattr(usage, "prompt_cache_hit_tokens", 0) or 0
        if not cached_tok:
            ptd = getattr(usage, "prompt_tokens_details", None)
            if ptd:
                cached_tok = getattr(ptd, "cached_tokens", 0) or 0

        self.token_usage.prompt_tokens += int(p_tok)
        self.token_usage.completion_tokens += int(c_tok)
        self.token_usage.total_tokens += int(p_tok) + int(c_tok)
        self.token_usage.cached_prompt_tokens += int(cached_tok)

    def _invoke_vlm(self, step: int) -> tuple[str, str]:
        """Call the configured Vision-LLM with token-level streaming and graceful fallback.

        Returns:
            ``(assistant_text, raw_thinking)``
        """
        if not self.enable_streaming:
            non_stream_resp = self.client.chat.completions.create(
                model=self.config.model_name,
                messages=self.messages,  # type: ignore[arg-type]
                temperature=0.2,
            )
            return self._extract_non_streaming_response(non_stream_resp)

        response: Any = None
        try:
            response = self.client.chat.completions.create(
                model=self.config.model_name,
                messages=self.messages,  # type: ignore[arg-type]
                temperature=0.2,
                stream=True,
                stream_options={"include_usage": True},
            )
        except Exception as exc:
            err_lower = str(exc).lower()
            if isinstance(exc, TypeError) or "stream_options" in err_lower:
                try:
                    response = self.client.chat.completions.create(
                        model=self.config.model_name,
                        messages=self.messages,  # type: ignore[arg-type]
                        temperature=0.2,
                        stream=True,
                    )
                except Exception as exc2:
                    if isinstance(exc2, TypeError) or "stream" in str(exc2).lower():
                        response = self.client.chat.completions.create(
                            model=self.config.model_name,
                            messages=self.messages,  # type: ignore[arg-type]
                            temperature=0.2,
                        )
                    else:
                        raise
            elif "stream" in err_lower:
                response = self.client.chat.completions.create(
                    model=self.config.model_name,
                    messages=self.messages,  # type: ignore[arg-type]
                    temperature=0.2,
                )
            else:
                raise

        # If the client or test mock returned a non-streaming completion object directly
        choices_attr = getattr(response, "choices", None)
        if isinstance(choices_attr, (list, tuple)) and len(choices_attr) > 0:
            return self._extract_non_streaming_response(response)

        if not hasattr(response, "__iter__"):
            fallback_resp = self.client.chat.completions.create(
                model=self.config.model_name,
                messages=self.messages,  # type: ignore[arg-type]
                temperature=0.2,
            )
            return self._extract_non_streaming_response(fallback_resp)

        content_parts: list[str] = []
        raw_thinking_parts: list[str] = []
        router = _ThinkStreamRouter()

        for chunk in response:
            if self._abort_requested.is_set():
                break
            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage is not None:
                self._record_usage(chunk_usage)

            chunk_choices = getattr(chunk, "choices", None)
            if not chunk_choices:
                continue
            delta = getattr(chunk_choices[0], "delta", None)
            if delta is None:
                continue

            r_piece = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
            if isinstance(r_piece, str) and r_piece:
                raw_thinking_parts.append(r_piece)
                self.emit_stream_delta("reasoning_delta", step=step, delta=r_piece)

            c_piece = getattr(delta, "content", None)
            if isinstance(c_piece, str) and c_piece:
                content_parts.append(c_piece)
                for delta_type, part in router.feed(c_piece):
                    self.emit_stream_delta(delta_type, step=step, delta=part)

        for delta_type, part in router.flush():
            self.emit_stream_delta(delta_type, step=step, delta=part)

        return "".join(content_parts), "".join(raw_thinking_parts)

    def _extract_non_streaming_response(self, response: Any) -> tuple[str, str]:
        """Extract assistant text, structured reasoning, and usage from a standard completion."""
        choice = response.choices[0]
        assistant_text = choice.message.content or ""
        raw_thinking = (
            getattr(choice.message, "reasoning_content", None)
            or getattr(choice.message, "reasoning", None)
            or ""
        )
        self._record_usage(getattr(response, "usage", None))
        return assistant_text, str(raw_thinking)

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
        self.events.clear()
        self._init_system_prompt()
        self.state = SessionState.IDLE
        self.turn_count = 0
        self.total_steps = 0

    def capture_frame(self) -> Image.Image:
        """Capture live viewport frame of the target without altering window focus."""
        return self._capture_target(self.target)

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
            title=self.title,
            events=[e.model_dump() for e in self.events],
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
                r_dict = t_dict.get("target_rect") or t_dict.get("rect") or {}
                rect = Rect(
                    x=r_dict.get("x", 0),
                    y=r_dict.get("y", 0),
                    width=r_dict.get("width", 800),
                    height=r_dict.get("height", 600),
                )
                targets_list.append(
                    TargetInfo(
                        target_id=t_dict.get("target_id", ""),
                        target_type=_parse_target_type(t_dict.get("target_type", "window")),
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
                target_type=_parse_target_type(meta.target_type),
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
        session.title = getattr(meta, "title", "") or meta.target_title
        if raw_messages:
            session.messages = raw_messages
        session.recorded_steps = session_storage.load_recorded_steps(session_id)
        raw_events = session_storage.load_events(session_id)
        if raw_events:
            session.events = [SessionEvent.model_validate(e) for e in raw_events]
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
        with self._turn_lock:
            return self._execute_turn_locked(user_instruction, max_steps=max_steps)

    def _execute_turn_locked(
        self,
        user_instruction: str,
        max_steps: int | None = None,
    ) -> TurnResult:
        self._abort_requested.clear()
        effective_max_steps = max_steps if max_steps is not None else self.config.max_steps
        self.turn_count += 1
        self.state = SessionState.RUNNING
        logger.info(
            "Session %s (target='%s' [%s], %dx%d) starting turn %d: %s",
            self.session_id,
            self.target.title,
            self.target.target_id,
            self.target.rect.width,
            self.target.rect.height,
            self.turn_count,
            user_instruction,
        )
        self.emit_event(
            SessionEventType.TURN_START,
            payload={
                "turn": self.turn_count,
                "instruction": user_instruction,
                "target_title": self.target.title,
                "target_id": self.target.target_id,
            },
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

            # 1. Capture screenshot(s) and build visual observation
            captured_image_sizes: dict[str, tuple[int, int]] = {}
            if len(self.targets) <= 1:
                try:
                    img = self._capture_target(self.target)
                    img_b64 = self._encode_image(img)
                    if img.width > 0 and img.height > 0:
                        captured_image_sizes[self.target.target_id] = (img.width, img.height)
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
                        t_img = self._capture_target(t)
                        last_img = t_img
                        if t_img.width > 0 and t_img.height > 0:
                            captured_image_sizes[t.target_id] = (t_img.width, t_img.height)
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
                    assistant_text, raw_thinking = self._invoke_vlm(step=turn_steps)
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

            # Determine Think (internal reasoning) and Talk (communication to user)
            thinking_text, talk_text = extract_think_and_talk(
                assistant_text=assistant_text,
                raw_thinking=raw_thinking,
            )

            self.messages.append({"role": "assistant", "content": assistant_text})

            # Emit Think (internal CoT) only if genuine reasoning exists
            if thinking_text:
                self.emit_event(
                    SessionEventType.REASONING,
                    step=turn_steps,
                    payload={"thinking": thinking_text, "content": thinking_text},
                )

            # Emit Talk (communication to user) if model spoke to the user and did not declare completion
            has_task_completed = "TASK_COMPLETED" in assistant_text
            if talk_text and not has_task_completed:
                clean_talk = clean_conversational_text(talk_text)
                self.emit_event(
                    SessionEventType.MESSAGE,
                    step=turn_steps,
                    payload={"text": clean_talk, "content": clean_talk},
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
                    active_cdp = self._get_cdp_backend(self.target)
                    if active_cdp is not None:
                        result = self._execute_cdp_codeact(code_to_exec, active_cdp, self.target)
                    else:
                        result = self.engine.execute(
                            code_to_exec,
                            self.target,
                            targets=self.targets,
                            on_switch_target=self._on_engine_switch_target,
                            image_sizes=captured_image_sizes,
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
                        self.emit_event(
                            SessionEventType.TASK_COMPLETED,
                            step=turn_steps,
                            payload={"summary": msg},
                        )
                        self.emit_event(
                            SessionEventType.TURN_FINISHED,
                            step=turn_steps,
                            payload={"status": "completed", "conversational": False},
                        )
                        self.save()
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

                    active_cdp = self._get_cdp_backend(self.target)
                    if active_cdp is not None:
                        result = self._execute_cdp_guarded(payload_data, active_cdp, self.target)
                    else:
                        result = self.engine.execute(
                            payload_data,
                            self.target,
                            targets=self.targets,
                            on_switch_target=self._on_engine_switch_target,
                            image_sizes=captured_image_sizes,
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

            # 6. Check Completion or Conversational Response
            if has_task_completed:
                if not executed_action or action_success:
                    raw_summary = assistant_text.split("TASK_COMPLETED")[-1].strip(": \n")
                    summary = (
                        clean_conversational_text(raw_summary)
                        if not executed_action
                        else raw_summary
                    )
                    final_resp = summary or talk_text or "Instruction completed."
                    is_conv = is_conversational_turn(executed_action, final_resp)

                    self.state = SessionState.WAITING_INPUT
                    self.freeze_completed_turn()

                    if is_conv:
                        # For conversational dialogue, emit MESSAGE so it appears in assistant speech bubble
                        self.emit_event(
                            SessionEventType.MESSAGE,
                            step=turn_steps,
                            payload={"text": final_resp, "content": final_resp},
                        )

                    self.emit_event(
                        SessionEventType.TASK_COMPLETED,
                        step=turn_steps,
                        payload={"summary": final_resp, "conversational": is_conv},
                    )
                    self.emit_event(
                        SessionEventType.TURN_FINISHED,
                        step=turn_steps,
                        payload={"status": "completed", "conversational": is_conv},
                    )
                    self.save()
                    return TurnResult(
                        success=True,
                        completed=True,
                        response=final_resp,
                        steps_executed=turn_steps,
                    )

            if not executed_action and not has_task_completed:
                if talk_text:
                    clean_talk = clean_conversational_text(talk_text)
                    # Model answered a question, asked for clarification, or responded with dialogue
                    self.state = SessionState.WAITING_INPUT
                    self.freeze_completed_turn()
                    self.emit_event(
                        SessionEventType.TASK_COMPLETED,
                        step=turn_steps,
                        payload={"summary": clean_talk, "conversational": True},
                    )
                    self.emit_event(
                        SessionEventType.TURN_FINISHED,
                        step=turn_steps,
                        payload={"status": "completed", "conversational": True},
                    )
                    self.save()
                    return TurnResult(
                        success=True,
                        completed=True,
                        response=clean_talk,
                        steps_executed=turn_steps,
                    )
                else:
                    self.messages.append(
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "No executable code block or response was found. Please provide an action or message.",
                                }
                            ],
                        }
                    )
                    self.emit_event(
                        SessionEventType.STEP_FINISHED,
                        step=turn_steps,
                        payload={
                            "status": "retry_prompted",
                            "message": "No actionable code or dialogue response found. Prompted agent to take action.",
                        },
                    )

        self.state = SessionState.WAITING_INPUT
        self.freeze_completed_turn()
        self.emit_event(
            SessionEventType.TURN_FINISHED,
            step=turn_steps,
            payload={"status": "step_limit_reached"},
        )
        self.save()
        return TurnResult(
            success=True,
            completed=False,
            response="Step limit reached for this turn.",
            steps_executed=turn_steps,
        )
