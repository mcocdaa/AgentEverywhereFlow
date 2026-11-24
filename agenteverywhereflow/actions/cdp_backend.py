"""Zero-extra-dependency Chrome DevTools Protocol (CDP) headless browser backend.

Provides true background/headless browser and Electron automation with:
- Zero focus stealing
- Zero OS cursor movement
- Zero global clipboard mutation
- Native UTF-8 / CJK input via ``Input.insertText``
- Compact semantic accessibility tree extraction via ``Accessibility.getFullAXTree``
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import threading
import urllib.error
import urllib.request
from collections.abc import Coroutine
from typing import Any, Literal, TypeVar

import websockets
from PIL import Image
from pydantic import BaseModel, Field, model_validator

from agenteverywhereflow.capturer.base import Rect, TargetInfo, TargetType

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

# CDP Input.dispatchKeyEvent modifier bitmasks
CDP_MODIFIER_ALT = 1
CDP_MODIFIER_CTRL = 2
CDP_MODIFIER_META = 4
CDP_MODIFIER_SHIFT = 8

_MODIFIER_ALIASES: dict[str, tuple[int, str, str, int]] = {
    "alt": (CDP_MODIFIER_ALT, "Alt", "AltLeft", 18),
    "option": (CDP_MODIFIER_ALT, "Alt", "AltLeft", 18),
    "ctrl": (CDP_MODIFIER_CTRL, "Control", "ControlLeft", 17),
    "control": (CDP_MODIFIER_CTRL, "Control", "ControlLeft", 17),
    "meta": (CDP_MODIFIER_META, "Meta", "MetaLeft", 91),
    "cmd": (CDP_MODIFIER_META, "Meta", "MetaLeft", 91),
    "command": (CDP_MODIFIER_META, "Meta", "MetaLeft", 91),
    "super": (CDP_MODIFIER_META, "Meta", "MetaLeft", 91),
    "win": (CDP_MODIFIER_META, "Meta", "MetaLeft", 91),
    "shift": (CDP_MODIFIER_SHIFT, "Shift", "ShiftLeft", 16),
}

# Named non-character & common punctuation keys: name -> (key, code, windowsVirtualKeyCode, text)
_SPECIAL_KEY_MAP: dict[str, tuple[str, str, int, str]] = {
    "enter": ("Enter", "Enter", 13, "\r"),
    "return": ("Enter", "Enter", 13, "\r"),
    "tab": ("Tab", "Tab", 9, ""),
    "escape": ("Escape", "Escape", 27, ""),
    "esc": ("Escape", "Escape", 27, ""),
    "backspace": ("Backspace", "Backspace", 8, ""),
    "delete": ("Delete", "Delete", 46, ""),
    "del": ("Delete", "Delete", 46, ""),
    "space": (" ", "Space", 32, " "),
    " ": (" ", "Space", 32, " "),
    "up": ("ArrowUp", "ArrowUp", 38, ""),
    "arrowup": ("ArrowUp", "ArrowUp", 38, ""),
    "down": ("ArrowDown", "ArrowDown", 40, ""),
    "arrowdown": ("ArrowDown", "ArrowDown", 40, ""),
    "left": ("ArrowLeft", "ArrowLeft", 37, ""),
    "arrowleft": ("ArrowLeft", "ArrowLeft", 37, ""),
    "right": ("ArrowRight", "ArrowRight", 39, ""),
    "arrowright": ("ArrowRight", "ArrowRight", 39, ""),
    "home": ("Home", "Home", 36, ""),
    "end": ("End", "End", 35, ""),
    "pageup": ("PageUp", "PageUp", 33, ""),
    "pagedown": ("PageDown", "PageDown", 34, ""),
    "insert": ("Insert", "Insert", 45, ""),
    "alt": ("Alt", "AltLeft", 18, ""),
    "option": ("Alt", "AltLeft", 18, ""),
    "ctrl": ("Control", "ControlLeft", 17, ""),
    "control": ("Control", "ControlLeft", 17, ""),
    "meta": ("Meta", "MetaLeft", 91, ""),
    "cmd": ("Meta", "MetaLeft", 91, ""),
    "command": ("Meta", "MetaLeft", 91, ""),
    "super": ("Meta", "MetaLeft", 91, ""),
    "win": ("Meta", "MetaLeft", 91, ""),
    "shift": ("Shift", "ShiftLeft", 16, ""),
    "-": ("-", "Minus", 189, "-"),
    "=": ("=", "Equal", 187, "="),
    "[": ("[", "BracketLeft", 219, "["),
    "]": ("]", "BracketRight", 221, "]"),
    "\\": ("\\", "Backslash", 220, "\\"),
    ";": (";", "Semicolon", 186, ";"),
    "'": ("'", "Quote", 222, "'"),
    ",": (",", "Comma", 188, ","),
    ".": (".", "Period", 190, "."),
    "/": ("/", "Slash", 191, "/"),
    "`": ("`", "Backquote", 192, "`"),
}
for _fn_idx in range(1, 13):
    _SPECIAL_KEY_MAP[f"f{_fn_idx}"] = (f"F{_fn_idx}", f"F{_fn_idx}", 111 + _fn_idx, "")

_BUTTON_MASKS: dict[str, int] = {
    "left": 1,
    "right": 2,
    "middle": 4,
    "none": 0,
}


class CDPError(RuntimeError):
    """Raised when a CDP JSON-RPC command fails or times out."""


class CDPTargetInfo(BaseModel):
    """Discovered Chrome DevTools Protocol page target metadata."""

    id: str = Field(description="CDP target identifier from /json/list")
    title: str = Field(default="", description="Page title")
    url: str = Field(default="", description="Current page URL")
    ws_url: str = Field(description="WebSocket debugger URL for this page")
    target_id: str = Field(default="", description="AEFlow target identifier (cdp:<id>)")
    target_type: str = Field(default="page", description="CDP target type")
    description: str = Field(default="", description="Optional target description")

    @model_validator(mode="after")
    def _ensure_target_id(self) -> CDPTargetInfo:
        if not self.target_id:
            self.target_id = f"cdp:{self.id}"
        return self

    def to_target_info(self, width: int = 1280, height: int = 800) -> TargetInfo:
        """Convert into an AEFlow :class:`TargetInfo` descriptor."""
        display_title = self.title.strip() or self.url.strip() or self.target_id
        return TargetInfo(
            target_id=self.target_id,
            target_type=TargetType.WINDOW,
            title=display_title,
            process_name="cdp",
            rect=Rect(x=0, y=0, width=max(1, width), height=max(1, height)),
            is_minimized=False,
            native_handle=0,
        )


def discover_cdp_targets(
    endpoint: str = "http://127.0.0.1:9222",
    timeout: float = 1.5,
) -> list[CDPTargetInfo]:
    """Query a Chrome/Edge/Electron remote-debugging endpoint for inspectable page tabs.

    Returns an empty list gracefully if no CDP browser is listening or the request fails.
    """
    base = endpoint.strip().rstrip("/")
    if not base:
        return []
    url = f"{base}/json/list"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        data = json.loads(raw)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
        logger.debug("CDP discovery at %s returned no targets: %s", url, exc)
        return []

    if not isinstance(data, list):
        return []

    targets: list[CDPTargetInfo] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "page":
            continue
        ws_url = str(item.get("webSocketDebuggerUrl") or "").strip()
        target_raw_id = str(item.get("id") or "").strip()
        if not ws_url or not target_raw_id:
            continue
        targets.append(
            CDPTargetInfo(
                id=target_raw_id,
                title=str(item.get("title") or ""),
                url=str(item.get("url") or ""),
                ws_url=ws_url,
                target_id=f"cdp:{target_raw_id}",
                target_type="page",
                description=str(item.get("description") or ""),
            )
        )
    return targets


def resolve_cdp_target(
    query: str,
    endpoint: str = "http://127.0.0.1:9222",
    timeout: float = 1.5,
) -> CDPTargetInfo | None:
    """Resolve a ``cdp:<id>`` or raw page ID/title query against discovered CDP targets."""
    q = query.strip()
    if not q:
        return None
    targets = discover_cdp_targets(endpoint=endpoint, timeout=timeout)
    q_lower = q.lower()
    raw_id = q[4:] if q_lower.startswith("cdp:") else q
    for t in targets:
        if t.target_id.lower() == q_lower or t.id.lower() == raw_id.lower():
            return t
    for t in targets:
        if raw_id.lower() in t.title.lower() or raw_id.lower() in t.url.lower():
            return t
    return None


def _resolve_key_spec(key: str) -> tuple[str, str, int, str]:
    """Map a logical key name into CDP ``(key, code, windowsVirtualKeyCode, text)``."""
    raw = key.strip() if len(key) > 1 else key
    lower = raw.lower()
    if lower in _SPECIAL_KEY_MAP:
        return _SPECIAL_KEY_MAP[lower]
    if len(raw) == 1:
        ch = raw
        if "a" <= ch.lower() <= "z":
            return (ch, f"Key{ch.upper()}", ord(ch.upper()), ch)
        if "0" <= ch <= "9":
            return (ch, f"Digit{ch}", ord(ch), ch)
        return (ch, "", ord(ch), ch)
    return (raw, raw, 0, "")


def _extract_ax_str(field: Any) -> str:
    """Extract string value from a CDP AXValue dict (``{"type": "...", "value": ...}``)."""
    if isinstance(field, dict):
        val = field.get("value")
        return "" if val is None else str(val)
    if field is None:
        return ""
    return str(field)


def _ws_is_open(ws: Any) -> bool:
    """Return True if a ``websockets`` connection appears open across v12–v17."""
    if ws is None:
        return False
    state = getattr(ws, "state", None)
    if state is not None:
        return getattr(state, "name", str(state)) == "OPEN"
    closed = getattr(ws, "closed", None)
    if isinstance(closed, bool):
        return not closed
    return True


class CDPActionBackend:
    """Headless browser ActionBackend communicating directly over CDP WebSocket.

    Exposes both ``async_*`` coroutines (for native async callers) and thread-safe
    synchronous methods matching :class:`ActionBackend` (backed by a lazily-started
    dedicated background asyncio event loop thread so sync calls work safely from
    any thread, including FastAPI worker threads or nested event loops).
    """

    def __init__(
        self,
        ws_url: str,
        timeout: float = 15.0,
        target_info: CDPTargetInfo | None = None,
    ) -> None:
        self.ws_url = ws_url
        self.timeout = timeout
        self.target_info = target_info

        self.viewport_width: int = 1280
        self.viewport_height: int = 800
        self.layout_metrics: dict[str, Any] = {}

        # Active WebSocket state (bound to whichever asyncio loop opened it)
        self._ws: Any = None
        self._ws_loop: asyncio.AbstractEventLoop | None = None
        self._async_lock: asyncio.Lock | None = None
        self._cmd_id: int = 0

        # Dedicated background event loop for thread-safe sync wrappers
        self._bg_loop: asyncio.AbstractEventLoop | None = None
        self._bg_thread: threading.Thread | None = None
        self._bg_lock = threading.Lock()

    def _ensure_bg_loop(self) -> asyncio.AbstractEventLoop:
        """Lazily start a private daemon thread hosting an asyncio event loop."""
        with self._bg_lock:
            if self._bg_loop is not None and not self._bg_loop.is_closed():
                return self._bg_loop

            loop = asyncio.new_event_loop()
            ready = threading.Event()

            def _runner() -> None:
                asyncio.set_event_loop(loop)
                ready.set()
                loop.run_forever()

            thread = threading.Thread(target=_runner, name="aef-cdp-loop", daemon=True)
            thread.start()
            ready.wait(timeout=5.0)
            self._bg_loop = loop
            self._bg_thread = thread
            return loop

    def _run_sync(self, coro: Coroutine[Any, Any, _T]) -> _T:
        """Execute an async CDP coroutine synchronously on the dedicated background loop."""
        loop = self._ensure_bg_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout=self.timeout + 5.0)

    def _get_async_lock(self) -> asyncio.Lock:
        """Return an ``asyncio.Lock`` bound to the currently running event loop."""
        running_loop = asyncio.get_running_loop()
        if self._async_lock is None or self._ws_loop is not running_loop:
            self._async_lock = asyncio.Lock()
        return self._async_lock

    async def async_connect(self) -> Any:
        """Establish or reuse the CDP WebSocket connection on the current event loop."""
        running_loop = asyncio.get_running_loop()
        if self._ws is not None and self._ws_loop is running_loop and _ws_is_open(self._ws):
            return self._ws

        if self._ws is not None and self._ws_loop is running_loop:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

        self._ws = await websockets.connect(
            self.ws_url,
            max_size=None,
            open_timeout=self.timeout,
        )
        self._ws_loop = running_loop
        return self._ws

    async def async_close(self) -> None:
        """Close the active WebSocket connection."""
        ws = self._ws
        self._ws = None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                pass

    def close(self) -> None:
        """Synchronously close the WebSocket connection and shut down the background loop."""
        with self._bg_lock:
            loop = self._bg_loop
            thread = self._bg_thread
            self._bg_loop = None
            self._bg_thread = None

        if loop is not None and not loop.is_closed():
            if self._ws is not None and self._ws_loop is loop:
                try:
                    fut = asyncio.run_coroutine_threadsafe(self.async_close(), loop)
                    fut.result(timeout=2.0)
                except Exception:
                    pass
            loop.call_soon_threadsafe(loop.stop)
            if thread is not None and thread.is_alive():
                thread.join(timeout=2.0)
            try:
                loop.close()
            except Exception:
                pass

    async def __aenter__(self) -> CDPActionBackend:
        await self.async_connect()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.async_close()

    def __enter__(self) -> CDPActionBackend:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    async def async_send_command(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a CDP JSON-RPC command and await its matching response."""
        lock = self._get_async_lock()
        async with lock:
            ws = await self.async_connect()
            self._cmd_id += 1
            cmd_id = self._cmd_id
            payload = {"id": cmd_id, "method": method, "params": params or {}}
            await ws.send(json.dumps(payload))

            deadline = asyncio.get_running_loop().time() + self.timeout
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    raise CDPError(f"CDP command '{method}' timed out after {self.timeout}s")
                raw_msg = await asyncio.wait_for(ws.recv(), timeout=remaining)
                if isinstance(raw_msg, bytes):
                    raw_msg = raw_msg.decode("utf-8", errors="replace")
                msg = json.loads(raw_msg)
                if not isinstance(msg, dict):
                    continue
                if msg.get("id") != cmd_id:
                    # Asynchronous CDP domain event (no matching id) — skip
                    continue
                if "error" in msg and msg["error"]:
                    raise CDPError(f"CDP error for '{method}': {msg['error']}")
                result = msg.get("result")
                return result if isinstance(result, dict) else {}

    def send_command(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Thread-safe synchronous wrapper for :meth:`async_send_command`."""
        return self._run_sync(self.async_send_command(method, params=params))

    # ------------------------------------------------------------------
    # Async CDP Action Primitives
    # ------------------------------------------------------------------

    async def async_capture(self) -> Image.Image:
        """Capture the page viewport via ``Page.captureScreenshot`` without focusing the tab."""
        try:
            metrics = await self.async_send_command("Page.getLayoutMetrics")
            self.layout_metrics = metrics
            vp = metrics.get("cssLayoutViewport") or metrics.get("layoutViewport") or {}
            if isinstance(vp, dict):
                cw = vp.get("clientWidth")
                ch = vp.get("clientHeight")
                if isinstance(cw, (int, float)) and cw > 0:
                    self.viewport_width = int(cw)
                if isinstance(ch, (int, float)) and ch > 0:
                    self.viewport_height = int(ch)
        except Exception as exc:
            logger.debug("Page.getLayoutMetrics skipped/unavailable: %s", exc)

        result = await self.async_send_command("Page.captureScreenshot", {"format": "png"})
        data_b64 = result.get("data")
        if not isinstance(data_b64, str) or not data_b64:
            raise CDPError("Page.captureScreenshot returned empty image payload")

        img_bytes = base64.b64decode(data_b64)
        img = Image.open(io.BytesIO(img_bytes))
        img.load()
        if not self.layout_metrics:
            self.viewport_width = img.width
            self.viewport_height = img.height
        return img

    async def async_move_to(self, x: float, y: float) -> None:
        """Dispatch a background ``mouseMoved`` event to ``(x, y)``."""
        await self.async_send_command(
            "Input.dispatchMouseEvent",
            {
                "type": "mouseMoved",
                "x": float(x),
                "y": float(y),
                "button": "none",
                "buttons": 0,
            },
        )

    async def async_click(
        self,
        x: float,
        y: float,
        button: Literal["left", "middle", "right"] = "left",
        click_count: int = 1,
    ) -> None:
        """Dispatch headless mouse move + press + release events via CDP."""
        btn_mask = _BUTTON_MASKS.get(button, 1)
        fx, fy = float(x), float(y)
        await self.async_move_to(fx, fy)
        for count in range(1, max(1, click_count) + 1):
            await self.async_send_command(
                "Input.dispatchMouseEvent",
                {
                    "type": "mousePressed",
                    "x": fx,
                    "y": fy,
                    "button": button,
                    "buttons": btn_mask,
                    "clickCount": count,
                },
            )
            await self.async_send_command(
                "Input.dispatchMouseEvent",
                {
                    "type": "mouseReleased",
                    "x": fx,
                    "y": fy,
                    "button": button,
                    "buttons": 0,
                    "clickCount": count,
                },
            )

    async def async_double_click(self, x: float, y: float) -> None:
        """Dispatch a left double-click at ``(x, y)``."""
        await self.async_click(x, y, button="left", click_count=2)

    async def async_right_click(self, x: float, y: float) -> None:
        """Dispatch a right-click at ``(x, y)``."""
        await self.async_click(x, y, button="right", click_count=1)

    async def async_drag(
        self,
        from_x: float,
        from_y: float,
        to_x: float,
        to_y: float,
        duration: float = 0.5,
        steps: int = 10,
    ) -> None:
        """Dispatch a smooth headless drag gesture from ``(from_x, from_y)`` to ``(to_x, to_y)``."""
        sx, sy = float(from_x), float(from_y)
        ex, ey = float(to_x), float(to_y)
        num_steps = max(1, steps)
        step_delay = max(0.0, duration) / num_steps

        await self.async_move_to(sx, sy)
        await self.async_send_command(
            "Input.dispatchMouseEvent",
            {
                "type": "mousePressed",
                "x": sx,
                "y": sy,
                "button": "left",
                "buttons": 1,
                "clickCount": 1,
            },
        )
        for idx in range(1, num_steps + 1):
            ratio = idx / num_steps
            cx = sx + (ex - sx) * ratio
            cy = sy + (ey - sy) * ratio
            await self.async_send_command(
                "Input.dispatchMouseEvent",
                {
                    "type": "mouseMoved",
                    "x": cx,
                    "y": cy,
                    "button": "left",
                    "buttons": 1,
                },
            )
            if step_delay > 0:
                await asyncio.sleep(step_delay)
        await self.async_send_command(
            "Input.dispatchMouseEvent",
            {
                "type": "mouseReleased",
                "x": ex,
                "y": ey,
                "button": "left",
                "buttons": 0,
                "clickCount": 1,
            },
        )

    async def async_scroll(
        self,
        x: float,
        y: float,
        clicks: int,
        delta_x: float = 0.0,
    ) -> None:
        """Dispatch a headless ``mouseWheel`` event at ``(x, y)``.

        Follows AEFlow convention: positive ``clicks`` scrolls up (negative CDP ``deltaY``);
        negative ``clicks`` scrolls down (positive CDP ``deltaY``).
        """
        delta_y = -float(clicks) * 120.0
        await self.async_send_command(
            "Input.dispatchMouseEvent",
            {
                "type": "mouseWheel",
                "x": float(x),
                "y": float(y),
                "deltaX": float(delta_x),
                "deltaY": delta_y,
            },
        )

    async def async_type_text(self, text: str) -> None:
        """Insert arbitrary UTF-8 / CJK text directly via ``Input.insertText``."""
        await self.async_send_command("Input.insertText", {"text": text})

    async def async_press_key(self, key: str, modifiers: int = 0) -> None:
        """Press and release a key via ``Input.dispatchKeyEvent`` (``rawKeyDown`` / ``keyUp``)."""
        key_val, code_val, vk_code, text_val = _resolve_key_spec(key)
        down_params: dict[str, Any] = {
            "type": "rawKeyDown",
            "key": key_val,
            "code": code_val,
            "windowsVirtualKeyCode": vk_code,
            "nativeVirtualKeyCode": vk_code,
            "modifiers": modifiers,
        }
        # Only attach text payload when no Ctrl/Alt/Meta shortcut modifier is held
        if (
            text_val
            and (modifiers & (CDP_MODIFIER_ALT | CDP_MODIFIER_CTRL | CDP_MODIFIER_META)) == 0
        ):
            down_params["text"] = text_val
            down_params["unmodifiedText"] = text_val

        up_params: dict[str, Any] = {
            "type": "keyUp",
            "key": key_val,
            "code": code_val,
            "windowsVirtualKeyCode": vk_code,
            "nativeVirtualKeyCode": vk_code,
            "modifiers": modifiers,
        }

        await self.async_send_command("Input.dispatchKeyEvent", down_params)
        await self.async_send_command("Input.dispatchKeyEvent", up_params)

    async def async_hotkey(self, *keys: str) -> None:
        """Press a key chord with proper CDP modifier bitmasks (Alt=1, Ctrl=2, Meta=4, Shift=8)."""
        if not keys:
            return
        if len(keys) == 1:
            await self.async_press_key(keys[0])
            return

        pressed_mods: list[tuple[int, str, str, int]] = []
        regular_keys: list[str] = []
        for raw_key in keys:
            norm = raw_key.strip().lower()
            if norm in _MODIFIER_ALIASES:
                pressed_mods.append(_MODIFIER_ALIASES[norm])
            else:
                regular_keys.append(raw_key)

        active_mask = 0
        for bit, key_name, code_name, vk_code in pressed_mods:
            active_mask |= bit
            await self.async_send_command(
                "Input.dispatchKeyEvent",
                {
                    "type": "rawKeyDown",
                    "key": key_name,
                    "code": code_name,
                    "windowsVirtualKeyCode": vk_code,
                    "nativeVirtualKeyCode": vk_code,
                    "modifiers": active_mask,
                },
            )

        for main_key in regular_keys:
            await self.async_press_key(main_key, modifiers=active_mask)

        for bit, key_name, code_name, vk_code in reversed(pressed_mods):
            active_mask &= ~bit
            await self.async_send_command(
                "Input.dispatchKeyEvent",
                {
                    "type": "keyUp",
                    "key": key_name,
                    "code": code_name,
                    "windowsVirtualKeyCode": vk_code,
                    "nativeVirtualKeyCode": vk_code,
                    "modifiers": active_mask,
                },
            )

    async def async_get_accessibility_tree(self, max_nodes: int = 200) -> list[dict[str, Any]]:
        """Query ``Accessibility.getFullAXTree`` and return compact non-ignored semantic nodes."""
        result = await self.async_send_command("Accessibility.getFullAXTree")
        raw_nodes = result.get("nodes", [])
        if not isinstance(raw_nodes, list):
            return []

        compact: list[dict[str, Any]] = []
        for node in raw_nodes:
            if not isinstance(node, dict):
                continue
            if node.get("ignored", False):
                continue
            entry: dict[str, Any] = {
                "nodeId": str(node.get("nodeId", "")),
                "role": _extract_ax_str(node.get("role")),
                "name": _extract_ax_str(node.get("name")),
                "ignored": False,
            }
            val = _extract_ax_str(node.get("value"))
            if val:
                entry["value"] = val
            if "backendDOMNodeId" in node:
                entry["backendDOMNodeId"] = node["backendDOMNodeId"]
            compact.append(entry)
            if len(compact) >= max_nodes:
                break
        return compact

    # ------------------------------------------------------------------
    # Thread-Safe Sync Wrappers (ActionBackend Protocol)
    # ------------------------------------------------------------------

    def capture(self) -> Image.Image:
        """Capture the CDP page viewport synchronously."""
        return self._run_sync(self.async_capture())

    def move_to(self, x: float, y: float) -> None:
        """Move pointer synchronously to ``(x, y)``."""
        self._run_sync(self.async_move_to(x, y))

    def click(
        self,
        x: float,
        y: float,
        button: Literal["left", "middle", "right"] = "left",
        click_count: int = 1,
    ) -> None:
        """Click synchronously at ``(x, y)``."""
        self._run_sync(self.async_click(x, y, button=button, click_count=click_count))

    def double_click(self, x: float, y: float) -> None:
        """Double-click synchronously at ``(x, y)``."""
        self._run_sync(self.async_double_click(x, y))

    def right_click(self, x: float, y: float) -> None:
        """Right-click synchronously at ``(x, y)``."""
        self._run_sync(self.async_right_click(x, y))

    def drag(
        self,
        from_x: float,
        from_y: float,
        to_x: float,
        to_y: float,
        duration: float = 0.5,
    ) -> None:
        """Drag synchronously from ``(from_x, from_y)`` to ``(to_x, to_y)``."""
        self._run_sync(self.async_drag(from_x, from_y, to_x, to_y, duration=duration))

    def scroll(self, x: float, y: float, clicks: int) -> None:
        """Scroll synchronously at ``(x, y)``."""
        self._run_sync(self.async_scroll(x, y, clicks))

    def type_text(self, text: str) -> None:
        """Insert text synchronously via ``Input.insertText``."""
        self._run_sync(self.async_type_text(text))

    def press_key(self, key: str) -> None:
        """Press a single key synchronously."""
        self._run_sync(self.async_press_key(key))

    def hotkey(self, *keys: str) -> None:
        """Press a hotkey chord synchronously."""
        self._run_sync(self.async_hotkey(*keys))

    def get_accessibility_tree(self, max_nodes: int = 200) -> list[dict[str, Any]]:
        """Retrieve compact semantic accessibility tree nodes synchronously."""
        return self._run_sync(self.async_get_accessibility_tree(max_nodes=max_nodes))
