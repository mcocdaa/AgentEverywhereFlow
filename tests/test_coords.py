"""Tests for coordinate projection."""

from agenteverywhereflow.actions.coords import CoordinateProjector
from agenteverywhereflow.capturer.base import Rect, TargetInfo, TargetType


def test_coordinate_projector_display() -> None:
    target = TargetInfo(
        target_id="display:1",
        target_type=TargetType.DISPLAY,
        title="Primary Display",
        rect=Rect(x=0, y=0, width=1920, height=1080),
    )

    # Exact pixel mapping
    sx, sy = CoordinateProjector.to_screen_coords(
        target=target, x=500, y=400, img_width=1920, img_height=1080
    )
    assert sx == 500
    assert sy == 400


def test_coordinate_projector_offset_window() -> None:
    # Window located at x=200, y=100 with size 800x600
    target = TargetInfo(
        target_id="hwnd:123",
        target_type=TargetType.WINDOW,
        title="App Window",
        rect=Rect(x=200, y=100, width=800, height=600),
    )

    # Click at relative point (150, 50) within the window
    sx, sy = CoordinateProjector.to_screen_coords(
        target=target, x=150, y=50, img_width=800, img_height=600
    )
    assert sx == 200 + 150
    assert sy == 100 + 50


def test_coordinate_projector_normalized() -> None:
    target = TargetInfo(
        target_id="hwnd:123",
        target_type=TargetType.WINDOW,
        title="App Window",
        rect=Rect(x=100, y=100, width=1000, height=500),
    )

    # 0.5, 0.5 normalized coordinates
    sx, sy = CoordinateProjector.to_screen_coords(
        target=target, x=0.5, y=0.5, img_width=1000, img_height=500, is_normalized=True
    )
    assert sx == 100 + 500
    assert sy == 100 + 250
