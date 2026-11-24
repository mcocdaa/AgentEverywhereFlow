"""Native macOS display, window, and region capture implementation.

Uses PyObjC (Quartz / AppKit / ApplicationServices) with lazy imports so this module
can be safely imported and unit-tested on Linux and Windows without macOS frameworks.
"""

import ctypes
import logging
import shutil
import subprocess
import sys
from typing import Any

import mss
from PIL import Image

from agenteverywhereflow.capturer.base import BaseCapturer, Rect, TargetInfo, TargetType

logger = logging.getLogger(__name__)

_SYSTEM_OVERLAY_OWNERS = frozenset(
    {
        "Window Server",
        "Dock",
        "Control Center",
        "Notification Center",
        "Spotlight",
        "SystemUIServer",
        "Wallpaper",
    }
)

_AEFLOW_OVERLAY_OWNERS = frozenset({"AEFlow Desktop"})
_AEFLOW_OVERLAY_TITLES = frozenset(
    {
        "AEFlow Target Picker",
        "AEFlow Target Border",
        "AEFlow Status Pill",
    }
)


def _get_quartz() -> Any | None:
    """Lazily import Quartz framework (pyobjc-framework-Quartz)."""
    try:
        import Quartz

        return Quartz
    except Exception:
        return None


def _get_appkit() -> Any | None:
    """Lazily import AppKit framework (pyobjc-framework-Cocoa)."""
    try:
        import AppKit

        return AppKit
    except Exception:
        return None


def _get_application_services() -> Any | None:
    """Lazily import ApplicationServices framework if available."""
    try:
        import ApplicationServices

        return ApplicationServices
    except Exception:
        return None


def _parse_cg_rect(bounds: Any) -> Rect:
    """Parse a Quartz CGRect object or kCGWindowBounds dictionary into a Rect."""
    if isinstance(bounds, dict):
        x_val = bounds.get("X")
        if x_val is None:
            x_val = bounds.get("x", 0)
        y_val = bounds.get("Y")
        if y_val is None:
            y_val = bounds.get("y", 0)
        w_val = bounds.get("Width")
        if w_val is None:
            w_val = bounds.get("width", 0)
        h_val = bounds.get("Height")
        if h_val is None:
            h_val = bounds.get("height", 0)
        x = int(round(float(x_val or 0)))
        y = int(round(float(y_val or 0)))
        w = int(round(float(w_val or 0)))
        h = int(round(float(h_val or 0)))
        return Rect(x=x, y=y, width=max(0, w), height=max(0, h))

    origin = getattr(bounds, "origin", None)
    size = getattr(bounds, "size", None)
    if origin is not None and size is not None:
        return Rect(
            x=int(round(float(getattr(origin, "x", 0)))),
            y=int(round(float(getattr(origin, "y", 0)))),
            width=max(0, int(round(float(getattr(size, "width", 0))))),
            height=max(0, int(round(float(getattr(size, "height", 0))))),
        )

    return Rect(x=0, y=0, width=0, height=0)


def check_screen_recording_permission(log_warning: bool = True) -> bool:
    """Check whether macOS Screen Recording (TCC) permission has been granted.

    Uses ``Quartz.CGPreflightScreenCaptureAccess()`` with a CoreGraphics ``ctypes``
    fallback. Logs actionable System Settings instructions when denied.
    """
    granted: bool | None = None
    quartz = _get_quartz()
    if quartz is not None and hasattr(quartz, "CGPreflightScreenCaptureAccess"):
        try:
            granted = bool(quartz.CGPreflightScreenCaptureAccess())
        except Exception:
            granted = None

    if granted is None and sys.platform == "darwin":
        try:
            cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
            cg.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
            granted = bool(cg.CGPreflightScreenCaptureAccess())
        except Exception:
            granted = None

    if granted is False and log_warning:
        logger.warning(
            "macOS Screen Recording permission is NOT granted. "
            "Open System Settings -> Privacy & Security -> Screen & System Audio Recording, "
            "enable your terminal/host app (e.g. Terminal, iTerm2, VS Code), "
            "then fully quit (Cmd+Q) and relaunch it."
        )

    return bool(granted) if granted is not None else False


