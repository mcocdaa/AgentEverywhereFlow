"""FastAPI Dialogue Service Application for AgentEverywhereFlow (aef serve)."""

import asyncio
import io
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import (
    BackgroundTasks,
    FastAPI,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from PIL import Image
from pydantic import BaseModel, Field, field_validator

import agenteverywhereflow.config as cfg_mod
from agenteverywhereflow import __version__
from agenteverywhereflow.actions.cdp_backend import (
    CDPActionBackend,
    discover_cdp_targets,
    resolve_cdp_target,
)
from agenteverywhereflow.capturer import get_capturer
from agenteverywhereflow.capturer.base import Rect, TargetInfo, TargetType
from agenteverywhereflow.capturer.selector import resolve_display, resolve_target
from agenteverywhereflow.config import ExecutionMode
from agenteverywhereflow.security.permission import ApprovalDecision, PermissionMode
from agenteverywhereflow.server.webui import get_webui_dist_path, get_webui_status
from agenteverywhereflow.session.manager import session_manager
from agenteverywhereflow.session.state import SessionEvent, SessionEventType, SessionState
from agenteverywhereflow.workflow.exporter import WorkflowExporter
from agenteverywhereflow.workflow.runner import WorkflowRunner

logger = logging.getLogger(__name__)

_THUMBNAIL_CACHE_TTL_SECONDS = 0.5
_THUMBNAIL_CACHE_MAX_ENTRIES = 64


class _ThumbnailCache:
    """Lightweight thread-safe in-memory TTL cache for target JPEG thumbnails."""

    def __init__(
        self,
        ttl_seconds: float = _THUMBNAIL_CACHE_TTL_SECONDS,
        max_entries: int = _THUMBNAIL_CACHE_MAX_ENTRIES,
    ) -> None:
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._lock = threading.Lock()
        self._entries: dict[tuple[str, int, int], tuple[float, bytes]] = {}

    def get(self, key: tuple[str, int, int]) -> bytes | None:
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            ts, payload = entry
            if now - ts <= self._ttl:
                return payload
            self._entries.pop(key, None)
            return None

    def set(self, key: tuple[str, int, int], payload: bytes) -> None:
        now = time.monotonic()
        with self._lock:
            if len(self._entries) >= self._max_entries:
                # Evict expired entries first, then oldest
                expired = [k for k, (ts, _) in self._entries.items() if now - ts > self._ttl]
                for k in expired:
                    self._entries.pop(k, None)
                if len(self._entries) >= self._max_entries:
                    oldest_key = min(self._entries, key=lambda k: self._entries[k][0])
                    self._entries.pop(oldest_key, None)
            self._entries[key] = (now, payload)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


class _GlobalEventBus:
    """Thread-safe lightweight pub/sub bus powering ``WS /api/v1/events``."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subscribers: list[
            tuple[asyncio.AbstractEventLoop, asyncio.Queue[dict[str, Any]]]
        ] = []

    def subscribe(
        self,
        loop: asyncio.AbstractEventLoop,
        queue: asyncio.Queue[dict[str, Any]],
    ) -> None:
        with self._lock:
            self._subscribers.append((loop, queue))

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        with self._lock:
            self._subscribers = [(lp, q) for lp, q in self._subscribers if q is not queue]

    def publish(
        self,
        event_type: str,
        session_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        message: dict[str, Any] = {
            "event_type": event_type,
            "session_id": session_id,
            "payload": payload or {},
            "timestamp": time.time(),
        }
        with self._lock:
            subs = list(self._subscribers)
        for loop, queue in subs:
            if loop.is_closed():
                continue
            try:
                loop.call_soon_threadsafe(queue.put_nowait, message)
            except RuntimeError:
                pass


def _serialize_target(t: TargetInfo) -> dict[str, Any]:
    """Serialize a :class:`TargetInfo` into a standard API dict."""
    return {
        "target_id": t.target_id,
        "target_type": t.target_type.value
        if hasattr(t.target_type, "value")
        else str(t.target_type),
        "title": t.title,
        "native_handle": t.native_handle,
        "process_name": t.process_name,
        "pid": t.pid,
        "is_minimized": t.is_minimized,
        "rect": {
            "x": t.rect.x,
            "y": t.rect.y,
            "width": t.rect.width,
            "height": t.rect.height,
        },
    }


def _mask_api_key(api_key: str) -> str:
    """Return a safe masked preview of an API key."""
    return cfg_mod.mask_api_key(api_key)


def _normalize_mode_str(val: str) -> ExecutionMode:
    v_lower = val.lower().strip()
    if v_lower in ("minimal_python", "minimal", "codeact"):
        return ExecutionMode.MINIMAL_PYTHON
    if v_lower in ("control_guarded", "guarded"):
        return ExecutionMode.CONTROL_GUARDED
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Invalid default_mode '{val}'. Expected 'minimal' or 'guarded'.",
    )


def _normalize_permission_str(val: str) -> PermissionMode:
    v_lower = val.lower().strip()
    if v_lower in ("auto", "autonomous"):
        return PermissionMode.AUTO
    if v_lower in ("manual", "approval"):
        return PermissionMode.MANUAL
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Invalid default_permission '{val}'. Expected 'auto' or 'manual'.",
    )


def _get_masked_config_payload() -> dict[str, Any]:
    """Build the masked runtime configuration response using ``config.py`` helper if available."""
    helper = getattr(cfg_mod, "get_masked_config", None)
    cfg = cfg_mod.config
    fallback_perm = (
        cfg.default_permission_mode.value
        if hasattr(cfg.default_permission_mode, "value")
        else str(cfg.default_permission_mode)
    )
    fallback_mode = (
        cfg.default_mode.value if hasattr(cfg.default_mode, "value") else str(cfg.default_mode)
    )
    fallback_key = (cfg.api_key or "").strip()

    if callable(helper):
        try:
            raw = helper(cfg)
            if isinstance(raw, dict):
                api_key_set = bool(raw.get("api_key_set", bool(fallback_key)))
                preview = raw.get("api_key_preview")
                if preview is None:
                    preview = _mask_api_key(fallback_key) if api_key_set else ""
                default_perm = raw.get("default_permission") or raw.get(
                    "default_permission_mode", fallback_perm
                )
                return {
                    "model_name": str(raw.get("model_name", cfg.model_name)),
                    "base_url": str(raw.get("base_url", cfg.base_url)),
                    "api_key_set": api_key_set,
                    "api_key_preview": str(preview),
                    "default_mode": str(raw.get("default_mode", fallback_mode)),
                    "default_permission": str(default_perm),
                    "summon_hotkey": str(raw.get("summon_hotkey", cfg.summon_hotkey)),
                }
        except Exception as exc:
            logger.debug("config.get_masked_config helper failed, using fallback: %s", exc)

    return {
        "model_name": cfg.model_name,
        "base_url": cfg.base_url,
        "api_key_set": bool(fallback_key),
        "api_key_preview": _mask_api_key(fallback_key),
        "default_mode": fallback_mode,
        "default_permission": fallback_perm,
        "summon_hotkey": cfg.summon_hotkey,
    }


def _write_env_updates_preserving_other_keys(env_path: Path, env_updates: dict[str, str]) -> None:
    """Persist env key updates to ``~/.aef/.env`` while preserving unrelated lines and comments."""
    existing_lines: list[str] = []
    if env_path.exists():
        try:
            existing_lines = env_path.read_text(encoding="utf-8").splitlines()
        except Exception:
            existing_lines = []

    updated_keys: set[str] = set()
    new_lines: list[str] = []
    for line in existing_lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key_part = stripped.split("=", 1)[0].strip()
            if key_part in env_updates:
                new_lines.append(f"{key_part}={env_updates[key_part]}")
                updated_keys.add(key_part)
                continue
        new_lines.append(line)

    for k, v in env_updates.items():
        if k not in updated_keys:
            new_lines.append(f"{k}={v}")

    env_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = env_path.with_suffix(env_path.suffix + ".tmp")
    content = "\n".join(new_lines) + ("\n" if new_lines else "")
    tmp_path.write_text(content, encoding="utf-8")
    os.replace(tmp_path, env_path)


def _apply_config_patch(updates: dict[str, Any]) -> dict[str, Any]:
    """Apply partial configuration updates to ``~/.aef/.env`` and live runtime config."""
    env_updates: dict[str, str] = {}
    helper_updates: dict[str, Any] = {}

    if "api_key" in updates and updates["api_key"] is not None:
        val = str(updates["api_key"]).strip()
        env_updates["AEF_API_KEY"] = val
        helper_updates["api_key"] = val
    if "model_name" in updates and updates["model_name"] is not None:
        val = str(updates["model_name"]).strip()
        env_updates["AEF_MODEL_NAME"] = val
        helper_updates["model_name"] = val
    if "base_url" in updates and updates["base_url"] is not None:
        val = str(updates["base_url"]).strip()
        env_updates["AEF_BASE_URL"] = val
        helper_updates["base_url"] = val
    if "default_mode" in updates and updates["default_mode"] is not None:
        mode_enum = _normalize_mode_str(str(updates["default_mode"]))
        env_updates["AEF_DEFAULT_MODE"] = mode_enum.value
        helper_updates["default_mode"] = mode_enum.value
    perm_raw = updates.get("default_permission")
    if perm_raw is None:
        perm_raw = updates.get("default_permission_mode")
    if perm_raw is not None:
        perm_enum = _normalize_permission_str(str(perm_raw))
        env_updates["AEF_DEFAULT_PERMISSION_MODE"] = perm_enum.value
        helper_updates["default_permission_mode"] = perm_enum.value
    if "summon_hotkey" in updates and updates["summon_hotkey"] is not None:
        val = str(updates["summon_hotkey"]).strip()
        env_updates["AEF_SUMMON_HOTKEY"] = val
        helper_updates["summon_hotkey"] = val

    if not env_updates:
        return _get_masked_config_payload()

    env_path = cfg_mod.get_global_env_file()
    helper = getattr(cfg_mod, "update_env_config", None)
    used_helper = False
    if callable(helper):
        try:
            helper(helper_updates, env_path=env_path)
            used_helper = True
        except Exception as exc:
            logger.debug("config.update_env_config helper failed, using fallback: %s", exc)

    if not used_helper:
        _write_env_updates_preserving_other_keys(env_path, env_updates)

    # Ensure live runtime singleton reflects every patched field immediately
    live_cfg = cfg_mod.config
    if "api_key" in helper_updates:
        live_cfg.api_key = helper_updates["api_key"]
    if "model_name" in helper_updates:
        live_cfg.model_name = helper_updates["model_name"]
    if "base_url" in helper_updates:
        live_cfg.base_url = helper_updates["base_url"]
    if "default_mode" in helper_updates:
        live_cfg.default_mode = ExecutionMode(helper_updates["default_mode"])
    if "default_permission_mode" in helper_updates:
        live_cfg.default_permission_mode = PermissionMode(helper_updates["default_permission_mode"])
    if "summon_hotkey" in helper_updates:
        live_cfg.summon_hotkey = helper_updates["summon_hotkey"]

    return _get_masked_config_payload()


def _parse_region_target_id(target_id: str) -> TargetInfo | None:
    """Parse ``region:x,y,w,h`` into a synthetic :class:`TargetInfo`."""
    if not target_id.lower().startswith("region:"):
        return None
    parts = target_id.split(":", 1)[1].split(",")
    if len(parts) != 4:
        return None
    try:
        x, y, w, h = (int(p.strip()) for p in parts)
    except ValueError:
        return None
    if w <= 0 or h <= 0:
        return None
    return TargetInfo(
        target_id=f"region:{x},{y},{w},{h}",
        target_type=TargetType.REGION,
        title=f"Region ({w}x{h} at {x},{y})",
        process_name="region",
        rect=Rect(x=x, y=y, width=w, height=h),
        native_handle=0,
    )


def _resolve_request_targets(
    *,
    target_id: str | None,
    target_ids: list[str] | None,
    region: "RegionSpec | None",
    cdp_ws_url: str | None,
    cdp_endpoint: str,
    title: str | None,
    capturer: Any,
    fallback_targets: list[TargetInfo] | None = None,
) -> tuple[list[TargetInfo], CDPActionBackend | None, list[TargetInfo]]:
    """Resolve CDP, custom region, multi-target, or single OS targets for session bind/rebind.

    Returns:
        ``(resolved_targets, cdp_backend, all_os_targets)``
    """
    resolved_targets: list[TargetInfo] = []
    cdp_backend: CDPActionBackend | None = None
    all_os_targets: list[TargetInfo] = []

    # 1. Headless CDP target via explicit cdp_ws_url or 'cdp:*' target_id
    is_cdp_request = bool(cdp_ws_url) or (
        bool(target_id) and str(target_id).strip().lower().startswith("cdp:")
    )
    if is_cdp_request:
        ws_url = (cdp_ws_url or "").strip()
        cdp_info = None
        if not ws_url and target_id and target_id.strip().lower().startswith("cdp:"):
            cdp_info = resolve_cdp_target(target_id, endpoint=cdp_endpoint)
            if cdp_info is not None:
                ws_url = cdp_info.ws_url
        if not ws_url:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No CDP page target matching '{target_id}' found at {cdp_endpoint}.",
            )
        if cdp_info is None:
            raw_id = (
                target_id[4:]
                if target_id and target_id.lower().startswith("cdp:")
                else ws_url.rstrip("/").split("/")[-1]
            )
            cdp_target = TargetInfo(
                target_id=f"cdp:{raw_id}",
                target_type=TargetType.WINDOW,
                title=title or f"CDP Page ({raw_id})",
                process_name="cdp",
                rect=Rect(x=0, y=0, width=1280, height=800),
                native_handle=0,
            )
        else:
            cdp_target = cdp_info.to_target_info()
            if title:
                cdp_target.title = title
        cdp_backend = CDPActionBackend(ws_url=ws_url, target_info=cdp_info)
        resolved_targets.append(cdp_target)
        return resolved_targets, cdp_backend, all_os_targets

    # 2. Custom screen crop region target
    if region is not None:
        r = region
        region_id = f"region:{r.x},{r.y},{r.width},{r.height}"
        region_title = r.title or title or f"Region ({r.width}x{r.height} at {r.x},{r.y})"
        region_target = TargetInfo(
            target_id=region_id,
            target_type=TargetType.REGION,
            title=region_title,
            process_name="region",
            rect=Rect(x=r.x, y=r.y, width=r.width, height=r.height),
            native_handle=0,
        )
        resolved_targets.append(region_target)
        return resolved_targets, None, all_os_targets

    # 3. Multi-target OS resolution (target_ids) or single target_id
    queries: list[str] = []
    if target_id:
        queries.append(target_id)
    if target_ids:
        for q in target_ids:
            if q and q not in queries:
                queries.append(q)

    if not queries:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="One of 'target_id', 'target_ids', 'region', or 'cdp_ws_url' must be provided.",
        )

    fetched_os = False
    seen_ids: set[str] = set()
    for q in queries:
        q_clean = q.strip()
        if q_clean.lower().startswith("region:"):
            reg_t = _parse_region_target_id(q_clean)
            if reg_t is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid region target query '{q}'.",
                )
            if title and len(queries) == 1:
                reg_t.title = title
            matched = reg_t
        else:
            if not fetched_os:
                all_os_targets = capturer.list_targets()
                fetched_os = True
            matched_opt = resolve_target(all_os_targets, q_clean)
            if matched_opt is None and fallback_targets:
                matched_opt = resolve_target(fallback_targets, q_clean)
            if matched_opt is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"No window or screen matching query '{q}' found.",
                )
            matched = matched_opt
        if matched.target_id not in seen_ids:
            resolved_targets.append(matched)
            seen_ids.add(matched.target_id)

    return resolved_targets, None, all_os_targets


# Request & Response Schemas
class RegionSpec(BaseModel):
    """Custom rectangular screen region specification."""

    x: int = Field(description="Left X coordinate in virtual screen pixels")
    y: int = Field(description="Top Y coordinate in virtual screen pixels")
    width: int = Field(gt=0, description="Region width in pixels")
    height: int = Field(gt=0, description="Region height in pixels")
    title: str | None = Field(default=None, description="Optional human-readable region title")
    parent_target_id: str | None = Field(
        default=None, description="Optional parent window/display target ID reference"
    )


class CreateSessionRequest(BaseModel):
    target_id: str | None = Field(
        default=None,
        description="Target ID, native handle, table index, 'cdp:<id>', or process/title query",
    )
    target_ids: list[str] | None = Field(
        default=None,
        description="Optional list of multiple target IDs/queries to bind into a multi-target session",
    )
    region: RegionSpec | None = Field(
        default=None,
        description="Optional custom screen crop region target ({x, y, width, height, title?})",
    )
    cdp_ws_url: str | None = Field(
        default=None,
        description="Optional Chrome DevTools Protocol WebSocket URL for headless browser control",
    )
    cdp_endpoint: str = Field(
        default="http://127.0.0.1:9222",
        description="CDP discovery HTTP endpoint used when resolving 'cdp:*' target_id",
    )
    title: str | None = Field(default=None, description="Optional initial custom session title")
    mode: ExecutionMode = Field(default=ExecutionMode.MINIMAL_PYTHON)
    permission_mode: PermissionMode = Field(default=PermissionMode.AUTO)
    session_id: str | None = Field(default=None)
    move_to_display: str | None = Field(
        default=None,
        description="Optional display ID/number to automatically relocate the window target to (e.g. 'display:2' or '2')",
    )

    @field_validator("mode", mode="before")
    @classmethod
    def normalize_mode(cls, v: Any) -> Any:
        """Allow 'minimal_python', 'minimal', 'codeact', and 'guarded' aliases."""
        if isinstance(v, str):
            v_lower = v.lower().strip()
            if v_lower in ("minimal_python", "minimal", "codeact"):
                return ExecutionMode.MINIMAL_PYTHON
            if v_lower in ("control_guarded", "guarded"):
                return ExecutionMode.CONTROL_GUARDED
        return v

    @field_validator("permission_mode", mode="before")
    @classmethod
    def normalize_permission_mode(cls, v: Any) -> Any:
        """Allow 'auto' and 'manual' variations."""
        if isinstance(v, str):
            v_lower = v.lower().strip()
            if v_lower in ("auto", "autonomous"):
                return PermissionMode.AUTO
            if v_lower in ("manual", "approval"):
                return PermissionMode.MANUAL
        return v


class UpdateConfigRequest(BaseModel):
    """Partial configuration update payload for ``PATCH /api/v1/config``."""

    api_key: str | None = Field(default=None, description="Provider API key (write-only)")
    model_name: str | None = Field(default=None, description="Vision-LLM model identifier")
    base_url: str | None = Field(default=None, description="OpenAI-compatible API base URL")
    default_mode: str | None = Field(default=None, description="Default execution mode")
    default_permission: str | None = Field(
        default=None, description="Default permission mode ('auto' or 'manual')"
    )
    default_permission_mode: str | None = Field(
        default=None, description="Alias for default_permission"
    )
    summon_hotkey: str | None = Field(default=None, description="Global desktop summon hotkey")


class MoveTargetRequest(BaseModel):
    display_id: str = Field(
        description="Target display ID or index to move the window to, e.g. 'display:2' or '2'"
    )


class UpdateSessionRequest(BaseModel):
    title: str | None = Field(default=None, description="Custom title / rename for session")


class SendMessageRequest(BaseModel):
    instruction: str = Field(description="Instruction or task to execute in this dialogue turn")
    max_steps: int = Field(default=100, description="Max steps for this turn")
    async_execution: bool = Field(
        default=False, description="Whether to execute in background and return immediately"
    )


class SubmitApprovalRequest(BaseModel):
    approved: bool = Field(description="Whether to approve the pending action")
    reason: str | None = Field(default=None, description="Optional rejection reason or guidance")


class UpdatePermissionRequest(BaseModel):
    mode: PermissionMode = Field(description="New permission mode: auto or manual")


class SwitchTargetRequest(BaseModel):
    """Payload for rebinding a session's active viewport(s) while preserving context."""

    target_id: str | None = Field(
        default=None,
        description="Target ID, native handle, table index, 'cdp:<id>', 'region:x,y,w,h', or process/title query",
    )
    target_ids: list[str] | None = Field(
        default=None,
        description="Optional list of multiple target IDs/queries to bind into the session",
    )
    region: RegionSpec | None = Field(
        default=None,
        description="Optional custom screen crop region target ({x, y, width, height, title?})",
    )
    cdp_ws_url: str | None = Field(
        default=None,
        description="Optional Chrome DevTools Protocol WebSocket URL for headless browser control",
    )
    cdp_endpoint: str = Field(
        default="http://127.0.0.1:9222",
        description="CDP discovery HTTP endpoint used when resolving 'cdp:*' target_id",
    )
    title: str | None = Field(default=None, description="Optional custom target title override")
    replace_targets: bool = Field(
        default=True,
        description="Whether to replace the session's bound target pool instead of keeping stale targets",
    )


class RunWorkflowRequest(BaseModel):
    workflow_yaml: str | None = Field(
        default=None, description="Declarative YAML workflow definition to execute"
    )
    session_id: str | None = Field(
        default=None, description="Source session ID to build and replay workflow from"
    )
    target_id: str | None = Field(
        default=None, description="Optional target ID or window title override for execution"
    )
    dry_run: bool = Field(
        default=False, description="Validate and inspect planned steps without GUI side effects"
    )
    step_delay: float = Field(default=0.5, description="Delay between workflow steps in seconds")


def create_app() -> FastAPI:
    """Build and configure the FastAPI dialogue service application."""
    app = FastAPI(
        title="AgentEverywhereFlow Dialogue Service",
        description="Unified conversational agent service with viewport isolation and human-in-the-loop permission gate.",
        version=__version__,
    )

    # Enable CORS for web frontends / local dashboards
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    webui_dir = get_webui_dist_path()
    thumbnail_cache = _ThumbnailCache()
    event_bus = _GlobalEventBus()
    attached_session_ids: set[tuple[str, int]] = set()
    app.state.event_bus = event_bus
    app.state.thumbnail_cache = thumbnail_cache

    def _attach_bus_listener(session: Any) -> None:
        key = (session.session_id, id(session))
        if key in attached_session_ids:
            return
        attached_session_ids.add(key)

        def _on_session_event(ev: SessionEvent) -> None:
            ev_type = str(ev.event_type.value if hasattr(ev.event_type, "value") else ev.event_type)
            if ev_type == SessionEventType.APPROVAL_REQUIRED.value:
                event_bus.publish(
                    "approval_required",
                    session_id=session.session_id,
                    payload=ev.payload,
                )
                event_bus.publish(
                    "session_state_changed",
                    session_id=session.session_id,
                    payload={
                        "state": session.state.value,
                        "step": ev.step,
                        "trigger": ev_type,
                    },
                )
            elif ev_type in (
                SessionEventType.TURN_START.value,
                SessionEventType.APPROVAL_RESOLVED.value,
                SessionEventType.TASK_COMPLETED.value,
                SessionEventType.TURN_FINISHED.value,
                SessionEventType.ABORTED.value,
                SessionEventType.ERROR.value,
            ):
                event_bus.publish(
                    "session_state_changed",
                    session_id=session.session_id,
                    payload={
                        "state": session.state.value,
                        "step": ev.step,
                        "trigger": ev_type,
                    },
                )

        session.add_listener(_on_session_event)

    @app.get("/api/v1/webui/status")
    def webui_status() -> dict[str, Any]:
        """Status of prebuilt WebUI assets."""
        return get_webui_status()

    if webui_dir is None:

        @app.get("/", response_class=HTMLResponse)
        def root_overview() -> str:
            """Friendly root landing page with service status and WebUI guidance."""
            return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AgentEverywhereFlow Daemon Online</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #0b0f19;
            color: #f1f5f9;
            margin: 0;
            padding: 40px 20px;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 80vh;
        }}
        .card {{
            max-width: 640px;
            width: 100%;
            background: #131b2e;
            border: 1px solid #1e293b;
            border-radius: 20px;
            padding: 36px;
            box-shadow: 0 20px 40px rgba(0, 0, 0, 0.6);
            text-align: center;
        }}
        .badge {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 5px 12px;
            background: rgba(16, 185, 129, 0.15);
            border: 1px solid rgba(16, 185, 129, 0.4);
            border-radius: 9999px;
            color: #34d399;
            font-size: 12px;
            font-weight: 600;
            margin-bottom: 20px;
        }}
        .dot {{ width: 8px; height: 8px; border-radius: 50%; background: #34d399; box-shadow: 0 0 8px #34d399; }}
        h1 {{ font-size: 26px; font-weight: 700; margin: 0 0 10px; color: #ffffff; letter-spacing: -0.5px; }}
        h1 span {{ color: #818cf8; }}
        p {{ color: #94a3b8; font-size: 14px; line-height: 1.6; margin: 0 0 24px; }}
        .actions {{ display: flex; gap: 12px; justify-content: center; flex-wrap: wrap; margin-bottom: 28px; }}
        .btn {{
            display: inline-flex;
            align-items: center;
            padding: 10px 20px;
            border-radius: 10px;
            font-size: 13px;
            font-weight: 600;
            text-decoration: none;
            transition: all 0.2s;
        }}
        .btn-primary {{ background: #4f46e5; color: white; box-shadow: 0 4px 12px rgba(79, 70, 229, 0.3); }}
        .btn-primary:hover {{ background: #4338ca; transform: translateY(-1px); }}
        .btn-outline {{ background: #1e293b; color: #cbd5e1; border: 1px solid #334155; }}
        .btn-outline:hover {{ background: #334155; color: white; }}
        .guide-box {{
            background: #0a0f1d;
            border: 1px solid #1e293b;
            border-radius: 12px;
            padding: 16px;
            text-align: left;
            font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
            font-size: 12px;
            color: #cbd5e1;
        }}
        .guide-header {{ color: #818cf8; font-weight: 600; margin-bottom: 8px; font-family: sans-serif; }}
        .code {{ color: #38bdf8; }}
        .comment {{ color: #64748b; }}
    </style>
</head>
<body>
    <div class="card">
        <div class="badge"><span class="dot"></span> BACKEND DAEMON ONLINE • v{__version__}</div>
        <h1>AgentEverywhere<span>Flow</span></h1>
        <p>The headless dialogue service & viewport isolation engine is active and ready to accept API and WebSocket requests.</p>
        
        <div class="actions">
            <a href="/docs" class="btn btn-primary">Swagger API Docs</a>
            <a href="/api/v1/health" class="btn btn-outline">Health Check API</a>
            <a href="/api/v1/targets" class="btn btn-outline">List System Targets</a>
        </div>

        <div class="guide-box">
            <div class="guide-header">How to install &amp; launch the interactive WebUI Console:</div>
            <div class="comment"># Download prebuilt WebUI assets (zero Node.js required):</div>
            <div><span class="code">aef ui install</span></div>
            <div class="comment" style="margin-top: 6px;"># Launch WebUI Studio directly in your browser:</div>
            <div><span class="code">aef ui</span></div>
        </div>
    </div>
</body>
</html>"""

    @app.get("/api/v1/health")
    def health_check() -> dict[str, Any]:
        """System health and readiness check."""
        return {
            "status": "ok",
            "version": __version__,
            "active_sessions": len(session_manager.list_sessions()),
        }

    @app.get("/api/v1/config")
    def get_config() -> dict[str, Any]:
        """Return current runtime configuration with masked API key."""
        return _get_masked_config_payload()

    @app.patch("/api/v1/config")
    def patch_config(req: UpdateConfigRequest) -> dict[str, Any]:
        """Partially update runtime configuration and persist changes to ~/.aef/.env."""
        return _apply_config_patch(req.model_dump(exclude_none=True))

    @app.get("/api/v1/targets")
    def list_targets(
        displays_only: bool = False,
        windows_only: bool = False,
    ) -> list[dict[str, Any]]:
        """List active screens and application windows available for binding."""
        capturer = get_capturer()
        targets = capturer.list_targets(
            include_displays=not windows_only,
            include_windows=not displays_only,
        )
        return [_serialize_target(t) for t in targets]

    @app.get("/api/v1/targets/at-point")
    def target_at_point(
        x: int = Query(..., description="Virtual screen X coordinate"),
        y: int = Query(..., description="Virtual screen Y coordinate"),
        exclude_pid: int | None = Query(
            default=None,
            description="Optional OS PID to ignore (e.g. desktop picker overlay PID)",
        ),
    ) -> dict[str, Any]:
        """Return the topmost target window or display under screen coordinate ``(x, y)``."""
        capturer = get_capturer()
        at_point_fn = getattr(capturer, "target_at_point", None)
        found: TargetInfo | None = None
        used_native = False
        if callable(at_point_fn):
            try:
                candidate = at_point_fn(x, y, exclude_pid=exclude_pid)
                if candidate is None or isinstance(candidate, TargetInfo):
                    found = candidate
                    used_native = True
            except TypeError:
                try:
                    candidate = at_point_fn(x, y)
                    if candidate is None or isinstance(candidate, TargetInfo):
                        found = candidate
                        used_native = True
                except Exception:
                    used_native = False
            except Exception:
                used_native = False

        if not used_native:
            all_targets = capturer.list_targets(include_displays=True, include_windows=True)
            windows = [
                t
                for t in all_targets
                if t.target_type == TargetType.WINDOW
                and not t.is_minimized
                and (exclude_pid is None or getattr(t, "pid", None) != exclude_pid)
            ]
            for t in windows:
                r = t.rect
                if r.x <= x < r.x + r.width and r.y <= y < r.y + r.height:
                    found = t
                    break
            if found is None:
                displays = [t for t in all_targets if t.target_type == TargetType.DISPLAY]
                for d in displays:
                    r = d.rect
                    if r.x <= x < r.x + r.width and r.y <= y < r.y + r.height:
                        found = d
                        break
        elif found is None:
            try:
                display_candidates = capturer.list_targets(
                    include_displays=True, include_windows=False
                )
            except Exception:
                display_candidates = []
            for d in display_candidates:
                if getattr(d, "target_type", None) != TargetType.DISPLAY:
                    continue
                r = d.rect
                if r.x <= x < r.x + r.width and r.y <= y < r.y + r.height:
                    found = d
                    break

        return {"target": _serialize_target(found) if found is not None else None}

    @app.get("/api/v1/targets/cdp")
    def list_cdp_targets(
        endpoint: str = Query(
            default="http://127.0.0.1:9222",
            description="Chrome/Edge/Electron remote debugging HTTP endpoint",
        ),
    ) -> dict[str, Any]:
        """Discover headless Chrome DevTools Protocol page targets."""
        targets = discover_cdp_targets(endpoint=endpoint)
        return {
            "endpoint": endpoint,
            "targets": [t.model_dump() for t in targets],
        }

    @app.get("/api/v1/targets/{target_id}/thumbnail")
    def get_target_thumbnail(
        target_id: str,
        width: int = Query(default=360, ge=32, le=1920),
        quality: int = Query(default=70, ge=10, le=95),
    ) -> Response:
        """Capture and return a resized JPEG thumbnail for a target with 500ms TTL caching."""
        cache_key = (target_id, width, quality)
        cached = thumbnail_cache.get(cache_key)
        if cached is not None:
            return Response(
                content=cached,
                media_type="image/jpeg",
                headers={"Cache-Control": "no-cache", "X-Thumbnail-Cache": "hit"},
            )

        capturer = get_capturer()
        selected: TargetInfo | None = None
        if target_id.lower().startswith("region:"):
            selected = _parse_region_target_id(target_id)
            if selected is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid region target_id '{target_id}'. Expected 'region:x,y,w,h'.",
                )
        else:
            all_targets = capturer.list_targets()
            selected = resolve_target(all_targets, target_id)
            if selected is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"No target matching '{target_id}' found.",
                )

        try:
            jpeg_bytes: bytes | None = None
            thumb_fn = getattr(capturer, "capture_thumbnail", None)
            if callable(thumb_fn):
                try:
                    raw = thumb_fn(selected, max_width=width, quality=quality)
                    if isinstance(raw, (bytes, bytearray)):
                        jpeg_bytes = bytes(raw)
                except Exception:
                    jpeg_bytes = None

            if jpeg_bytes is None:
                img = capturer.capture(selected)
                if img.width > width and img.width > 0:
                    new_height = max(1, int(img.height * (width / img.width)))
                    img = img.resize((width, new_height), Image.Resampling.LANCZOS)
                if img.mode in ("RGBA", "LA", "P"):
                    img = img.convert("RGB")
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=quality)
                jpeg_bytes = buf.getvalue()
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to capture thumbnail for '{target_id}': {exc}",
            ) from exc

        thumbnail_cache.set(cache_key, jpeg_bytes)
        return Response(
            content=jpeg_bytes,
            media_type="image/jpeg",
            headers={"Cache-Control": "no-cache", "X-Thumbnail-Cache": "miss"},
        )

    @app.post("/api/v1/targets/{target_id}/move")
    def move_target(target_id: str, req: MoveTargetRequest) -> dict[str, Any]:
        """Move a window target to the specified display (e.g. virtual display)."""
        capturer = get_capturer()
        all_targets = capturer.list_targets()
        selected = resolve_target(all_targets, target_id)
        if not selected:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No window matching query '{target_id}' found.",
            )
        if selected.target_type != TargetType.WINDOW:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Only window targets can be relocated to another display.",
            )
        disp = resolve_display(all_targets, req.display_id)
        if not disp:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No display matching '{req.display_id}' found.",
            )
        success = capturer.move_window_to_display(selected, disp)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to relocate window '{selected.title}' to {disp.title}.",
            )
        return {
            "success": True,
            "target": {
                "target_id": selected.target_id,
                "title": selected.title,
                "rect": {
                    "x": selected.rect.x,
                    "y": selected.rect.y,
                    "width": selected.rect.width,
                    "height": selected.rect.height,
                },
            },
            "display": {
                "target_id": disp.target_id,
                "title": disp.title,
                "rect": {
                    "x": disp.rect.x,
                    "y": disp.rect.y,
                    "width": disp.rect.width,
                    "height": disp.rect.height,
                },
            },
        }

    @app.post("/api/v1/sessions", status_code=status.HTTP_201_CREATED)
    def create_session(req: CreateSessionRequest) -> dict[str, Any]:
        """Create and bind a new conversational dialogue session (OS window/display, multi-target, region, or CDP)."""
        capturer = get_capturer()
        resolved_targets, cdp_backend, all_targets = _resolve_request_targets(
            target_id=req.target_id,
            target_ids=req.target_ids,
            region=req.region,
            cdp_ws_url=req.cdp_ws_url,
            cdp_endpoint=req.cdp_endpoint,
            title=req.title,
            capturer=capturer,
        )

        # Relocate primary window target to specified display if requested
        if (
            cdp_backend is None
            and req.region is None
            and req.move_to_display
            and resolved_targets[0].target_type == TargetType.WINDOW
        ):
            if not all_targets:
                all_targets = capturer.list_targets()
            disp = resolve_display(all_targets, req.move_to_display)
            if disp:
                capturer.move_window_to_display(resolved_targets[0], disp)

        primary_target = resolved_targets[0]
        session = session_manager.create_session(
            target=primary_target,
            targets=resolved_targets,
            session_id=req.session_id,
            mode=req.mode,
            permission_mode=req.permission_mode,
        )
        if req.title:
            session.title = req.title.strip()
        if cdp_backend is not None:
            session.set_cdp_backend(cdp_backend, target_id=primary_target.target_id)

        # In manual mode on the server, configure approval gate to wait for external approval API
        if req.permission_mode == PermissionMode.MANUAL:
            session.permission_gate.set_handler(session.wait_for_server_approval)

        _attach_bus_listener(session)
        event_bus.publish(
            "session_created",
            session_id=session.session_id,
            payload={
                "session_id": session.session_id,
                "target_id": session.target.target_id,
                "title": session.title or session.target.title,
                "mode": session.mode.value,
                "permission_mode": session.permission_gate.mode.value,
                "state": session.state.value,
                "backend": "cdp" if cdp_backend is not None else "os",
                "target_count": len(session.targets),
            },
        )

        return {
            "session_id": session.session_id,
            "target": {
                "target_id": session.target.target_id,
                "target_type": session.target.target_type.value
                if hasattr(session.target.target_type, "value")
                else str(session.target.target_type),
                "title": session.target.title,
                "rect": {
                    "x": session.target.rect.x,
                    "y": session.target.rect.y,
                    "width": session.target.rect.width,
                    "height": session.target.rect.height,
                },
            },
            "targets": [_serialize_target(t) for t in session.targets],
            "backend": "cdp" if cdp_backend is not None else "os",
            "mode": session.mode.value,
            "permission_mode": session.permission_gate.mode.value,
            "state": session.state.value,
        }

    def _resolve_session(session_id: str) -> Any:
        """Retrieve an active session from memory or restore a persisted session from disk."""
        session = session_manager.get_session(session_id)
        if session is None:
            session = session_manager.restore_session(session_id)
            if session:
                session.permission_gate.set_handler(session.wait_for_server_approval)
        if session is not None:
            _attach_bus_listener(session)
        return session

    @app.get("/api/v1/sessions")
    def list_sessions() -> list[dict[str, Any]]:
        """List all active dialogue sessions (including persisted sessions from disk)."""
        active = session_manager.list_sessions()
        active_ids = {s.session_id for s in active}
        for meta in session_manager.list_all_stored():
            if meta.session_id not in active_ids:
                restored = session_manager.restore_session(meta.session_id)
                if restored:
                    restored.permission_gate.set_handler(restored.wait_for_server_approval)
                    active.append(restored)
                    active_ids.add(restored.session_id)

        return [
            {
                "session_id": s.session_id,
                "target_id": s.target.target_id,
                "title": getattr(s, "title", "") or s.target.title,
                "target_title": s.target.title,
                "mode": s.mode.value,
                "permission_mode": s.permission_gate.mode.value,
                "state": s.state.value,
                "turn_count": s.turn_count,
                "total_steps": s.total_steps,
                "created_at": getattr(s, "created_at", 0),
            }
            for s in active
        ]

    @app.get("/api/v1/sessions/{session_id}")
    def get_session(session_id: str) -> dict[str, Any]:
        """Get state and metadata for a specific session."""
        session = _resolve_session(session_id)
        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

        pending_dict = None
        if session.pending_approval:
            pending_dict = session.pending_approval.model_dump()

        return {
            "session_id": session.session_id,
            "title": getattr(session, "title", "") or session.target.title,
            "target": {
                "target_id": session.target.target_id,
                "target_type": session.target.target_type.value
                if hasattr(session.target.target_type, "value")
                else str(session.target.target_type),
                "title": session.target.title,
                "rect": {
                    "x": session.target.rect.x,
                    "y": session.target.rect.y,
                    "width": session.target.rect.width,
                    "height": session.target.rect.height,
                },
            },
            "targets": [
                _serialize_target(t) for t in getattr(session, "targets", [session.target])
            ],
            "backend": "cdp" if getattr(session, "cdp_backend", None) is not None else "os",
            "mode": session.mode.value,
            "permission_mode": session.permission_gate.mode.value,
            "state": session.state.value,
            "turn_count": session.turn_count,
            "total_steps": session.total_steps,
            "pending_approval": pending_dict,
            "recorded_steps": getattr(session, "recorded_steps", []),
        }

    @app.get("/api/v1/sessions/{session_id}/history")
    def get_session_history(session_id: str) -> dict[str, Any]:
        """Fetch past messages and recorded action steps for a session."""
        session = _resolve_session(session_id)
        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        return {
            "session_id": session.session_id,
            "events": [e.model_dump() for e in getattr(session, "events", [])],
            "recorded_steps": getattr(session, "recorded_steps", []),
            "turn_count": session.turn_count,
            "total_steps": session.total_steps,
        }

    @app.post("/api/v1/sessions/{session_id}/message")
    def send_message(
        session_id: str,
        req: SendMessageRequest,
        background_tasks: BackgroundTasks,
    ) -> Any:
        """Send an instruction to the dialogue session."""
        session = _resolve_session(session_id)
        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

        if req.async_execution:
            # Run in thread pool to not block event loop
            def run_turn() -> None:
                try:
                    session.execute_turn(req.instruction, max_steps=req.max_steps)
                except Exception as exc:
                    logger.exception(
                        "Unhandled exception during execute_turn in session %s: %s",
                        session_id,
                        exc,
                    )
                    session.state = SessionState.ERROR
                    session.emit_event(
                        SessionEventType.ERROR,
                        step=session.turn_count,
                        payload={"error": f"Internal execution error: {exc}"},
                    )

            thread = threading.Thread(target=run_turn, daemon=True)
            thread.start()
            return {
                "status": "processing",
                "session_id": session_id,
                "turn": session.turn_count + 1,
            }

        # Synchronous execution
        res = session.execute_turn(req.instruction, max_steps=req.max_steps)
        return res.model_dump()

    @app.get("/api/v1/sessions/{session_id}/approval")
    def get_pending_approval(session_id: str) -> dict[str, Any] | None:
        """Fetch pending approval request for session if waiting."""
        session = _resolve_session(session_id)
        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        if not session.pending_approval:
            return None
        return session.pending_approval.model_dump()

    @app.post("/api/v1/sessions/{session_id}/approval")
    def submit_approval(session_id: str, req: SubmitApprovalRequest) -> dict[str, Any]:
        """Approve or reject a pending action in manual permission mode."""
        session = _resolve_session(session_id)
        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

        decision = ApprovalDecision(approved=req.approved, reason=req.reason)
        success = session.submit_approval(decision)
        if not success:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No pending approval request found for this session.",
            )
        return {"status": "ok", "approved": req.approved}

    @app.post("/api/v1/sessions/{session_id}/permission")
    def update_permission(session_id: str, req: UpdatePermissionRequest) -> dict[str, Any]:
        """Toggle session permission mode (auto or manual)."""
        session = _resolve_session(session_id)
        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

        session.set_permission_mode(req.mode)
        if req.mode == PermissionMode.MANUAL:
            session.permission_gate.set_handler(session.wait_for_server_approval)
        else:
            session.permission_gate.set_handler(None)

        event_bus.publish(
            "session_state_changed",
            session_id=session.session_id,
            payload={
                "state": session.state.value,
                "permission_mode": req.mode.value,
                "trigger": "permission_updated",
            },
        )
        return {"status": "ok", "permission_mode": req.mode.value}

    @app.post("/api/v1/sessions/{session_id}/target")
    def switch_target(session_id: str, req: SwitchTargetRequest) -> dict[str, Any]:
        """Rebind a session to new window(s), display, region, or CDP tab while preserving context."""
        session = _resolve_session(session_id)
        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

        capturer = get_capturer()
        bound_targets: list[TargetInfo] = list(getattr(session, "targets", []))
        resolved_targets, cdp_backend, _ = _resolve_request_targets(
            target_id=req.target_id,
            target_ids=req.target_ids,
            region=req.region,
            cdp_ws_url=req.cdp_ws_url,
            cdp_endpoint=req.cdp_endpoint,
            title=req.title,
            capturer=capturer,
            fallback_targets=bound_targets,
        )

        session.rebind_targets(
            resolved_targets,
            replace_pool=req.replace_targets,
            cdp_backend=cdp_backend,
        )

        active_backend = "cdp" if getattr(session, "cdp_backend", None) is not None else "os"
        serialized_target = _serialize_target(session.target)
        serialized_targets = [_serialize_target(t) for t in session.targets]
        event_bus.publish(
            "session_target_switched",
            session_id=session.session_id,
            payload={
                "session_id": session.session_id,
                "target_id": session.target.target_id,
                "title": session.target.title,
                "target": serialized_target,
                "targets": serialized_targets,
                "backend": active_backend,
                "context_preserved": True,
                "turn_count": session.turn_count,
            },
        )
        return {
            "status": "ok",
            "context_preserved": True,
            "turn_count": session.turn_count,
            "backend": active_backend,
            "target": serialized_target,
            "targets": serialized_targets,
        }

    @app.post("/api/v1/sessions/{session_id}/reset")
    def reset_session(session_id: str) -> dict[str, Any]:
        """Reset conversation context while keeping target window."""
        session = _resolve_session(session_id)
        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        session.reset_history()
        event_bus.publish(
            "session_state_changed",
            session_id=session.session_id,
            payload={"state": session.state.value, "trigger": "reset"},
        )
        return {"status": "ok", "message": "Session conversation history cleared."}

    @app.post("/api/v1/sessions/{session_id}/abort")
    def abort_session(session_id: str) -> dict[str, Any]:
        """Abort active turn execution immediately for this session."""
        session = _resolve_session(session_id)
        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        session.abort()
        event_bus.publish(
            "session_state_changed",
            session_id=session.session_id,
            payload={"state": session.state.value, "trigger": "abort"},
        )
        return {"status": "ok", "message": f"Session '{session_id}' aborted."}

    @app.get("/api/v1/sessions/{session_id}/screenshot")
    def get_screenshot(session_id: str) -> Response:
        """Capture and stream current target viewport screenshot."""
        session = _resolve_session(session_id)
        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

        try:
            img = session.capture_frame()
            buffer = io.BytesIO()
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            img.save(buffer, format="JPEG", quality=85)
            buffer.seek(0)
            return StreamingResponse(buffer, media_type="image/jpeg")
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to capture screenshot: {e}",
            ) from e

    @app.patch("/api/v1/sessions/{session_id}")
    def update_session(session_id: str, req: UpdateSessionRequest) -> dict[str, Any]:
        """Update session metadata such as title / rename."""
        session = _resolve_session(session_id)
        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        if req.title is not None:
            session.title = req.title.strip()
            session.save()
        return {
            "status": "ok",
            "session_id": session.session_id,
            "title": getattr(session, "title", "") or session.target.title,
        }

    @app.get("/api/v1/sessions/{session_id}/export")
    def export_session(
        session_id: str,
        format: str = "python",
        workflow_name: str | None = None,
    ) -> dict[str, Any]:
        """Export recorded session trajectory to Python script, YAML workflow, or Markdown report."""
        session = _resolve_session(session_id)
        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

        exporter = WorkflowExporter()
        spec = exporter.from_session(session, name=workflow_name or f"session_{session_id}")
        fmt = format.lower().strip()
        if fmt == "python":
            content = exporter.to_python_script(spec)
        elif fmt == "yaml":
            content = exporter.to_yaml(spec)
        elif fmt == "markdown":
            content = exporter.to_markdown(spec)
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported export format '{format}'. Expected 'python', 'yaml', or 'markdown'.",
            )

        return {
            "session_id": session_id,
            "format": fmt,
            "content": content,
            "step_count": len(spec.steps),
            "workflow": spec.model_dump(mode="json"),
        }

    @app.post("/api/v1/workflows/run")
    def run_workflow(req: RunWorkflowRequest) -> dict[str, Any]:
        """Validate (dry-run) or execute a recorded workflow from YAML or a session ID."""
        exporter = WorkflowExporter()
        if req.workflow_yaml:
            try:
                spec = exporter.from_yaml(req.workflow_yaml)
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid workflow_yaml: {exc}",
                ) from exc
        elif req.session_id:
            session = _resolve_session(req.session_id)
            if not session:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Session '{req.session_id}' not found",
                )
            spec = exporter.from_session(session)
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Either 'workflow_yaml' or 'session_id' must be provided.",
            )

        if req.target_id:
            for step in spec.steps:
                step.target = req.target_id

        if req.dry_run:
            return {
                "status": "dry_run",
                "steps": len(spec.steps),
                "steps_executed": len(spec.steps),
                "workflow": spec.model_dump(mode="json"),
            }

        runner = WorkflowRunner()
        speed = max(0.1, 0.3 / max(0.05, req.step_delay))
        result = runner.play(spec, speed=speed, dry_run=False)
        return {
            "status": "completed" if result.success else "failed",
            "steps_executed": result.executed_steps,
            "total_steps": result.total_steps,
            "elapsed_seconds": result.elapsed_seconds,
            "error": result.error or None,
            "workflow": spec.model_dump(mode="json"),
        }

    @app.delete("/api/v1/sessions/{session_id}")
    def delete_session(session_id: str) -> dict[str, Any]:
        """Terminate and remove a dialogue session."""
        existing = session_manager.get_session(session_id)
        if existing is not None:
            cdp = getattr(existing, "cdp_backend", None)
            if cdp is not None and hasattr(cdp, "close"):
                try:
                    cdp.close()
                except Exception:
                    pass
        success = session_manager.close_session(session_id, delete_storage=True)
        if not success:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        event_bus.publish(
            "session_deleted",
            session_id=session_id,
            payload={"session_id": session_id},
        )
        return {"status": "ok", "message": f"Session '{session_id}' closed."}

    @app.websocket("/api/v1/events")
    async def global_events_websocket(websocket: WebSocket) -> None:
        """Global lightweight event bus WebSocket for Desktop Tray, Status Pill, and multi-session sync."""
        await websocket.accept()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        loop = asyncio.get_running_loop()
        event_bus.subscribe(loop, queue)

        async def _sender() -> None:
            while True:
                msg = await queue.get()
                await websocket.send_json(msg)

        send_task = asyncio.create_task(_sender())
        try:
            while True:
                raw = await websocket.receive_text()
                if raw.strip() in ("ping", '{"action":"ping"}', '{"action": "ping"}'):
                    await websocket.send_json({"event_type": "pong", "timestamp": time.time()})
        except WebSocketDisconnect:
            pass
        finally:
            send_task.cancel()
            event_bus.unsubscribe(queue)

    @app.websocket("/api/v1/sessions/{session_id}/ws")
    async def session_websocket(websocket: WebSocket, session_id: str) -> None:
        """Bidirectional WebSocket for real-time events, streaming reasoning, and approvals."""
        await websocket.accept()
        session = _resolve_session(session_id)
        if not session:
            await websocket.send_json(
                {
                    "session_id": session_id,
                    "event_type": "error",
                    "step": 0,
                    "payload": {"error": f"Session '{session_id}' not found."},
                }
            )
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Session not found")
            return
        event_queue: asyncio.Queue[SessionEvent] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def on_event(ev: SessionEvent) -> None:
            loop.call_soon_threadsafe(event_queue.put_nowait, ev)

        session.add_listener(on_event)

        async def send_events() -> None:
            while True:
                ev = await event_queue.get()
                await websocket.send_json(ev.model_dump())

        send_task = asyncio.create_task(send_events())

        try:
            while True:
                data = await websocket.receive_json()
                action = data.get("action")
                if action == "message":
                    instruction = data.get("instruction", "")
                    max_steps = int(data.get("max_steps", 100))
                    logger.info(
                        "WS received message for session %s (bound target='%s'): %s",
                        session.session_id,
                        session.target.title,
                        instruction,
                    )

                    def run_ws_turn(inst: str = instruction, steps: int = max_steps) -> None:
                        try:
                            session.execute_turn(inst, max_steps=steps)
                        except Exception as exc:
                            logger.exception(
                                "Unhandled exception during WS execute_turn in session %s: %s",
                                session.session_id,
                                exc,
                            )
                            session.state = SessionState.ERROR
                            session.emit_event(
                                SessionEventType.ERROR,
                                step=session.turn_count,
                                payload={"error": f"Internal execution error: {exc}"},
                            )

                    # Run turn in background thread
                    threading.Thread(
                        target=run_ws_turn,
                        daemon=True,
                    ).start()
                elif action == "abort":
                    session.abort()
                elif action == "approval":
                    approved = bool(data.get("approved", True))
                    reason = data.get("reason")
                    session.submit_approval(ApprovalDecision(approved=approved, reason=reason))
                elif action == "permission":
                    perm_str = data.get("mode", "auto")
                    new_perm = PermissionMode(perm_str)
                    session.set_permission_mode(new_perm)
                    if new_perm == PermissionMode.MANUAL:
                        session.permission_gate.set_handler(session.wait_for_server_approval)
                    else:
                        session.permission_gate.set_handler(None)
        except WebSocketDisconnect:
            pass
        finally:
            send_task.cancel()
            session.remove_listener(on_event)

    if webui_dir is not None:
        from fastapi.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=str(webui_dir), html=True), name="webui")

    return app
