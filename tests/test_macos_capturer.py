"""Unit tests for native macOS capturer, TCC preflight helpers, and thumbnails."""

from __future__ import annotations

import io
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from agenteverywhereflow.capturer import MacCapturer, get_capturer
from agenteverywhereflow.capturer.base import Rect, TargetInfo, TargetType
from agenteverywhereflow.capturer.macos import (
    check_accessibility_permission,
    check_screen_recording_permission,
)


def _make_fake_quartz() -> SimpleNamespace:
    """Create a realistic mock Quartz module for unit testing on any OS."""

    def cg_display_bounds(did: int) -> SimpleNamespace:
        if did == 101:
            return SimpleNamespace(
                origin=SimpleNamespace(x=0.0, y=0.0),
                size=SimpleNamespace(width=1728.0, height=1117.0),
            )
        return SimpleNamespace(
            origin=SimpleNamespace(x=1728.0, y=0.0),
            size=SimpleNamespace(width=2560.0, height=1440.0),
        )

    # 2x2 BGRA image (blue pixel: B=255, G=0, R=0, A=255)
    raw_bgra = bytes([255, 0, 0, 255] * 4)

    return SimpleNamespace(
        kCGWindowListOptionOnScreenOnly=1,
        kCGWindowListOptionIncludingWindow=8,
        kCGWindowListExcludeDesktopElements=16,
        kCGNullWindowID=0,
        kCGWindowImageBoundsIgnoreFraming=1,
        kCGWindowImageNominalResolution=16,
        CGRectNull="CGRectNull",
        CGRectMake=lambda x, y, w, h: ("CGRect", x, y, w, h),
        CGPointMake=lambda x, y: ("CGPoint", x, y),
        CGGetActiveDisplayList=MagicMock(return_value=(0, [101, 202], 2)),
        CGDisplayBounds=MagicMock(side_effect=cg_display_bounds),
        CGWindowListCopyWindowInfo=MagicMock(
            return_value=[
                # Frontmost overlay owned by picker (excluded by exclude_pid)
                {
                    "kCGWindowLayer": 0,
                    "kCGWindowAlpha": 1.0,
                    "kCGWindowIsOnscreen": True,
                    "kCGWindowNumber": 900,
                    "kCGWindowOwnerName": "AEFlowDesktop",
                    "kCGWindowName": "Picker Overlay",
                    "kCGWindowOwnerPID": 9999,
                    "kCGWindowBounds": {"X": 0, "Y": 0, "Width": 1728, "Height": 1117},
                },
                # Front application window (Chrome)
                {
                    "kCGWindowLayer": 0,
                    "kCGWindowAlpha": 1.0,
                    "kCGWindowIsOnscreen": True,
                    "kCGWindowNumber": 1001,
                    "kCGWindowOwnerName": "Google Chrome",
                    "kCGWindowName": "Docs - Chrome",
                    "kCGWindowOwnerPID": 4201,
                    "kCGWindowBounds": {"X": 100, "Y": 80, "Width": 1200, "Height": 800},
                },
                # Back application window overlapping Chrome
                {
                    "kCGWindowLayer": 0,
                    "kCGWindowAlpha": 1.0,
                    "kCGWindowIsOnscreen": True,
                    "kCGWindowNumber": 1002,
                    "kCGWindowOwnerName": "Terminal",
                    "kCGWindowName": "zsh",
                    "kCGWindowOwnerPID": 4202,
                    "kCGWindowBounds": {"X": 50, "Y": 50, "Width": 1400, "Height": 900},
                },
                # Should be filtered: non-zero layer (status bar / menu)
                {
                    "kCGWindowLayer": 25,
                    "kCGWindowAlpha": 1.0,
                    "kCGWindowIsOnscreen": True,
                    "kCGWindowNumber": 1003,
                    "kCGWindowOwnerName": "Control Center",
                    "kCGWindowName": "MenuBar",
                    "kCGWindowOwnerPID": 100,
                    "kCGWindowBounds": {"X": 0, "Y": 0, "Width": 1728, "Height": 32},
                },
                # Should be filtered: tiny window
                {
                    "kCGWindowLayer": 0,
                    "kCGWindowAlpha": 1.0,
                    "kCGWindowIsOnscreen": True,
                    "kCGWindowNumber": 1004,
                    "kCGWindowOwnerName": "Helper",
                    "kCGWindowName": "Tiny",
                    "kCGWindowOwnerPID": 101,
                    "kCGWindowBounds": {"X": 0, "Y": 0, "Width": 10, "Height": 10},
                },
                # Should be filtered: transparent window
                {
                    "kCGWindowLayer": 0,
                    "kCGWindowAlpha": 0.0,
                    "kCGWindowIsOnscreen": True,
                    "kCGWindowNumber": 1005,
                    "kCGWindowOwnerName": "Ghost",
                    "kCGWindowName": "Hidden",
                    "kCGWindowOwnerPID": 102,
                    "kCGWindowBounds": {"X": 0, "Y": 0, "Width": 500, "Height": 500},
                },
            ]
        ),
        CGWindowListCreateImage=MagicMock(return_value="FAKE_CGIMAGE"),
        CGImageGetWidth=MagicMock(return_value=2),
        CGImageGetHeight=MagicMock(return_value=2),
        CGImageGetBytesPerRow=MagicMock(return_value=8),
        CGImageGetDataProvider=MagicMock(return_value="FAKE_PROVIDER"),
        CGDataProviderCopyData=MagicMock(return_value=raw_bgra),
        CGPreflightScreenCaptureAccess=MagicMock(return_value=True),
    )


