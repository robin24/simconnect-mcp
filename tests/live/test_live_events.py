"""Live verification of event dispatch.

Run with MSFS running and an aircraft loaded:  uv run pytest -m live

The mocked suite (tests/test_events.py) simulates SimConnect's exception
correlation with a hand-rolled fake dispatcher. These tests exercise the
same code paths against the real DLL, where the fake's assumptions about
timing, packet IDs, and exception codes cannot be taken for granted.
"""
from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.live


async def test_catalog_event_fires(live_manager, restore_parking_brake):
    """PARKING_BRAKES is in the library's static AircraftEvents catalog.

    Also covers the L2 live-follow-up fix: the catalog branch now
    correlates through the real dispatcher exactly like the mapped branch
    (events.py's _fire()) instead of calling the found Event object
    directly with no correlation at all, so the message must say SimConnect
    *accepted* the packet -- never that the event "triggered successfully",
    which live testing showed to be false for ENGINE_AUTO_START on a
    Cessna Citation Longitude (see the live-follow-up report). PARKING_BRAKES
    is used here rather than ENGINE_AUTO_START specifically because it is
    already restored immediately by restore_parking_brake -- ENGINE_AUTO_START
    was live-verified during that same follow-up to have a real but *delayed*
    effect (well past this test's lifetime), which is unsafe to fire from an
    unattended, automated suite.
    """
    from simconnect_mcp.tools.events import trigger_event

    result = await trigger_event("PARKING_BRAKES")
    assert result.status == "ok"
    assert result.resolved_via == "catalog"
    assert "successfully" not in result.message.lower()
    assert "accepted the packet" in result.message.lower()


async def test_negative_parameter_reaches_the_sim(live_manager, restore_vs_hold):
    """AP_VS_VAR_SET_ENGLISH with a descent rate; verified by reading it back.

    The parameter is sent as a two's-complement DWORD (_to_dword); this
    proves that encoding survives the real SetDataOnSimObject round trip,
    not just a MagicMock capturing the call.
    """
    from simconnect_mcp.tools.events import trigger_event

    result = await trigger_event("AP_VS_VAR_SET_ENGLISH", parameter=-1800)
    assert result.status == "ok"

    value = live_manager.accessor.read("AUTOPILOT_VERTICAL_HOLD_VAR", unit="feet per minute")
    assert value == pytest.approx(-1800, abs=50)


async def test_unknown_event_reports_not_found_not_a_timeout(live_manager):
    """A bogus event name must resolve to EVENT_NOT_FOUND, not hang or crash.

    map_to_sim_event() succeeds for any string -- exception correlation on
    the follow-up send is what actually proves the event doesn't exist, and
    that correlation only means something when it runs against the real
    dispatcher's packet IDs and exception delivery.
    """
    from simconnect_mcp.tools.events import trigger_event

    result = await trigger_event("DEFINITELY_NOT_A_REAL_EVENT_NAME")
    assert result.status == "error"
    assert result.error == "EVENT_NOT_FOUND"


async def test_trigger_custom_event_delivers_a_key_event_via_mobiflight_rpn(
    live_manager, restore_sim_rate
):
    """final-fix-C / C1: msfs_trigger_custom_event called
    manager.mobiflight.trigger_event(), a method MobiFlightVariableRequests
    does not have -- every call raised AttributeError, surfaced to the
    caller as an UNEXPECTED error with a suggestion to check that MSFS is
    running while it was running and connected. No live test covered this
    tool, which is how the defect reached this state undetected.

    SIM_RATE_INCR is used rather than an aircraft control (e.g.
    PARKING_BRAKES) because it is sim-level: no aircraft-specific event
    system can intercept it, unlike the loaded PMDG 737, which was measured
    live to ignore PARKING_BRAKES delivered through this exact RPN path
    while still accepting SIM_RATE_INCR. That distinguishes "the delivery
    mechanism is broken" from "this aircraft ignores this particular event."

    Polls rather than reading back once immediately: mobiflight.set() writes
    to a client data area and returns before the sim's own dispatch loop
    next runs the WASM module, so the key event is not applied synchronously
    with the call returning. Measured live with a throwaway probe: the
    change landed ~100ms after set() returned, not before it returned.
    """
    from simconnect_mcp.tools.events import trigger_custom_event

    original_rate = restore_sim_rate

    result = await trigger_custom_event("SIM_RATE_INCR")
    assert result.status == "ok"

    new_rate = original_rate
    for _ in range(20):  # up to ~2s, well over the ~100ms measured
        new_rate = live_manager.accessor.read("SIMULATION_RATE")
        if new_rate != original_rate:
            break
        await asyncio.sleep(0.1)

    assert new_rate != original_rate, (
        f"SIMULATION_RATE stayed at {original_rate} for 2s after firing "
        "SIM_RATE_INCR through msfs_trigger_custom_event"
    )
