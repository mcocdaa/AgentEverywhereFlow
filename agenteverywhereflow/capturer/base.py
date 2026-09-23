"""Base abstractions for screen and window capture."""

from abc import ABC, abstractmethod
from enum import Enum
from typing import NamedTuple
from PIL import Image
from pydantic import BaseModel, Field


class TargetType(str, Enum):
    """Type of the captured target."""
    DISPLAY = "display"  # Full screen / physical monitor
    WINDOW = "window"    # Dedicated application window


class Rect(NamedTuple):
    """Bounding box coordinates (x, y, width, height)."""
    x: int
    y: int
    width: int
    height: int


class TargetInfo(BaseModel):
    """Metadata describing a screen or window target."""
    target_id: str = Field(description="Unique ID, e.g. 'display:0' or 'hwnd:12345'")
    target_type: TargetType = Field(description="Target classification")
    title: str = Field(description="Human readable title or display description")
    process_name: str = Field(default="", description="Process executable name (if window)")
    rect: Rect = Field(description="Bounding rectangle in virtual screen coordinates")
    is_minimized: bool = Field(default=False, description="Whether the window is minimized")
    native_handle: int = Field(default=0, description="OS-level window or display handle")

    def __str__(self) -> str:
        kind = "🖥️ [Display]" if self.target_type == TargetType.DISPLAY else "🪟 [Window]"
        return f"{kind} {self.title} ({self.rect.width}x{self.rect.height})"


class BaseCapturer(ABC):
    """Abstract base class for OS-level window and display capture."""

    @abstractmethod
    def list_targets(self, include_displays: bool = True, include_windows: bool = True) -> list[TargetInfo]:
        """Enumerate all available displays and top-level active windows."""
        pass

    @abstractmethod
    def capture(self, target: TargetInfo) -> Image.Image:
        """Capture the target display or window into a PIL Image."""
        pass

    @abstractmethod
    def focus(self, target: TargetInfo) -> bool:
        """Bring the specified window target to the foreground."""
        pass