def test_get_capturer_dispatches_darwin_to_mac_capturer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agenteverywhereflow.capturer.sys.platform", "darwin")
    capturer = get_capturer()
    assert isinstance(capturer, MacCapturer)


def test_mac_capturer_list_displays_and_windows() -> None:
    fake_quartz = _make_fake_quartz()
    capturer = MacCapturer()

    with patch("agenteverywhereflow.capturer.macos._get_quartz", return_value=fake_quartz):
        targets = capturer.list_targets(include_displays=True, include_windows=True)

    displays = [t for t in targets if t.target_type == TargetType.DISPLAY]
    windows = [t for t in targets if t.target_type == TargetType.WINDOW]

    assert len(displays) == 2
    assert displays[0].target_id == "display:1"
    assert displays[0].rect == Rect(0, 0, 1728, 1117)
    assert displays[1].target_id == "display:2"
    assert displays[1].rect == Rect(1728, 0, 2560, 1440)

    # Layer 25, tiny, and alpha==0 windows must be filtered out
    assert len(windows) == 3
    chrome = windows[1]
    assert chrome.target_id == "macwin:1001"
    assert chrome.native_handle == 1001
    assert chrome.title == "Docs - Chrome"
    assert chrome.process_name == "Google Chrome"
    assert chrome.pid == 4201
    assert chrome.rect == Rect(100, 80, 1200, 800)


def test_mac_capturer_z_order_target_at_point_excludes_pid() -> None:
    fake_quartz = _make_fake_quartz()
    capturer = MacCapturer()

    with patch("agenteverywhereflow.capturer.macos._get_quartz", return_value=fake_quartz):
        # Without exclusion, frontmost overlay (pid=9999) wins
        hit_overlay = capturer.target_at_point(200, 200)
        assert hit_overlay is not None
        assert hit_overlay.pid == 9999

        # Excluding picker overlay PID hits Chrome (topmost beneath overlay)
        hit_chrome = capturer.target_at_point(200, 200, exclude_pid=9999)
        assert hit_chrome is not None
        assert hit_chrome.target_id == "macwin:1001"

        # Point (60, 60) is outside Chrome (100, 80) but inside Terminal (50, 50)
        hit_term = capturer.target_at_point(60, 60, exclude_pid=9999)
        assert hit_term is not None
        assert hit_term.target_id == "macwin:1002"


def test_mac_capturer_capture_window_refreshes_bounds_and_converts_cgimage() -> None:
    fake_quartz = _make_fake_quartz()
    # Live bounds lookup returns updated moved window position
    fake_quartz.CGWindowListCopyWindowInfo = MagicMock(
        return_value=[
            {
                "kCGWindowNumber": 1001,
                "kCGWindowOwnerPID": 4201,
                "kCGWindowBounds": {"X": 300, "Y": 200, "Width": 900, "Height": 600},
            }
        ]
    )
    capturer = MacCapturer()
    target = TargetInfo(
        target_id="macwin:1001",
        target_type=TargetType.WINDOW,
        title="Docs - Chrome",
        rect=Rect(100, 80, 1200, 800),
        native_handle=1001,
    )

    with patch("agenteverywhereflow.capturer.macos._get_quartz", return_value=fake_quartz):
        img = capturer.capture(target)

    # Bounds and pid refreshed dynamically
    assert target.rect == Rect(300, 200, 900, 600)
    assert target.pid == 4201
    # CGImage BGRA (255, 0, 0, 255) converted to RGB (0, 0, 255)
    assert img.size == (2, 2)
    assert img.getpixel((0, 0)) == (0, 0, 255)
    assert target.last_capture_size == (2, 2)


