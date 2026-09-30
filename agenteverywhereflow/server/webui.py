"""WebUI asset manager and distributor for AgentEverywhereFlow.

Allows serving the full Vue/React WebUI directly from the Python backend
without requiring Node.js or cloning a separate repository.
"""

from __future__ import annotations

import io
import os
import shutil
import zipfile
from pathlib import Path
from typing import Any

WEBUI_REPO = "mcocdaa/AgentEverywhereFlow-WebUI"


def get_webui_dist_path() -> Path | None:
    """Find the path to the prebuilt WebUI static files if available.

    Checks:
    1. AEF_WEBUI_DIR environment variable
    2. Package-embedded dist (agenteverywhereflow/server/webui_dist)
    3. User cached / installed dist (~/.aef/webui/dist or ~/.aef/webui)
    """
    # 1. Custom env var override
    env_dir = os.environ.get("AEF_WEBUI_DIR")
    if env_dir:
        p = Path(env_dir)
        if (p / "index.html").is_file():
            return p

    # 2. Embedded inside python package (agenteverywhereflow/server/webui_dist)
    pkg_dist = Path(__file__).parent / "webui_dist"
    if (pkg_dist / "index.html").is_file():
        return pkg_dist

    # 3. User cache directory (~/.aef/webui/dist or ~/.aef/webui)
    user_dist = Path.home() / ".aef" / "webui" / "dist"
    if (user_dist / "index.html").is_file():
        return user_dist

    user_root = Path.home() / ".aef" / "webui"
    if (user_root / "index.html").is_file():
        return user_root

    return None


def is_webui_available() -> bool:
    """Check if compiled WebUI assets exist and can be served."""
    return get_webui_dist_path() is not None


def install_webui(
    version: str | None = None,
    force: bool = False,
) -> Path:
    """Download and install/update prebuilt WebUI static assets into ~/.aef/webui/dist.

    Args:
        version: Specific release tag or 'latest'
        force: Force re-download even if already present
    """
    import httpx

    target_dir = Path.home() / ".aef" / "webui" / "dist"
    if target_dir.exists() and (target_dir / "index.html").is_file() and not force:
        return target_dir

    target_dir.parent.mkdir(parents=True, exist_ok=True)

    # 1. Try copying from package-embedded assets if present
    pkg_dist = Path(__file__).parent / "webui_dist"
    if (pkg_dist / "index.html").is_file() and not force:
        shutil.copytree(pkg_dist, target_dir, dirs_exist_ok=True)
        return target_dir

    # 2. Download prebuilt release bundle from GitHub Releases
    tag = version or "latest"
    if tag == "latest":
        url = f"https://github.com/{WEBUI_REPO}/releases/latest/download/webui-dist.zip"
    else:
        url = f"https://github.com/{WEBUI_REPO}/releases/download/{tag}/webui-dist.zip"

    headers = {"User-Agent": "AEFlow-WebUI-Installer"}
    with httpx.Client(follow_redirects=True, timeout=30.0) as client:
        resp = client.get(url, headers=headers)
        if resp.status_code == 200:
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                zf.extractall(target_dir)
            return target_dir

        # Fallback to package-embedded if download fails
        if (pkg_dist / "index.html").is_file():
            shutil.copytree(pkg_dist, target_dir, dirs_exist_ok=True)
            return target_dir

        raise RuntimeError(
            f"Failed to download WebUI assets from {url} (HTTP {resp.status_code}). "
            "Please verify network connectivity or check if a release asset is published."
        )


def get_webui_status() -> dict[str, Any]:
    """Retrieve detailed status of the WebUI installation."""
    dist_path = get_webui_dist_path()
    if not dist_path:
        return {
            "installed": False,
            "path": None,
            "has_index": False,
            "asset_count": 0,
        }

    assets_dir = dist_path / "assets"
    asset_files = list(assets_dir.glob("*")) if assets_dir.is_dir() else []
    return {
        "installed": True,
        "path": str(dist_path.resolve()),
        "has_index": (dist_path / "index.html").is_file(),
        "asset_count": len(asset_files),
        "is_embedded": "webui_dist" in str(dist_path),
    }
