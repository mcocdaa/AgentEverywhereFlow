"""Windows native display and window capture implementation.

Supports:
- Per-monitor DPI awareness (DPI-aware v2)
- Multi-display enumeration (Display 1, Display 2, ...)
- Native top-level window filtering (DWM cloaked window exclusion)
- Occlusion-resistant window capturing via PrintWindow (PW_RENDERFULLCONTENT)
"""

import sys
from typing import Any

import mss
from PIL import Image

from agenteverywhereflow.capturer.base import BaseCapturer, Rect, TargetInfo, TargetType

# Windows-specific imports with graceful fallbacks
is_windows = sys.platform == "win32"
if is_windows:
    import ctypes
    from ctypes import wintypes

    import win32con
    import win32gui
    import win32process
    import win32ui
else:
    ctypes = None  # type: ignore
    wintypes = None  # type: ignore
    win32gui = None  # type: ignore
    win32process = None  # type: ignore
    win32con = None  # type: ignore
    win32ui = None  # type: ignore


class WindowsCapturer(BaseCapturer):
    """Native Windows capturer utilizing Win32 API, DWM, and mss."""

    def __init__(self) -> None:
        self._set_dpi_awareness()

    def _set_dpi_awareness(self) -> None:
        """Enable Per-Monitor DPI Awareness v2 to eliminate coordinate drift."""
        if not is_windows:
            return
        try:
            # PROCESS_PER_MONITOR_DPI_AWARE_V2 = 2
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # type: ignore[attr-defined]
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()  # type: ignore[attr-defined]
            except Exception:
                pass

    def list_targets(
        self, include_displays: bool = True, include_windows: bool = True
    ) -> list[TargetInfo]:
        """List all physical displays and eligible top-level windows."""
        targets: list[TargetInfo] = []

        # 1. Enumerate Displays
        if include_displays:
            targets.extend(self._list_displays())

        # 2. Enumerate Windows
        if include_windows and is_windows:
            targets.extend(self._list_windows())

        return targets

    def _list_displays(self) -> list[TargetInfo]:
        """Enumerate all connected monitors using mss."""
        displays: list[TargetInfo] = []
        with mss.mss() as sct:
            # sct.monitors[0] is the bounding box of ALL monitors combined.
            # sct.monitors[1..n] are individual physical displays.
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
        """Enumerate active top-level windows."""
        windows: list[TargetInfo] = []

        def enum_callback(hwnd: int, extra: Any) -> bool:
            if not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
                return True

            title = win32gui.GetWindowText(hwnd).strip()
            if not title:
                return True

            # Exclude desktop shell manager, overlay and invisible system frames
            class_name = win32gui.GetClassName(hwnd)
            if class_name in ("Progman", "WorkerW", "Shell_TrayWnd", "Windows.UI.Core.CoreWindow"):
                return True
            if title in ("Program Manager", "NVIDIA GeForce Overlay", "Windows Input Experience"):
                return True

            # Exclude cloaked windows (e.g. UWP suspended apps, background store apps)
            DWMWA_CLOAKED = 14
            is_cloaked = ctypes.c_int(0)
            ctypes.windll.dwmapi.DwmGetWindowAttribute(  # type: ignore[attr-defined]
                hwnd,
                DWMWA_CLOAKED,
                ctypes.byref(is_cloaked),
                ctypes.sizeof(is_cloaked),
            )
            if is_cloaked.value != 0:
                return True

            is_minimized = bool(win32gui.IsIconic(hwnd))

            # Extract window rect (use restored placement rectangle if minimized)
            if is_minimized:
                try:
                    placement = win32gui.GetWindowPlacement(hwnd)
                    norm_rect = placement[4]  # (left, top, right, bottom)
                    width = norm_rect[2] - norm_rect[0]
                    height = norm_rect[3] - norm_rect[1]
                    rect_raw = norm_rect
                except Exception:
                    rect_raw = win32gui.GetWindowRect(hwnd)
                    width = rect_raw[2] - rect_raw[0]
                    height = rect_raw[3] - rect_raw[1]
            else:
                rect_raw = win32gui.GetWindowRect(hwnd)
                width = rect_raw[2] - rect_raw[0]
                height = rect_raw[3] - rect_raw[1]

            # Filter out zero-size or off-screen invisible handles
            if width <= 30 or height <= 30:
                return True

            # Get process info: try psutil first, then native kernel32 QueryFullProcessImageNameW
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            proc_name = ""
            try:
                import psutil

                proc_name = psutil.Process(pid).name()
            except Exception:
                try:
                    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                    h_proc = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
                        PROCESS_QUERY_LIMITED_INFORMATION, False, pid
                    )
                    if h_proc:
                        buf = ctypes.create_unicode_buffer(1024)
                        size = ctypes.c_ulong(1024)
                        if ctypes.windll.kernel32.QueryFullProcessImageNameW(  # type: ignore[attr-defined]
                            h_proc, 0, buf, ctypes.byref(size)
                        ):
                            from pathlib import Path

                            proc_name = Path(buf.value).name
                        ctypes.windll.kernel32.CloseHandle(h_proc)  # type: ignore[attr-defined]
                except Exception:
                    pass

            windows.append(
                TargetInfo(
                    target_id=f"hwnd:{hex(hwnd)}",
                    target_type=TargetType.WINDOW,
                    title=title,
                    process_name=proc_name,
                    rect=Rect(x=rect_raw[0], y=rect_raw[1], width=width, height=height),
                    is_minimized=is_minimized,
                    native_handle=hwnd,
                )
            )
            return True

        win32gui.EnumWindows(enum_callback, None)
        return windows

    def capture(self, target: TargetInfo) -> Image.Image:
        """Capture the target. Uses PrintWindow for windows, mss for displays."""
        if target.target_type == TargetType.DISPLAY:
            return self._capture_rect(target.rect)

        # Window capture
        if is_windows and target.native_handle:
            hwnd = target.native_handle
            # If minimized, restore it
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)

            # Update latest bounding rectangle
            rect_raw = win32gui.GetWindowRect(hwnd)
            width = max(1, rect_raw[2] - rect_raw[0])
            height = max(1, rect_raw[3] - rect_raw[1])

            # Try native PrintWindow with PW_RENDERFULLCONTENT (captures even if occluded)
            try:
                PW_RENDERFULLCONTENT = 0x00000002
                hwnd_dc = win32gui.GetWindowDC(hwnd)
                mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
                save_dc = mfc_dc.CreateCompatibleDC()

                save_bitmap = win32ui.CreateBitmap()
                save_bitmap.CreateCompatibleBitmap(mfc_dc, width, height)
                save_dc.SelectObject(save_bitmap)

                ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), PW_RENDERFULLCONTENT)  # type: ignore[attr-defined]

                bmpinfo = save_bitmap.GetInfo()
                bmpstr = save_bitmap.GetBitmapBits(True)
                img = Image.frombuffer(
                    "RGB",
                    (bmpinfo["bmWidth"], bmpinfo["bmHeight"]),
                    bmpstr,
                    "raw",
                    "BGRX",
                    0,
                    1,
                )

                win32gui.DeleteObject(save_bitmap.GetHandle())
                save_dc.DeleteDC()
                mfc_dc.DeleteDC()
                win32gui.ReleaseDC(hwnd, hwnd_dc)

                if img.width > 0 and img.height > 0:
                    return img
            except Exception:
                pass  # Fall back to mss rect capture

            return self._capture_rect(Rect(rect_raw[0], rect_raw[1], width, height))

        return self._capture_rect(target.rect)

    def _capture_rect(self, rect: Rect) -> Image.Image:
        """Fallback capture via mss."""
        with mss.mss() as sct:
            monitor = {
                "top": rect.y,
                "left": rect.x,
                "width": max(1, rect.width),
                "height": max(1, rect.height),
            }
            sct_img = sct.grab(monitor)
            return Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")

    def focus(self, target: TargetInfo) -> bool:
        """Bring the target window to foreground reliably bypassing Windows lock."""
        if target.target_type == TargetType.DISPLAY:
            return True
        if is_windows and target.native_handle:
            hwnd = target.native_handle
            try:
                import sys
                import time

                from rich.console import Console

                from agenteverywhereflow.config import config

                # 1. Restore if minimized
                is_iconic = win32gui.IsIconic(hwnd)
                if is_iconic:
                    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                else:
                    win32gui.ShowWindow(hwnd, win32con.SW_SHOW)

                # 2. AttachThreadInput trick to bypass Windows SetForegroundWindow lock
                fore_hwnd = win32gui.GetForegroundWindow()
                if config.debug:
                    Console(file=sys.__stdout__).print(
                        f"[dim magenta]  [DEBUG-WIN32] focus(): target=0x{hwnd:x}, fore_hwnd=0x{fore_hwnd:x}, is_iconic={is_iconic}[/dim magenta]"
                    )

                if fore_hwnd != hwnd:
                    fore_thread, _ = win32process.GetWindowThreadProcessId(fore_hwnd)
                    cur_thread = win32process.GetCurrentThreadId()
                    target_thread, _ = win32process.GetWindowThreadProcessId(hwnd)

                    if fore_thread and fore_thread != cur_thread:
                        win32process.AttachThreadInput(cur_thread, fore_thread, True)
                    if target_thread and target_thread != cur_thread:
                        win32process.AttachThreadInput(cur_thread, target_thread, True)

                    win32gui.BringWindowToTop(hwnd)
                    win32gui.SetForegroundWindow(hwnd)

                    if fore_thread and fore_thread != cur_thread:
                        win32process.AttachThreadInput(cur_thread, fore_thread, False)
                    if target_thread and target_thread != cur_thread:
                        win32process.AttachThreadInput(cur_thread, target_thread, False)
                else:
                    win32gui.BringWindowToTop(hwnd)

                time.sleep(0.08)
                return True
            except Exception as e:
                import sys

                from rich.console import Console

                from agenteverywhereflow.config import config

                if config.debug:
                    Console(file=sys.__stdout__).print(
                        f"[bold red]  [DEBUG-WIN32] AttachThreadInput focus failed: {e}, fallback SetForegroundWindow[/bold red]"
                    )
                try:
                    win32gui.SetForegroundWindow(hwnd)
                    return True
                except Exception:
                    return False
        return False