def test_mac_capturer_capture_region_preserves_exact_rect() -> None:
    fake_quartz = _make_fake_quartz()
    capturer = MacCapturer()
    region = TargetInfo(
        target_id="region:120,140,400,300",
        target_type=TargetType.REGION,
        title="Custom Region",
        rect=Rect(120, 140, 400, 300),
        native_handle=1001,  # optional parent handle must NOT hijack region bounds
    )

    fake_sct_img = SimpleNamespace(
        size=(400, 300),
        bgra=bytes([0, 255, 0, 255] * (400 * 300)),
    )
    fake_sct = MagicMock()
    fake_sct.__enter__.return_value = fake_sct
    fake_sct.grab.return_value = fake_sct_img

    with (
        patch("agenteverywhereflow.capturer.macos._get_quartz", return_value=fake_quartz),
        patch("agenteverywhereflow.capturer.macos.mss.mss", return_value=fake_sct),
    ):
        img = capturer.capture(region)

    assert img.size == (400, 300)
    assert region.rect == Rect(120, 140, 400, 300)
    fake_sct.grab.assert_called_once_with({"top": 140, "left": 120, "width": 400, "height": 300})
    fake_quartz.CGWindowListCreateImage.assert_not_called()


def test_mac_capturer_focus_and_move_window() -> None:
    fake_quartz = _make_fake_quartz()
    fake_running_app = MagicMock()
    fake_running_app.activateWithOptions_.return_value = True
    fake_appkit = SimpleNamespace(
        NSApplicationActivateIgnoringOtherApps=2,
        NSRunningApplication=SimpleNamespace(
            runningApplicationWithProcessIdentifier_=MagicMock(return_value=fake_running_app)
        ),
    )
    fake_app_services = SimpleNamespace(
        kAXValueCGPointType=1,
        AXUIElementCreateApplication=MagicMock(return_value="APP_EL"),
        AXUIElementCopyAttributeValue=MagicMock(return_value=(0, ["WIN_EL"])),
        AXValueCreate=MagicMock(return_value="AX_POS"),
        AXUIElementSetAttributeValue=MagicMock(return_value=0),
    )

    capturer = MacCapturer()
    win = TargetInfo(
        target_id="macwin:1001",
        target_type=TargetType.WINDOW,
        title="Chrome",
        pid=4201,
        rect=Rect(100, 80, 1000, 700),
        native_handle=1001,
    )

    with (
        patch("agenteverywhereflow.capturer.macos._get_quartz", return_value=fake_quartz),
        patch("agenteverywhereflow.capturer.macos._get_appkit", return_value=fake_appkit),
        patch(
            "agenteverywhereflow.capturer.macos._get_application_services",
            return_value=fake_app_services,
        ),
    ):
        assert capturer.focus(win) is True
        fake_running_app.activateWithOptions_.assert_called_once_with(2)

        # Move to display index 2 (rect x=1728, width=2560, height=1440)
        moved = capturer.move_window_to_display(win, 2)
        assert moved is True
        assert win.rect.x >= 1728
        assert win.rect.width == 1000
        assert win.rect.height == 700


def test_tcc_preflight_helpers_log_actionable_warnings(caplog: pytest.LogCaptureFixture) -> None:
    denied_quartz = SimpleNamespace(CGPreflightScreenCaptureAccess=MagicMock(return_value=False))
    denied_as = SimpleNamespace(AXIsProcessTrusted=MagicMock(return_value=False))

    with (
        caplog.at_level(logging.WARNING),
        patch("agenteverywhereflow.capturer.macos._get_quartz", return_value=denied_quartz),
        patch(
            "agenteverywhereflow.capturer.macos._get_application_services", return_value=denied_as
        ),
    ):
        assert check_screen_recording_permission(log_warning=True) is False
        assert check_accessibility_permission(log_warning=True) is False

    assert "Screen & System Audio Recording" in caplog.text
    assert "Accessibility" in caplog.text

    granted_quartz = SimpleNamespace(CGPreflightScreenCaptureAccess=MagicMock(return_value=True))
    granted_as = SimpleNamespace(AXIsProcessTrusted=MagicMock(return_value=True))
    with (
        patch("agenteverywhereflow.capturer.macos._get_quartz", return_value=granted_quartz),
        patch(
            "agenteverywhereflow.capturer.macos._get_application_services", return_value=granted_as
        ),
    ):
        assert check_screen_recording_permission(log_warning=True) is True
        assert check_accessibility_permission(log_warning=True) is True


