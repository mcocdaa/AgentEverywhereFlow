"""Unit tests for pluggable ActionBackend, OSGuiBackend, and CDPActionBackend."""

from __future__ import annotations

import asyncio
import base64
import http.server
import io
import json
import threading
from typing import Any
from unittest.mock import MagicMock

import pytest
import websockets
from PIL import Image

from agenteverywhereflow.actions.backend import ActionBackend, OSGuiBackend
from agenteverywhereflow.actions.cdp_backend import (
    CDP_MODIFIER_ALT,
    CDP_MODIFIER_CTRL,
    CDP_MODIFIER_META,
    CDP_MODIFIER_SHIFT,
    CDPActionBackend,
    CDPError,
    CDPTargetInfo,
    discover_cdp_targets,
    resolve_cdp_target,
)
from agenteverywhereflow.capturer.base import Rect, TargetInfo, TargetType
from agenteverywhereflow.config import ExecutionMode
from agenteverywhereflow.session.session import ChatSession


def _make_png_b64(
    width: int = 320, height: int = 240, color: tuple[int, int, int] = (12, 34, 56)
) -> str:
    img = Image.new("RGB", (width, height), color=color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


class _MockCDPServer:
    """In-process mock Chrome DevTools Protocol WebSocket server on an ephemeral port."""

    def __init__(self, png_b64: str | None = None, fail_method: str | None = None) -> None:
        self.png_b64 = png_b64 or _make_png_b64(320, 240, (25, 100, 200))
        self.fail_method = fail_method
        self.received: list[dict[str, Any]] = []
        self._loop = asyncio.new_event_loop()
        self._thread: threading.Thread | None = None
        self._server: Any = None
        self.ws_url: str = ""

    async def _handler(self, websocket: Any) -> None:
        async for raw in websocket:
            req = json.loads(raw)
            cmd_id = req["id"]
            method = req["method"]
            params = req.get("params", {})
            self.received.append({"method": method, "params": params})

            # Inject an unsolicited asynchronous CDP domain event before replying
            await websocket.send(
                json.dumps({"method": "Page.frameNavigated", "params": {"frame": {"id": "main"}}})
            )

            if self.fail_method and method == self.fail_method:
                await websocket.send(
                    json.dumps(
                        {"id": cmd_id, "error": {"code": -32000, "message": "Mock CDP failure"}}
                    )
                )
                continue

            if method == "Page.getLayoutMetrics":
                result: dict[str, Any] = {
                    "cssLayoutViewport": {"clientWidth": 320, "clientHeight": 240},
                    "layoutViewport": {"clientWidth": 320, "clientHeight": 240},
                }
            elif method == "Page.captureScreenshot":
                result = {"data": self.png_b64}
            elif method == "Accessibility.getFullAXTree":
                result = {
                    "nodes": [
                        {
                            "nodeId": "1",
                            "ignored": False,
                            "role": {"type": "role", "value": "RootWebArea"},
                            "name": {"type": "computedString", "value": "Mock App"},
                            "backendDOMNodeId": 10,
                        },
                        {
                            "nodeId": "2",
                            "ignored": True,
                            "role": {"type": "role", "value": "none"},
                            "name": {"type": "computedString", "value": "Hidden"},
                        },
                        {
                            "nodeId": "3",
                            "ignored": False,
                            "role": {"type": "role", "value": "button"},
                            "name": {"type": "computedString", "value": "Submit"},
                            "value": {"type": "string", "value": ""},
                            "backendDOMNodeId": 42,
                        },
                        {
                            "nodeId": "4",
                            "ignored": False,
                            "role": {"type": "role", "value": "textbox"},
                            "name": {"type": "computedString", "value": "Search"},
                            "value": {"type": "string", "value": "hello"},
                            "backendDOMNodeId": 43,
                        },
                    ]
                }
            else:
                result = {}

            await websocket.send(json.dumps({"id": cmd_id, "result": result}))

    def start(self) -> None:
        ready = threading.Event()

        async def _serve() -> None:
            self._server = await websockets.serve(self._handler, "127.0.0.1", 0)
            sock = next(iter(self._server.sockets))
            port = sock.getsockname()[1]
            self.ws_url = f"ws://127.0.0.1:{port}/devtools/page/TEST1"
            ready.set()

        def _run() -> None:
            asyncio.set_event_loop(self._loop)
            self._loop.run_until_complete(_serve())
            self._loop.run_forever()

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()
        assert ready.wait(timeout=5.0), "Mock CDP WS server failed to start"

    def stop(self) -> None:
        if self._server is not None:

            async def _shutdown() -> None:
                self._server.close()
                await self._server.wait_closed()

            fut = asyncio.run_coroutine_threadsafe(_shutdown(), self._loop)
            try:
                fut.result(timeout=3.0)
            except Exception:
                pass
        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        self._loop.close()


def test_action_backend_protocol_conformance() -> None:
    """Verify both OSGuiBackend and CDPActionBackend satisfy ActionBackend Protocol."""
    target = TargetInfo(
        target_id="hwnd:0x42",
        target_type=TargetType.WINDOW,
        title="Editor",
        rect=Rect(x=100, y=50, width=800, height=600),
        native_handle=0x42,
    )
    os_backend = OSGuiBackend(target=target, capturer=MagicMock(), input_driver=MagicMock())
    cdp_backend = CDPActionBackend(ws_url="ws://127.0.0.1:9222/devtools/page/1")

    assert isinstance(os_backend, ActionBackend)
    assert isinstance(cdp_backend, ActionBackend)


def test_os_gui_backend_projection_and_window_handle_safety() -> None:
    """Verify OSGuiBackend projects viewport coordinates and zeroes window_handle for DISPLAY targets."""
    mock_capturer = MagicMock()
    mock_capturer.capture.return_value = Image.new("RGB", (800, 600), "white")
    mock_driver = MagicMock()

    # Window target passes native_handle
    win_target = TargetInfo(
        target_id="xwin:0x100",
        target_type=TargetType.WINDOW,
        title="Demo Window",
        rect=Rect(x=100, y=200, width=800, height=600),
        native_handle=0x100,
    )
    win_backend = OSGuiBackend(win_target, capturer=mock_capturer, input_driver=mock_driver)
    assert win_backend.capture().size == (800, 600)
    win_backend.click(50, 60)
    win_backend.double_click(10, 20)
    win_backend.right_click(30, 40)
    win_backend.move_to(70, 80)
    win_backend.drag(10, 10, 90, 90, duration=0.2)
    win_backend.scroll(50, 50, -3)
    win_backend.type_text("hello")
    win_backend.press_key("enter")
    win_backend.hotkey("ctrl", "a")
    assert win_backend.get_accessibility_tree() == []

    first_click = mock_driver.click.call_args_list[0].kwargs
    assert first_click["x"] == 150
    assert first_click["y"] == 260
    assert first_click["window_handle"] == 0x100

    # Display target must NEVER leak native_handle=1 into window-level driver calls
    disp_target = TargetInfo(
        target_id="display:1",
        target_type=TargetType.DISPLAY,
        title="Display 1",
        rect=Rect(x=0, y=0, width=1920, height=1080),
        native_handle=1,
    )
    mock_driver.reset_mock()
    disp_backend = OSGuiBackend(disp_target, capturer=mock_capturer, input_driver=mock_driver)
    disp_backend.click(100, 100)
    assert mock_driver.click.call_args.kwargs["window_handle"] == 0


def test_discover_cdp_targets_filters_pages_and_handles_offline() -> None:
    """Verify discover_cdp_targets queries /json/list, filters pages, and returns [] offline."""
    payload = [
        {
            "id": "PAGE_A",
            "type": "page",
            "title": "Dashboard",
            "url": "https://example.com/dashboard",
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/PAGE_A",
            "description": "Main tab",
        },
        {
            "id": "SW_1",
            "type": "service_worker",
            "title": "Worker",
            "url": "https://example.com/sw.js",
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/worker/SW_1",
        },
        {
            "id": "PAGE_NO_WS",
            "type": "page",
            "title": "No WS",
            "url": "about:blank",
        },
        {
            "id": "PAGE_B",
            "type": "page",
            "title": "Docs",
            "url": "https://docs.example.com",
            "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/PAGE_B",
        },
    ]

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/json/list":
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return None

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        endpoint = f"http://127.0.0.1:{port}"
        targets = discover_cdp_targets(endpoint=endpoint, timeout=2.0)
        assert len(targets) == 2
        assert isinstance(targets[0], CDPTargetInfo)
        assert targets[0].id == "PAGE_A"
        assert targets[0].target_id == "cdp:PAGE_A"
        assert targets[0].title == "Dashboard"
        assert targets[0].url == "https://example.com/dashboard"
        assert targets[0].ws_url == "ws://127.0.0.1:9222/devtools/page/PAGE_A"

        ti = targets[0].to_target_info(1440, 900)
        assert ti.target_id == "cdp:PAGE_A"
        assert ti.process_name == "cdp"
        assert ti.rect == Rect(0, 0, 1440, 900)

        resolved = resolve_cdp_target("cdp:PAGE_B", endpoint=endpoint)
        assert resolved is not None
        assert resolved.title == "Docs"
    finally:
        server.shutdown()
        server.server_close()

    # Offline endpoint must return [] gracefully without raising
    assert discover_cdp_targets(endpoint="http://127.0.0.1:1", timeout=0.3) == []


def test_cdp_backend_sync_operations_end_to_end() -> None:
    """Verify every CDPActionBackend sync method against a real local WebSocket mock server."""
    srv = _MockCDPServer()
    srv.start()
    try:
        with CDPActionBackend(ws_url=srv.ws_url, timeout=5.0) as cdp:
            # 1. capture()
            img = cdp.capture()
            assert img.size == (320, 240)
            assert img.getpixel((5, 5)) == (25, 100, 200)
            assert cdp.viewport_width == 320
            assert cdp.viewport_height == 240

            # 2. click()
            srv.received.clear()
            cdp.click(120, 80)
            assert [c["method"] for c in srv.received] == [
                "Input.dispatchMouseEvent",
                "Input.dispatchMouseEvent",
                "Input.dispatchMouseEvent",
            ]
            assert [c["params"]["type"] for c in srv.received] == [
                "mouseMoved",
                "mousePressed",
                "mouseReleased",
            ]
            assert srv.received[1]["params"]["button"] == "left"
            assert srv.received[1]["params"]["clickCount"] == 1

            # 3. double_click()
            srv.received.clear()
            cdp.double_click(50, 60)
            types = [c["params"]["type"] for c in srv.received]
            assert types == [
                "mouseMoved",
                "mousePressed",
                "mouseReleased",
                "mousePressed",
                "mouseReleased",
            ]
            assert srv.received[3]["params"]["clickCount"] == 2
            assert srv.received[4]["params"]["clickCount"] == 2

            # 4. right_click()
            srv.received.clear()
            cdp.right_click(200, 150)
            assert srv.received[1]["params"]["button"] == "right"
            assert srv.received[1]["params"]["buttons"] == 2

            # 5. move_to()
            srv.received.clear()
            cdp.move_to(15, 25)
            assert len(srv.received) == 1
            assert srv.received[0]["params"]["type"] == "mouseMoved"
            assert srv.received[0]["params"]["x"] == 15.0
            assert srv.received[0]["params"]["y"] == 25.0

            # 6. drag()
            srv.received.clear()
            cdp.drag(10, 10, 110, 60, duration=0.0)
            assert srv.received[0]["params"]["type"] == "mouseMoved"
            assert srv.received[1]["params"]["type"] == "mousePressed"
            assert srv.received[-1]["params"]["type"] == "mouseReleased"
            assert srv.received[-1]["params"]["x"] == 110.0
            assert srv.received[-1]["params"]["y"] == 60.0

            # 7. scroll() — negative scrolls down (+deltaY), positive scrolls up (-deltaY)
            srv.received.clear()
            cdp.scroll(100, 120, -3)
            cdp.scroll(100, 120, 2)
            assert srv.received[0]["params"]["type"] == "mouseWheel"
            assert srv.received[0]["params"]["deltaY"] == 360.0
            assert srv.received[1]["params"]["deltaY"] == -240.0

            # 8. type_text() with Unicode / Chinese / emoji
            srv.received.clear()
            cdp.type_text("你好 AEFlow 🚀")
            assert srv.received == [
                {"method": "Input.insertText", "params": {"text": "你好 AEFlow 🚀"}}
            ]

            # 9. press_key()
            srv.received.clear()
            cdp.press_key("enter")
            assert len(srv.received) == 2
            assert srv.received[0]["method"] == "Input.dispatchKeyEvent"
            assert srv.received[0]["params"]["type"] == "rawKeyDown"
            assert srv.received[0]["params"]["key"] == "Enter"
            assert srv.received[0]["params"]["windowsVirtualKeyCode"] == 13
            assert srv.received[1]["params"]["type"] == "keyUp"

            # 10. hotkey() with modifier bitmasks (Alt=1, Ctrl=2, Meta=4, Shift=8)
            srv.received.clear()
            cdp.hotkey("ctrl", "shift", "k")
            # Down: ctrl (2), shift (10), k (10); Up: k (10), shift (2), ctrl (0)
            assert len(srv.received) == 6
            assert srv.received[0]["params"]["modifiers"] == CDP_MODIFIER_CTRL
            assert srv.received[1]["params"]["modifiers"] == (
                CDP_MODIFIER_CTRL | CDP_MODIFIER_SHIFT
            )
            assert srv.received[2]["params"]["type"] == "rawKeyDown"
            assert srv.received[2]["params"]["code"] == "KeyK"
            assert srv.received[2]["params"]["modifiers"] == (
                CDP_MODIFIER_CTRL | CDP_MODIFIER_SHIFT
            )
            assert "text" not in srv.received[2]["params"]
            assert srv.received[3]["params"]["type"] == "keyUp"
            assert srv.received[5]["params"]["modifiers"] == 0

            srv.received.clear()
            cdp.hotkey("alt", "meta", "a")
            assert srv.received[2]["params"]["modifiers"] == (CDP_MODIFIER_ALT | CDP_MODIFIER_META)

            # 11. get_accessibility_tree()
            ax_nodes = cdp.get_accessibility_tree(max_nodes=2)
            assert len(ax_nodes) == 2
            assert all(n["ignored"] is False for n in ax_nodes)
            assert ax_nodes[0]["role"] == "RootWebArea"
            assert ax_nodes[0]["name"] == "Mock App"
            assert ax_nodes[1]["nodeId"] == "3"
            assert ax_nodes[1]["role"] == "button"
            assert ax_nodes[1]["name"] == "Submit"

            # Full non-ignored count
            ax_all = cdp.get_accessibility_tree(max_nodes=20)
            assert len(ax_all) == 3
            assert ax_all[2]["value"] == "hello"
    finally:
        srv.stop()


def test_cdp_backend_async_and_error_handling() -> None:
    """Verify native async usage and CDPError propagation on CDP JSON-RPC errors."""
    srv = _MockCDPServer(fail_method="Page.captureScreenshot")
    srv.start()

    async def _run() -> None:
        async with CDPActionBackend(ws_url=srv.ws_url, timeout=5.0) as cdp:
            await cdp.async_type_text("async works")
            with pytest.raises(CDPError, match="Mock CDP failure"):
                await cdp.async_capture()

    try:
        asyncio.run(_run())
        assert any(
            c["method"] == "Input.insertText" and c["params"]["text"] == "async works"
            for c in srv.received
        )
    finally:
        srv.stop()


def test_chat_session_with_cdp_backend_executes_headlessly() -> None:
    """Verify ChatSession routes both capture and CodeAct/Guarded actions through CDP without OS focus."""
    srv = _MockCDPServer()
    srv.start()
    try:
        cdp = CDPActionBackend(ws_url=srv.ws_url, timeout=5.0)
        cdp_target = CDPTargetInfo(
            id="TAB99",
            title="Headless Chrome Tab",
            url="https://example.com",
            ws_url=srv.ws_url,
        ).to_target_info()

        # Minimal CodeAct mode
        session = ChatSession(
            target=cdp_target,
            mode=ExecutionMode.MINIMAL_PYTHON,
            planner_func=lambda msgs, tgt, step: (
                "Clicking input and typing.\n"
                "```python\nclick(80, 90)\ntype_text('headless ok')\npress('enter')\n```\n"
                "TASK_COMPLETED: Submitted via CDP."
            ),
            cdp_backend=cdp,
        )
        session.capturer = MagicMock()

        res = session.execute_turn("Fill form headlessly")
        assert res.success is True
        assert res.completed is True
        # OS capturer must NEVER be called for capture or focus!
        session.capturer.capture.assert_not_called()
        session.capturer.focus.assert_not_called()

        methods = [c["method"] for c in srv.received]
        assert "Page.captureScreenshot" in methods
        assert "Input.dispatchMouseEvent" in methods
        assert "Input.insertText" in methods
        assert "Input.dispatchKeyEvent" in methods

        # Guarded mode
        srv.received.clear()
        guarded_steps = iter(
            [
                '```json\n{"action": "click", "x": 45, "y": 55}\n```',
                '```json\n{"action": "finish", "message": "Guarded CDP done"}\n```',
            ]
        )
        guarded_session = ChatSession(
            target=cdp_target,
            mode=ExecutionMode.CONTROL_GUARDED,
            planner_func=lambda msgs, tgt, step: next(guarded_steps),
            cdp_backend=cdp,
        )
        guarded_session.capturer = MagicMock()
        res_g = guarded_session.execute_turn("Click via guarded CDP")
        assert res_g.success is True
        assert res_g.completed is True
        guarded_session.capturer.capture.assert_not_called()
        guarded_session.capturer.focus.assert_not_called()
        assert any(c["method"] == "Input.dispatchMouseEvent" for c in srv.received)

        cdp.close()
    finally:
        srv.stop()
