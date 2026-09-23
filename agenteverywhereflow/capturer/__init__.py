"""Capturer package and factory."""

import sys
from agenteverywhereflow.capturer.base import BaseCapturer, Rect, TargetInfo, TargetType
from agenteverywhereflow.capturer.linux import LinuxCapturer
from agenteverywhereflow.capturer.windows import WindowsCapturer


def get_capturer() -> BaseCapturer:
    """Get the platform-appropriate screen and window capturer."""
    if sys.platform == "win32":
        return WindowsCapturer()
    elif sys.platform.startswith("linux"):
        return LinuxCapturer()
    else:
        # Fallback to WindowsCapturer/mss generic on other platforms
        return WindowsCapturer()


__all__ = ["BaseCapturer", "Rect", "TargetInfo", "TargetType", "get_capturer"]
