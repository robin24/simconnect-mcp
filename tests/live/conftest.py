"""Fixtures for tests that need a running MSFS.

Deselected by default via `addopts = "-m 'not live'"` in pyproject.toml.
Run them with:  uv run pytest -m live
"""
from __future__ import annotations

import pytest

from simconnect_mcp.connection import SimConnectManager


@pytest.fixture(scope="session")
def live_manager():
    """A connected SimConnectManager, or skip if MSFS is not running."""
    manager = SimConnectManager()
    result = manager.connect()
    if result["status"] != "ok":
        pytest.skip(f"MSFS not available: {result.get('message')}")
    yield manager
    manager.disconnect()
