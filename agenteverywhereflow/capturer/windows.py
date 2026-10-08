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

    @staticmethod
    def get_window_bounds(hwnd: int) -> tuple[int, int, int, int]:
        """Return (x, y, width, height) of the window using DWM extended frame bounds.

        Eliminates the 7-14px invisible shadow margin introduced by Win32 GetWindowRect on Windows 10/11.
        """
        if not is_windows or not hwnd:
            return (0, 0, 800, 600)

        # 1. Restored placement if minimized
        try:
            if win32gui.IsIconic(hwnd):
                placement = win32gui.GetWindowPlacement(hwnd)
                norm = placement[4]
                return (norm[0], norm[1], max(1, norm[2] - norm[0]), max(1, norm[3] - norm[1]))
        except Exception:
            pass

        # 2. DWM extended frame bounds (exact visual window rectangle)
        try:
            rect = wintypes.RECT()
            DWMWA_EXTENDED_FRAME_BOUNDS = 9
            hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(  # type: ignore[attr-defined]
                hwnd,
                DWMWA_EXTENDED_FRAME_BOUNDS,
                ctypes.byref(rect),
                ctypes.sizeof(rect),
            )
            if hr == 0:
                w = max(1, rect.right - rect.left)
                h = max(1, rect.bottom - rect.top)
                return (rect.left, rect.top, w, h)
        except Exception:
            pass

        # 3. Fallback to standard GetWindowRect
        try:
            rect_raw = win32gui.GetWindowRect(hwnd)
            w = max(1, rect_raw[2] - rect_raw[0])
            h = max(1, rect_raw[3] - rect_raw[1])
            return (rect_raw[0], rect_raw[1], w, h)
        except Exception:
            return (0, 0, 800, 600)

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
            x, y, width, height = self.get_window_bounds(hwnd)

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
                    rect=Rect(x=x, y=y, width=width, height=height),
                    is_minimized=is_minimized,
                    native_handle=hwnd,
                )
            )
            return True

        win32gui.EnumWindows(enum_callback, None)
        return windows

    def _get_active_popup(self, hwnd: int) -> int:
        """Find the active owned popup/modal dialog for this window, if any."""
        if not is_windows:
            return 0
        try:
            popup = win32gui.GetLastActivePopup(hwnd)
            if (
                popup
                and popup != hwnd
                and win32gui.IsWindow(popup)
                and win32gui.IsWindowVisible(popup)
            ):
                return popup
        except Exception:
            pass
        return 0

    def _is_foreground_or_child(self, hwnd: int, popup_hwnd: int = 0) -> bool:
        """Check if hwnd or its popup/descendant is the current foreground window."""
        if not is_windows:
            return False
        try:
            fore = win32gui.GetForegroundWindow()
            if not fore:
                return False
            if fore == hwnd or (popup_hwnd and fore == popup_hwnd):
                return True
            owner = win32gui.GetWindow(fore, win32con.GW_OWNER)
            if owner == hwnd:
                return True
            _, fore_pid = win32process.GetWindowThreadProcessId(fore)
            _, tgt_pid = win32process.GetWindowThreadProcessId(hwnd)
            if fore_pid == tgt_pid:
                return True
        except Exception:
            pass
        return False

    def _print_window(self, hwnd: int, width: int, height: int) -> Image.Image | None:
        """Capture a single window using PrintWindow (PW_RENDERFULLCONTENT)."""
        if not is_windows:
            return None
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
            pass
        return None

    def capture(self, target: TargetInfo) -> Image.Image:
        """Capture the target. Handles displays, foreground windows with popups, and background windows."""
        if target.target_type == TargetType.DISPLAY:
            return self._capture_rect(target.rect)

        # Window capture
        if is_windows and target.native_handle:
            hwnd = target.native_handle
            # If minimized, restore without stealing active focus
            if win32gui.IsIconic(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)

            # Check for active owned modal popup (e.g. file dialog, alert box)
            popup_hwnd = self._get_active_popup(hwnd)

            # Update latest bounding rectangle dynamically
            x, y, width, height = self.get_window_bounds(hwnd)

            # If an active popup exists, union its bounding box with parent so dialog is visible
            if popup_hwnd:
                px, py, pw, ph = self.get_window_bounds(popup_hwnd)
                ux = min(x, px)
                uy = min(y, py)
                ur = max(x + width, px + pw)
                ub = max(y + height, py + ph)
                target.rect = Rect(x=ux, y=uy, width=ur - ux, height=ub - uy)
            else:
                target.rect = Rect(x=x, y=y, width=width, height=height)

            # If the target window or its popup/child is currently in the foreground,
            # capture directly from screen DC. This preserves 100% fidelity:
            # child popups, modal file dialogs, context menus, tooltips, and comboboxes.
            if self._is_foreground_or_child(hwnd, popup_hwnd):
                return self._capture_rect(target.rect)

            # Otherwise (window is in background/occluded), use PrintWindow with PW_RENDERFULLCONTENT
            try:
                img_parent = self._print_window(hwnd, width, height)
                if popup_hwnd and img_parent:
                    try:
                        px, py, pw, ph = self.get_window_bounds(popup_hwnd)
                        img_popup = self._print_window(popup_hwnd, pw, ph)
                        if img_popup:
                            if target.rect.width != width or target.rect.height != height:
                                canvas = Image.new(
                                    "RGB", (target.rect.width, target.rect.height), (0, 0, 0)
                                )
                                canvas.paste(img_parent, (x - target.rect.x, y - target.rect.y))
                                canvas.paste(img_popup, (px - target.rect.x, py - target.rect.y))
                                return canvas
                            else:
                                img_parent.paste(img_popup, (px - x, py - y))
                                return img_parent
                    except Exception:
                        pass
                if img_parent:
                    return img_parent
            except Exception:
                pass

            return self._capture_rect(target.rect)

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
                    # Windows Alt-key tap trick to unlock SetForegroundWindow permissions
                    try:
                        import win32api

                        win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
                        win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
                    except Exception:
                        pass

                    fore_thread, _ = win32process.GetWindowThreadProcessId(fore_hwnd)
                    cur_thread = win32process.GetCurrentThreadId()
                    target_thread, _ = win32process.GetWindowThreadProcessId(hwnd)

                    if fore_thread and fore_thread != cur_thread:
                        win32process.AttachThreadInput(cur_thread, fore_thread, True)
                    if target_thread and target_thread != cur_thread:
                        win32process.AttachThreadInput(cur_thread, target_thread, True)

                    win32gui.BringWindowToTop(hwnd)
                    win32gui.SetForegroundWindow(hwnd)

                    # 3. Z-order topmost elevation trick to pierce through other topmost windows
                    try:
                        ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
                        is_topmost = bool(ex_style & win32con.WS_EX_TOPMOST)

                        win32gui.SetWindowPos(
                            hwnd,
                            win32con.HWND_TOPMOST,
                            0,
                            0,
                            0,
                            0,
                            win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
                        )
                        if not is_topmost:
                            win32gui.SetWindowPos(
                                hwnd,
                                win32con.HWND_NOTOPMOST,
                                0,
                                0,
                                0,
                                0,
                                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
                            )
                    except Exception:
                        pass

                    if fore_thread and fore_thread != cur_thread:
                        win32process.AttachThreadInput(cur_thread, fore_thread, False)
                    if target_thread and target_thread != cur_thread:
                        win32process.AttachThreadInput(cur_thread, target_thread, False)
                else:
                    win32gui.BringWindowToTop(hwnd)

                popup_hwnd = self._get_active_popup(hwnd)
                if popup_hwnd and win32gui.IsWindow(popup_hwnd):
                    try:
                        win32gui.BringWindowToTop(popup_hwnd)
                        win32gui.SetForegroundWindow(popup_hwnd)
                    except Exception:
                        pass

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
