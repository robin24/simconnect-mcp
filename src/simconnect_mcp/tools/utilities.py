"""Utility tools — text overlay, position teleport."""

from __future__ import annotations

from simconnect_mcp.connection import SimConnectManager
from simconnect_mcp.tools import handle_simconnect_errors, require_connection

# SIMCONNECT_TEXT_TYPE members for the PRINT_* colour variants.
_TEXT_COLORS = {
    "white": "SIMCONNECT_TEXT_TYPE_PRINT_WHITE",
    "red": "SIMCONNECT_TEXT_TYPE_PRINT_RED",
    "green": "SIMCONNECT_TEXT_TYPE_PRINT_GREEN",
    "blue": "SIMCONNECT_TEXT_TYPE_PRINT_BLUE",
    "yellow": "SIMCONNECT_TEXT_TYPE_PRINT_YELLOW",
    "magenta": "SIMCONNECT_TEXT_TYPE_PRINT_MAGENTA",
    "cyan": "SIMCONNECT_TEXT_TYPE_PRINT_CYAN",
    "black": "SIMCONNECT_TEXT_TYPE_PRINT_BLACK",
}


@handle_simconnect_errors
@require_connection
async def send_sim_text(text: str, duration_s: float = 5.0, color: str = "white") -> dict:
    """Display a text overlay message in the simulator (debug feedback).

    Args:
        text: Text message to display in the sim
        duration_s: How long to display it, in seconds
        color: One of white, red, green, blue, yellow, magenta, cyan, black

    Returns:
        Confirmation dict.
    """
    color_key = color.strip().lower()
    if color_key not in _TEXT_COLORS:
        return {
            "status": "error",
            "error": "INVALID_COLOR",
            "message": f"Unknown text colour '{color}'.",
            "suggestion": f"Use one of: {', '.join(sorted(_TEXT_COLORS))}.",
        }

    manager = SimConnectManager()

    def _send() -> None:
        from SimConnect.Enum import SIMCONNECT_TEXT_TYPE

        text_type = getattr(SIMCONNECT_TEXT_TYPE, _TEXT_COLORS[color_key])
        # The library method is sendText, not send_text.
        manager.sm.sendText(text, duration_s, text_type)

    await manager.run_sync(_send)
    return {
        "status": "ok",
        "message": f"Text displayed in sim: '{text}'",
        "duration_s": duration_s,
        "color": color_key,
    }


@handle_simconnect_errors
@require_connection
async def set_aircraft_position(
    latitude: float,
    longitude: float,
    altitude: float | None = None,
    heading: float | None = None,
    on_ground: bool = False,
) -> dict:
    """Teleport aircraft to a specific position (test scenario setup).

    Args:
        latitude: Target latitude in degrees
        longitude: Target longitude in degrees
        altitude: Target altitude in feet (optional — uses ground level if omitted)
        heading: Target heading in degrees true (optional)
        on_ground: If True, place aircraft on ground at the position

    Returns:
        Confirmation dict with new position.
    """
    manager = SimConnectManager()

    def _set_pos() -> None:
        manager.aq.set("PLANE_LATITUDE", latitude)
        manager.aq.set("PLANE_LONGITUDE", longitude)
        if altitude is not None:
            manager.aq.set("PLANE_ALTITUDE", altitude)
        if heading is not None:
            manager.aq.set("PLANE_HEADING_DEGREES_TRUE", heading)

    await manager.run_sync(_set_pos)

    result = {
        "status": "ok",
        "message": "Aircraft position updated",
        "latitude": latitude,
        "longitude": longitude,
    }
    if altitude is not None:
        result["altitude"] = altitude
    if heading is not None:
        result["heading"] = heading
    return result
