"""FastAPI Dialogue Service Application for AgentEverywhereFlow (aef serve)."""

import asyncio
import io
import logging
import threading
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from agenteverywhereflow import __version__
from agenteverywhereflow.capturer import get_capturer
from agenteverywhereflow.capturer.selector import resolve_target
from agenteverywhereflow.config import ExecutionMode
from agenteverywhereflow.security.permission import ApprovalDecision, PermissionMode
from agenteverywhereflow.server.webui import get_webui_dist_path, get_webui_status
from agenteverywhereflow.session.manager import session_manager
from agenteverywhereflow.session.state import SessionEvent, SessionEventType, SessionState

logger = logging.getLogger(__name__)


# Request & Response Schemas
class CreateSessionRequest(BaseModel):
    target_id: str = Field(
        description="Target ID, native handle, table index, or process/title query"
    )
    mode: ExecutionMode = Field(default=ExecutionMode.MINIMAL_PYTHON)
    permission_mode: PermissionMode = Field(default=PermissionMode.AUTO)
    session_id: str | None = Field(default=None)

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
    target_id: str = Field(description="New target identifier to bind session to")


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
            <a href="/docs" class="btn btn-primary">📖 Swagger API Docs</a>
            <a href="/api/v1/health" class="btn btn-outline">⚡ Health Check API</a>
            <a href="/api/v1/targets" class="btn btn-outline">🖥️ List System Targets</a>
        </div>

        <div class="guide-box">
            <div class="guide-header">💡 How to launch the interactive WebUI Console:</div>
            <div class="comment"># In your terminal, launch the companion frontend:</div>
            <div>cd <span class="code">AgentEverywhereFlow-WebUI</span></div>
            <div><span class="code">pnpm dev</span></div>
            <div class="comment" style="margin-top: 6px;"># Open in browser: <span style="color: #a5b4fc;">http://localhost:5173</span></div>
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
        return [
            {
                "target_id": t.target_id,
                "target_type": t.target_type.value,
                "title": t.title,
                "native_handle": t.native_handle,
                "process_name": t.process_name,
                "is_minimized": t.is_minimized,
                "rect": {
                    "x": t.rect.x,
                    "y": t.rect.y,
                    "width": t.rect.width,
                    "height": t.rect.height,
                },
            }
            for t in targets
        ]

    @app.post("/api/v1/sessions", status_code=status.HTTP_201_CREATED)
    def create_session(req: CreateSessionRequest) -> dict[str, Any]:
        """Create and bind a new conversational dialogue session."""
        capturer = get_capturer()
        all_targets = capturer.list_targets()
        selected = resolve_target(all_targets, req.target_id)
        if not selected:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No window or screen matching query '{req.target_id}' found.",
            )

        session = session_manager.create_session(
            target=selected,
            session_id=req.session_id,
            mode=req.mode,
            permission_mode=req.permission_mode,
        )

        # In manual mode on the server, configure approval gate to wait for external approval API
        if req.permission_mode == PermissionMode.MANUAL:
            session.permission_gate.set_handler(session.wait_for_server_approval)

        return {
            "session_id": session.session_id,
            "target": {
                "target_id": session.target.target_id,
                "title": session.target.title,
                "rect": {
                    "width": session.target.rect.width,
                    "height": session.target.rect.height,
                },
            },
            "mode": session.mode.value,
            "permission_mode": session.permission_gate.mode.value,
            "state": session.state.value,
        }

    def _resolve_session(session_id: str) -> Any:
        """Retrieve an active session from memory or restore a persisted session from disk."""
        return session_manager.get_session(session_id) or session_manager.restore_session(
            session_id
        )

    @app.get("/api/v1/sessions")
    def list_sessions() -> list[dict[str, Any]]:
        """List all active dialogue sessions (including persisted sessions from disk)."""
        active = session_manager.list_sessions()
        active_ids = {s.session_id for s in active}
        for meta in session_manager.list_all_stored():
            if meta.session_id not in active_ids:
                restored = session_manager.restore_session(meta.session_id)
                if restored:
                    active.append(restored)
                    active_ids.add(restored.session_id)

        return [
            {
                "session_id": s.session_id,
                "target_id": s.target.target_id,
                "title": s.target.title,
                "mode": s.mode.value,
                "permission_mode": s.permission_gate.mode.value,
                "state": s.state.value,
                "turn_count": s.turn_count,
                "total_steps": s.total_steps,
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
            "target": {
                "target_id": session.target.target_id,
                "title": session.target.title,
                "rect": {
                    "x": session.target.rect.x,
                    "y": session.target.rect.y,
                    "width": session.target.rect.width,
                    "height": session.target.rect.height,
                },
            },
            "mode": session.mode.value,
            "permission_mode": session.permission_gate.mode.value,
            "state": session.state.value,
            "turn_count": session.turn_count,
            "total_steps": session.total_steps,
            "pending_approval": pending_dict,
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

        return {"status": "ok", "permission_mode": req.mode.value}

    @app.post("/api/v1/sessions/{session_id}/target")
    def switch_target(session_id: str, req: SwitchTargetRequest) -> dict[str, Any]:
        """Switch target window or screen for this session."""
        session = _resolve_session(session_id)
        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

        capturer = get_capturer()
        all_targets = capturer.list_targets()
        new_target = resolve_target(all_targets, req.target_id)
        if not new_target:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Target query '{req.target_id}' could not be resolved.",
            )

        session.switch_target(new_target)
        return {
            "status": "ok",
            "target": {
                "target_id": new_target.target_id,
                "title": new_target.title,
            },
        }

    @app.post("/api/v1/sessions/{session_id}/reset")
    def reset_session(session_id: str) -> dict[str, Any]:
        """Reset conversation context while keeping target window."""
        session = _resolve_session(session_id)
        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        session.reset_history()
        return {"status": "ok", "message": "Session conversation history cleared."}

    @app.post("/api/v1/sessions/{session_id}/abort")
    def abort_session(session_id: str) -> dict[str, Any]:
        """Abort active turn execution immediately for this session."""
        session = _resolve_session(session_id)
        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        session.abort()
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

    @app.delete("/api/v1/sessions/{session_id}")
    def delete_session(session_id: str) -> dict[str, Any]:
        """Terminate and remove a dialogue session."""
        success = session_manager.close_session(session_id, delete_storage=True)
        if not success:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        return {"status": "ok", "message": f"Session '{session_id}' closed."}

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
                    # Run turn in background thread
                    threading.Thread(
                        target=session.execute_turn,
                        args=(instruction, max_steps),
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
