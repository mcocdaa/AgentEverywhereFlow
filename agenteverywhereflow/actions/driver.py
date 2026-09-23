"""OS-level mouse and keyboard driver with headless and safe-import fallback."""

import sys
import time
from typing import Any, Literal


class InputDriver:
    """Low-level OS input driver for keyboard and mouse."""

    def __init__(self) -> None:
        self._gui_backend: Any = None
        self._backend_available: bool | None = None

    def _get_backend(self) -> Any:
        """Lazy load GUI automation backend to prevent headless/CI import failures."""
        if self._backend_available is False:
            return None

        if self._gui_backend is None:
            try:
                import pyautogui
                pyautogui.PAUSE = 0.05
                pyautogui.FAILSAFE = True
                self._gui_backend = pyautogui
                self._backend_available = True
            except Exception as e:
                self._backend_available = False
                self._gui_backend = None
                # Silently allow fallback in headless test environments
                return None

        return self._gui_backend

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
    ) -> None:
        """Perform mouse click at (x, y) or current position."""
        backend = self._get_backend()
        if backend:
            if x is not None and y is not None:
                backend.click(x=x, y=y, button=button, clicks=clicks)
            else:
                backend.click(button=button, clicks=clicks)

    def double_click(self, x: int | None = None, y: int | None = None) -> None:
        """Perform a double click."""
        self.click(x=x, y=y, button="left", clicks=2)

    def right_click(self, x: int | None = None, y: int | None = None) -> None:
        """Perform a right click."""
        self.click(x=x, y=y, button="right", clicks=1)

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

    def type_text(self, text: str, interval: float = 0.02) -> None:
        """Type unicode text into currently focused input."""
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

    def press_key(self, key: str) -> None:
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
