"""Linux desktop display and window capture implementation."""

import shutil
import subprocess

import mss
from PIL import Image

from agenteverywhereflow.capturer.base import BaseCapturer, Rect, TargetInfo, TargetType


class LinuxCapturer(BaseCapturer):
    """Linux desktop capturer (X11 / Xlib / wmctrl / mss)."""

    def list_targets(
        self, include_displays: bool = True, include_windows: bool = True
    ) -> list[TargetInfo]:
        targets: list[TargetInfo] = []

        if include_displays:
            targets.extend(self._list_displays())

        if include_windows:
            targets.extend(self._list_windows())

        return targets

    def _list_displays(self) -> list[TargetInfo]:
        displays: list[TargetInfo] = []
        with mss.mss() as sct:
            for idx, mon in enumerate(sct.monitors[1:], start=1):
                rect = Rect(
                    x=mon["left"],
                    y=mon["top"],
                    width=mon["width"],
                    height=mon["height"],
                )
                displays.append(
                    TargetInfo(
                        target_id=f"display:{idx}",
                        target_type=TargetType.DISPLAY,
                        title=f"Display {idx} ({mon['width']}x{mon['height']})",
                        rect=rect,
                        native_handle=idx,
                    )
                )
        return displays

    def _list_windows(self) -> list[TargetInfo]:
        """Enumerate windows via wmctrl if present."""
        windows: list[TargetInfo] = []
        if not shutil.which("wmctrl"):
            return windows

        try:
            # wmctrl -l -G -p : gives id, desktop, pid, x, y, w, h, client_machine, title
            res = subprocess.run(
                ["wmctrl", "-l", "-G", "-p"],
                capture_output=True,
                text=True,
                check=True,
            )
            for line in res.stdout.strip().split("\n"):
                if not line.strip():
                    continue
                parts = line.split(maxsplit=8)
                if len(parts) >= 9:
                    win_id_hex = parts[0]
                    # desktop_num = parts[1]
                    # pid = parts[2]
                    x, y, w, h = int(parts[3]), int(parts[4]), int(parts[5]), int(parts[6])
                    title = parts[8].strip()

                    # Filter out tiny system panels or docks
                    if w > 30 and h > 30 and title:
                        windows.append(
                            TargetInfo(
                                target_id=f"xwin:{win_id_hex}",
                                target_type=TargetType.WINDOW,
                                title=title,
                                rect=Rect(x=x, y=y, width=w, height=h),
                                native_handle=int(win_id_hex, 16),
                            )
                        )
        except Exception:
            pass

        return windows

    def capture(self, target: TargetInfo) -> Image.Image:
        # 1. If target is a specific window with native X11 handle, capture directly
        if target.target_type == TargetType.WINDOW and target.native_handle > 0:
            try:
                from Xlib import X, display

                d = display.Display()
                win = d.create_resource_object("window", target.native_handle)
                geom = win.get_geometry()
                raw = win.get_image(0, 0, geom.width, geom.height, X.ZPixmap, 0xFFFFFFFF)
                if raw and raw.data:
                    return Image.frombytes(
                        "RGB", (geom.width, geom.height), raw.data, "raw", "BGRX"
                    )
            except Exception:
                pass

        # 2. Display or fallback to mss
        try:
            with mss.mss() as sct:
                monitor = {
                    "top": target.rect.y,
                    "left": target.rect.x,
                    "width": max(1, target.rect.width),
                    "height": max(1, target.rect.height),
                }
                sct_img = sct.grab(monitor)
                return Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
        except Exception:
            pass

        # 3. Fallback to blank placeholder canvas (e.g. rootless XWayland without root drawable)
        w = max(1, target.rect.width)
        h = max(1, target.rect.height)
        return Image.new("RGB", (w, h), color=(30, 30, 30))

    def focus(self, target: TargetInfo) -> bool:
        if target.target_type == TargetType.DISPLAY:
            return True
        if shutil.which("wmctrl") and target.target_id.startswith("xwin:"):
            win_id = target.target_id.split("xwin:")[1]
            try:
                subprocess.run(["wmctrl", "-i", "-a", win_id], check=True)
                return True
            except Exception:
                return False
        return False
