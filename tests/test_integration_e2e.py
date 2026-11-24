"""End-to-end cross-branch integration tests across SDK, OS capturer/engines, CDP, and FastAPI."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from PIL import Image

import agenteverywhereflow as aef
import agenteverywhereflow.config as cfg_mod
from agenteverywhereflow.actions import (
    ActionBackend,
    CDPActionBackend,
    CoordinateProjector,
    InputDriver,
    OSGuiBackend,
    discover_cdp_targets,
    driver,
)
from agenteverywhereflow.capturer.base import BaseCapturer, Rect, TargetInfo, TargetType
from agenteverywhereflow.config import AppConfig
from agenteverywhereflow.server.app import create_app
from agenteverywhereflow.session import session_manager


class _FakeOSCapturer(BaseCapturer):
    """Concrete BaseCapturer exercising real target_at_point, capture_thumbnail, and Retina sizes."""

    def __init__(self, retina_scale: int = 1) -> None:
        self.retina_scale = retina_scale
        self.capture_calls: list[str] = []
        self.targets: list[TargetInfo] = [
            TargetInfo(
                target_id="display:1",
                target_type=TargetType.DISPLAY,
                title="Primary Display",
                rect=Rect(x=0, y=0, width=1920, height=1080),
                native_handle=1,
            ),
            TargetInfo(
                target_id="xwin:0x101",
                target_type=TargetType.WINDOW,
                title="Overlay Picker",
                process_name="aeflow-desktop",
                pid=9999,
                rect=Rect(x=0, y=0, width=1920, height=1080),
                native_handle=0x101,
            ),
            TargetInfo(
                target_id="xwin:0x202",
                target_type=TargetType.WINDOW,
                title="Chrome - Docs",
                process_name="chrome",
                pid=4321,
                rect=Rect(x=100, y=120, width=800, height=600),
                native_handle=0x202,
            ),
            TargetInfo(
                target_id="xwin:0x303",
                target_type=TargetType.WINDOW,
                title="VS Code",
                process_name="code",
                pid=5432,
                rect=Rect(x=920, y=120, width=900, height=700),
                native_handle=0x303,
            ),
        ]

    def list_targets(
        self, include_displays: bool = True, include_windows: bool = True
    ) -> list[TargetInfo]:
        out: list[TargetInfo] = []
        for t in self.targets:
            if t.target_type == TargetType.DISPLAY and include_displays:
                out.append(t)
            elif t.target_type == TargetType.WINDOW and include_windows:
                out.append(t)
        return out

    def capture(self, target: TargetInfo) -> Image.Image:
        self.capture_calls.append(target.target_id)
        w = max(1, target.rect.width * self.retina_scale)
        h = max(1, target.rect.height * self.retina_scale)
        img = Image.new("RGB", (w, h), color=(32, 64, 128))
        target.last_capture_size = (img.width, img.height)
        return img

    def focus(self, target: TargetInfo) -> bool:
        return True


def test_actions_package_exports_all_cross_branch_symbols() -> None:
    """Verify agenteverywhereflow.actions re-exports driver, coords, OSGuiBackend & CDP APIs."""
    assert isinstance(driver, InputDriver)
    assert CoordinateProjector is not None
    assert ActionBackend is not None
    assert OSGuiBackend is not None
    assert CDPActionBackend is not None
    assert callable(discover_cdp_targets)

    # OSGuiBackend respects Retina last_capture_size and passes window_handle=0 for REGION
    region = TargetInfo(
        target_id="region:100,50,400,300",
        target_type=TargetType.REGION,
        title="Retina Crop",
        rect=Rect(x=100, y=50, width=400, height=300),
        native_handle=0,
    )
    fake_cap = _FakeOSCapturer(retina_scale=2)
    backend = OSGuiBackend(target=region, capturer=fake_cap)
    shot = backend.capture()
    assert shot.size == (800, 600)
    assert region.last_capture_size == (800, 600)
    with patch.object(backend.driver, "click") as mock_click:
        backend.click(400, 300)
    # 400/800 * 400 + 100 = 300; 300/600 * 300 + 50 = 200
    assert mock_click.call_args.kwargs["x"] == 300
    assert mock_click.call_args.kwargs["y"] == 200
    assert mock_click.call_args.kwargs["window_handle"] == 0


def test_e2e_server_region_multitarget_at_point_thumbnail_and_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end verification across server/app.py, BaseCapturer, engines, and config.py."""
    env_file = tmp_path / ".aef" / ".env"
    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.write_text("# Keep comment\nCUSTOM_FLAG=true\n", encoding="utf-8")

    snapshot = {f: getattr(cfg_mod.config, f) for f in AppConfig.model_fields}
    monkeypatch.setattr(cfg_mod, "get_global_env_file", lambda: env_file)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    fake_cap = _FakeOSCapturer(retina_scale=2)
    session_manager.clear()

    try:
        app = create_app()
        client = TestClient(app)

        with (
            patch("agenteverywhereflow.server.app.get_capturer", return_value=fake_cap),
            patch("agenteverywhereflow.session.session.get_capturer", return_value=fake_cap),
            patch("agenteverywhereflow.engine.python_repl.get_capturer", return_value=fake_cap),
            patch("agenteverywhereflow.engine.guarded.get_capturer", return_value=fake_cap),
        ):
            # 1. GET /api/v1/targets/at-point uses BaseCapturer.target_at_point with exclude_pid
            r_hit = client.get("/api/v1/targets/at-point?x=200&y=200&exclude_pid=9999")
            assert r_hit.status_code == 200
            hit_target = r_hit.json()["target"]
            assert hit_target["target_id"] == "xwin:0x202"
            assert hit_target["pid"] == 4321

            # 2. GET /api/v1/targets/{id}/thumbnail delegates to BaseCapturer.capture_thumbnail + caches
            r_thumb1 = client.get("/api/v1/targets/xwin:0x202/thumbnail?width=320&quality=70")
            assert r_thumb1.status_code == 200
            assert r_thumb1.headers["content-type"] == "image/jpeg"
            assert r_thumb1.headers["x-thumbnail-cache"] == "miss"
            thumb_img = Image.open(io.BytesIO(r_thumb1.content))
            assert thumb_img.format == "JPEG"
            assert thumb_img.size == (320, 240)

            r_thumb2 = client.get("/api/v1/targets/xwin:0x202/thumbnail?width=320&quality=70")
            assert r_thumb2.status_code == 200
            assert r_thumb2.headers["x-thumbnail-cache"] == "hit"

            # 3. POST /api/v1/sessions with region -> TargetType.REGION + Retina CodeAct execution
            r_reg = client.post(
                "/api/v1/sessions",
                json={
                    "region": {
                        "x": 100,
                        "y": 50,
                        "width": 400,
                        "height": 300,
                        "title": "Invoice Crop",
                    },
                    "session_id": "e2e-region-1",
                    "mode": "minimal",
                    "permission_mode": "auto",
                },
            )
            assert r_reg.status_code == 201
            reg_body = r_reg.json()
            assert reg_body["target"]["target_type"] == "region"
            assert reg_body["target"]["target_id"] == "region:100,50,400,300"

            sess_reg = session_manager.get_session("e2e-region-1")
            assert sess_reg is not None
            assert sess_reg.target.target_type is TargetType.REGION
            sess_reg.planner_func = lambda msgs, tgt, step: (
                "```python\nclick(400, 300)\n```\nTASK_COMPLETED: Clicked center of Retina crop."
            )
            with patch("agenteverywhereflow.engine.python_repl.driver.click") as mock_click:
                r_msg = client.post(
                    "/api/v1/sessions/e2e-region-1/message",
                    json={"instruction": "Click crop center", "max_steps": 1},
                )
            assert r_msg.status_code == 200
            assert r_msg.json()["success"] is True
            # 2x Retina image (800x600) mapped back to logical region (400x300) at (100, 50)
            assert mock_click.call_args.kwargs["x"] == 300
            assert mock_click.call_args.kwargs["y"] == 200
            assert mock_click.call_args.kwargs["window_handle"] == 0

            # 4. POST /api/v1/sessions with target_ids + switch to custom region
            r_multi = client.post(
                "/api/v1/sessions",
                json={
                    "target_ids": ["xwin:0x202", "xwin:0x303"],
                    "session_id": "e2e-multi-1",
                    "mode": "guarded",
                },
            )
            assert r_multi.status_code == 201
            assert [t["target_id"] for t in r_multi.json()["targets"]] == [
                "xwin:0x202",
                "xwin:0x303",
            ]
            r_sw = client.post(
                "/api/v1/sessions/e2e-multi-1/target",
                json={"target_id": "region:20,30,300,200"},
            )
            assert r_sw.status_code == 200
            assert r_sw.json()["target"]["target_type"] == "region"

            # 5. GET & PATCH /api/v1/config uses pkg-sdk get_masked_config / update_env_config
            r_patch = client.patch(
                "/api/v1/config",
                json={
                    "api_key": "sk-integration-secret-7788",
                    "model_name": "gpt-4o",
                    "base_url": "https://api.openai.com/v1",
                    "default_mode": "minimal",
                    "default_permission": "manual",
                    "summon_hotkey": "alt+space",
                },
            )
            assert r_patch.status_code == 200
            patched = r_patch.json()
            assert patched["api_key_set"] is True
            assert patched["api_key_preview"] == "sk-int...7788"
            assert patched["default_permission"] == "manual"
            assert patched["summon_hotkey"] == "alt+space"

            r_get = client.get("/api/v1/config")
            assert r_get.status_code == 200
            assert r_get.json()["api_key_preview"] == "sk-int...7788"
            env_text = env_file.read_text(encoding="utf-8")
            assert "# Keep comment" in env_text
            assert "CUSTOM_FLAG=true" in env_text
            assert "AEF_API_KEY=sk-integration-secret-7788" in env_text

            # 6. High-level 3-line SDK aef.run() works end-to-end with Retina REGION target
            region_target = TargetInfo(
                target_id="region:10,20,400,200",
                target_type=TargetType.REGION,
                title="SDK Crop",
                rect=Rect(x=10, y=20, width=400, height=200),
                native_handle=0,
            )

            def _sdk_planner(messages: list[dict[str, Any]], tgt: TargetInfo, step: int) -> str:
                return "```python\nclick(400, 200)\n```\nTASK_COMPLETED: SDK click done."

            with (
                patch("agenteverywhereflow.agent.loop.get_capturer", return_value=fake_cap),
                patch("agenteverywhereflow.engine.python_repl.driver.click") as mock_sdk_click,
            ):
                sdk_res = aef.run(
                    region_target,
                    "Click center of region",
                    api_key="sk-sdk-test",
                    model="gpt-4o",
                    max_steps=2,
                    planner_func=_sdk_planner,
                )
            assert sdk_res.success is True
            assert sdk_res.steps == 1
            assert "SDK click done" in sdk_res.message
            # 2x Retina (800x400) -> click(400, 200) maps to logical (200, 100) + (10, 20) = (210, 120)
            assert mock_sdk_click.call_args.kwargs["x"] == 210
            assert mock_sdk_click.call_args.kwargs["y"] == 120
            assert mock_sdk_click.call_args.kwargs["window_handle"] == 0
    finally:
        session_manager.clear()
        for field, value in snapshot.items():
            setattr(cfg_mod.config, field, value)
