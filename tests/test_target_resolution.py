"""Tests for target resolution logic."""

from agenteverywhereflow.capturer.base import Rect, TargetInfo, TargetType
from agenteverywhereflow.capturer.selector import resolve_target


def test_resolve_target_by_target_id():
    t1 = TargetInfo(
        target_id="display:1",
        target_type=TargetType.DISPLAY,
        title="Display 1",
        rect=Rect(x=0, y=0, width=1920, height=1080),
    )
    t2 = TargetInfo(
        target_id="hwnd:0x1b0a4",
        target_type=TargetType.WINDOW,
        title="1.txt - Notepad",
        rect=Rect(x=100, y=100, width=800, height=600),
        native_handle=0x1B0A4,
        process_name="notepad.exe",
    )
    targets = [t1, t2]

    # Exact target_id match
    assert resolve_target(targets, "display:1") == t1
    assert resolve_target(targets, "hwnd:0x1b0a4") == t2
    assert resolve_target(targets, "HWND:0X1B0A4") == t2


def test_resolve_target_by_native_handle():
    t1 = TargetInfo(
        target_id="hwnd:0x1b0a4",
        target_type=TargetType.WINDOW,
        title="1.txt - Notepad",
        rect=Rect(x=100, y=100, width=800, height=600),
        native_handle=0x1B0A4,
        process_name="notepad.exe",
    )
    targets = [t1]

    # Hex handle match
    assert resolve_target(targets, "0x1b0a4") == t1
    assert resolve_target(targets, "0X1B0A4") == t1
    # Decimal handle match
    assert resolve_target(targets, str(0x1B0A4)) == t1


def test_resolve_target_by_table_index():
    t1 = TargetInfo(
        target_id="display:1",
        target_type=TargetType.DISPLAY,
        title="Display 1",
        rect=Rect(x=0, y=0, width=1920, height=1080),
    )
    t2 = TargetInfo(
        target_id="hwnd:0x1b0a4",
        target_type=TargetType.WINDOW,
        title="1.txt - Notepad",
        rect=Rect(x=100, y=100, width=800, height=600),
        native_handle=0x1B0A4,
        process_name="notepad.exe",
    )
    targets = [t1, t2]

    assert resolve_target(targets, "1") == t1
    assert resolve_target(targets, "2") == t2
    assert resolve_target(targets, "99") is None


def test_resolve_target_by_process_name():
    t1 = TargetInfo(
        target_id="hwnd:0x1b0a4",
        target_type=TargetType.WINDOW,
        title="Untitled - Text Editor",
        rect=Rect(x=100, y=100, width=800, height=600),
        native_handle=0x1B0A4,
        process_name="notepad.exe",
    )
    targets = [t1]

    assert resolve_target(targets, "notepad.exe") == t1
    assert resolve_target(targets, "NOTEPAD.EXE") == t1
    assert resolve_target(targets, "notepad") == t1


def test_resolve_target_by_title_substring():
    t1 = TargetInfo(
        target_id="hwnd:0x1b0a4",
        target_type=TargetType.WINDOW,
        title="MyProject - Visual Studio Code",
        rect=Rect(x=100, y=100, width=800, height=600),
        native_handle=0x1B0A4,
        process_name="Code.exe",
    )
    targets = [t1]

    assert resolve_target(targets, "Visual Studio Code") == t1
    assert resolve_target(targets, "myproject") == t1
    assert resolve_target(targets, "nonexistent") is None
