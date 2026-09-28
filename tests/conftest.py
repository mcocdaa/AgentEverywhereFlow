"""Pytest configuration and global test isolation fixtures."""

from collections.abc import Generator
from pathlib import Path

import pytest

from agenteverywhereflow.session import session_storage


@pytest.fixture(autouse=True)
def isolate_session_storage(tmp_path: Path) -> Generator[None, None, None]:
    """Ensure all test sessions are persisted to an isolated temporary directory."""
    original_base = session_storage.base_dir
    session_storage.base_dir = tmp_path / "sessions"
    try:
        yield
    finally:
        session_storage.base_dir = original_base
