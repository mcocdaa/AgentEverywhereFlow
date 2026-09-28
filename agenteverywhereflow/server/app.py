"""FastAPI Dialogue Service Application for AgentEverywhereFlow (aef serve)."""

import asyncio
import io
import threading
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from agenteverywhereflow import __version__
from agenteverywhereflow.capturer import get_capturer
from agenteverywhereflow.capturer.selector import resolve_target
from agenteverywhereflow.config import ExecutionMode
from agenteverywhereflow.security.permission import ApprovalDecision, PermissionMode
from agenteverywhereflow.session.manager import session_manager
from agenteverywhereflow.session.state import SessionEvent


# Request & Response Schemas
class CreateSessionRequest(BaseModel):
    target_id: str = Field(
        description="Target ID, native handle, table index, or process/title query"
    )
    mode: ExecutionMode = Field(default=ExecutionMode.MINIMAL_PYTHON)
    permission_mode: PermissionMode = Field(default=PermissionMode.AUTO)
    session_id: str | None = Field(default=None)


class SendMessageRequest(BaseModel):
    instruction: str = Field(description="Instruction or task to execute in this dialogue turn")
    max_steps: int = Field(default=15, description="Max steps for this turn")
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

    @app.get("/api/v1/sessions")
    def list_sessions() -> list[dict[str, Any]]:
        """List all active dialogue sessions."""
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
            for s in session_manager.list_sessions()
        ]

    @app.get("/api/v1/sessions/{session_id}")
    def get_session(session_id: str) -> dict[str, Any]:
        """Get state and metadata for a specific session."""
        session = session_manager.get_session(session_id)
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
        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

        if req.async_execution:
            # Run in thread pool to not block event loop
            def run_turn() -> None:
                session.execute_turn(req.instruction, max_steps=req.max_steps)

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
        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        if not session.pending_approval:
            return None
        return session.pending_approval.model_dump()

    @app.post("/api/v1/sessions/{session_id}/approval")
    def submit_approval(session_id: str, req: SubmitApprovalRequest) -> dict[str, Any]:
        """Approve or reject a pending action in manual permission mode."""
        session = session_manager.get_session(session_id)
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
        session = session_manager.get_session(session_id)
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
        session = session_manager.get_session(session_id)
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
        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        session.reset_history()
        return {"status": "ok", "message": "Session conversation history cleared."}

    @app.get("/api/v1/sessions/{session_id}/screenshot")
    def get_screenshot(session_id: str) -> Response:
        """Capture and stream current target viewport screenshot."""
        session = session_manager.get_session(session_id)
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
        success = session_manager.close_session(session_id)
        if not success:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        return {"status": "ok", "message": f"Session '{session_id}' closed."}

    @app.websocket("/api/v1/sessions/{session_id}/ws")
    async def session_websocket(websocket: WebSocket, session_id: str) -> None:
        """Bidirectional WebSocket for real-time events, streaming reasoning, and approvals."""
        session = session_manager.get_session(session_id)
        if not session:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        await websocket.accept()
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
                    max_steps = int(data.get("max_steps", 15))
                    # Run turn in background thread
                    threading.Thread(
                        target=session.execute_turn,
                        args=(instruction, max_steps),
                        daemon=True,
                    ).start()
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

    return app
