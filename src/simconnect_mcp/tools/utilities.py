"""Utility tools — text overlay, position teleport."""

from __future__ import annotations

import time
from typing import Annotated, Literal

from pydantic import Field

from simconnect_mcp.connection import SimConnectManager
from simconnect_mcp.tools import handle_simconnect_errors, require_connection
from simconnect_mcp.tools.models import PositionResult, TextResult, ToolError

# SIMCONNECT_TEXT_TYPE members for the PRINT_* colour variants.
_TEXT_COLORS: dict[str, str] = {
    "white": "SIMCONNECT_TEXT_TYPE_PRINT_WHITE",
    "red": "SIMCONNECT_TEXT_TYPE_PRINT_RED",
    "green": "SIMCONNECT_TEXT_TYPE_PRINT_GREEN",
    "blue": "SIMCONNECT_TEXT_TYPE_PRINT_BLUE",
    "yellow": "SIMCONNECT_TEXT_TYPE_PRINT_YELLOW",
    "magenta": "SIMCONNECT_TEXT_TYPE_PRINT_MAGENTA",
    "cyan": "SIMCONNECT_TEXT_TYPE_PRINT_CYAN",
    "black": "SIMCONNECT_TEXT_TYPE_PRINT_BLACK",
}

_TextColor = Literal["white", "red", "green", "blue", "yellow", "magenta", "cyan", "black"]


@handle_simconnect_errors
@require_connection
async def send_sim_text(
    text: Annotated[
        str,
        Field(description="Text message to display in the sim", min_length=1, max_length=200),
    ],
    duration_s: Annotated[
        float, Field(description="How long to display it, in seconds", ge=0.1, le=60)
    ] = 5.0,
    color: Annotated[
        _TextColor,
        Field(description="One of white, red, green, blue, yellow, magenta, cyan, black"),
    ] = "white",
) -> TextResult | ToolError:
    """Display a text overlay message in the simulator (debug feedback)."""
    # The Literal above rejects an invalid colour at the schema boundary for
    # real MCP callers before this body ever runs -- but a direct Python
    # call (as tests do, and as get_simvar_bulk's MAX_BULK_VARIABLES check
    # in simvars.py notes for the same reason) bypasses that entirely, so
    # this stays as the actual enforcement for that path, and as a
    # case/whitespace-forgiving check either way.
    color_key = color.strip().lower()
    if color_key not in _TEXT_COLORS:
        return ToolError(
            error="INVALID_COLOR",
            message=f"Unknown text colour '{color}'.",
            suggestion=f"Use one of: {', '.join(sorted(_TEXT_COLORS))}.",
        )

    manager = SimConnectManager()

    def _send() -> None:
        from SimConnect.Enum import SIMCONNECT_TEXT_TYPE

        text_type = getattr(SIMCONNECT_TEXT_TYPE, _TEXT_COLORS[color_key])
        # The library method is sendText, not send_text.
        manager.sm.sendText(text, duration_s, text_type)

    await manager.run_sync(_send)
    return TextResult(
        message=f"Text displayed in sim: '{text}'",
        duration_s=duration_s,
        color=color_key,
    )


