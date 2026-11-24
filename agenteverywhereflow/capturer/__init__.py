"""Capturer package and platform factory."""

import sys

from agenteverywhereflow.capturer.base import (
    BaseCapturer,
    Rect,
    TargetInfo,
    TargetType,
    detect_linux_session_type,
)
from agenteverywhereflow.capturer.linux import LinuxCapturer
from agenteverywhereflow.capturer.macos import MacCapturer
from agenteverywhereflow.capturer.windows import WindowsCapturer


def get_capturer() -> BaseCapturer:
    """Get the platform-appropriate screen and window capturer."""
    if sys.platform == "win32":
        return WindowsCapturer()
    elif sys.platform == "darwin":
        return MacCapturer()
    elif sys.platform.startswith("linux"):
        return LinuxCapturer()
    else:
        return WindowsCapturer()


__all__ = [
    "BaseCapturer",
    "LinuxCapturer",
    "MacCapturer",
    "Rect",
    "TargetInfo",
    "TargetType",
    "WindowsCapturer",
    "detect_linux_session_type",
    "get_capturer",
]
