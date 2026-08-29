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


@pytest.fixture(autouse=True)
def reset_singleton():
    """Override the root suite's autouse fixture of the same name -- a no-op.

    tests/conftest.py resets (disconnects + discards) the SimConnectManager
    singleton before and after every test, which is correct for the mocked
    suite but is fatal here: it tears down the real connection `live_manager`
    just established, before the first test body even runs (confirmed by a
    throwaway two-test diagnostic: `live_manager.accessor` was already None
    on test 1). Redefining the fixture under this name shadows the parent
    one for everything under tests/live/, so the single live connection
    survives for the whole session.
    """
    yield


# --- Restore fixtures ---
#
# These tests run against a real aircraft. Anything that writes a SimVar or
# fires a state-changing event captures the prior value on setup and puts it
# back on teardown (via `yield`), so restoration happens even if the test
# body raises.


@pytest.fixture
def restore_autopilot_altitude(live_manager):
    """AUTOPILOT_ALTITUDE_LOCK_VAR is written directly; restore the original."""
    original = live_manager.accessor.read("AUTOPILOT_ALTITUDE_LOCK_VAR", unit="feet")
    yield original
    live_manager.accessor.write("AUTOPILOT_ALTITUDE_LOCK_VAR", original, unit="feet")


@pytest.fixture
def restore_vs_hold(live_manager):
    """AP_VS_VAR_SET_ENGLISH changes AUTOPILOT_VERTICAL_HOLD_VAR; restore it.

    Restoration re-fires the same event with the original value rather than
    writing the SimVar directly -- only the event path is verified live to
    actually move this variable, so that is the one used in reverse too.
    """
    original = live_manager.accessor.read("AUTOPILOT_VERTICAL_HOLD_VAR", unit="feet per minute")
    yield original
    payload = int(round(original)) & 0xFFFFFFFF  # two's complement, as trigger_event does
    event = live_manager.ae.find("AP_VS_VAR_SET_ENGLISH")
    event(payload)


@pytest.fixture
def restore_parking_brake(live_manager):
    """PARKING_BRAKES is a toggle; firing it again restores the prior state."""
    yield
    event = live_manager.ae.find("PARKING_BRAKES")
    event()
