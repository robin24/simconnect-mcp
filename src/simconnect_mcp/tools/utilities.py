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
    airspeed: int = 0,
    pitch: float = 0.0,
    bank: float = 0.0,
) -> dict:
    """Reposition the aircraft (test scenario setup).

    Uses SimConnect's SIMCONNECT_DATA_INITPOSITION, which repositions the
    aircraft atomically. Writing PLANE_LATITUDE/LONGITUDE individually, as
    this used to, is unreliable and cannot set the on-ground state.

    Args:
        latitude: Target latitude, -90 to 90 degrees
        longitude: Target longitude, -180 to 180 degrees
        altitude: Target altitude in feet. Omit to keep the current altitude.
        heading: Target heading in degrees true. Omit to keep the current heading.
        on_ground: Place the aircraft on the ground at the position
        airspeed: Target airspeed in knots (0 for a stationary placement)
        pitch: Pitch in degrees
        bank: Bank in degrees

    Returns:
        Confirmation dict with the position applied.
    """
    if not -90.0 <= latitude <= 90.0 or not -180.0 <= longitude <= 180.0:
        return {
            "status": "error",
            "error": "INVALID_POSITION",
            "message": f"Latitude {latitude} / longitude {longitude} is out of range.",
            "suggestion": "Latitude must be -90..90 and longitude -180..180.",
        }

    manager = SimConnectManager()

    def _current(name: str, fallback: float) -> float:
        try:
            value = manager.accessor.read(name, unit="degrees" if name == "PLANE_HEADING_DEGREES_TRUE" else None)
            return float(value) if value is not None else fallback
        except Exception:
            return fallback

    def _set_pos() -> None:
        target_alt = altitude if altitude is not None else _current("PLANE_ALTITUDE", 0.0)
        target_hdg = (
            heading if heading is not None
            else _current("PLANE_HEADING_DEGREES_TRUE", 0.0)
        )
        manager.sm.set_pos(
            _Altitude=target_alt,
            _Latitude=latitude,
            _Longitude=longitude,
            _Airspeed=airspeed,
            _Pitch=pitch,
            _Bank=bank,
            _Heading=target_hdg,
            _OnGround=1 if on_ground else 0,
        )

    await manager.run_sync(_set_pos)

    result = {
        "status": "ok",
        "message": "Aircraft repositioned",
        "latitude": latitude,
        "longitude": longitude,
        "on_ground": on_ground,
        "airspeed": airspeed,
    }
    if altitude is not None:
        result["altitude"] = altitude
    if heading is not None:
        result["heading"] = heading
    return result
