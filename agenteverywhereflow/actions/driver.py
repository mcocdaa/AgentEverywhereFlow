"""OS-level mouse and keyboard driver with native window-level event injection."""

import sys
import time
from typing import Any, Literal


class InputDriver:
    """Low-level OS input driver for keyboard and mouse."""

    def __init__(self) -> None:
        self._gui_backend: Any = None
        self._backend_available: bool | None = None
        self._x11_display: Any = None

    def _get_backend(self) -> Any:
        """Lazy load GUI automation backend to prevent headless/CI import failures."""
        if self._backend_available is False:
            return None

        if self._gui_backend is None:
            try:
                import pyautogui
                pyautogui.PAUSE = 0.05
                pyautogui.FAILSAFE = False
                self._gui_backend = pyautogui
                self._backend_available = True
            except Exception:
                self._backend_available = False
                self._gui_backend = None
                return None

        return self._gui_backend

    def _get_x11_display(self) -> Any:
        """Retrieve X11 display connection if on Linux."""
        if sys.platform.startswith("linux") and self._x11_display is None:
            try:
                from Xlib import display
                self._x11_display = display.Display()
            except Exception:
                self._x11_display = False
        return self._x11_display if self._x11_display else None

    def _find_x11_child_at(self, disp: Any, win: Any, rx: int, ry: int) -> tuple[Any, int, int]:
        """Recursively locate the innermost child widget under window relative (rx, ry)."""
        try:
            tree = win.query_tree()
            for child in reversed(tree.children):
                cg = child.get_geometry()
                if cg.x <= rx < cg.x + cg.width and cg.y <= ry < cg.y + cg.height:
                    return self._find_x11_child_at(disp, child, rx - cg.x, ry - cg.y)
        except Exception:
            pass
        return win, rx, ry

    def move_to(self, x: int, y: int, duration: float = 0.1) -> None:
        """Move cursor smoothly to target screen coordinates."""
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
    ) -> None:
        """Perform mouse click at (x, y) or via direct window handle event."""
        # 1. If on Linux and window handle is provided, perform deep child hit-test and event injection
        x_disp = self._get_x11_display()
        if x_disp and window_handle > 0:
            try:
                from Xlib import X
                from Xlib.protocol import event

                btn_code = 1 if button == "left" else (3 if button == "right" else 2)
                btn_mask = X.Button1Mask if button == "left" else (X.Button3Mask if button == "right" else X.Button2Mask)

                parent_win = x_disp.create_resource_object("window", window_handle)
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
                x_disp.flush()
                return
            except Exception:
                pass

        # 2. Windows direct message injection
        if sys.platform == "win32" and window_handle > 0:
            try:
                import win32api
                import win32con

                lparam = (window_rel_y << 16) | (window_rel_x & 0xFFFF)
                msg_down = win32con.WM_LBUTTONDOWN if button == "left" else win32con.WM_RBUTTONDOWN
                msg_up = win32con.WM_LBUTTONUP if button == "left" else win32con.WM_RBUTTONUP

                for _ in range(clicks):
                    win32api.PostMessage(window_handle, msg_down, 0, lparam)
                    win32api.PostMessage(window_handle, msg_up, 0, lparam)
                return
            except Exception:
                pass

        # 3. Standard fallback: PyAutoGUI
        backend = self._get_backend()
        if backend:
            if x is not None and y is not None:
                backend.click(x=x, y=y, button=button, clicks=clicks)
            else:
                backend.click(button=button, clicks=clicks)

    def double_click(self, x: int | None = None, y: int | None = None, window_handle: int = 0) -> None:
        """Perform a double click."""
        self.click(x=x, y=y, button="left", clicks=2, window_handle=window_handle)

    def right_click(self, x: int | None = None, y: int | None = None, window_handle: int = 0) -> None:
        """Perform a right click."""
        self.click(x=x, y=y, button="right", clicks=1, window_handle=window_handle)

    def drag_to(self, x: int, y: int, duration: float = 0.5) -> None:
        """Drag mouse to (x, y)."""
        backend = self._get_backend()
        if backend:
            backend.dragTo(x, y, duration=duration)

    def scroll(self, amount: int, x: int | None = None, y: int | None = None) -> None:
        """Scroll vertical wheel. Positive is up, negative is down."""
        backend = self._get_backend()
        if backend:
            if x is not None and y is not None:
                backend.moveTo(x, y)
            backend.scroll(amount)

    def type_text(self, text: str, interval: float = 0.02, window_handle: int = 0) -> None:
        """Type unicode text into currently focused input or target window."""
        x_disp = self._get_x11_display()
        if x_disp and window_handle > 0:
            try:
                from Xlib import X, XK
                from Xlib.protocol import event

                parent_win = x_disp.create_resource_object("window", window_handle)
                # If window has active child/focused window, locate it
                target_win = parent_win
                tree = parent_win.query_tree()
                if tree.children:
                    # Target the last focused or input child
                    target_win = tree.children[-1]

                for char in text:
                    keysym = XK.string_to_keysym(char)
                    if keysym:
                        keycode = x_disp.keysym_to_keycode(keysym)
                        state = X.ShiftMask if char.isupper() else 0

                        evt_down = event.KeyPress(
                            time=X.CurrentTime,
                            root=x_disp.screen().root,
                            window=target_win,
                            same_screen=1,
                            child=X.NONE,
                            root_x=0, root_y=0, event_x=0, event_y=0,
                            state=state, detail=keycode,
                        )
                        target_win.send_event(evt_down, event_mask=X.KeyPressMask)

                        evt_up = event.KeyRelease(
                            time=X.CurrentTime,
                            root=x_disp.screen().root,
                            window=target_win,
                            same_screen=1,
                            child=X.NONE,
                            root_x=0, root_y=0, event_x=0, event_y=0,
                            state=state, detail=keycode,
                        )
                        target_win.send_event(evt_up, event_mask=X.KeyReleaseMask)
                x_disp.flush()
                return
            except Exception:
                pass

        backend = self._get_backend()
        if not backend:
            return

        try:
            backend.write(text, interval=interval)
        except Exception:
            if sys.platform == "win32":
                try:
                    import win32clipboard
                    win32clipboard.OpenClipboard()
                    win32clipboard.EmptyClipboard()
                    win32clipboard.SetClipboardText(text)
                    win32clipboard.CloseClipboard()
                    backend.hotkey("ctrl", "v")
                except Exception:
                    backend.write(text)
            else:
                backend.write(text)

    def press_key(self, key: str, window_handle: int = 0) -> None:
        """Press and release a single key (e.g. 'enter', 'esc', 'tab', 'backspace')."""
        backend = self._get_backend()
        if backend:
            backend.press(key.lower())

    def hotkey(self, *keys: str) -> None:
        """Press key combinations (e.g. ('ctrl', 'c'), ('alt', 'tab'))."""
        backend = self._get_backend()
        if backend:
            backend.hotkey(*(k.lower() for k in keys))

    def wait(self, seconds: float) -> None:
        """Pause execution."""
        time.sleep(seconds)


# Default singleton
driver = InputDriver()
