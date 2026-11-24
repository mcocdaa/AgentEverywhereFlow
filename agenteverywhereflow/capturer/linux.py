"""Linux desktop display, window, and region capture implementation."""

import logging
import shutil
import subprocess
from typing import Any

import mss
import psutil
from PIL import Image

from agenteverywhereflow.capturer.base import (
    BaseCapturer,
    Rect,
    TargetInfo,
    TargetType,
    detect_linux_session_type,
)

logger = logging.getLogger(__name__)


class LinuxCapturer(BaseCapturer):
    """Linux desktop capturer (X11 / Xlib / wmctrl / mss)."""

    def __init__(self) -> None:
        self._display: Any = None
        self._warned_wayland: bool = False
        self._warned_wmctrl: bool = False
        self._warn_wayland_once()

    def _warn_wayland_once(self) -> None:
        """Emit a one-time actionable warning when running under a Wayland session."""
        if not self._warned_wayland and detect_linux_session_type() == "wayland":
            self._warned_wayland = True
            logger.warning(
                "Wayland session detected (XDG_SESSION_TYPE=wayland). Native Wayland windows "
                "cannot be enumerated or captured via X11/mss/wmctrl. Switch your login session "
                "to 'Ubuntu on Xorg', or run the isolated headless Xvfb stack via "
                "'docker compose up'."
            )

    def _warn_missing_wmctrl_once(self) -> None:
        """Emit a one-time warning when wmctrl is not installed."""
        if not self._warned_wmctrl:
            self._warned_wmctrl = True
            logger.warning(
                "'wmctrl' is not installed; Linux window enumeration and focusing are disabled. "
                "Install it with: sudo apt-get install -y wmctrl xclip"
            )

    def _get_display(self) -> Any:
        if self._display is None:
            from Xlib import display

            self._display = display.Display()
        return self._display

    def close(self) -> None:
        """Close cached X11 display connection if open."""
        if self._display is not None:
            try:
                self._display.close()
            except Exception:
                pass
            self._display = None

    def list_targets(
        self, include_displays: bool = True, include_windows: bool = True
    ) -> list[TargetInfo]:
        self._warn_wayland_once()
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
        """Enumerate windows via wmctrl if present, enriching with PID and process_name."""
        windows: list[TargetInfo] = []
        if not shutil.which("wmctrl"):
            self._warn_missing_wmctrl_once()
            return windows

        try:
            # wmctrl -l -G -p : gives id, desktop, pid, x, y, w, h, client_machine, title
            res = subprocess.run(
                ["wmctrl", "-l", "-G", "-p"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=True,
            )
            for line in res.stdout.strip().split("\n"):
                if not line.strip():
                    continue
                parts = line.split(maxsplit=8)
                if len(parts) >= 9:
                    win_id_hex = parts[0]
                    try:
                        pid = int(parts[2])
                    except ValueError:
                        pid = 0
                    x, y, w, h = int(parts[3]), int(parts[4]), int(parts[5]), int(parts[6])
                    title = parts[8].strip()

                    proc_name = ""
                    if pid > 0:
                        try:
                            proc_name = psutil.Process(pid).name()
                        except Exception:
                            proc_name = ""

                    # Filter out tiny system panels and docks
                    if w > 30 and h > 30 and title:
                        windows.append(
                            TargetInfo(
                                target_id=f"xwin:{win_id_hex}",
                                target_type=TargetType.WINDOW,
                                title=title,
                                process_name=proc_name,
                                pid=pid,
                                rect=Rect(x=x, y=y, width=w, height=h),
                                native_handle=int(win_id_hex, 16),
                            )
                        )
        except Exception:
            pass

        return windows

    def _capture_rect_mss(self, rect: Rect) -> Image.Image:
        """Capture an arbitrary screen rectangle via mss with gray placeholder fallback."""
        try:
            with mss.mss() as sct:
                monitor = {
                    "top": rect.y,
                    "left": rect.x,
                    "width": max(1, rect.width),
                    "height": max(1, rect.height),
                }
                sct_img = sct.grab(monitor)
                return Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
        except Exception:
            pass

        w = max(1, rect.width)
        h = max(1, rect.height)
        return Image.new("RGB", (w, h), color=(30, 30, 30))

    def capture(self, target: TargetInfo) -> Image.Image:
        """Capture a region, display, or X11 window, refreshing window geometry live."""
        self._warn_wayland_once()

        # 1. Explicit REGION branch — never attempt Xlib get_image on a region
        if target.target_type == TargetType.REGION:
            img = self._capture_rect_mss(target.rect)
            target.last_capture_size = (img.width, img.height)
            return img

        # 2. If target is a specific window with native X11 handle, refresh rect and capture
        if target.target_type == TargetType.WINDOW and target.native_handle > 0:
            try:
                from Xlib import X

                d = self._get_display()
                win = d.create_resource_object("window", target.native_handle)
                geom = win.get_geometry()

                # Refresh live screen-space bounding rectangle to prevent coordinate drift
                try:
                    root = d.screen().root
                    coords = root.translate_coords(win, 0, 0)
                    target.rect = Rect(
                        x=int(coords.x),
                        y=int(coords.y),
                        width=max(1, int(geom.width)),
                        height=max(1, int(geom.height)),
                    )
                except Exception:
                    if geom.width > 0 and geom.height > 0:
                        target.rect = Rect(
                            x=target.rect.x,
                            y=target.rect.y,
                            width=int(geom.width),
                            height=int(geom.height),
                        )

                raw = win.get_image(0, 0, geom.width, geom.height, X.ZPixmap, 0xFFFFFFFF)
                if raw and raw.data:
                    img = Image.frombytes("RGB", (geom.width, geom.height), raw.data, "raw", "BGRX")
                    target.last_capture_size = (img.width, img.height)
                    return img
            except Exception:
                self.close()

        # 3. Display or fallback to mss
        img = self._capture_rect_mss(target.rect)
        target.last_capture_size = (img.width, img.height)
        return img

    def focus(self, target: TargetInfo) -> bool:
        if target.target_type in (TargetType.DISPLAY, TargetType.REGION):
            return True
        if not shutil.which("wmctrl"):
            self._warn_missing_wmctrl_once()
            return False
        if target.target_id.startswith("xwin:"):
            win_id = target.target_id.split("xwin:")[1]
            try:
                subprocess.run(["wmctrl", "-i", "-a", win_id], check=True)
                return True
            except Exception:
                return False
        return False

    def move_window_to_display(self, target: TargetInfo, display: TargetInfo) -> bool:
        """Relocate a window target into the bounding rectangle of the specified display."""
        if target.target_type != TargetType.WINDOW or display.target_type != TargetType.DISPLAY:
            return False
        if shutil.which("wmctrl") and target.target_id.startswith("xwin:"):
            win_id = target.target_id.split("xwin:")[1]
            try:
                dest_x = display.rect.x + max(0, (display.rect.width - target.rect.width) // 2)
                dest_y = display.rect.y + max(0, (display.rect.height - target.rect.height) // 2)
                subprocess.run(
                    [
                        "wmctrl",
                        "-i",
                        "-r",
                        win_id,
                        "-e",
                        f"0,{dest_x},{dest_y},{target.rect.width},{target.rect.height}",
                    ],
                    check=True,
                )
                target.rect = Rect(
                    x=dest_x, y=dest_y, width=target.rect.width, height=target.rect.height
                )
                return True
            except Exception:
                pass
        return False

    def target_at_point(self, x: int, y: int, exclude_pid: int | None = None) -> TargetInfo | None:
        """Return topmost X11 window at ``(x, y)`` using ``_NET_CLIENT_LIST_STACKING`` z-order."""
        windows = self._list_windows()
        if not windows:
            return None

        by_handle = {w.native_handle: w for w in windows if w.native_handle > 0}
        ordered_windows: list[TargetInfo] = []

        try:
            from Xlib import X

            d = self._get_display()
            root = d.screen().root
            atom = d.intern_atom("_NET_CLIENT_LIST_STACKING")
            prop = root.get_full_property(atom, X.AnyPropertyType)
            if prop and getattr(prop, "value", None):
                # EWMH _NET_CLIENT_LIST_STACKING is bottom-to-top; reverse for topmost first
                seen: set[int] = set()
                for xid in reversed(list(prop.value)):
                    xid_int = int(xid)
                    if xid_int in by_handle and xid_int not in seen:
                        ordered_windows.append(by_handle[xid_int])
                        seen.add(xid_int)
                for w in windows:
                    if w.native_handle not in seen:
                        ordered_windows.append(w)
        except Exception:
            ordered_windows = windows

        for win in ordered_windows:
            if win.is_minimized:
                continue
            if exclude_pid is not None and win.pid and win.pid == exclude_pid:
                continue
            r = win.rect
            if r.x <= x < r.x + r.width and r.y <= y < r.y + r.height:
                return win

        return None
