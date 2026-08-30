"""Facilities tools — airport and navaid lookup.

Not implemented yet: the SimConnect library's FacilitiesRequests only prints
results to stdout (fatal on a stdio MCP server -- see dispatch.py's module
docstring) and returns nothing usable to a caller. Phase 2 replaces it with
this server's own facilities handler, routed through SimConnectDispatcher
the same way SimVar reads are. Until then, both tools below report
NOT_IMPLEMENTED honestly rather than a fabricated empty success.
"""

from __future__ import annotations

from simconnect_mcp.tools import handle_simconnect_errors, require_connection
from simconnect_mcp.tools.models import ToolError

_NOT_IMPLEMENTED = ToolError(
    error="NOT_IMPLEMENTED",
    message=(
        "Facility lookup is not available yet. The SimConnect library's "
        "FacilitiesRequests only prints results to stdout and returns nothing, "
        "so this server implements its own facilities handler instead."
    ),
    suggestion=(
        "Use msfs_get_aircraft_snapshot(sections=['position']) for the current "
        "position, or an external navdata source for airport details."
    ),
)


@handle_simconnect_errors
@require_connection
async def get_nearby_airports() -> ToolError:
    """Get nearby airports from the SimConnect facilities subscription.

    Not implemented yet -- see the module docstring. Kept as a tool (rather
    than removed) so its replacement in Phase 2 is a drop-in.
    """
    return _NOT_IMPLEMENTED


@handle_simconnect_errors
@require_connection
async def get_facility_info(icao: str, facility_type: str = "airport") -> ToolError:
    """Get details on a specific airport, waypoint, NDB, or VOR.

    Not implemented yet -- see the module docstring. Kept as a tool (rather
    than removed) so its replacement in Phase 2 is a drop-in.
    """
    return _NOT_IMPLEMENTED