@handle_simconnect_errors
@require_connection
async def set_aircraft_position(
    latitude: Annotated[
        float, Field(description="Target latitude, degrees", ge=-90, le=90)
    ],
    longitude: Annotated[
        float, Field(description="Target longitude, degrees", ge=-180, le=180)
    ],
    altitude: Annotated[
        float | None,
        Field(
            description="Target altitude in feet. Omit to keep the current altitude.",
            ge=-2000, le=275000,
        ),
    ] = None,
    heading: Annotated[
        float | None,
        Field(
            description="Target heading in degrees true. Omit to keep the current heading.",
            ge=0, lt=360,
        ),
    ] = None,
    on_ground: Annotated[
        bool, Field(description="Place the aircraft on the ground at the position")
    ] = False,
    airspeed: Annotated[
        int, Field(description="Target airspeed in knots (0 for a stationary placement)",
                   ge=0, le=2000)
    ] = 0,
    pitch: Annotated[float, Field(description="Pitch in degrees", ge=-180, le=180)] = 0.0,
    bank: Annotated[float, Field(description="Bank in degrees", ge=-180, le=180)] = 0.0,
) -> PositionResult | ToolError:
    """Reposition the aircraft (test scenario setup).

    Uses SimConnect's SIMCONNECT_DATA_INITPOSITION, which repositions the
    aircraft atomically. Writing PLANE_LATITUDE/LONGITUDE individually, as
    this used to, is unreliable and cannot set the on-ground state.

    The response reports the position read back from the sim after the
    move, in `latitude`/`longitude`/etc -- never the request, which is
    echoed separately under `requested` for comparison. A field the
    read-back could not confirm is null (and listed in `unverified`), not
    silently replaced by what was asked for. `status` still reports "ok"
    since the reposition command itself may well have succeeded even if the
    confirming read did not; check `unverified`/`warning` for that.
    """
    manager = SimConnectManager()

    def _current(
        name: str, fallback: float, unit: str | None = None
    ) -> float:
        """Best-effort read used only to fill in an omitted request field.

        This is a genuine "we don't know, use this default" fallback for
        picking a value to hand to set_pos when the caller didn't supply
        one -- never used for reporting what actually happened, which is
        `_verify`'s job below.
        """
        try:
            value = manager.accessor.read(name, unit=unit)
            return float(value) if value is not None else fallback
        except Exception:
            return fallback

    def _verify(name: str, unit: str | None = None) -> float | None:
        """Read back a value after the write, for reporting. Never guesses.

        Returns None on failure rather than echoing the request as if it
        were a measurement: doing that made the response claim status "ok"
        with the requested position reported as fact, and made the
        divergence warning below impossible to trigger, since a request
        compared against itself is always equal.
        """
        try:
            value = manager.accessor.read(name, unit=unit)
            return float(value) if value is not None else None
        except Exception:
            return None

    def _set_pos() -> dict:
        target_alt = (
            altitude if altitude is not None else _current("PLANE_ALTITUDE", 0.0)
        )
        target_hdg = (
            heading
            if heading is not None
            else _current("PLANE_HEADING_DEGREES_TRUE", 0.0, unit="degrees")
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
        # The sim overrides the requested altitude when OnGround is set, so
        # report where the aircraft actually ended up rather than what we asked
        # for. Reading back inside the same locked callable avoids a race.
        time.sleep(0.3)
        on_ground_actual = _verify("SIM_ON_GROUND")
        return {
            "latitude": _verify("PLANE_LATITUDE", unit="degrees"),
            "longitude": _verify("PLANE_LONGITUDE", unit="degrees"),
            "altitude": _verify("PLANE_ALTITUDE", unit="feet"),
            "heading": _verify("PLANE_HEADING_DEGREES_TRUE", unit="degrees"),
            "on_ground": None if on_ground_actual is None else bool(on_ground_actual),
        }

    actual = await manager.run_sync(_set_pos)

    unverified = [field for field in
                  ("latitude", "longitude", "altitude", "heading", "on_ground")
                  if actual[field] is None]

    warnings = []
    if unverified:
        warnings.append(
            "Could not read back " + ", ".join(unverified) + " after repositioning "
            "(reported as null); the sim may have disconnected. These values were "
            "not confirmed, only requested."
        )
    if (
        altitude is not None
        and actual["altitude"] is not None
        and abs(actual["altitude"] - altitude) > 10
    ):
        warnings.append(
            f"Requested altitude {altitude} ft but aircraft is at "
            f"{actual['altitude']:.0f} ft. With on_ground set, the sim snaps "
            "the aircraft to terrain and ignores the requested altitude."
        )

    return PositionResult(
        message="Aircraft repositioned",
        latitude=actual["latitude"],
        longitude=actual["longitude"],
        altitude=actual["altitude"],
        heading=actual["heading"],
        on_ground=actual["on_ground"],
        airspeed=airspeed,
        requested={
            "latitude": latitude,
            "longitude": longitude,
            "altitude": altitude,
            "heading": heading,
            "on_ground": on_ground,
            "airspeed": airspeed,
        },
        unverified=unverified or None,
        warning=" ".join(warnings) if warnings else None,
    )
