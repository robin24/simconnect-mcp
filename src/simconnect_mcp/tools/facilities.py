"""Facilities tools — airport and navaid lookup."""

from __future__ import annotations

from typing import Any

from simconnect_mcp.connection import SimConnectManager
from simconnect_mcp.tools import handle_simconnect_errors, require_connection


@handle_simconnect_errors
@require_connection
async def get_nearby_airports() -> dict:
    """Get nearby airports from the SimConnect facilities subscription.

    Returns airports that the simulator has loaded based on aircraft position.
    Availability depends on the sim's facilities data subscription.

    Returns:
        Dict with list of nearby airports (ICAO, name, lat, lon, altitude).
    """
    manager = SimConnectManager()

    if manager.fr is None:
        return {
            "status": "error",
            "error": "FACILITIES_NOT_AVAILABLE",
            "message": "FacilitiesRequests not available in this SimConnect build.",
            "suggestion": "This feature requires SimConnect facilities support.",
        }

    def _get_airports() -> list[dict]:
        airports = []
        try:
            airport_list = manager.fr.Airports
            for apt in airport_list:
                airports.append({
                    "icao": getattr(apt, "Icao", ""),
                    "name": getattr(apt, "Name", ""),
                    "latitude": getattr(apt, "Latitude", 0),
                    "longitude": getattr(apt, "Longitude", 0),
                    "altitude": getattr(apt, "Altitude", 0),
                })
        except Exception as e:
            return [{"error": str(e)}]
        return airports

    airports = await manager.run_sync(_get_airports)
    return {
        "status": "ok",
        "count": len(airports),
        "airports": airports,
    }


@handle_simconnect_errors
@require_connection
async def get_facility_info(icao: str, facility_type: str = "airport") -> dict:
    """Get details on a specific airport, waypoint, NDB, or VOR.

    Args:
        icao: ICAO identifier (e.g., 'KJFK', 'EGLL', 'LAX')
        facility_type: One of 'airport', 'waypoint', 'ndb', 'vor'

    Returns:
        Facility information dict.
    """
    manager = SimConnectManager()

    if manager.fr is None:
        return {
            "status": "error",
            "error": "FACILITIES_NOT_AVAILABLE",
            "message": "FacilitiesRequests not available.",
        }

    def _get_facility() -> dict[str, Any] | None:
        try:
            facilities = {
                "airport": getattr(manager.fr, "Airports", []),
                "waypoint": getattr(manager.fr, "Waypoints", []),
                "ndb": getattr(manager.fr, "NDBs", []),
                "vor": getattr(manager.fr, "VORs", []),
            }
            facility_list = facilities.get(facility_type, [])
            for fac in facility_list:
                fac_icao = getattr(fac, "Icao", "")
                if fac_icao.upper() == icao.upper():
                    return {
                        "icao": fac_icao,
                        "name": getattr(fac, "Name", ""),
                        "latitude": getattr(fac, "Latitude", 0),
                        "longitude": getattr(fac, "Longitude", 0),
                        "altitude": getattr(fac, "Altitude", 0),
                    }
        except Exception:
            pass
        return None

    info = await manager.run_sync(_get_facility)
    if info is None:
        return {
            "status": "error",
            "error": "FACILITY_NOT_FOUND",
            "message": f"Could not find {facility_type} '{icao}'.",
            "suggestion": "The facility may not be loaded. Try flying closer or check the ICAO code.",
        }
    return {"status": "ok", "facility": info}
