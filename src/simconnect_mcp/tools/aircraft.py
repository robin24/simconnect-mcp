"""Aircraft state snapshots, grouped into selectable sections.

This module used to hold three separate tools (get_aircraft_state,
get_aircraft_position, get_aircraft_systems) that differed only in which
SimVars they read -- get_aircraft_state was close to a superset of the
other two. They are replaced by get_aircraft_snapshot(sections=[...]),
one capability instead of three near-duplicate tool entries.

This was also the last module reading through manager.aq (the legacy
SimConnect.AircraftRequests), via a local _read_vars helper. Every other
tool module was migrated to manager.accessor (SimVarAccessor) in Phase 0,
because AircraftRequests binds each variable to one fixed unit, cannot
reach variables outside its 828-entry table, returns bytes for strings,
and reports failure as a bare None indistinguishable from "the value is
zero". This module now reads through manager.accessor.read_many too.

SECTIONS below pins an explicit unit per variable for exactly the same
reason get_simvar lets a caller override the catalog default: reading
"whatever unit the catalog happens to default to" is not reproducible.
Two entries are deliberate improvements over the catalog default rather
than neutral choices -- PLANE_HEADING_DEGREES_TRUE/_MAGNETIC default to
Radians in the catalog, and GROUND_ALTITUDE defaults to Meters; both are
pinned to the unit a human actually wants (degrees, feet).
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field

from simconnect_mcp.connection import SimConnectManager
from simconnect_mcp.tools import handle_simconnect_errors, require_connection
from simconnect_mcp.tools.models import AircraftSnapshot, ToolError
from simconnect_mcp.tools.simvars import diagnose_bulk_entries

# (name, unit, index) per section. Units are explicit so the snapshot is
# reproducible regardless of catalog defaults -- see the module docstring.
SECTIONS: dict[str, list[tuple[str, str | None, int | None]]] = {
    "identity": [
        ("TITLE", None, None),
        ("ATC_TYPE", None, None),
        ("ATC_ID", None, None),
    ],
    "position": [
        ("PLANE_LATITUDE", "degrees", None),
        ("PLANE_LONGITUDE", "degrees", None),
        ("PLANE_ALTITUDE", "feet", None),
        ("PLANE_HEADING_DEGREES_TRUE", "degrees", None),
        ("PLANE_HEADING_DEGREES_MAGNETIC", "degrees", None),
        ("GROUND_ALTITUDE", "feet", None),
        ("SIM_ON_GROUND", "bool", None),
        ("AIRSPEED_INDICATED", "knots", None),
        ("AIRSPEED_TRUE", "knots", None),
        ("GROUND_VELOCITY", "knots", None),
        ("VERTICAL_SPEED", "feet per minute", None),
    ],
    "engines": [
        ("GENERAL_ENG_THROTTLE_LEVER_POSITION", "percent", 1),
        ("GENERAL_ENG_THROTTLE_LEVER_POSITION", "percent", 2),
        ("ENG_N1_RPM", "percent", 1),
        ("ENG_N1_RPM", "percent", 2),
        ("ENG_N2_RPM", "percent", 1),
        ("ENG_N2_RPM", "percent", 2),
        ("FUEL_TOTAL_QUANTITY", "gallons", None),
        ("FUEL_TOTAL_QUANTITY_WEIGHT", "pounds", None),
    ],
    "systems": [
        ("ELECTRICAL_MASTER_BATTERY", "bool", None),
        ("ELECTRICAL_AVIONICS_BUS_VOLTAGE", "volts", None),
        ("GENERAL_ENG_GENERATOR_ACTIVE", "bool", 1),
        ("GENERAL_ENG_GENERATOR_ACTIVE", "bool", 2),
        ("FLAPS_HANDLE_INDEX", "number", None),
        ("GEAR_HANDLE_POSITION", "bool", None),
        ("SPOILERS_HANDLE_POSITION", "percent", None),
        ("ELEVATOR_POSITION", "position", None),
        ("AILERON_POSITION", "position", None),
        ("RUDDER_POSITION", "position", None),
    ],
    "autopilot": [
        ("AUTOPILOT_MASTER", "bool", None),
        ("AUTOPILOT_HEADING_LOCK_DIR", "degrees", None),
        ("AUTOPILOT_ALTITUDE_LOCK_VAR", "feet", None),
        ("AUTOPILOT_VERTICAL_HOLD_VAR", "feet per minute", None),
        ("AUTOPILOT_AIRSPEED_HOLD_VAR", "knots", None),
    ],
    "environment": [
        ("AMBIENT_TEMPERATURE", "celsius", None),
        ("AMBIENT_WIND_VELOCITY", "knots", None),
        ("AMBIENT_WIND_DIRECTION", "degrees", None),
        ("BAROMETER_PRESSURE", "millibars", None),
        ("SIMULATION_RATE", "number", None),
        ("ZULU_TIME", "seconds", None),
        ("LOCAL_TIME", "seconds", None),
    ],
}

_VALID_SECTIONS = ", ".join(SECTIONS)


@handle_simconnect_errors
@require_connection(needs_accessor=True)
async def get_aircraft_snapshot(
    # Deliberately no min_length/max_length here, mirroring
    # get_simvar_bulk's identical choice in simvars.py: every entry is
    # validated against SECTIONS below and rejected with a friendly,
    # dynamically-generated INVALID_SECTION suggestion naming the valid
    # options. A schema-level bound would only ever reject something this
    # runtime check already rejects better -- trading a specific "did you
    # mean one of: identity, position, ..." for a generic framework
    # validation error -- so it would make the failure case worse, not
    # safer, with no corresponding SimConnect-side cost to guard against
    # (unlike get_simvar_bulk's variables, SECTIONS' variable lists are
    # fixed by us, not the caller; selecting all six sections still reads a
    # small, constant, already-known number of variables in one batch).
    sections: Annotated[
        list[str] | None,
        Field(
            description=f"Sections to include: {_VALID_SECTIONS}. Omit for all of them."
        ),
    ] = None,
) -> AircraftSnapshot | ToolError:
    """Read a snapshot of the current aircraft state.

    Narrow with 'sections' to keep the response small -- for example
    sections=['position'] for a position fix, or ['engines', 'systems']
    when debugging a systems issue. All variables across the chosen
    sections are read in a single batched call.
    """
    if sections is None:
        chosen = list(SECTIONS)
    else:
        chosen = list(dict.fromkeys(sections))  # dedupe, preserve order
        unknown = [s for s in chosen if s not in SECTIONS]
        if unknown:
            return ToolError(
                error="INVALID_SECTION",
                message=f"Unknown section(s): {', '.join(unknown)}",
                suggestion=f"Valid sections are: {_VALID_SECTIONS}.",
            )

    requests: list[tuple[str, str | None, int | None]] = []
    for section in chosen:
        requests.extend(SECTIONS[section])

    manager = SimConnectManager()
    # No timeout argument: read_many sizes the batch budget from the number
    # of requests (see SimVarAccessor.read_many). It used to take a TOTAL
    # budget defaulting to one variable's worth, so all six sections -- 44
    # variables -- shared a single read's budget.
    data = await manager.run_sync(lambda: manager.accessor.read_many(requests))

    # A failed entry used to surface here as a raw exception string with no
    # error code and no suggestion, while get_simvar_bulk diagnosed the
    # identical dicts. Same data, same treatment.
    caller_units = {
        (name if index is None else f"{name}:{index}"): unit
        for name, unit, index in requests
    }
    ok_count, error_count = diagnose_bulk_entries(data, caller_units)

    return AircraftSnapshot(
        sections=chosen, ok_count=ok_count, error_count=error_count, data=data
    )