def check_accessibility_permission(log_warning: bool = True) -> bool:
    """Check whether macOS Accessibility (TCC) permission has been granted.

    Uses ``ApplicationServices.AXIsProcessTrusted()`` with a ``ctypes`` fallback.
    Logs actionable System Settings instructions when denied.
    """
    trusted: bool | None = None
    app_services = _get_application_services()
    if app_services is not None and hasattr(app_services, "AXIsProcessTrusted"):
        try:
            trusted = bool(app_services.AXIsProcessTrusted())
        except Exception:
            trusted = None

    if trusted is None:
        quartz = _get_quartz()
        if quartz is not None and hasattr(quartz, "AXIsProcessTrusted"):
            try:
                trusted = bool(quartz.AXIsProcessTrusted())
            except Exception:
                trusted = None

    if trusted is None and sys.platform == "darwin":
        try:
            as_lib = ctypes.CDLL(
                "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices"
            )
            as_lib.AXIsProcessTrusted.restype = ctypes.c_bool
            trusted = bool(as_lib.AXIsProcessTrusted())
        except Exception:
            trusted = None

    if trusted is False and log_warning:
        logger.warning(
            "macOS Accessibility permission is NOT granted; synthetic mouse/keyboard "
            "events and AXUIElement window positioning may be silently dropped. "
            "Open System Settings -> Privacy & Security -> Accessibility, enable your "
            "terminal/host app, then fully quit (Cmd+Q) and relaunch it."
        )

    return bool(trusted) if trusted is not None else False


