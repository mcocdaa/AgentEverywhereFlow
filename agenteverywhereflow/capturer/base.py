"""Base abstractions for screen and window capture."""

import io
import os
from abc import ABC, abstractmethod
from enum import StrEnum
from typing import NamedTuple

from PIL import Image
from pydantic import BaseModel, Field


class TargetType(StrEnum):
    """Type of the captured target."""

    DISPLAY = "display"  # Full screen / physical monitor
    WINDOW = "window"  # Dedicated application window
    REGION = "region"  # Custom screen bounding region


class Rect(NamedTuple):
    """Bounding box coordinates (x, y, width, height)."""

    x: int
    y: int
    width: int
    height: int


def detect_linux_session_type() -> str:
    """Detect whether the current Linux graphical session is Wayland or X11.

    Returns:
        ``"wayland"``, ``"x11"``, or ``"unknown"``.
    """
    session_type = os.environ.get("XDG_SESSION_TYPE", "").strip().lower()
    if session_type == "wayland":
        return "wayland"
    if session_type == "x11":
        return "x11"

    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    return "unknown"


class TargetInfo(BaseModel):
    """Metadata describing a screen, window, or region target."""

    target_id: str = Field(description="Unique ID, e.g. 'display:1', 'hwnd:12345', 'macwin:42'")
    target_type: TargetType = Field(description="Target classification")
    title: str = Field(description="Human readable title or display description")
    process_name: str = Field(default="", description="Process executable name (if window)")
    pid: int = Field(default=0, description="Owner process ID (if window)")
    rect: Rect = Field(description="Bounding rectangle in virtual screen coordinates")
    is_minimized: bool = Field(default=False, description="Whether the window is minimized")
    native_handle: int = Field(default=0, description="OS-level window or display handle")
    last_capture_size: tuple[int, int] | None = Field(
        default=None,
        exclude=True,
        description="Actual (width, height) of the most recently captured image for DPI scaling",
    )

    def __str__(self) -> str:
        if self.target_type == TargetType.DISPLAY:
            kind = "🖥️ [Display]"
        elif self.target_type == TargetType.REGION:
            kind = "📐 [Region]"
        else:
            kind = "🪟 [Window]"
        return f"{kind} {self.title} ({self.rect.width}x{self.rect.height})"


class BaseCapturer(ABC):
    """Abstract base class for OS-level window, display, and region capture."""

    @abstractmethod
    def list_targets(
        self, include_displays: bool = True, include_windows: bool = True
    ) -> list[TargetInfo]:
        """Enumerate all available displays and top-level active windows."""
        pass

    @abstractmethod
    def capture(self, target: TargetInfo) -> Image.Image:
        """Capture the target display, window, or region into a PIL Image."""
        pass

    @abstractmethod
    def focus(self, target: TargetInfo) -> bool:
        """Bring the specified window target to the foreground."""
        pass

    def move_window_to_display(self, target: TargetInfo, display: TargetInfo) -> bool:
        """Relocate a window target into the bounding rectangle of the specified display.

        Default implementation returns False; platform-specific capturers override this.
        """
        return False

    def target_at_point(self, x: int, y: int, exclude_pid: int | None = None) -> TargetInfo | None:
        """Return the topmost window target containing screen point ``(x, y)``.

        Default implementation searches ``list_targets(include_displays=False, include_windows=True)``
        in order and skips minimized windows or windows matching ``exclude_pid``.
        OS-specific capturers override this using native z-order queries.
        """
        for target in self.list_targets(include_displays=False, include_windows=True):
            if target.target_type != TargetType.WINDOW or target.is_minimized:
                continue
            if exclude_pid is not None and target.pid and target.pid == exclude_pid:
                continue
            r = target.rect
            if r.x <= x < r.x + r.width and r.y <= y < r.y + r.height:
                return target
        return None

    def capture_thumbnail(
        self, target: TargetInfo, max_width: int = 360, quality: int = 70
    ) -> bytes:
        """Capture a target and encode it as a scaled JPEG thumbnail.

        Args:
            target: Target to capture.
            max_width: Maximum output width in pixels; aspect ratio is preserved.
            quality: JPEG compression quality (1-95).

        Returns:
            JPEG-encoded bytes of the thumbnail image.
        """
        img = self.capture(target)
        clamped_width = max(1, int(max_width))
        clamped_quality = max(1, min(95, int(quality)))

        if img.width > clamped_width:
            scale = clamped_width / img.width
            new_height = max(1, round(img.height * scale))
            img = img.resize((clamped_width, new_height), Image.Resampling.LANCZOS)

        if img.mode != "RGB":
            img = img.convert("RGB")

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=clamped_quality, optimize=True)
        return buf.getvalue()
