"""WebUI asset manager and distributor for AgentEverywhereFlow.

Allows serving the full React WebUI directly from the Python backend
without requiring Node.js or cloning a separate repository.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any

WEBUI_REPO = "mcocdaa/AgentEverywhereFlow-UI"
VERSION_MARKER_NAME = "version.json"
LEGACY_MARKER_NAME = ".installed"


def _get_pkg_webui_dist() -> Path:
    """Return the package-embedded WebUI dist directory."""
    return Path(__file__).parent / "webui_dist"


def _get_user_webui_dist() -> Path:
    """Return the user-installed/updated WebUI dist directory (~/.aef/webui/dist)."""
    return Path.home() / ".aef" / "webui" / "dist"


def _get_sibling_webui_dist() -> Path | None:
    """Return a sibling AgentEverywhereFlow-UI/dist directory if present (local dev)."""
    parents = Path(__file__).resolve().parents
    if len(parents) > 3:
        for repo_name in ("AgentEverywhereFlow-UI", "AgentEverywhereFlow-WebUI"):
            candidate = parents[3] / repo_name / "dist"
            if (candidate / "index.html").is_file():
                return candidate
    return None


def _is_user_dist_preferred(user_dist: Path, pkg_dist: Path) -> bool:
    """Determine whether the user-installed WebUI bundle should take precedence over embedded assets."""
    user_index = user_dist / "index.html"
    if not user_index.is_file():
        return False

    pkg_index = pkg_dist / "index.html"
    if not pkg_index.is_file():
        return True

    if (user_dist / VERSION_MARKER_NAME).is_file() or (user_dist / LEGACY_MARKER_NAME).is_file():
        return True

    try:
        return user_index.stat().st_mtime > pkg_index.stat().st_mtime
    except OSError:
        return False


def _write_version_marker(target_dir: Path, *, version: str, source: str) -> None:
    """Write a small version.json marker so user updates take precedence cleanly."""
    marker_payload = {
        "version": version,
        "source": source,
        "installed_at": time.time(),
    }
    (target_dir / VERSION_MARKER_NAME).write_text(
        json.dumps(marker_payload, indent=2) + "\n",
        encoding="utf-8",
    )


def _read_version_marker(dist_dir: Path) -> dict[str, Any] | None:
    """Read version.json metadata from a WebUI dist directory if present."""
    marker_path = dist_dir / VERSION_MARKER_NAME
    if not marker_path.is_file():
        return None
    try:
        data = json.loads(marker_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def get_webui_dist_path() -> Path | None:
    """Find the path to the prebuilt WebUI static files if available.

    Precedence order:
    1. ``AEF_WEBUI_DIR`` environment variable override
    2. User-updated dist (``~/.aef/webui/dist``) when ``index.html`` exists and
       a ``version.json`` / ``.installed`` marker or newer timestamp is present
    3. Package-embedded dist (``agenteverywhereflow/server/webui_dist``)
    4. Legacy user root (``~/.aef/webui``) or unmarked user dist fallback
    5. Sibling repository fallback (``../AgentEverywhereFlow-UI/dist``)
    """
    # 1. Custom env var override
    env_dir = os.environ.get("AEF_WEBUI_DIR")
    if env_dir:
        p = Path(env_dir)
        if (p / "index.html").is_file():
            return p

    pkg_dist = _get_pkg_webui_dist()
    user_dist = _get_user_webui_dist()

    # 2. User-updated dist takes precedence over embedded wheel assets when marked or newer
    if _is_user_dist_preferred(user_dist, pkg_dist):
        return user_dist

    # 3. Embedded inside python package (agenteverywhereflow/server/webui_dist)
    if (pkg_dist / "index.html").is_file():
        return pkg_dist

    # 4. Legacy user directory fallback (~/.aef/webui)
    user_root = Path.home() / ".aef" / "webui"
    if (user_root / "index.html").is_file():
        return user_root

    # 5. Sibling local development repository fallback
    sibling_dist = _get_sibling_webui_dist()
    if sibling_dist is not None:
        return sibling_dist

    return None


def is_webui_available() -> bool:
    """Check if compiled WebUI assets exist and can be served."""
    return get_webui_dist_path() is not None


def install_webui(
    version: str | None = None,
    force: bool = False,
) -> Path:
    """Download and install/update prebuilt WebUI static assets into ~/.aef/webui/dist.

    Writes a ``version.json`` marker so ``get_webui_dist_path()`` always serves
    the user-updated bundle ahead of package-embedded assets.

    Args:
        version: Specific release tag or 'latest'
        force: Force re-download even if already present
    """
    import httpx

    target_dir = _get_user_webui_dist()
    if target_dir.exists() and (target_dir / "index.html").is_file() and not force:
        if not (target_dir / VERSION_MARKER_NAME).is_file():
            _write_version_marker(target_dir, version=version or "local", source="existing")
        return target_dir

    target_dir.parent.mkdir(parents=True, exist_ok=True)

    # 1. Try copying from package-embedded assets if present and not forcing remote update
    pkg_dist = _get_pkg_webui_dist()
    if (pkg_dist / "index.html").is_file() and not force:
        shutil.copytree(pkg_dist, target_dir, dirs_exist_ok=True)
        _write_version_marker(target_dir, version=version or "embedded", source="embedded")
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
            target_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                zf.extractall(target_dir)
            _write_version_marker(target_dir, version=tag, source="github_release")
            return target_dir

        # Fallback to package-embedded if download fails
        if (pkg_dist / "index.html").is_file():
            shutil.copytree(pkg_dist, target_dir, dirs_exist_ok=True)
            _write_version_marker(target_dir, version="embedded", source="embedded_fallback")
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
            "is_embedded": False,
            "source": None,
            "version": None,
        }

    resolved = dist_path.resolve()
    pkg_dist = _get_pkg_webui_dist().resolve()
    user_dist = _get_user_webui_dist().resolve()
    env_dir = os.environ.get("AEF_WEBUI_DIR")

    is_embedded = resolved == pkg_dist or "webui_dist" in str(dist_path)
    if env_dir and resolved == Path(env_dir).resolve():
        source = "env"
    elif resolved == user_dist or str(resolved).startswith(
        str((Path.home() / ".aef" / "webui").resolve())
    ):
        source = "user"
    elif is_embedded:
        source = "embedded"
    else:
        source = "sibling"

    marker = _read_version_marker(dist_path)
    assets_dir = dist_path / "assets"
    asset_files = list(assets_dir.glob("*")) if assets_dir.is_dir() else []
    return {
        "installed": True,
        "path": str(resolved),
        "has_index": (dist_path / "index.html").is_file(),
        "asset_count": len(asset_files),
        "is_embedded": is_embedded,
        "source": source,
        "version": marker.get("version") if marker else None,
    }