class MacCapturer(BaseCapturer):
    """Native macOS screen, window, and region capturer using Quartz and AppKit."""

    def __init__(self) -> None:
        self._warned_screen_recording = False

    def _preflight_screen_recording_once(self) -> None:
        if not self._warned_screen_recording and sys.platform == "darwin":
            self._warned_screen_recording = True
            check_screen_recording_permission(log_warning=True)

    def list_targets(
        self, include_displays: bool = True, include_windows: bool = True
    ) -> list[TargetInfo]:
        """Enumerate connected macOS displays and visible on-screen windows."""
        targets: list[TargetInfo] = []
        if include_displays:
            targets.extend(self._list_displays())
        if include_windows:
            targets.extend(self._list_windows())
        return targets

    def _list_displays(self) -> list[TargetInfo]:
        """Enumerate logical display rectangles via Quartz with mss fallback."""
        displays: list[TargetInfo] = []
        quartz = _get_quartz()
        if (
            quartz is not None
            and hasattr(quartz, "CGGetActiveDisplayList")
            and hasattr(quartz, "CGDisplayBounds")
        ):
            try:
                result = quartz.CGGetActiveDisplayList(32, None, None)
                # PyObjC returns (err, display_ids, count)
                if isinstance(result, tuple) and len(result) >= 2:
                    err = result[0]
                    display_ids = result[1] or []
                    count = int(result[2]) if len(result) >= 3 else len(display_ids)
                else:
                    err = 0
                    display_ids = list(result) if result else []
                    count = len(display_ids)

                if err == 0 and display_ids:
                    for idx, did in enumerate(list(display_ids)[:count], start=1):
                        rect = _parse_cg_rect(quartz.CGDisplayBounds(did))
                        if rect.width > 0 and rect.height > 0:
                            displays.append(
                                TargetInfo(
                                    target_id=f"display:{idx}",
                                    target_type=TargetType.DISPLAY,
                                    title=f"Display {idx} ({rect.width}x{rect.height})",
                                    rect=rect,
                                    native_handle=int(did),
                                )
                            )
                    if displays:
                        return displays
            except Exception as exc:
                logger.debug("Quartz display enumeration failed, falling back to mss: %s", exc)

        # Fallback to mss monitor enumeration
        try:
            with mss.mss() as sct:
                for idx, mon in enumerate(sct.monitors[1:], start=1):
                    rect = Rect(
                        x=int(mon["left"]),
                        y=int(mon["top"]),
                        width=int(mon["width"]),
                        height=int(mon["height"]),
                    )
                    displays.append(
                        TargetInfo(
                            target_id=f"display:{idx}",
                            target_type=TargetType.DISPLAY,
                            title=f"Display {idx} ({rect.width}x{rect.height})",
                            rect=rect,
                            native_handle=idx,
                        )
                    )
        except Exception as exc:
            logger.debug("mss display enumeration failed on macOS: %s", exc)

        return displays

    def _list_windows(self) -> list[TargetInfo]:
        """Enumerate visible layer-0 windows in native front-to-back z-order."""
        quartz = _get_quartz()
        if quartz is None:
            return []

        windows: list[TargetInfo] = []
        try:
            options = int(getattr(quartz, "kCGWindowListOptionOnScreenOnly", 1)) | int(
                getattr(quartz, "kCGWindowListExcludeDesktopElements", 16)
            )
            null_id = int(getattr(quartz, "kCGNullWindowID", 0))
            raw_windows = quartz.CGWindowListCopyWindowInfo(options, null_id) or []

            for info in raw_windows:
                try:
                    layer = int(info.get("kCGWindowLayer", 0))
                    if layer != 0:
                        continue

                    alpha = float(info.get("kCGWindowAlpha", 1.0))
                    if alpha <= 0.0:
                        continue

                    if info.get("kCGWindowIsOnscreen") is False:
                        continue

                    wid = int(info.get("kCGWindowNumber", 0) or 0)
                    if wid <= 0:
                        continue

                    rect = _parse_cg_rect(info.get("kCGWindowBounds") or {})
                    if rect.width <= 30 or rect.height <= 30:
                        continue

                    owner = str(info.get("kCGWindowOwnerName", "") or "").strip()
                    name = str(info.get("kCGWindowName", "") or "").strip()
                    pid = int(info.get("kCGWindowOwnerPID", 0) or 0)

                    if owner in _SYSTEM_OVERLAY_OWNERS and not name:
                        continue
                    if owner == "Window Server":
                        continue

                    title = name or owner
                    if not title:
                        continue

                    windows.append(
                        TargetInfo(
                            target_id=f"macwin:{wid}",
                            target_type=TargetType.WINDOW,
                            title=title,
                            process_name=owner,
                            pid=pid,
                            rect=rect,
                            is_minimized=False,
                            native_handle=wid,
                        )
                    )
                except Exception:
                    continue
        except Exception as exc:
            logger.debug("Quartz window enumeration failed: %s", exc)

        return windows

    def _refresh_window_bounds(self, target: TargetInfo, quartz: Any) -> None:
        """Refresh a window target's live bounding rect and PID from Quartz."""
        if target.native_handle <= 0:
            return
        try:
            opt = int(getattr(quartz, "kCGWindowListOptionIncludingWindow", 8))
            raw_list = quartz.CGWindowListCopyWindowInfo(opt, target.native_handle) or []
            for info in raw_list:
                rect = _parse_cg_rect(info.get("kCGWindowBounds") or {})
                if rect.width > 0 and rect.height > 0:
                    target.rect = rect
                if not target.pid:
                    pid = int(info.get("kCGWindowOwnerPID", 0) or 0)
                    if pid > 0:
                        target.pid = pid
                break
        except Exception:
            pass

    @staticmethod
    def _cgimage_to_pil(cg_image: Any, quartz: Any) -> Image.Image | None:
        """Convert a Quartz CGImageRef into an RGB PIL Image."""
        if cg_image is None:
            return None
        try:
            width = int(quartz.CGImageGetWidth(cg_image))
            height = int(quartz.CGImageGetHeight(cg_image))
            if width <= 0 or height <= 0:
                return None

            bytes_per_row = int(quartz.CGImageGetBytesPerRow(cg_image))
            provider = quartz.CGImageGetDataProvider(cg_image)
            raw_data = quartz.CGDataProviderCopyData(provider)
            raw_bytes = bytes(raw_data)
            if not raw_bytes:
                return None

            stride = bytes_per_row if bytes_per_row >= width * 4 else width * 4
            rgba_img = Image.frombuffer(
                "RGBA",
                (width, height),
                raw_bytes,
                "raw",
                "BGRA",
                stride,
                1,
            )
            return rgba_img.convert("RGB")
        except Exception as exc:
            logger.debug("CGImage to PIL conversion failed: %s", exc)
            return None

    def _capture_rect_quartz(self, rect: Rect) -> Image.Image | None:
        """Capture an arbitrary screen rectangle via Quartz CGWindowListCreateImage."""
        quartz = _get_quartz()
        if quartz is None or not hasattr(quartz, "CGWindowListCreateImage"):
            return None
        try:
            cg_rect = quartz.CGRectMake(
                float(rect.x),
                float(rect.y),
                float(max(1, rect.width)),
                float(max(1, rect.height)),
            )
            opt = int(getattr(quartz, "kCGWindowListOptionOnScreenOnly", 1))
            null_id = int(getattr(quartz, "kCGNullWindowID", 0))
            img_opt = int(getattr(quartz, "kCGWindowImageNominalResolution", 16))
            cg_img = quartz.CGWindowListCreateImage(cg_rect, opt, null_id, img_opt)
            return self._cgimage_to_pil(cg_img, quartz)
        except Exception as exc:
            logger.debug("Quartz rect capture failed: %s", exc)
            return None

    def _capture_rect(self, rect: Rect) -> Image.Image:
        """Capture a screen rectangle using mss with Quartz and placeholder fallbacks."""
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
        except Exception as exc:
            logger.debug("mss capture failed on macOS, trying Quartz fallback: %s", exc)

        quartz_img = self._capture_rect_quartz(rect)
        if quartz_img is not None:
            return quartz_img

        return Image.new("RGB", (max(1, rect.width), max(1, rect.height)), color=(30, 30, 30))

    def capture(self, target: TargetInfo) -> Image.Image:
        """Capture a macOS region, display, or isolated window."""
        self._preflight_screen_recording_once()

        # 1. Region capture — preserve exact user-selected crop bounds
        if target.target_type == TargetType.REGION:
            img = self._capture_rect(target.rect)
            target.last_capture_size = (img.width, img.height)
            return img

        # 2. Display capture
        if target.target_type == TargetType.DISPLAY:
            img = self._capture_rect(target.rect)
            target.last_capture_size = (img.width, img.height)
            return img

        # 3. Isolated window capture via Quartz CGWindowListCreateImage
        quartz = _get_quartz()
        if quartz is not None and target.native_handle > 0:
            self._refresh_window_bounds(target, quartz)
            if hasattr(quartz, "CGWindowListCreateImage"):
                try:
                    cg_rect_null = getattr(quartz, "CGRectNull", None)
                    list_opt = int(getattr(quartz, "kCGWindowListOptionIncludingWindow", 8))
                    img_opt = int(getattr(quartz, "kCGWindowImageBoundsIgnoreFraming", 1)) | int(
                        getattr(quartz, "kCGWindowImageNominalResolution", 16)
                    )
                    cg_img = quartz.CGWindowListCreateImage(
                        cg_rect_null,
                        list_opt,
                        target.native_handle,
                        img_opt,
                    )
                    pil_img = self._cgimage_to_pil(cg_img, quartz)
                    if pil_img is not None:
                        target.last_capture_size = (pil_img.width, pil_img.height)
                        return pil_img
                except Exception as exc:
                    logger.debug(
                        "Quartz window capture failed for %s, falling back to rect grab: %s",
                        target.target_id,
                        exc,
                    )

        img = self._capture_rect(target.rect)
        target.last_capture_size = (img.width, img.height)
        return img

    def focus(self, target: TargetInfo) -> bool:
        """Bring the owning macOS application to the foreground."""
        if target.target_type in (TargetType.DISPLAY, TargetType.REGION):
            return True

        pid = target.pid
        if pid <= 0 and target.native_handle > 0:
            quartz = _get_quartz()
            if quartz is not None:
                self._refresh_window_bounds(target, quartz)
                pid = target.pid

        appkit = _get_appkit()
        if appkit is not None and pid > 0:
            try:
                ns_app_cls = appkit.NSRunningApplication
                running_app = ns_app_cls.runningApplicationWithProcessIdentifier_(pid)
                if running_app is not None:
                    activate_opts = int(
                        getattr(appkit, "NSApplicationActivateIgnoringOtherApps", 1 << 1)
                    )
                    if running_app.activateWithOptions_(activate_opts):
                        return True
            except Exception as exc:
                logger.debug("AppKit activation failed for pid %s: %s", pid, exc)

        # Fallback: osascript via System Events
        if shutil.which("osascript") and pid > 0:
            try:
                script = (
                    'tell application "System Events" to set frontmost of '
                    f"(first process whose unix id is {int(pid)}) to true"
                )
                res = subprocess.run(
                    ["osascript", "-e", script],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=5,
                )
                return res.returncode == 0
            except Exception:
                return False

        return False

    def move_window_to_display(self, target: TargetInfo, display: TargetInfo | int) -> bool:
        """Reposition a macOS window onto the specified display (TargetInfo or 1-based index)."""
        if target.target_type != TargetType.WINDOW:
            return False

        display_target: TargetInfo | None = None
        if isinstance(display, int):
            displays = self._list_displays()
            if 1 <= display <= len(displays):
                display_target = displays[display - 1]
        elif display.target_type == TargetType.DISPLAY:
            display_target = display

        if display_target is None:
            return False

        pid = target.pid
        if pid <= 0 and target.native_handle > 0:
            quartz = _get_quartz()
            if quartz is not None:
                self._refresh_window_bounds(target, quartz)
                pid = target.pid

        if pid <= 0:
            return False

        dest_x = display_target.rect.x + max(
            0, (display_target.rect.width - target.rect.width) // 2
        )
        dest_y = display_target.rect.y + max(
            0, (display_target.rect.height - target.rect.height) // 2
        )

        # 1. Try Accessibility API (AXUIElement)
        app_services = _get_application_services()
        quartz = _get_quartz()
        if (
            app_services is not None
            and hasattr(app_services, "AXUIElementCreateApplication")
            and hasattr(app_services, "AXUIElementCopyAttributeValue")
            and hasattr(app_services, "AXUIElementSetAttributeValue")
        ):
            try:
                app_el = app_services.AXUIElementCreateApplication(pid)
                err, ax_windows = app_services.AXUIElementCopyAttributeValue(
                    app_el, "AXWindows", None
                )
                if err == 0 and ax_windows:
                    ax_win = ax_windows[0]
                    if hasattr(app_services, "AXValueCreate") and quartz is not None:
                        cg_point = quartz.CGPointMake(float(dest_x), float(dest_y))
                        k_cg_point_type = int(getattr(app_services, "kAXValueCGPointType", 1))
                        ax_pos = app_services.AXValueCreate(k_cg_point_type, cg_point)
                        set_err = app_services.AXUIElementSetAttributeValue(
                            ax_win, "AXPosition", ax_pos
                        )
                        if set_err == 0:
                            target.rect = Rect(
                                x=dest_x,
                                y=dest_y,
                                width=target.rect.width,
                                height=target.rect.height,
                            )
                            return True
            except Exception as exc:
                logger.debug("AXUIElement window move failed, falling back to AppleScript: %s", exc)

        # 2. Fallback: AppleScript via System Events
        if shutil.which("osascript"):
            try:
                script = (
                    'tell application "System Events" to set position of window 1 of '
                    f"(first process whose unix id is {int(pid)}) to {{{int(dest_x)}, {int(dest_y)}}}"
                )
                res = subprocess.run(
                    ["osascript", "-e", script],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=5,
                )
                if res.returncode == 0:
                    target.rect = Rect(
                        x=dest_x,
                        y=dest_y,
                        width=target.rect.width,
                        height=target.rect.height,
                    )
                    return True
            except Exception:
                return False

        return False

    def target_at_point(self, x: int, y: int, exclude_pid: int | None = None) -> TargetInfo | None:
        """Return the topmost visible window or display at screen point ``(x, y)`` using Quartz z-order.

        ``CGWindowListCopyWindowInfo`` already returns windows in front-to-back order.
        Excludes ``exclude_pid`` and AEFlow Desktop overlay windows (defense-in-depth on macOS
        where WKWebView helper PIDs can differ from the parent app PID), falling back to the
        first display containing ``(x, y)`` when no application window matches.
        """
        for win in self._list_windows():
            if exclude_pid is not None and win.pid and win.pid == exclude_pid:
                continue
            if win.process_name in _AEFLOW_OVERLAY_OWNERS or win.title in _AEFLOW_OVERLAY_TITLES:
                continue
            r = win.rect
            if r.x <= x < r.x + r.width and r.y <= y < r.y + r.height:
                return win

        for disp in self._list_displays():
            r = disp.rect
            if r.x <= x < r.x + r.width and r.y <= y < r.y + r.height:
                return disp
        return None
