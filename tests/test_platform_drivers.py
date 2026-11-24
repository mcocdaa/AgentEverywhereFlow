"""Cross-platform unit tests for Linux/Windows capturers, InputDriver, engines, and DPI scaling."""

from __future__ import annotations

import ctypes
import importlib
import logging
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from agenteverywhereflow.actions.driver import (
    _INPUT,
    INPUT_KEYBOARD,
    INPUT_MOUSE,
    KEYEVENTF_KEYUP,
    MOUSEEVENTF_LEFTDOWN,
    MOUSEEVENTF_LEFTUP,
    MOUSEEVENTF_WHEEL,
    InputDriver,
    build_win32_key_input,
    build_win32_mouse_input,
)
from agenteverywhereflow.agent.prompts import get_prompt_for_target
from agenteverywhereflow.capturer.base import (
    Rect,
    TargetInfo,
    TargetType,
    detect_linux_session_type,
)
from agenteverywhereflow.capturer.linux import LinuxCapturer
from agenteverywhereflow.capturer.windows import WindowsCapturer
from agenteverywhereflow.engine.guarded import GuardedActionEngine
from agenteverywhereflow.engine.python_repl import PythonReplEngine
from agenteverywhereflow.workflow.models import WorkflowDefinition, WorkflowStep, WorkflowTarget
from agenteverywhereflow.workflow.runner import WorkflowRunner


