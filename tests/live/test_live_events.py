"""Live verification of event dispatch.

Run with MSFS running and an aircraft loaded:  uv run pytest -m live

The mocked suite (tests/test_events.py) simulates SimConnect's exception
correlation with a hand-rolled fake dispatcher. These tests exercise the
same code paths against the real DLL, where the fake's assumptions about
timing, packet IDs, and exception codes cannot be taken for granted.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.live


async def test_catalog_event_fires(live_manager, restore_parking_brake):
    """PARKING_BRAKES is in the library's static AircraftEvents catalog."""
    from simconnect_mcp.tools.events import trigger_event

    result = await trigger_event("PARKING_BRAKES")
    assert result["status"] == "ok"
    assert result["resolved_via"] == "catalog"


async def test_negative_parameter_reaches_the_sim(live_manager, restore_vs_hold):
    """AP_VS_VAR_SET_ENGLISH with a descent rate; verified by reading it back.

    The parameter is sent as a two's-complement DWORD (_to_dword); this
    proves that encoding survives the real SetDataOnSimObject round trip,
    not just a MagicMock capturing the call.
    """
    from simconnect_mcp.tools.events import trigger_event

    result = await trigger_event("AP_VS_VAR_SET_ENGLISH", parameter=-1800)
    assert result["status"] == "ok"

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
    assert result["status"] == "error"
    assert result["error"] == "EVENT_NOT_FOUND"
