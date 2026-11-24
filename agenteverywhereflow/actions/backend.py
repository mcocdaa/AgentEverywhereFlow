"""Pluggable ActionBackend abstraction for OS GUI and headless protocol automation."""

from __future__ import annotations

from typing import Any, Literal, Protocol, runtime_checkable

from PIL import Image

from agenteverywhereflow.actions.coords import CoordinateProjector
from agenteverywhereflow.actions.driver import InputDriver, driver
from agenteverywhereflow.capturer import get_capturer
from agenteverywhereflow.capturer.base import BaseCapturer, TargetInfo, TargetType


@runtime_checkable
class ActionBackend(Protocol):
    """Unified viewport capture and input execution interface for AEFlow targets.

    Implementations include:
    - :class:`OSGuiBackend`: Standard OS-level capture + input injection (Windows/Linux/macOS).
    - :class:`CDPActionBackend`: True background/headless Chrome DevTools Protocol backend.
    """

    def capture(self) -> Image.Image:
        """Capture the current target viewport as a PIL Image."""
        ...

    def click(
        self,
        x: float,
        y: float,
        button: Literal["left", "middle", "right"] = "left",
        click_count: int = 1,
    ) -> None:
        """Click at target-local coordinates ``(x, y)``."""
        ...

    def double_click(self, x: float, y: float) -> None:
        """Double-click at target-local coordinates ``(x, y)``."""
        ...

    def right_click(self, x: float, y: float) -> None:
        """Right-click at target-local coordinates ``(x, y)``."""
        ...

    def move_to(self, x: float, y: float) -> None:
        """Move pointer to target-local coordinates ``(x, y)``."""
        ...

    def drag(
        self,
        from_x: float,
        from_y: float,
        to_x: float,
        to_y: float,
        duration: float = 0.5,
    ) -> None:
        """Press left button at ``(from_x, from_y)`` and drag to ``(to_x, to_y)``."""
        ...

    def scroll(self, x: float, y: float, clicks: int) -> None:
        """Scroll mouse wheel at target-local coordinates ``(x, y)``.

        Positive ``clicks`` scrolls up; negative ``clicks`` scrolls down.
        """
        ...

    def type_text(self, text: str) -> None:
        """Insert/type UTF-8 text into the currently focused element."""
        ...

    def press_key(self, key: str) -> None:
        """Press and release a single logical key (e.g. ``'enter'``, ``'tab'``, ``'escape'``)."""
        ...

    def hotkey(self, *keys: str) -> None:
        """Press a key combination (e.g. ``hotkey('ctrl', 'a')``)."""
        ...

    def get_accessibility_tree(self, max_nodes: int = 200) -> list[dict[str, Any]]:
        """Return a compact list of semantic accessibility nodes if supported by the backend."""
        ...


class OSGuiBackend:
    """ActionBackend adapter wrapping the OS-level capturer and :class:`InputDriver`."""

    def __init__(
        self,
        target: TargetInfo,
        capturer: BaseCapturer | None = None,
        input_driver: InputDriver | None = None,
        auto_focus: bool = True,
    ) -> None:
        self.target = target
        self.capturer: BaseCapturer = capturer or get_capturer()
        self.driver: InputDriver = input_driver or driver
        self.auto_focus = auto_focus

    def _window_handle(self) -> int:
        """Return OS window handle only for window targets (0 for displays/regions)."""
        if self.target.target_type == TargetType.WINDOW:
            return self.target.native_handle
        return 0

    def _focus_if_needed(self) -> None:
        if self.auto_focus:
            self.capturer.focus(self.target)

    def _to_screen(self, x: float, y: float) -> tuple[int, int]:
        return CoordinateProjector.to_screen_coords(
            target=self.target,
            x=x,
            y=y,
            img_width=self.target.rect.width,
            img_height=self.target.rect.height,
        )

    def capture(self) -> Image.Image:
        """Capture the bound target viewport via the OS capturer."""
        return self.capturer.capture(self.target)

    def click(
        self,
        x: float,
        y: float,
        button: Literal["left", "middle", "right"] = "left",
        click_count: int = 1,
    ) -> None:
        """Focus target (if enabled) and click at viewport-local ``(x, y)``."""
        self._focus_if_needed()
        sx, sy = self._to_screen(x, y)
        self.driver.click(
            x=sx,
            y=sy,
            button=button,
            clicks=click_count,
            window_handle=self._window_handle(),
            window_rel_x=int(x),
            window_rel_y=int(y),
        )

    def double_click(self, x: float, y: float) -> None:
        """Double-click at viewport-local ``(x, y)``."""
        self.click(x, y, button="left", click_count=2)

    def right_click(self, x: float, y: float) -> None:
        """Right-click at viewport-local ``(x, y)``."""
        self.click(x, y, button="right", click_count=1)

    def move_to(self, x: float, y: float) -> None:
        """Move OS cursor to viewport-local ``(x, y)``."""
        sx, sy = self._to_screen(x, y)
        self.driver.move_to(x=sx, y=sy)

    def drag(
        self,
        from_x: float,
        from_y: float,
        to_x: float,
        to_y: float,
        duration: float = 0.5,
    ) -> None:
        """Drag mouse between two viewport-local coordinates."""
        self._focus_if_needed()
        sx1, sy1 = self._to_screen(from_x, from_y)
        sx2, sy2 = self._to_screen(to_x, to_y)
        self.driver.drag(from_x=sx1, from_y=sy1, to_x=sx2, to_y=sy2, duration=duration)

    def scroll(self, x: float, y: float, clicks: int) -> None:
        """Scroll wheel at viewport-local ``(x, y)``."""
        self._focus_if_needed()
        sx, sy = self._to_screen(x, y)
        self.driver.scroll(clicks, x=sx, y=sy)

    def type_text(self, text: str) -> None:
        """Type text into the target window."""
        self._focus_if_needed()
        self.driver.type_text(text, window_handle=self._window_handle())

    def press_key(self, key: str) -> None:
        """Press a single key in the target window."""
        self._focus_if_needed()
        self.driver.press_key(key, window_handle=self._window_handle())

    def hotkey(self, *keys: str) -> None:
        """Send a key combination to the target window."""
        self._focus_if_needed()
        self.driver.hotkey(*keys, window_handle=self._window_handle())

    def get_accessibility_tree(self, max_nodes: int = 200) -> list[dict[str, Any]]:
        """Return accessibility tree nodes (not yet implemented for raw OS GUI backend)."""
        _ = max_nodes
        return []

    def close(self) -> None:
        """Release any backend resources (no-op for OS GUI backend)."""
        return None
