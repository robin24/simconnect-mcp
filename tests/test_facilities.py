"""Tests for the facilities tools.

FacilitiesRequests only prints results to stdout and returns nothing usable
(see facilities.py's module docstring), so neither tool can actually look
anything up yet. These tests guard against the tempting-but-wrong shortcut
of reporting a fake success (status "ok" with an empty list) instead of
being honest that the feature does not work yet.
"""

from __future__ import annotations

from simconnect_mcp.tools.facilities import get_facility_info, get_nearby_airports
from simconnect_mcp.tools.models import ToolError


async def test_facilities_report_not_implemented_rather_than_faking_success(mock_simconnect):
    """Fails against an implementation that returns {"status": "ok", "airports": []}
    or similar -- a caller reading that response has no way to tell "no airports
    nearby" apart from "this feature doesn't work", so the honest NOT_IMPLEMENTED
    error is required instead."""
    result = await get_nearby_airports()
    assert isinstance(result, ToolError)
    assert result.error == "NOT_IMPLEMENTED"


async def test_get_nearby_airports_does_not_touch_facilities_requests(mock_simconnect):
    """Fails against an implementation that still calls into manager.fr (the
    library's FacilitiesRequests) before giving up -- that object's iteration
    print()s to stdout, which corrupts the stdio JSON-RPC stream on a real
    MCP server (see dispatch.py's module docstring). NOT_IMPLEMENTED must be
    returned unconditionally, without ever touching it."""
    await get_nearby_airports()
    # The mock fixture doesn't set manager.fr at all (stays None from
    # __init__), so any attribute access on it would raise -- reaching this
    # assertion at all is itself proof fr was never touched.
    assert mock_simconnect["manager"].fr is None


async def test_get_facility_info_reports_not_implemented(mock_simconnect):
    """Same honesty requirement as get_nearby_airports, for the single-facility
    lookup tool."""
    result = await get_facility_info("KJFK")
    assert isinstance(result, ToolError)
    assert result.error == "NOT_IMPLEMENTED"


async def test_get_facility_info_not_implemented_regardless_of_facility_type(mock_simconnect):
    """The stub must not accidentally succeed for some facility_type values
    while failing for others -- there is no working code path at all yet."""
    result = await get_facility_info("KJFK", facility_type="vor")
    assert isinstance(result, ToolError)
    assert result.error == "NOT_IMPLEMENTED"


async def test_facilities_not_implemented_message_names_the_real_reason(mock_simconnect):
    """The message should explain *why* (FacilitiesRequests prints to stdout
    and returns nothing) rather than a generic "not supported", so a reader
    understands this is a known architectural gap, not a bug to report."""
    result = await get_nearby_airports()
    assert "FacilitiesRequests" in result.message
    assert result.suggestion is not None
