"""Unit tests for virtual display routing and window relocation."""

from fastapi.testclient import TestClient

from agenteverywhereflow.capturer.base import BaseCapturer, Rect, TargetInfo, TargetType
from agenteverywhereflow.capturer.selector import get_displays, resolve_display
from agenteverywhereflow.server.app import create_app


class MockCapturerWithDisplays(BaseCapturer):
    def __init__(self) -> None:
        self.targets = [
            TargetInfo(
                target_id="display:1",
                target_type=TargetType.DISPLAY,
                title="Display 1 (Physical)",
                rect=Rect(x=0, y=0, width=2560, height=1600),
                native_handle=1,
            ),
            TargetInfo(
                target_id="display:2",
                target_type=TargetType.DISPLAY,
                title="Display 2 (Virtual Headless)",
                rect=Rect(x=2560, y=0, width=1920, height=1080),
                native_handle=2,
            ),
            TargetInfo(
                target_id="hwnd:0x12345",
                target_type=TargetType.WINDOW,
                title="微信",
                process_name="Weixin.exe",
                rect=Rect(x=100, y=100, width=800, height=600),
                native_handle=0x12345,
            ),
        ]

    def list_targets(
        self, include_displays: bool = True, include_windows: bool = True
    ) -> list[TargetInfo]:
        return self.targets

    def capture(self, target: TargetInfo):
        from PIL import Image

        return Image.new("RGB", (target.rect.width, target.rect.height), color=(255, 255, 255))

    def focus(self, target: TargetInfo) -> bool:
        return True

    def move_window_to_display(self, target: TargetInfo, display: TargetInfo) -> bool:
        if target.target_type != TargetType.WINDOW or display.target_type != TargetType.DISPLAY:
            return False
        # Center inside target display
        target_w = min(target.rect.width, display.rect.width - 40)
        target_h = min(target.rect.height, display.rect.height - 60)
        dest_x = display.rect.x + (display.rect.width - target_w) // 2
        dest_y = display.rect.y + (display.rect.height - target_h) // 2
        target.rect = Rect(x=dest_x, y=dest_y, width=target_w, height=target_h)
        return True


def test_resolve_and_get_displays() -> None:
    capturer = MockCapturerWithDisplays()
    targets = capturer.list_targets()

    displays = get_displays(targets)
    assert len(displays) == 2
    assert displays[0].target_id == "display:1"
    assert displays[1].target_id == "display:2"

    disp2 = resolve_display(targets, "2")
    assert disp2 is not None
    assert disp2.target_id == "display:2"

    disp_virtual = resolve_display(targets, "virtual")
    assert disp_virtual is not None
    assert disp_virtual.target_id == "display:2"


def test_move_window_to_virtual_display() -> None:
    capturer = MockCapturerWithDisplays()
    targets = capturer.list_targets()

    win = targets[2]
    disp2 = targets[1]
    assert win.rect.x == 100

    success = capturer.move_window_to_display(win, disp2)
    assert success is True
    # The window should now be located inside Display 2 (x >= 2560)
    assert win.rect.x >= 2560
    assert win.rect.x + win.rect.width <= disp2.rect.x + disp2.rect.width


def test_server_move_target_api(monkeypatch) -> None:
    mock_capturer = MockCapturerWithDisplays()
    monkeypatch.setattr("agenteverywhereflow.server.app.get_capturer", lambda: mock_capturer)

    app = create_app()
    client = TestClient(app)

    # 1. Test move window to display 2
    resp = client.post("/api/v1/targets/微信/move", json={"display_id": "2"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["target"]["rect"]["x"] >= 2560
    assert data["display"]["target_id"] == "display:2"

    # 2. Test create session with move_to_display
    resp_sess = client.post(
        "/api/v1/sessions",
        json={"target_id": "微信", "move_to_display": "2", "mode": "minimal"},
    )
    assert resp_sess.status_code == 201
