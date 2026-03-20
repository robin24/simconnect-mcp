"""Aircraft state tools — snapshots of position, systems, and overall state."""

from __future__ import annotations

from typing import Any

from simconnect_mcp.connection import SimConnectManager
from simconnect_mcp.tools import handle_simconnect_errors, require_connection


def _read_vars(manager: SimConnectManager, var_names: list[str]) -> dict[str, Any]:
    """Read multiple SimVars, returning a dict of name→value."""
    result = {}
    for name in var_names:
        try:
            result[name] = manager.aq.get(name)
        except Exception:
            result[name] = None
    return result


@handle_simconnect_errors
@require_connection
async def get_aircraft_state() -> dict:
    """Get a comprehensive aircraft state snapshot (~30 key SimVars).

    Returns position, speed, heading, altitude, control surfaces, engine
    state, fuel, electrical, autopilot, and other key parameters.
    """
    manager = SimConnectManager()

    vars_to_read = [
        # Identity
        "TITLE", "ATC_TYPE", "ATC_ID",
        # Position
        "PLANE_LATITUDE", "PLANE_LONGITUDE", "PLANE_ALTITUDE",
        "PLANE_HEADING_DEGREES_MAGNETIC", "PLANE_HEADING_DEGREES_TRUE",
        "GROUND_ALTITUDE", "SIM_ON_GROUND",
        # Speed
        "AIRSPEED_INDICATED", "AIRSPEED_TRUE", "GROUND_VELOCITY", "VERTICAL_SPEED",
        # Controls
        "FLAPS_HANDLE_INDEX", "GEAR_HANDLE_POSITION", "SPOILERS_HANDLE_POSITION",
        "ELEVATOR_POSITION", "AILERON_POSITION", "RUDDER_POSITION",
        # Engine
        "GENERAL_ENG_THROTTLE_LEVER_POSITION:1",
        "FUEL_TOTAL_QUANTITY_WEIGHT",
        # Electrical
        "ELECTRICAL_MASTER_BATTERY",
        # Autopilot
        "AUTOPILOT_MASTER", "AUTOPILOT_HEADING_LOCK_DIR",
        "AUTOPILOT_ALTITUDE_LOCK_VAR",
        # Environment
        "AMBIENT_TEMPERATURE", "BAROMETER_PRESSURE",
        # Sim
        "SIMULATION_RATE", "LOCAL_TIME", "ZULU_TIME",
    ]

    data = await manager.run_sync(_read_vars, manager, vars_to_read)
    return {"status": "ok", "aircraft_state": data}


@handle_simconnect_errors
@require_connection
async def get_aircraft_position() -> dict:
    """Get aircraft position, heading, speed, and ground contact state.

    Returns a focused subset of position-related SimVars.
    """
    manager = SimConnectManager()

    vars_to_read = [
        "PLANE_LATITUDE", "PLANE_LONGITUDE", "PLANE_ALTITUDE",
        "PLANE_HEADING_DEGREES_TRUE", "PLANE_HEADING_DEGREES_MAGNETIC",
        "GROUND_ALTITUDE", "SIM_ON_GROUND",
        "AIRSPEED_INDICATED", "GROUND_VELOCITY", "VERTICAL_SPEED",
    ]

    data = await manager.run_sync(_read_vars, manager, vars_to_read)
    return {"status": "ok", "position": data}


@handle_simconnect_errors
@require_connection
async def get_aircraft_systems() -> dict:
    """Get aircraft systems state — engines, fuel, electrical, hydraulics.

    Returns engine parameters (up to 4 engines), fuel quantities,
    electrical bus states, and other system variables.
    """
    manager = SimConnectManager()

    vars_to_read = [
        # Engines (up to 2 for brevity)
        "GENERAL_ENG_THROTTLE_LEVER_POSITION:1",
        "GENERAL_ENG_THROTTLE_LEVER_POSITION:2",
        "ENG_N1_RPM:1", "ENG_N1_RPM:2",
        "ENG_N2_RPM:1", "ENG_N2_RPM:2",
        # Fuel
        "FUEL_TOTAL_QUANTITY", "FUEL_TOTAL_QUANTITY_WEIGHT",
        # Electrical
        "ELECTRICAL_MASTER_BATTERY",
        "ELECTRICAL_AVIONICS_BUS_VOLTAGE",
        "GENERAL_ENG_GENERATOR_ACTIVE:1",
        "GENERAL_ENG_GENERATOR_ACTIVE:2",
        # Controls
        "FLAPS_HANDLE_INDEX", "GEAR_HANDLE_POSITION",
        "SPOILERS_HANDLE_POSITION",
    ]

    data = await manager.run_sync(_read_vars, manager, vars_to_read)
    return {"status": "ok", "systems": data}