# =============================================================================
# 1. Linux Session Detection & LinuxCapturer Tests
# =============================================================================
def test_detect_linux_session_type(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.delenv("DISPLAY", raising=False)
    assert detect_linux_session_type() == "unknown"

    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    assert detect_linux_session_type() == "wayland"

    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    assert detect_linux_session_type() == "x11"

    monkeypatch.delenv("XDG_SESSION_TYPE", raising=False)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert detect_linux_session_type() == "wayland"

    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
    assert detect_linux_session_type() == "x11"


def test_linux_capturer_region_skips_xlib_and_wmctrl(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    capturer = LinuxCapturer()
    region = TargetInfo(
        target_id="region:50,60,300,200",
        target_type=TargetType.REGION,
        title="Crop Region",
        rect=Rect(50, 60, 300, 200),
        native_handle=0x123456,  # parent handle must NOT trigger Xlib window capture
    )

    fake_sct_img = SimpleNamespace(size=(300, 200), bgra=bytes([10, 20, 30, 255] * (300 * 200)))
    fake_sct = MagicMock()
    fake_sct.__enter__.return_value = fake_sct
    fake_sct.grab.return_value = fake_sct_img

    with (
        patch.object(capturer, "_get_display") as mock_xdisp,
        patch("agenteverywhereflow.capturer.linux.mss.mss", return_value=fake_sct),
        patch("agenteverywhereflow.capturer.linux.subprocess.run") as mock_subproc,
    ):
        img = capturer.capture(region)
        assert capturer.focus(region) is True

    assert img.size == (300, 200)
    assert region.rect == Rect(50, 60, 300, 200)
    assert region.last_capture_size == (300, 200)
    mock_xdisp.assert_not_called()
    mock_subproc.assert_not_called()


def test_linux_capturer_populates_process_name_and_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    capturer = LinuxCapturer()
    wmctrl_out = (
        "0x02400007  0 4321  100 120 1024 768  host Firefox Browser\n"
        "0x02400009  0 9999  0   0   10   10   host TinyPanel\n"
    )
    fake_proc = MagicMock()
    fake_proc.name.return_value = "firefox-bin"

    with (
        patch("agenteverywhereflow.capturer.linux.shutil.which", return_value="/usr/bin/wmctrl"),
        patch(
            "agenteverywhereflow.capturer.linux.subprocess.run",
            return_value=SimpleNamespace(stdout=wmctrl_out),
        ),
        patch("agenteverywhereflow.capturer.linux.psutil.Process", return_value=fake_proc),
    ):
        wins = capturer._list_windows()

    assert len(wins) == 1
    assert wins[0].target_id == "xwin:0x02400007"
    assert wins[0].pid == 4321
    assert wins[0].process_name == "firefox-bin"
    assert wins[0].rect == Rect(100, 120, 1024, 768)


def test_linux_capturer_refreshes_window_rect_on_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    capturer = LinuxCapturer()
    win_target = TargetInfo(
        target_id="xwin:0x200001",
        target_type=TargetType.WINDOW,
        title="Moved Window",
        rect=Rect(10, 10, 400, 300),
        native_handle=0x200001,
    )

    fake_win = MagicMock()
    fake_win.get_geometry.return_value = SimpleNamespace(width=640, height=480)
    fake_win.get_image.return_value = SimpleNamespace(data=bytes([0, 0, 0, 255] * (640 * 480)))

    fake_root = MagicMock()
    fake_root.translate_coords.return_value = SimpleNamespace(x=250, y=180)

    fake_disp = MagicMock()
    fake_disp.create_resource_object.return_value = fake_win
    fake_disp.screen.return_value = SimpleNamespace(root=fake_root)

    with patch.object(capturer, "_get_display", return_value=fake_disp):
        img = capturer.capture(win_target)

    assert img.size == (640, 480)
    assert win_target.rect == Rect(250, 180, 640, 480)
    assert win_target.last_capture_size == (640, 480)


def test_linux_capturer_wayland_warning_emitted_once(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    with caplog.at_level(logging.WARNING):
        capturer = LinuxCapturer()
        capturer._warn_wayland_once()
        capturer._warn_wayland_once()

    wayland_records = [r for r in caplog.records if "Wayland session detected" in r.message]
    assert len(wayland_records) == 1
    assert "Ubuntu on Xorg" in wayland_records[0].message


def test_linux_capturer_z_order_target_at_point(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    capturer = LinuxCapturer()

    win_back = TargetInfo(
        target_id="xwin:0x100",
        target_type=TargetType.WINDOW,
        title="Back",
        pid=100,
        rect=Rect(0, 0, 1000, 800),
        native_handle=0x100,
    )
    win_front = TargetInfo(
        target_id="xwin:0x200",
        target_type=TargetType.WINDOW,
        title="Front",
        pid=200,
        rect=Rect(100, 100, 600, 400),
        native_handle=0x200,
    )

    fake_root = MagicMock()
    # Bottom-to-top stacking order: 0x100 is below 0x200
    fake_root.get_full_property.return_value = SimpleNamespace(value=[0x100, 0x200])
    fake_disp = MagicMock()
    fake_disp.screen.return_value = SimpleNamespace(root=fake_root)
    fake_disp.intern_atom.return_value = 42

    with (
        patch.object(capturer, "_list_windows", return_value=[win_back, win_front]),
        patch.object(capturer, "_get_display", return_value=fake_disp),
    ):
        top = capturer.target_at_point(200, 200)
        assert top is not None
        assert top.target_id == "xwin:0x200"

        under = capturer.target_at_point(200, 200, exclude_pid=200)
        assert under is not None
        assert under.target_id == "xwin:0x100"


# =============================================================================
# 2. WindowsCapturer REGION Preservation & Z-Order Hit-Test
# =============================================================================
def test_windows_capturer_region_preserves_rect_even_with_native_handle() -> None:
    capturer = WindowsCapturer()
    region = TargetInfo(
        target_id="region:100,200,500,400",
        target_type=TargetType.REGION,
        title="Selected Region",
        rect=Rect(100, 200, 500, 400),
        native_handle=0x98765,  # parent window HWND must NOT overwrite region.rect
    )
    dummy_img = Image.new("RGB", (500, 400), "white")

    with (
        patch("agenteverywhereflow.capturer.windows.is_windows", True),
        patch.object(capturer, "get_window_bounds") as mock_bounds,
        patch.object(capturer, "_capture_rect", return_value=dummy_img) as mock_grab,
    ):
        img = capturer.capture(region)
        assert capturer.focus(region) is True

    mock_bounds.assert_not_called()
    mock_grab.assert_called_once_with(Rect(100, 200, 500, 400))
    assert region.rect == Rect(100, 200, 500, 400)
    assert img.size == (500, 400)
    assert region.last_capture_size == (500, 400)


# =============================================================================
# 3. Cross-Platform InputDriver Tests (Linux XTest, macOS CGEvent, Win SendInput)
# =============================================================================
def test_linux_driver_uses_xtest_by_default_and_handles_display1() -> None:
    drv = InputDriver()
    fake_disp = MagicMock()
    fake_disp.screen.return_value = SimpleNamespace(
        root=SimpleNamespace(query_pointer=lambda: SimpleNamespace(root_x=10, root_y=20))
    )

    with (
        patch.object(drv, "_get_x11_display", return_value=fake_disp),
        patch("Xlib.ext.xtest.fake_input") as mock_fake_input,
    ):
        # Click on display:1 (window_handle=0) must use XTest and sync()
        drv.click(x=400, y=300, button="left", clicks=1, window_handle=0, restore_cursor=True)

    assert mock_fake_input.call_count >= 3
    fake_disp.sync.assert_called()
    # Must never attempt XSendEvent when use_xsendevent=False
    fake_disp.create_resource_object.assert_not_called()


def test_linux_driver_rejects_invalid_xid_1_even_with_xsendevent() -> None:
    drv = InputDriver(use_xsendevent=True)
    fake_disp = MagicMock()
    fake_disp.screen.return_value = SimpleNamespace(
        root=SimpleNamespace(query_pointer=lambda: SimpleNamespace(root_x=0, root_y=0))
    )

    with (
        patch.object(drv, "_get_x11_display", return_value=fake_disp),
        patch("Xlib.ext.xtest.fake_input") as mock_fake_input,
    ):
        drv.click(x=120, y=240, window_handle=1, restore_cursor=False)

    # XID 1 rejected before create_resource_object; cleanly falls through to XTest
    fake_disp.create_resource_object.assert_not_called()
    assert mock_fake_input.call_count >= 3
    fake_disp.sync.assert_called()


def test_linux_driver_xtest_keys_and_hotkeys() -> None:
    drv = InputDriver()
    fake_disp = MagicMock()
    fake_disp.keysym_to_keycode.side_effect = lambda ks: 50 + (ks % 50)

    with (
        patch.object(drv, "_get_x11_display", return_value=fake_disp),
        patch("Xlib.ext.xtest.fake_input") as mock_fake_input,
    ):
        drv.press_key("pageup")
        drv.press_key("f5")
        drv.hotkey("ctrl", "alt", "left")
        drv.type_text("Hi!", interval=0)

    assert mock_fake_input.call_count > 10
    assert fake_disp.sync.call_count >= 4


def test_macos_driver_type_text_uses_command_v_and_restores_clipboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("agenteverywhereflow.actions.driver.sys.platform", "darwin")
    drv = InputDriver()

    copied: list[str] = []
    fake_pyperclip = SimpleNamespace(
        paste=MagicMock(return_value="old-clip"),
        copy=MagicMock(side_effect=copied.append),
    )

    with (
        patch.dict(sys.modules, {"pyperclip": fake_pyperclip}),
        patch.object(drv, "hotkey") as mock_hotkey,
    ):
        drv.type_text("你好 macOS")

    assert copied == ["你好 macOS", "old-clip"]
    mock_hotkey.assert_called_once_with("command", "v", window_handle=0)


def test_macos_driver_double_click_sets_click_state_2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agenteverywhereflow.actions.driver.sys.platform", "darwin")
    drv = InputDriver()

    click_states: list[int] = []
    posted_events: list[tuple[int, object]] = []

    fake_quartz = SimpleNamespace(
        kCGHIDEventTap=0,
        kCGMouseEventClickState=1,
        kCGMouseButtonLeft=0,
        kCGEventLeftMouseDown=1,
        kCGEventLeftMouseUp=2,
        kCGEventMouseMoved=5,
        CGEventCreate=MagicMock(return_value="CUR_EVT"),
        CGEventGetLocation=MagicMock(return_value=SimpleNamespace(x=10.0, y=20.0)),
        CGEventCreateMouseEvent=MagicMock(
            side_effect=lambda src, etype, pos, btn: {"etype": etype, "pos": pos, "btn": btn}
        ),
        CGEventSetIntegerValueField=MagicMock(
            side_effect=lambda evt, field, val: click_states.append(val)
        ),
        CGEventPost=MagicMock(side_effect=lambda tap, evt: posted_events.append((tap, evt))),
    )

    with patch("agenteverywhereflow.actions.driver._get_quartz", return_value=fake_quartz):
        drv.double_click(x=320, y=240, restore_cursor=False)

    # First click sets clickState=1 (down + up), second click sets clickState=2 (down + up)
    assert click_states == [1, 1, 2, 2]
    assert len(posted_events) == 5  # move + down1 + up1 + down2 + up2


def test_macos_driver_hotkey_normalizes_cmd_to_command(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agenteverywhereflow.actions.driver.sys.platform", "darwin")
    drv = InputDriver()

    flags_set: list[int] = []
    fake_quartz = SimpleNamespace(
        kCGHIDEventTap=0,
        kCGEventFlagMaskCommand=1 << 20,
        kCGEventFlagMaskShift=1 << 17,
        CGEventCreateKeyboardEvent=MagicMock(side_effect=lambda src, kc, down: (kc, down)),
        CGEventSetFlags=MagicMock(side_effect=lambda evt, fl: flags_set.append(fl)),
        CGEventPost=MagicMock(),
    )

    with patch("agenteverywhereflow.actions.driver._get_quartz", return_value=fake_quartz):
        drv.hotkey("cmd", "shift", "c")

    expected_flags = (1 << 20) | (1 << 17)
    assert flags_set == [expected_flags, expected_flags]
    assert fake_quartz.CGEventPost.call_count == 2


def test_windows_sendinput_struct_construction_and_paste_without_pyautogui(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Verify exact Win32 INPUT ABI struct construction works on every OS
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        assert ctypes.sizeof(_INPUT) == 40
    else:
        assert ctypes.sizeof(_INPUT) == 28

    down = build_win32_mouse_input(MOUSEEVENTF_LEFTDOWN)
    assert down.type == INPUT_MOUSE
    assert down.mi.dwFlags == MOUSEEVENTF_LEFTDOWN

    scroll_down = build_win32_mouse_input(MOUSEEVENTF_WHEEL, mouse_data=-240)
    assert scroll_down.type == INPUT_MOUSE
    assert scroll_down.mi.mouseData == (-240) & 0xFFFFFFFF

    key_up = build_win32_key_input(ord("V"), key_up=True)
    assert key_up.type == INPUT_KEYBOARD
    assert key_up.ki.wVk == ord("V")
    assert key_up.ki.dwFlags == KEYEVENTF_KEYUP

    # Verify Windows click + Ctrl+V paste work even when PyAutoGUI is unavailable (backend=None)
    monkeypatch.setattr("agenteverywhereflow.actions.driver.sys.platform", "win32")
    drv = InputDriver()
    sent_batches: list[list[_INPUT]] = []
    copied: list[str] = []
    fake_pyperclip = SimpleNamespace(
        paste=MagicMock(return_value="prev"),
        copy=MagicMock(side_effect=copied.append),
    )

    with (
        patch.dict(sys.modules, {"pyperclip": fake_pyperclip}),
        patch.object(drv, "_get_backend", return_value=None),
        patch.object(drv, "_win32_get_cursor_pos", return_value=(10, 10)),
        patch.object(drv, "_win32_set_cursor_pos"),
        patch.object(
            drv,
            "_win32_send_inputs",
            side_effect=lambda batch: sent_batches.append(batch) or len(batch),
        ),
    ):
        drv.click(x=50, y=60)
        drv.type_text("Windows测试")

    # Click emitted LEFTDOWN then LEFTUP via SendInput
    assert sent_batches[0][0].mi.dwFlags == MOUSEEVENTF_LEFTDOWN
    assert sent_batches[1][0].mi.dwFlags == MOUSEEVENTF_LEFTUP
    # Type emitted 4-event Ctrl+V SendInput batch even though backend is None
    assert copied == ["Windows测试", "prev"]
    paste_batch = sent_batches[2]
    assert len(paste_batch) == 4
    assert paste_batch[0].ki.wVk == 0x11  # VK_CONTROL down
    assert paste_batch[1].ki.wVk == ord("V")  # V down
    assert paste_batch[2].ki.dwFlags == KEYEVENTF_KEYUP
    assert paste_batch[3].ki.dwFlags == KEYEVENTF_KEYUP


# =============================================================================
# 4. Engine, WorkflowRunner, Prompts & Retina 2x DPI Scaling Tests
# =============================================================================
def test_engines_pass_zero_window_handle_for_display_and_region_and_scale_retina() -> None:
    display_target = TargetInfo(
        target_id="display:1",
        target_type=TargetType.DISPLAY,
        title="Retina Display",
        rect=Rect(x=100, y=50, width=1440, height=900),
        native_handle=1,  # Display index MUST NOT be passed as window_handle
    )
    # Simulate a 2x Retina capture (2880x1800 image pixels for 1440x900 logical points)
    display_target.last_capture_size = (2880, 1800)

    region_target = TargetInfo(
        target_id="region:10,20,400,300",
        target_type=TargetType.REGION,
        title="Region",
        rect=Rect(x=10, y=20, width=400, height=300),
        native_handle=99,
    )

    # 1. PythonReplEngine
    repl = PythonReplEngine()
    with (
        patch.object(repl.capturer, "focus", return_value=True),
        patch("agenteverywhereflow.engine.python_repl.driver.click") as mock_click,
    ):
        res = repl.execute("click(1440, 900)", display_target)
        assert res.success, res.error
        # 1440/2880 * 1440 = 720 -> screen x = 100 + 720 = 820; y = 50 + 450 = 500
        assert mock_click.call_args.kwargs["x"] == 820
        assert mock_click.call_args.kwargs["y"] == 500
        assert mock_click.call_args.kwargs["window_handle"] == 0

        res_reg = repl.execute("click(100, 100)", region_target)
        assert res_reg.success, res_reg.error
        assert mock_click.call_args.kwargs["window_handle"] == 0

    # 2. GuardedActionEngine
    guarded = GuardedActionEngine()
    with (
        patch.object(guarded.capturer, "focus", return_value=True),
        patch("agenteverywhereflow.engine.guarded.driver.click") as mock_g_click,
    ):
        gres = guarded.execute({"action": "click", "x": 1440, "y": 900}, display_target)
        assert gres.success, gres.error
        assert mock_g_click.call_args.kwargs["x"] == 820
        assert mock_g_click.call_args.kwargs["y"] == 500
        assert mock_g_click.call_args.kwargs["window_handle"] == 0

    # Guarded sensitive combo checks cover macOS command/cmd
    assert guarded._is_sensitive_action({"action": "hotkey", "keys": ["command", "q"]}) is True
    assert guarded._is_sensitive_action({"action": "hotkey", "keys": ["cmd", "d"]}) is True
    assert guarded._is_sensitive_action({"action": "hotkey", "keys": ["win", "d"]}) is True
    assert guarded._is_sensitive_action({"action": "hotkey", "keys": ["command", "c"]}) is False

    # 3. WorkflowRunner
    wf = WorkflowDefinition(
        name="retina_display_wf",
        targets=[
            WorkflowTarget(
                target_id="display:1",
                title="Retina Display",
                rect={"x": 100, "y": 50, "width": 1440, "height": 900},
            )
        ],
        steps=[
            WorkflowStep(
                step_number=1,
                action="click",
                target="Retina Display",
                params={"x": 1440, "y": 900},
            )
        ],
    )
    runner = WorkflowRunner()
    runner.capturer = MagicMock()
    runner.capturer.list_targets.return_value = [display_target]
    runner.capturer.focus.return_value = True

    with patch("agenteverywhereflow.workflow.runner.driver.click") as mock_r_click:
        rres = runner.play(wf, speed=10.0)
        assert rres.success is True
        assert mock_r_click.call_args.kwargs["x"] == 820
        assert mock_r_click.call_args.kwargs["y"] == 500
        assert mock_r_click.call_args.kwargs["window_handle"] == 0


def test_prompts_os_modifier_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    tgt = TargetInfo(
        target_id="display:1",
        target_type=TargetType.DISPLAY,
        title="Screen",
        rect=Rect(0, 0, 1280, 720),
    )

    monkeypatch.setattr("agenteverywhereflow.agent.prompts.sys.platform", "darwin")
    mac_minimal = get_prompt_for_target(tgt, is_minimal_mode=True)
    mac_guarded = get_prompt_for_target(tgt, is_minimal_mode=False)
    assert 'hotkey("command", "c")' in mac_minimal
    assert '"command"' in mac_guarded

    monkeypatch.setattr("agenteverywhereflow.agent.prompts.sys.platform", "linux")
    linux_minimal = get_prompt_for_target(tgt, is_minimal_mode=True)
    assert 'hotkey("ctrl", "c")' in linux_minimal


def test_benchmarks_import_cleanly_on_non_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "Xlib", None)  # type: ignore[arg-type]
    monkeypatch.setitem(sys.modules, "Xlib.display", None)  # type: ignore[arg-type]

    for mod_name in ("benchmarks.bench_suite", "benchmarks.bench_autonomous"):
        sys.modules.pop(mod_name, None)
        mod = importlib.import_module(mod_name)
        assert mod.xlib_display is None
