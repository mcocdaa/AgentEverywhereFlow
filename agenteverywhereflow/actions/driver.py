"""OS-level mouse and keyboard driver with native cross-platform input injection.

Platform backends:
- Linux (X11): XTest (``Xlib.ext.xtest.fake_input``) by default so Chromium, Firefox,
  Electron, VS Code, and ``display:*`` targets all receive real hardware-level events,
  with opt-in ``XSendEvent`` support for occluded legacy X11 windows.
- Windows: Native ``ctypes`` Win32 ``SendInput`` (replacing deprecated ``mouse_event`` /
  ``keybd_event``) plus clipboard paste for IME-safe Unicode & ASCII text entry.
- macOS: Native Quartz ``CGEvent`` (with explicit ``kCGMouseEventClickState`` for
  reliable Finder/AppKit double-clicks) and ``pyperclip`` + ``Command+V`` paste for
  IME-safe ASCII and Unicode/CJK text entry.
"""

import ctypes
import sys
import time
from typing import Any, Literal

# =============================================================================
# Windows SendInput ctypes definitions (ABI-exact on all platforms for testing)
# =============================================================================
_ULONG_PTR = ctypes.c_uint64 if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_uint32

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
INPUT_HARDWARE = 2

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_ABSOLUTE = 0x8000

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004

WHEEL_DELTA = 120