def test_base_capturer_capture_thumbnail_preserves_aspect_ratio() -> None:
    capturer = MacCapturer()
    target = TargetInfo(
        target_id="display:1",
        target_type=TargetType.DISPLAY,
        title="Display 1",
        rect=Rect(0, 0, 1440, 900),
    )

    with patch.object(capturer, "capture", return_value=Image.new("RGB", (1440, 900), "navy")):
        jpeg_bytes = capturer.capture_thumbnail(target, max_width=360, quality=75)

    assert jpeg_bytes[:2] == b"\xff\xd8"  # JPEG magic header
    thumb = Image.open(io.BytesIO(jpeg_bytes))
    assert thumb.size == (360, 225)


def test_mac_capturer_target_at_point_excludes_aeflow_overlays_and_falls_back_to_display() -> None:
    """Verify MacCapturer.target_at_point skips AEFlow Desktop overlays and falls back to Display."""
    fake_quartz = _make_fake_quartz()
    fake_quartz.CGWindowListCopyWindowInfo = MagicMock(
        return_value=[
            # Owned by AEFlow Desktop (e.g. different helper PID not matching exclude_pid)
            {
                "kCGWindowLayer": 0,
                "kCGWindowAlpha": 1.0,
                "kCGWindowIsOnscreen": True,
                "kCGWindowNumber": 801,
                "kCGWindowOwnerName": "AEFlow Desktop",
                "kCGWindowName": "Main Overlay",
                "kCGWindowOwnerPID": 5001,
                "kCGWindowBounds": {"X": 0, "Y": 0, "Width": 1728, "Height": 1117},
            },
            # WKWebView helper window titled AEFlow Target Picker
            {
                "kCGWindowLayer": 0,
                "kCGWindowAlpha": 1.0,
                "kCGWindowIsOnscreen": True,
                "kCGWindowNumber": 802,
                "kCGWindowOwnerName": "WebKit Helper",
                "kCGWindowName": "AEFlow Target Picker",
                "kCGWindowOwnerPID": 5002,
                "kCGWindowBounds": {"X": 0, "Y": 0, "Width": 1728, "Height": 1117},
            },
            # Target border & status pill overlays
            {
                "kCGWindowLayer": 0,
                "kCGWindowAlpha": 1.0,
                "kCGWindowIsOnscreen": True,
                "kCGWindowNumber": 803,
                "kCGWindowOwnerName": "WebKit Helper",
                "kCGWindowName": "AEFlow Target Border",
                "kCGWindowOwnerPID": 5003,
                "kCGWindowBounds": {"X": 100, "Y": 80, "Width": 1200, "Height": 800},
            },
            {
                "kCGWindowLayer": 0,
                "kCGWindowAlpha": 1.0,
                "kCGWindowIsOnscreen": True,
                "kCGWindowNumber": 804,
                "kCGWindowOwnerName": "WebKit Helper",
                "kCGWindowName": "AEFlow Status Pill",
                "kCGWindowOwnerPID": 5004,
                "kCGWindowBounds": {"X": 600, "Y": 10, "Width": 400, "Height": 80},
            },
            # Real underlying application window (Chrome)
            {
                "kCGWindowLayer": 0,
                "kCGWindowAlpha": 1.0,
                "kCGWindowIsOnscreen": True,
                "kCGWindowNumber": 1001,
                "kCGWindowOwnerName": "Google Chrome",
                "kCGWindowName": "Docs - Chrome",
                "kCGWindowOwnerPID": 4201,
                "kCGWindowBounds": {"X": 100, "Y": 80, "Width": 1200, "Height": 800},
            },
        ]
    )

    capturer = MacCapturer()
    with patch("agenteverywhereflow.capturer.macos._get_quartz", return_value=fake_quartz):
        # Inside Chrome bounds: skips all AEFlow overlays even without exclude_pid
        hit_win = capturer.target_at_point(250, 250)
        assert hit_win is not None
        assert hit_win.target_id == "macwin:1001"
        assert hit_win.target_type == TargetType.WINDOW

        # Empty desktop area on primary display (0..1728, 0..1117): falls back to display:1
        hit_disp1 = capturer.target_at_point(10, 10)
        assert hit_disp1 is not None
        assert hit_disp1.target_id == "display:1"
        assert hit_disp1.target_type == TargetType.DISPLAY

        # Empty desktop area on secondary display (1728..4288, 0..1440): falls back to display:2
        hit_disp2 = capturer.target_at_point(2000, 300)
        assert hit_disp2 is not None
        assert hit_disp2.target_id == "display:2"
        assert hit_disp2.target_type == TargetType.DISPLAY

        # Outside all windows and displays -> None
        assert capturer.target_at_point(9999, 9999) is None
