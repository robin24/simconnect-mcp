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


# --- PMDG gate ---
#
# test_live_pmdg.py's PMDG-specific tests need a real PMDG 737/777 loaded
# (its client data area, or PMDG branding in TITLE) -- run against anything
# else, e.g. the Cessna Citation Longitude this suite otherwise assumes,
# they fail for a reason that has nothing to do with the code under test. A
# red suite that means "wrong aircraft loaded" trains everyone to ignore
# red exactly as effectively as a genuine regression would, so those tests
# skip instead when the loaded aircraft is not recognizably a PMDG.
#
# Deliberately NOT built on the product code's own resolution
# (tools.pmdg._detect_pmdg_variant / _probe_pmdg_variant): those are
# exactly what test_probe_identifies_the_737_ng3_data_area and its
# siblings exist to verify, so gating those tests' own skip decision on
# that same logic would let a regression there silently skip the test
# meant to catch it, instead of failing loudly. This gate is deliberately
# simpler and independent of it: a plain substring check on TITLE.


def _is_pmdg_title(title: str | None) -> bool:
    """True if `title` looks like a PMDG aircraft.

    A real PMDG 737/777 can carry NO "PMDG" branding in TITLE at all --
    see test_live_pmdg.py's module docstring and CLAUDE.md's Known Sim
    Behaviours for two live-verified examples. This plain check will miss
    those and skip anyway. That is an accepted, deliberate limitation: this
    gate's only job is avoiding false failures against an aircraft that was
    never going to make these tests pass, and an occasional unnecessary
    skip on an unbranded real PMDG is the safe direction to be wrong in --
    unlike running (and failing) these tests against, say, a Citation.
    """
    return bool(title) and "pmdg" in title.lower()


def _skip_unless_pmdg(title: str | None) -> None:
    """Skip the calling test unless `title` looks like a PMDG aircraft.

    Split out from require_pmdg below so the skip decision itself can be
    exercised with a fake title in a plain, non-live unit test (see
    test_pmdg_gate.py) -- proving a PMDG-looking title does NOT skip is
    exactly what confirms this fix didn't quietly turn the four PMDG tests
    into a permanent, silent no-op.
    """
    if not _is_pmdg_title(title):
        pytest.skip(f"needs a PMDG aircraft; TITLE is {title!r}")


@pytest.fixture(scope="session")
def require_pmdg(live_manager):
    """Skip the calling test unless the currently loaded aircraft is a PMDG.

    Session-scoped and reads TITLE once, same as live_manager itself: this
    whole suite already assumes the loaded aircraft does not change
    mid-session (that's why live_manager is session-scoped too).
    """
    _skip_unless_pmdg(live_manager.accessor.read("TITLE"))


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


@pytest.fixture
def restore_sim_rate(live_manager):
    """SIM_RATE_INCR/SIM_RATE_DECR double/halve SIMULATION_RATE.

    Restoration must happen even if the test's own assertion fails (final-
    fix-C explicitly calls this out), so this fires the compensating native
    event -- not through the tool under test -- in a bounded loop until the
    rate matches what it was on entry, rather than assuming a single
    corrective step undoes whatever the test did.
    """
    original = live_manager.accessor.read("SIMULATION_RATE")
    yield original
    for _ in range(10):
        current = live_manager.accessor.read("SIMULATION_RATE")
        if current == original:
            break
        event = live_manager.ae.find("SIM_RATE_DECR" if current > original else "SIM_RATE_INCR")
        event()