# Common Windows Virtual-Key codes
_WIN_VK_MAP: dict[str, int] = {
    "backspace": 0x08,
    "tab": 0x09,
    "enter": 0x0D,
    "return": 0x0D,
    "shift": 0x10,
    "ctrl": 0x11,
    "control": 0x11,
    "alt": 0x12,
    "option": 0x12,
    "pause": 0x13,
    "capslock": 0x14,
    "escape": 0x1B,
    "esc": 0x1B,
    "space": 0x20,
    "pageup": 0x21,
    "page_up": 0x21,
    "pgup": 0x21,
    "pagedown": 0x22,
    "page_down": 0x22,
    "pgdn": 0x22,
    "end": 0x23,
    "home": 0x24,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "printscreen": 0x2C,
    "insert": 0x2D,
    "delete": 0x2E,
    "del": 0x2E,
    "win": 0x5B,
    "super": 0x5B,
    "meta": 0x5B,
    "command": 0x5B,
    "cmd": 0x5B,
    **{f"f{i}": 0x6F + i for i in range(1, 13)},
}


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int32), ("y", ctypes.c_int32)]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_int32),
        ("dy", ctypes.c_int32),
        ("mouseData", ctypes.c_uint32),
        ("dwFlags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.c_uint16),
        ("wScan", ctypes.c_uint16),
        ("dwFlags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.c_uint32),
        ("wParamL", ctypes.c_uint16),
        ("wParamH", ctypes.c_uint16),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("mi", _MOUSEINPUT),
        ("ki", _KEYBDINPUT),
        ("hi", _HARDWAREINPUT),
    ]


class _INPUT(ctypes.Structure):
    _anonymous_ = ("union",)
    _fields_ = [
        ("type", ctypes.c_uint32),
        ("union", _INPUT_UNION),
    ]


def build_win32_mouse_input(flags: int, dx: int = 0, dy: int = 0, mouse_data: int = 0) -> _INPUT:
    """Build a Win32 ``INPUT`` struct for a mouse event."""
    inp = _INPUT()
    inp.type = INPUT_MOUSE
    inp.mi = _MOUSEINPUT(
        dx=int(dx),
        dy=int(dy),
        mouseData=int(mouse_data) & 0xFFFFFFFF,
        dwFlags=int(flags),
        time=0,
        dwExtraInfo=0,
    )
    return inp


def build_win32_key_input(
    vk: int, key_up: bool = False, scan: int = 0, extra_flags: int = 0
) -> _INPUT:
    """Build a Win32 ``INPUT`` struct for a keyboard event."""
    flags = int(extra_flags) | (KEYEVENTF_KEYUP if key_up else 0)
    inp = _INPUT()
    inp.type = INPUT_KEYBOARD
    inp.ki = _KEYBDINPUT(
        wVk=int(vk) & 0xFFFF,
        wScan=int(scan) & 0xFFFF,
        dwFlags=flags,
        time=0,
        dwExtraInfo=0,
    )
    return inp


# =============================================================================
# Linux X11 Keysym and Punctuation Maps
# =============================================================================
_X11_KEY_MAP: dict[str, str] = {
    "enter": "Return",
    "return": "Return",
    "backspace": "BackSpace",
    "tab": "Tab",
    "escape": "Escape",
    "esc": "Escape",
    "space": "space",
    "delete": "Delete",
    "del": "Delete",
    "insert": "Insert",
    "up": "Up",
    "down": "Down",
    "left": "Left",
    "right": "Right",
    "home": "Home",
    "end": "End",
    "pageup": "Page_Up",
    "page_up": "Page_Up",
    "pgup": "Page_Up",
    "pagedown": "Page_Down",
    "page_down": "Page_Down",
    "pgdn": "Page_Down",
    "capslock": "Caps_Lock",
    "caps_lock": "Caps_Lock",
    "printscreen": "Print",
    "prtsc": "Print",
    "pause": "Pause",
    "ctrl": "Control_L",
    "control": "Control_L",
    "ctrlleft": "Control_L",
    "ctrlright": "Control_R",
    "shift": "Shift_L",
    "shiftleft": "Shift_L",
    "shiftright": "Shift_R",
    "alt": "Alt_L",
    "altleft": "Alt_L",
    "altright": "Alt_R",
    "option": "Alt_L",
    "super": "Super_L",
    "meta": "Super_L",
    "win": "Super_L",
    "command": "Super_L",
    "cmd": "Super_L",
    **{f"f{i}": f"F{i}" for i in range(1, 13)},
}

_X11_PUNCTUATION_MAP: dict[str, str] = {
    " ": "space",
    "\n": "Return",
    "\t": "Tab",
    "-": "minus",
    "=": "equal",
    "[": "bracketleft",
    "]": "bracketright",
    "\\": "backslash",
    ";": "semicolon",
    "'": "apostrophe",
    ",": "comma",
    ".": "period",
    "/": "slash",
    "`": "grave",
    "!": "exclam",
    "@": "at",
    "#": "numbersign",
    "$": "dollar",
    "%": "percent",
    "^": "asciicircum",
    "&": "ampersand",
    "*": "asterisk",
    "(": "parenleft",
    ")": "parenright",
    "_": "underscore",
    "+": "plus",
    "{": "braceleft",
    "}": "braceright",
    "|": "bar",
    ":": "colon",
    '"': "quotedbl",
    "<": "less",
    ">": "greater",
    "?": "question",
    "~": "asciitilde",
}

_X11_SHIFTED_CHARS = frozenset('~!@#$%^&*()_+{}|:"<>?')


# =============================================================================
# macOS Virtual Keycodes (Carbon Events.h)
# =============================================================================
_MAC_KEYCODES: dict[str, int] = {
    "a": 0x00,
    "s": 0x01,
    "d": 0x02,
    "f": 0x03,
    "h": 0x04,
    "g": 0x05,
    "z": 0x06,
    "x": 0x07,
    "c": 0x08,
    "v": 0x09,
    "b": 0x0B,
    "q": 0x0C,
    "w": 0x0D,
    "e": 0x0E,
    "r": 0x0F,
    "y": 0x10,
    "t": 0x11,
    "1": 0x12,
    "2": 0x13,
    "3": 0x14,
    "4": 0x15,
    "6": 0x16,
    "5": 0x17,
    "=": 0x18,
    "9": 0x19,
    "7": 0x1A,
    "-": 0x1B,
    "8": 0x1C,
    "0": 0x1D,
    "]": 0x1E,
    "o": 0x1F,
    "u": 0x20,
    "[": 0x21,
    "i": 0x22,
    "p": 0x23,
    "enter": 0x24,
    "return": 0x24,
    "l": 0x25,
    "j": 0x26,
    "'": 0x27,
    "k": 0x28,
    ";": 0x29,
    "\\": 0x2A,
    ",": 0x2B,
    "/": 0x2C,
    "n": 0x2D,
    "m": 0x2E,
    ".": 0x2F,
    "tab": 0x30,
    "space": 0x31,
    "`": 0x32,
    "backspace": 0x33,
    "escape": 0x35,
    "esc": 0x35,
    "command": 0x37,
    "cmd": 0x37,
    "super": 0x37,
    "meta": 0x37,
    "shift": 0x38,
    "capslock": 0x39,
    "alt": 0x3A,
    "option": 0x3A,
    "opt": 0x3A,
    "ctrl": 0x3B,
    "control": 0x3B,
    "f1": 0x7A,
    "f2": 0x78,
    "f3": 0x63,
    "f4": 0x76,
    "f5": 0x60,
    "f6": 0x61,
    "f7": 0x62,
    "f8": 0x64,
    "f9": 0x65,
    "f10": 0x6D,
    "f11": 0x67,
    "f12": 0x6F,
    "home": 0x73,
    "pageup": 0x74,
    "page_up": 0x74,
    "pgup": 0x74,
    "delete": 0x75,
    "del": 0x75,
    "end": 0x77,
    "pagedown": 0x79,
    "page_down": 0x79,
    "pgdn": 0x79,
    "left": 0x7B,
    "right": 0x7C,
    "down": 0x7D,
    "up": 0x7E,
}


def _normalize_mac_key(key: str) -> str:
    """Normalize key aliases for macOS (e.g. ``cmd`` -> ``command``, ``opt`` -> ``option``)."""
    k = key.strip().lower()
    if k in ("cmd", "super", "meta", "win"):
        return "command"
    if k in ("opt", "alt"):
        return "option"
    if k == "control":
        return "ctrl"
    if k == "esc":
        return "escape"
    return k


def _get_quartz() -> Any | None:
    """Lazily load Quartz on macOS."""
    try:
        import Quartz

        return Quartz
    except Exception:
        return None


class InputDriver:
    """Low-level OS input driver for keyboard and mouse."""

    def __init__(self, use_xsendevent: bool = False) -> None:
        self.use_xsendevent: bool = use_xsendevent
        self._gui_backend: Any = None
        self._backend_available: bool | None = None
        self._x11_display: Any = None

    def _get_backend(self) -> Any:
        """Lazy load PyAutoGUI fallback backend to prevent headless/CI import failures."""
        if self._backend_available is False:
            return None

        if self._gui_backend is None:
            try:
                import pyautogui

                pyautogui.PAUSE = 0.05
                pyautogui.FAILSAFE = False
                self._gui_backend = pyautogui
                self._backend_available = True
            except (Exception, SystemExit):
                self._backend_available = False
                self._gui_backend = None
                return None

        return self._gui_backend

    # -------------------------------------------------------------------------
    # Linux X11 helpers (XTest default + opt-in XSendEvent)
    # -------------------------------------------------------------------------
    def _get_x11_display(self) -> Any:
        """Retrieve X11 display connection if on Linux."""
        if sys.platform.startswith("linux") and self._x11_display is None:
            try:
                from Xlib import display

                self._x11_display = display.Display()
            except Exception:
                self._x11_display = False
        return self._x11_display if self._x11_display else None

    def _resolve_valid_x11_window(self, x_disp: Any, window_handle: int) -> Any | None:
        """Validate that ``window_handle`` is a live X11 window (never invalid XID 1)."""
        if window_handle <= 1:
            return None
        try:
            win = x_disp.create_resource_object("window", window_handle)
            win.get_geometry()
            return win
        except Exception:
            return None

    def _find_x11_child_at(self, disp: Any, win: Any, rx: int, ry: int) -> tuple[Any, int, int]:
        """Recursively locate the innermost child widget under window relative (rx, ry)."""
        try:
            from Xlib import X

            tree = win.query_tree()
            for child in reversed(tree.children):
                try:
                    attrs = child.get_attributes()
                    if attrs.map_state != X.IsViewable:
                        continue
                except Exception:
                    continue
                cg = child.get_geometry()
                if cg.x <= rx < cg.x + cg.width and cg.y <= ry < cg.y + cg.height:
                    return self._find_x11_child_at(disp, child, rx - cg.x, ry - cg.y)
        except Exception:
            pass
        return win, rx, ry

    def _is_x11_descendant(self, candidate: Any, ancestor: Any) -> bool:
        """Return True if ``candidate`` equals ``ancestor`` or is a child inside its subtree."""
        cand_id = getattr(candidate, "id", candidate)
        anc_id = getattr(ancestor, "id", ancestor)
        if cand_id == anc_id:
            return True
        cur = candidate
        for _ in range(32):
            try:
                tree = cur.query_tree()
                parent = tree.parent
                if not parent or getattr(parent, "id", parent) in (0, 1):
                    return False
                if getattr(parent, "id", parent) == anc_id:
                    return True
                cur = parent
            except Exception:
                return False
        return False

    def _get_x11_active_input_window(self, x_disp: Any, parent_win: Any) -> Any:
        """Find the innermost child window of ``parent_win`` that currently holds input focus."""
        try:
            from Xlib import X

            focus = x_disp.get_input_focus().focus
            if (
                focus
                and focus not in (X.NONE, X.PointerRoot, 0, 1)
                and self._is_x11_descendant(focus, parent_win)
            ):
                return focus
        except Exception:
            pass
        return parent_win

    @staticmethod
    def _resolve_x11_keysym_and_shift(char_or_key: str) -> tuple[str, bool]:
        """Map a character or key name to an X11 keysym name and shift requirement."""
        if len(char_or_key) == 1:
            if char_or_key in _X11_PUNCTUATION_MAP:
                return _X11_PUNCTUATION_MAP[char_or_key], char_or_key in _X11_SHIFTED_CHARS
            return char_or_key, char_or_key.isupper()

        lowered = char_or_key.strip().lower()
        return _X11_KEY_MAP.get(lowered, char_or_key), False

    def _x11_keysym_to_keycode(self, x_disp: Any, keysym_name: str) -> int:
        """Convert an X11 keysym name to a hardware keycode (0 if unmapped)."""
        from Xlib import XK

        keysym = XK.string_to_keysym(keysym_name)
        if not keysym and len(keysym_name) == 1:
            keysym = ord(keysym_name)
        if not keysym:
            return 0
        return int(x_disp.keysym_to_keycode(keysym) or 0)

    def _x11_query_pointer(self, x_disp: Any) -> tuple[int, int] | None:
        try:
            ptr = x_disp.screen().root.query_pointer()
            return (int(ptr.root_x), int(ptr.root_y))
        except Exception:
            return None

    # -------------------------------------------------------------------------
    # Windows SendInput helpers
    # -------------------------------------------------------------------------
    @staticmethod
    def _win32_send_inputs(inputs: list[_INPUT]) -> int:
        arr_type = _INPUT * len(inputs)
        arr = arr_type(*inputs)
        return int(ctypes.windll.user32.SendInput(len(inputs), arr, ctypes.sizeof(_INPUT)))  # type: ignore[attr-defined]

    @staticmethod
    def _win32_get_cursor_pos() -> tuple[int, int] | None:
        try:
            pt = _POINT()
            if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):  # type: ignore[attr-defined]
                return (int(pt.x), int(pt.y))
        except Exception:
            pass
        return None

    @staticmethod
    def _win32_set_cursor_pos(x: int, y: int) -> None:
        ctypes.windll.user32.SetCursorPos(int(x), int(y))  # type: ignore[attr-defined]

    @staticmethod
    def _win32_key_to_vk(key: str) -> int:
        k = key.strip().lower()
        if k in _WIN_VK_MAP:
            return _WIN_VK_MAP[k]
        if len(k) == 1 and k.isalnum():
            return ord(k.upper())
        return 0

    # -------------------------------------------------------------------------
    # macOS Quartz CGEvent helpers
    # -------------------------------------------------------------------------
    @staticmethod
    def _mac_get_cursor_pos(quartz: Any) -> tuple[float, float] | None:
        try:
            evt = quartz.CGEventCreate(None)
            loc = quartz.CGEventGetLocation(evt)
            return (float(loc.x), float(loc.y))
        except Exception:
            return None

    @staticmethod
    def _mac_post_mouse_event(
        quartz: Any,
        event_type: int,
        x: float,
        y: float,
        button: int,
        click_state: int | None = None,
    ) -> None:
        evt = quartz.CGEventCreateMouseEvent(None, event_type, (float(x), float(y)), button)
        if click_state is not None:
            field_id = int(getattr(quartz, "kCGMouseEventClickState", 1))
            quartz.CGEventSetIntegerValueField(evt, field_id, int(click_state))
        tap = int(getattr(quartz, "kCGHIDEventTap", 0))
        quartz.CGEventPost(tap, evt)

    # -------------------------------------------------------------------------
    # Public Driver Methods
    # -------------------------------------------------------------------------
    def move_to(self, x: int, y: int, duration: float = 0.1) -> None:
        """Move cursor smoothly to target screen coordinates."""
        # 1. Linux X11 via XTest
        x_disp = self._get_x11_display()
        if x_disp is not None:
            try:
                from Xlib import X
                from Xlib.ext import xtest

                xtest.fake_input(x_disp, X.MotionNotify, x=int(x), y=int(y))
                x_disp.sync()
                return
            except Exception:
                pass

        # 2. Windows via SetCursorPos
        if sys.platform == "win32":
            try:
                self._win32_set_cursor_pos(int(x), int(y))
                return
            except Exception:
                pass

        # 3. macOS via Quartz CGEvent
        if sys.platform == "darwin":
            quartz = _get_quartz()
            if quartz is not None:
                try:
                    self._mac_post_mouse_event(
                        quartz,
                        int(getattr(quartz, "kCGEventMouseMoved", 5)),
                        float(x),
                        float(y),
                        int(getattr(quartz, "kCGMouseButtonLeft", 0)),
                    )
                    return
                except Exception:
                    pass

        backend = self._get_backend()
        if backend:
            backend.moveTo(x, y, duration=duration)

    def click(
        self,
        x: int | None = None,
        y: int | None = None,
        button: Literal["left", "middle", "right"] = "left",
        clicks: int = 1,
        window_handle: int = 0,
        window_rel_x: int = 0,
        window_rel_y: int = 0,
        restore_cursor: bool = True,
        use_xsendevent: bool | None = None,
    ) -> None:
        """Perform mouse click at ``(x, y)`` using native OS input injection."""
        # 1. Linux X11 (XTest default; XSendEvent opt-in only)
        x_disp = self._get_x11_display()
        if x_disp is not None:
            btn_code = 1 if button == "left" else (3 if button == "right" else 2)
            should_xsend = self.use_xsendevent if use_xsendevent is None else use_xsendevent

            if should_xsend and window_handle > 1:
                parent_win = self._resolve_valid_x11_window(x_disp, window_handle)
                if parent_win is not None:
                    try:
                        from Xlib import X
                        from Xlib.protocol import event

                        btn_mask = (
                            X.Button1Mask
                            if button == "left"
                            else (X.Button3Mask if button == "right" else X.Button2Mask)
                        )
                        target_win, local_x, local_y = self._find_x11_child_at(
                            x_disp, parent_win, window_rel_x, window_rel_y
                        )
                        mask = X.ButtonPressMask | X.ButtonReleaseMask
                        for _ in range(clicks):
                            evt_down = event.ButtonPress(
                                time=X.CurrentTime,
                                root=x_disp.screen().root,
                                window=target_win,
                                same_screen=1,
                                child=X.NONE,
                                root_x=x or 0,
                                root_y=y or 0,
                                event_x=local_x,
                                event_y=local_y,
                                state=0,
                                detail=btn_code,
                            )
                            target_win.send_event(evt_down, event_mask=mask)

                            evt_up = event.ButtonRelease(
                                time=X.CurrentTime,
                                root=x_disp.screen().root,
                                window=target_win,
                                same_screen=1,
                                child=X.NONE,
                                root_x=x or 0,
                                root_y=y or 0,
                                event_x=local_x,
                                event_y=local_y,
                                state=btn_mask,
                                detail=btn_code,
                            )
                            target_win.send_event(evt_up, event_mask=mask)
                        x_disp.sync()
                        return
                    except Exception:
                        pass

            # Default Linux X11 path: XTest fake_input (works on Chrome, Electron, Firefox, displays)
            try:
                from Xlib import X
                from Xlib.ext import xtest

                orig_pos = self._x11_query_pointer(x_disp) if restore_cursor else None
                if x is not None and y is not None:
                    xtest.fake_input(x_disp, X.MotionNotify, x=int(x), y=int(y))
                    x_disp.sync()

                for i in range(clicks):
                    xtest.fake_input(x_disp, X.ButtonPress, btn_code)
                    x_disp.sync()
                    xtest.fake_input(x_disp, X.ButtonRelease, btn_code)
                    x_disp.sync()
                    if i + 1 < clicks:
                        time.sleep(0.02)

                if orig_pos is not None:
                    xtest.fake_input(x_disp, X.MotionNotify, x=orig_pos[0], y=orig_pos[1])
                    x_disp.sync()
                return
            except Exception:
                pass

        # 2. Windows native ctypes SendInput
        if sys.platform == "win32":
            try:
                orig_pos = self._win32_get_cursor_pos() if restore_cursor else None
                if x is not None and y is not None:
                    self._win32_set_cursor_pos(int(x), int(y))
                    time.sleep(0.02)

                down_flag = (
                    MOUSEEVENTF_LEFTDOWN
                    if button == "left"
                    else (MOUSEEVENTF_RIGHTDOWN if button == "right" else MOUSEEVENTF_MIDDLEDOWN)
                )
                up_flag = (
                    MOUSEEVENTF_LEFTUP
                    if button == "left"
                    else (MOUSEEVENTF_RIGHTUP if button == "right" else MOUSEEVENTF_MIDDLEUP)
                )

                for _ in range(clicks):
                    self._win32_send_inputs([build_win32_mouse_input(down_flag)])
                    time.sleep(0.015)
                    self._win32_send_inputs([build_win32_mouse_input(up_flag)])
                    time.sleep(0.015)

                time.sleep(0.03)
                if orig_pos is not None:
                    self._win32_set_cursor_pos(orig_pos[0], orig_pos[1])
                return
            except Exception:
                pass

        # 3. macOS native Quartz CGEvent (explicit kCGMouseEventClickState for double-click)
        if sys.platform == "darwin":
            quartz = _get_quartz()
            if quartz is not None:
                try:
                    orig_mac_pos = self._mac_get_cursor_pos(quartz) if restore_cursor else None
                    if x is not None and y is not None:
                        target_x, target_y = float(x), float(y)
                        self._mac_post_mouse_event(
                            quartz,
                            int(getattr(quartz, "kCGEventMouseMoved", 5)),
                            target_x,
                            target_y,
                            int(getattr(quartz, "kCGMouseButtonLeft", 0)),
                        )
                    elif orig_mac_pos is not None:
                        target_x, target_y = orig_mac_pos
                    else:
                        cur = self._mac_get_cursor_pos(quartz) or (0.0, 0.0)
                        target_x, target_y = cur

                    if button == "right":
                        down_type = int(getattr(quartz, "kCGEventRightMouseDown", 3))
                        up_type = int(getattr(quartz, "kCGEventRightMouseUp", 4))
                        cg_btn = int(getattr(quartz, "kCGMouseButtonRight", 1))
                    elif button == "middle":
                        down_type = int(getattr(quartz, "kCGEventOtherMouseDown", 25))
                        up_type = int(getattr(quartz, "kCGEventOtherMouseUp", 26))
                        cg_btn = int(getattr(quartz, "kCGMouseButtonCenter", 2))
                    else:
                        down_type = int(getattr(quartz, "kCGEventLeftMouseDown", 1))
                        up_type = int(getattr(quartz, "kCGEventLeftMouseUp", 2))
                        cg_btn = int(getattr(quartz, "kCGMouseButtonLeft", 0))

                    for click_idx in range(1, clicks + 1):
                        self._mac_post_mouse_event(
                            quartz, down_type, target_x, target_y, cg_btn, click_state=click_idx
                        )
                        time.sleep(0.015)
                        self._mac_post_mouse_event(
                            quartz, up_type, target_x, target_y, cg_btn, click_state=click_idx
                        )
                        if click_idx < clicks:
                            time.sleep(0.02)

                    if orig_mac_pos is not None:
                        self._mac_post_mouse_event(
                            quartz,
                            int(getattr(quartz, "kCGEventMouseMoved", 5)),
                            orig_mac_pos[0],
                            orig_mac_pos[1],
                            int(getattr(quartz, "kCGMouseButtonLeft", 0)),
                        )
                    return
                except Exception:
                    pass

        # 4. Standard fallback: PyAutoGUI
        backend = self._get_backend()
        if backend:
            orig_pos = None
            if restore_cursor:
                try:
                    orig_pos = backend.position()
                except Exception:
                    orig_pos = None

            if x is not None and y is not None:
                backend.click(x=x, y=y, button=button, clicks=clicks)
            else:
                backend.click(button=button, clicks=clicks)

            if orig_pos is not None:
                try:
                    backend.moveTo(orig_pos[0], orig_pos[1])
                except Exception:
                    pass

    def double_click(
        self,
        x: int | None = None,
        y: int | None = None,
        window_handle: int = 0,
        window_rel_x: int = 0,
        window_rel_y: int = 0,
        restore_cursor: bool = True,
    ) -> None:
        """Perform a double click."""
        self.click(
            x=x,
            y=y,
            button="left",
            clicks=2,
            window_handle=window_handle,
            window_rel_x=window_rel_x,
            window_rel_y=window_rel_y,
            restore_cursor=restore_cursor,
        )

    def right_click(
        self,
        x: int | None = None,
        y: int | None = None,
        window_handle: int = 0,
        window_rel_x: int = 0,
        window_rel_y: int = 0,
        restore_cursor: bool = True,
    ) -> None:
        """Perform a right click."""
        self.click(
            x=x,
            y=y,
            button="right",
            clicks=1,
            window_handle=window_handle,
            window_rel_x=window_rel_x,
            window_rel_y=window_rel_y,
            restore_cursor=restore_cursor,
        )

    def drag_to(self, x: int, y: int, duration: float = 0.5) -> None:
        """Drag mouse to ``(x, y)``."""
        backend = self._get_backend()
        if backend:
            backend.dragTo(x, y, duration=duration)

    def drag(
        self,
        from_x: int,
        from_y: int,
        to_x: int,
        to_y: int,
        duration: float = 0.5,
    ) -> None:
        """Drag mouse from ``(from_x, from_y)`` to ``(to_x, to_y)``."""
        # 1. Linux X11 via XTest
        x_disp = self._get_x11_display()
        if x_disp is not None:
            try:
                from Xlib import X
                from Xlib.ext import xtest

                xtest.fake_input(x_disp, X.MotionNotify, x=int(from_x), y=int(from_y))
                x_disp.sync()
                xtest.fake_input(x_disp, X.ButtonPress, 1)
                x_disp.sync()
                steps = max(5, int(duration * 60))
                for i in range(1, steps + 1):
                    cx = int(from_x + (to_x - from_x) * (i / steps))
                    cy = int(from_y + (to_y - from_y) * (i / steps))
                    xtest.fake_input(x_disp, X.MotionNotify, x=cx, y=cy)
                    x_disp.sync()
                    if duration > 0:
                        time.sleep(duration / steps)
                xtest.fake_input(x_disp, X.ButtonRelease, 1)
                x_disp.sync()
                return
            except Exception:
                pass

        # 2. Windows via ctypes SendInput
        if sys.platform == "win32":
            try:
                self._win32_set_cursor_pos(int(from_x), int(from_y))
                time.sleep(0.05)
                self._win32_send_inputs([build_win32_mouse_input(MOUSEEVENTF_LEFTDOWN)])
                time.sleep(0.05)
                steps = max(10, int(duration * 60))
                for i in range(1, steps + 1):
                    cx = int(from_x + (to_x - from_x) * (i / steps))
                    cy = int(from_y + (to_y - from_y) * (i / steps))
                    self._win32_set_cursor_pos(cx, cy)
                    if duration > 0:
                        time.sleep(duration / steps)
                self._win32_send_inputs([build_win32_mouse_input(MOUSEEVENTF_LEFTUP)])
                time.sleep(0.05)
                return
            except Exception:
                pass

        # 3. macOS via Quartz CGEvent
        if sys.platform == "darwin":
            quartz = _get_quartz()
            if quartz is not None:
                try:
                    btn = int(getattr(quartz, "kCGMouseButtonLeft", 0))
                    self._mac_post_mouse_event(
                        quartz,
                        int(getattr(quartz, "kCGEventLeftMouseDown", 1)),
                        float(from_x),
                        float(from_y),
                        btn,
                        click_state=1,
                    )
                    steps = max(8, int(duration * 60))
                    drag_type = int(getattr(quartz, "kCGEventLeftMouseDragged", 6))
                    for i in range(1, steps + 1):
                        cx = from_x + (to_x - from_x) * (i / steps)
                        cy = from_y + (to_y - from_y) * (i / steps)
                        self._mac_post_mouse_event(quartz, drag_type, cx, cy, btn)
                        if duration > 0:
                            time.sleep(duration / steps)
                    self._mac_post_mouse_event(
                        quartz,
                        int(getattr(quartz, "kCGEventLeftMouseUp", 2)),
                        float(to_x),
                        float(to_y),
                        btn,
                        click_state=1,
                    )
                    return
                except Exception:
                    pass

        backend = self._get_backend()
        if backend:
            backend.moveTo(from_x, from_y)
            backend.dragTo(to_x, to_y, duration=duration)

    def scroll(
        self,
        amount: int,
        x: int | None = None,
        y: int | None = None,
        restore_cursor: bool = True,
    ) -> None:
        """Scroll vertical wheel. Positive is up, negative is down."""
        # 1. Linux X11 via XTest (button 4 = up, button 5 = down)
        x_disp = self._get_x11_display()
        if x_disp is not None:
            try:
                from Xlib import X
                from Xlib.ext import xtest

                orig_pos = self._x11_query_pointer(x_disp) if restore_cursor else None
                if x is not None and y is not None:
                    xtest.fake_input(x_disp, X.MotionNotify, x=int(x), y=int(y))
                    x_disp.sync()

                btn = 4 if amount >= 0 else 5
                for _ in range(abs(int(amount))):
                    xtest.fake_input(x_disp, X.ButtonPress, btn)
                    xtest.fake_input(x_disp, X.ButtonRelease, btn)
                x_disp.sync()

                if orig_pos is not None:
                    xtest.fake_input(x_disp, X.MotionNotify, x=orig_pos[0], y=orig_pos[1])
                    x_disp.sync()
                return
            except Exception:
                pass

        # 2. Windows via ctypes SendInput (MOUSEEVENTF_WHEEL)
        if sys.platform == "win32":
            try:
                orig_pos = self._win32_get_cursor_pos() if restore_cursor else None
                if x is not None and y is not None:
                    self._win32_set_cursor_pos(int(x), int(y))
                    time.sleep(0.02)

                clicks = int(amount)
                delta = clicks * WHEEL_DELTA if abs(clicks) < WHEEL_DELTA else clicks
                self._win32_send_inputs(
                    [build_win32_mouse_input(MOUSEEVENTF_WHEEL, mouse_data=delta)]
                )
                time.sleep(0.02)

                if orig_pos is not None:
                    self._win32_set_cursor_pos(orig_pos[0], orig_pos[1])
                return
            except Exception:
                pass

        # 3. macOS via Quartz CGEventCreateScrollWheelEvent
        if sys.platform == "darwin":
            quartz = _get_quartz()
            if quartz is not None and hasattr(quartz, "CGEventCreateScrollWheelEvent"):
                try:
                    orig_mac_pos = self._mac_get_cursor_pos(quartz) if restore_cursor else None
                    if x is not None and y is not None:
                        self._mac_post_mouse_event(
                            quartz,
                            int(getattr(quartz, "kCGEventMouseMoved", 5)),
                            float(x),
                            float(y),
                            int(getattr(quartz, "kCGMouseButtonLeft", 0)),
                        )
                    unit = int(getattr(quartz, "kCGScrollEventUnitLine", 1))
                    evt = quartz.CGEventCreateScrollWheelEvent(None, unit, 1, int(amount))
                    tap = int(getattr(quartz, "kCGHIDEventTap", 0))
                    quartz.CGEventPost(tap, evt)
                    if orig_mac_pos is not None:
                        self._mac_post_mouse_event(
                            quartz,
                            int(getattr(quartz, "kCGEventMouseMoved", 5)),
                            orig_mac_pos[0],
                            orig_mac_pos[1],
                            int(getattr(quartz, "kCGMouseButtonLeft", 0)),
                        )
                    return
                except Exception:
                    pass

        backend = self._get_backend()
        if backend:
            orig_pos = None
            if restore_cursor:
                try:
                    orig_pos = backend.position()
                except Exception:
                    orig_pos = None

            if x is not None and y is not None:
                backend.moveTo(x, y)
            backend.scroll(amount)

            if orig_pos is not None:
                try:
                    backend.moveTo(orig_pos[0], orig_pos[1])
                except Exception:
                    pass

    def _clipboard_paste(
        self, text: str, paste_keys: tuple[str, str], window_handle: int = 0
    ) -> bool:
        """Copy ``text`` to clipboard, trigger paste hotkey, and restore prior clipboard."""
        import pyperclip

        prev_clip: str | None = None
        try:
            prev_clip = pyperclip.paste()
        except Exception:
            prev_clip = None

        try:
            pyperclip.copy(text)
            time.sleep(0.03)
            self.hotkey(*paste_keys, window_handle=window_handle)
            time.sleep(0.04)
            return True
        finally:
            if prev_clip is not None:
                try:
                    pyperclip.copy(prev_clip)
                except Exception:
                    pass

    def type_text(
        self,
        text: str,
        interval: float = 0.02,
        window_handle: int = 0,
        use_xsendevent: bool | None = None,
    ) -> None:
        """Type ASCII or Unicode text into the currently focused element or target window."""
        if not text:
            return

        has_unicode = any(ord(c) > 127 for c in text)

        # 1. Windows: always use clipboard + native SendInput(Ctrl+V) to bypass IME (no pyautogui gate)
        if sys.platform == "win32":
            try:
                import pyperclip

                prev_clip: str | None = None
                try:
                    prev_clip = pyperclip.paste()
                except Exception:
                    prev_clip = None
                try:
                    pyperclip.copy(text)
                    time.sleep(0.03)
                    vk_ctrl = _WIN_VK_MAP["ctrl"]
                    vk_v = ord("V")
                    self._win32_send_inputs(
                        [
                            build_win32_key_input(vk_ctrl, key_up=False),
                            build_win32_key_input(vk_v, key_up=False),
                            build_win32_key_input(vk_v, key_up=True),
                            build_win32_key_input(vk_ctrl, key_up=True),
                        ]
                    )
                    time.sleep(0.04)
                    return
                finally:
                    if prev_clip is not None:
                        try:
                            pyperclip.copy(prev_clip)
                        except Exception:
                            pass
            except Exception:
                pass

        # 2. macOS: always use pyperclip + Command+V so both ASCII (under CJK IME) & Unicode work
        if sys.platform == "darwin":
            try:
                if self._clipboard_paste(text, ("command", "v"), window_handle=0):
                    return
            except Exception:
                pass

        # 3. Linux X11 (XTest default; XSendEvent opt-in)
        x_disp = self._get_x11_display()
        if x_disp is not None:
            if has_unicode:
                try:
                    if self._clipboard_paste(text, ("ctrl", "v"), window_handle=window_handle):
                        return
                except Exception:
                    pass
            else:
                should_xsend = self.use_xsendevent if use_xsendevent is None else use_xsendevent
                if should_xsend and window_handle > 1:
                    parent_win = self._resolve_valid_x11_window(x_disp, window_handle)
                    if parent_win is not None:
                        try:
                            from Xlib import X
                            from Xlib.protocol import event

                            target_win = self._get_x11_active_input_window(x_disp, parent_win)
                            key_events: list[tuple[int, int]] = []
                            can_send_x11 = True
                            for char in text:
                                sym_name, needs_shift = self._resolve_x11_keysym_and_shift(char)
                                keycode = self._x11_keysym_to_keycode(x_disp, sym_name)
                                if not keycode:
                                    can_send_x11 = False
                                    break
                                key_events.append((keycode, X.ShiftMask if needs_shift else 0))

                            if can_send_x11:
                                for keycode, state in key_events:
                                    evt_down = event.KeyPress(
                                        time=X.CurrentTime,
                                        root=x_disp.screen().root,
                                        window=target_win,
                                        same_screen=1,
                                        child=X.NONE,
                                        root_x=0,
                                        root_y=0,
                                        event_x=0,
                                        event_y=0,
                                        state=state,
                                        detail=keycode,
                                    )
                                    target_win.send_event(evt_down, event_mask=X.KeyPressMask)
                                    evt_up = event.KeyRelease(
                                        time=X.CurrentTime,
                                        root=x_disp.screen().root,
                                        window=target_win,
                                        same_screen=1,
                                        child=X.NONE,
                                        root_x=0,
                                        root_y=0,
                                        event_x=0,
                                        event_y=0,
                                        state=state,
                                        detail=keycode,
                                    )
                                    target_win.send_event(evt_up, event_mask=X.KeyReleaseMask)
                                x_disp.sync()
                                return
                        except Exception:
                            pass

                # Default Linux X11 ASCII path: XTest fake_input with Shift handling
                try:
                    from Xlib import X
                    from Xlib.ext import xtest

                    shift_keycode = self._x11_keysym_to_keycode(x_disp, "Shift_L")
                    resolved: list[tuple[int, bool]] = []
                    all_ok = True
                    for char in text:
                        sym_name, needs_shift = self._resolve_x11_keysym_and_shift(char)
                        keycode = self._x11_keysym_to_keycode(x_disp, sym_name)
                        if not keycode:
                            all_ok = False
                            break
                        resolved.append((keycode, needs_shift))

                    if all_ok:
                        for keycode, needs_shift in resolved:
                            if needs_shift and shift_keycode:
                                xtest.fake_input(x_disp, X.KeyPress, shift_keycode)
                            xtest.fake_input(x_disp, X.KeyPress, keycode)
                            xtest.fake_input(x_disp, X.KeyRelease, keycode)
                            if needs_shift and shift_keycode:
                                xtest.fake_input(x_disp, X.KeyRelease, shift_keycode)
                            x_disp.sync()
                            if interval > 0:
                                time.sleep(interval)
                        return
                except Exception:
                    pass

        # 4. Generic Unicode clipboard fallback
        if has_unicode:
            paste_mod = "command" if sys.platform == "darwin" else "ctrl"
            try:
                if self._clipboard_paste(text, (paste_mod, "v"), window_handle=window_handle):
                    return
            except Exception:
                pass

        # 5. PyAutoGUI fallback
        backend = self._get_backend()
        if not backend:
            return

        try:
            backend.write(text, interval=interval)
        except Exception:
            backend.write(text)

    def press_key(
        self, key: str, window_handle: int = 0, use_xsendevent: bool | None = None
    ) -> None:
        """Press and release a single key (e.g. 'enter', 'up', 'f5', 'escape')."""
        # 1. Linux X11 (XTest default; XSendEvent opt-in)
        x_disp = self._get_x11_display()
        if x_disp is not None:
            sym_name, needs_shift = self._resolve_x11_keysym_and_shift(key)
            should_xsend = self.use_xsendevent if use_xsendevent is None else use_xsendevent
            if should_xsend and window_handle > 1:
                parent_win = self._resolve_valid_x11_window(x_disp, window_handle)
                if parent_win is not None:
                    try:
                        from Xlib import X
                        from Xlib.protocol import event

                        keycode = self._x11_keysym_to_keycode(x_disp, sym_name)
                        if keycode:
                            target_win = self._get_x11_active_input_window(x_disp, parent_win)
                            state = X.ShiftMask if needs_shift else 0
                            evt_down = event.KeyPress(
                                time=X.CurrentTime,
                                root=x_disp.screen().root,
                                window=target_win,
                                same_screen=1,
                                child=X.NONE,
                                root_x=0,
                                root_y=0,
                                event_x=0,
                                event_y=0,
                                state=state,
                                detail=keycode,
                            )
                            target_win.send_event(evt_down, event_mask=X.KeyPressMask)
                            evt_up = event.KeyRelease(
                                time=X.CurrentTime,
                                root=x_disp.screen().root,
                                window=target_win,
                                same_screen=1,
                                child=X.NONE,
                                root_x=0,
                                root_y=0,
                                event_x=0,
                                event_y=0,
                                state=state,
                                detail=keycode,
                            )
                            target_win.send_event(evt_up, event_mask=X.KeyReleaseMask)
                            x_disp.sync()
                            return
                    except Exception:
                        pass

            try:
                from Xlib import X
                from Xlib.ext import xtest

                keycode = self._x11_keysym_to_keycode(x_disp, sym_name)
                if keycode:
                    shift_keycode = (
                        self._x11_keysym_to_keycode(x_disp, "Shift_L") if needs_shift else 0
                    )
                    if shift_keycode:
                        xtest.fake_input(x_disp, X.KeyPress, shift_keycode)
                    xtest.fake_input(x_disp, X.KeyPress, keycode)
                    xtest.fake_input(x_disp, X.KeyRelease, keycode)
                    if shift_keycode:
                        xtest.fake_input(x_disp, X.KeyRelease, shift_keycode)
                    x_disp.sync()
                    return
            except Exception:
                pass

        # 2. Windows via ctypes SendInput
        if sys.platform == "win32":
            vk = self._win32_key_to_vk(key)
            if vk:
                try:
                    self._win32_send_inputs(
                        [
                            build_win32_key_input(vk, key_up=False),
                            build_win32_key_input(vk, key_up=True),
                        ]
                    )
                    return
                except Exception:
                    pass

        # 3. macOS via Quartz CGEvent
        if sys.platform == "darwin":
            norm_key = _normalize_mac_key(key)
            quartz = _get_quartz()
            if quartz is not None and norm_key in _MAC_KEYCODES:
                try:
                    kc = _MAC_KEYCODES[norm_key]
                    tap = int(getattr(quartz, "kCGHIDEventTap", 0))
                    down = quartz.CGEventCreateKeyboardEvent(None, kc, True)
                    up = quartz.CGEventCreateKeyboardEvent(None, kc, False)
                    quartz.CGEventPost(tap, down)
                    quartz.CGEventPost(tap, up)
                    return
                except Exception:
                    pass
            backend = self._get_backend()
            if backend:
                backend.press(norm_key)
            return

        backend = self._get_backend()
        if backend:
            backend.press(key.lower())

    def hotkey(
        self, *keys: str, window_handle: int = 0, use_xsendevent: bool | None = None
    ) -> None:
        """Press key combinations (e.g. ``('ctrl', 'a')`` or ``('command', 'v')``)."""
        if not keys:
            return

        # 1. Linux X11 (XTest default; XSendEvent opt-in)
        x_disp = self._get_x11_display()
        if x_disp is not None:
            mod_keys = [k.strip().lower() for k in keys[:-1]]
            main_key = keys[-1]
            main_sym, main_needs_shift = self._resolve_x11_keysym_and_shift(main_key)
            should_xsend = self.use_xsendevent if use_xsendevent is None else use_xsendevent

            if should_xsend and window_handle > 1:
                parent_win = self._resolve_valid_x11_window(x_disp, window_handle)
                if parent_win is not None:
                    try:
                        from Xlib import X
                        from Xlib.protocol import event

                        target_win = self._get_x11_active_input_window(x_disp, parent_win)
                        state = 0
                        if any(m in ("ctrl", "control") for m in mod_keys):
                            state |= X.ControlMask
                        if "shift" in mod_keys or main_needs_shift:
                            state |= X.ShiftMask
                        if any(m in ("alt", "option") for m in mod_keys):
                            state |= X.Mod1Mask
                        if any(m in ("super", "meta", "win", "command", "cmd") for m in mod_keys):
                            state |= X.Mod4Mask

                        keycode = self._x11_keysym_to_keycode(x_disp, main_sym)
                        if keycode:
                            evt_down = event.KeyPress(
                                time=X.CurrentTime,
                                root=x_disp.screen().root,
                                window=target_win,
                                same_screen=1,
                                child=X.NONE,
                                root_x=0,
                                root_y=0,
                                event_x=0,
                                event_y=0,
                                state=state,
                                detail=keycode,
                            )
                            target_win.send_event(evt_down, event_mask=X.KeyPressMask)
                            evt_up = event.KeyRelease(
                                time=X.CurrentTime,
                                root=x_disp.screen().root,
                                window=target_win,
                                same_screen=1,
                                child=X.NONE,
                                root_x=0,
                                root_y=0,
                                event_x=0,
                                event_y=0,
                                state=state,
                                detail=keycode,
                            )
                            target_win.send_event(evt_up, event_mask=X.KeyReleaseMask)
                            x_disp.sync()
                            return
                    except Exception:
                        pass

            try:
                from Xlib import X
                from Xlib.ext import xtest

                mod_keycodes: list[int] = []
                for mk in mod_keys:
                    msym, _ = self._resolve_x11_keysym_and_shift(mk)
                    mkc = self._x11_keysym_to_keycode(x_disp, msym)
                    if mkc:
                        mod_keycodes.append(mkc)
                if main_needs_shift:
                    shift_kc = self._x11_keysym_to_keycode(x_disp, "Shift_L")
                    if shift_kc and shift_kc not in mod_keycodes:
                        mod_keycodes.append(shift_kc)

                main_kc = self._x11_keysym_to_keycode(x_disp, main_sym)
                if main_kc:
                    for mkc in mod_keycodes:
                        xtest.fake_input(x_disp, X.KeyPress, mkc)
                    xtest.fake_input(x_disp, X.KeyPress, main_kc)
                    xtest.fake_input(x_disp, X.KeyRelease, main_kc)
                    for mkc in reversed(mod_keycodes):
                        xtest.fake_input(x_disp, X.KeyRelease, mkc)
                    x_disp.sync()
                    return
            except Exception:
                pass

        # 2. Windows via ctypes SendInput
        if sys.platform == "win32":
            vks = [self._win32_key_to_vk(k) for k in keys]
            if vks and all(vks):
                try:
                    events = [build_win32_key_input(vk, key_up=False) for vk in vks]
                    events.extend(build_win32_key_input(vk, key_up=True) for vk in reversed(vks))
                    self._win32_send_inputs(events)
                    return
                except Exception:
                    pass

        # 3. macOS via Quartz CGEvent with cmd -> command normalization
        if sys.platform == "darwin":
            norm_keys = [_normalize_mac_key(k) for k in keys]
            quartz = _get_quartz()
            if quartz is not None:
                try:
                    mod_flags = 0
                    for mk in norm_keys[:-1]:
                        if mk == "command":
                            mod_flags |= int(getattr(quartz, "kCGEventFlagMaskCommand", 1 << 20))
                        elif mk == "shift":
                            mod_flags |= int(getattr(quartz, "kCGEventFlagMaskShift", 1 << 17))
                        elif mk == "option":
                            mod_flags |= int(getattr(quartz, "kCGEventFlagMaskAlternate", 1 << 19))
                        elif mk == "ctrl":
                            mod_flags |= int(getattr(quartz, "kCGEventFlagMaskControl", 1 << 18))

                    main_k = norm_keys[-1]
                    if main_k in _MAC_KEYCODES:
                        kc = _MAC_KEYCODES[main_k]
                        tap = int(getattr(quartz, "kCGHIDEventTap", 0))
                        down = quartz.CGEventCreateKeyboardEvent(None, kc, True)
                        up = quartz.CGEventCreateKeyboardEvent(None, kc, False)
                        if mod_flags:
                            quartz.CGEventSetFlags(down, mod_flags)
                            quartz.CGEventSetFlags(up, mod_flags)
                        quartz.CGEventPost(tap, down)
                        quartz.CGEventPost(tap, up)
                        return
                except Exception:
                    pass

            backend = self._get_backend()
            if backend:
                backend.hotkey(*norm_keys)
            return

        backend = self._get_backend()
        if backend:
            backend.hotkey(*(k.lower() for k in keys))

    def wait(self, seconds: float) -> None:
        """Pause execution."""
        time.sleep(seconds)


# Default singleton
driver = InputDriver()
