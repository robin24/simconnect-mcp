"""Event tools — trigger standard and custom SimConnect events."""

from __future__ import annotations

import logging
from ctypes.wintypes import DWORD
from typing import Annotated

from pydantic import Field

from simconnect_mcp.connection import SimConnectManager
from simconnect_mcp.dispatch import PendingRequest
from simconnect_mcp.tools import handle_simconnect_errors, require_connection
from simconnect_mcp.tools.formatting import (
    DEFAULT_LIMIT,
    ResponseFormat,
    build_search_result,
)
from simconnect_mcp.tools.models import EventResult, SearchResult, ToolError

logger = logging.getLogger(__name__)

# --- Event catalog ---

_EVENT_CATALOG: dict[str, list[dict]] | None = None
_FLAT_EVENTS: list[dict] | None = None


def _load_event_catalog() -> dict[str, list[dict]]:
    """Load the SimConnect event catalog from the installed library.

    Events live in SimConnect.EventList (not SimConnect.RequestList, which is
    what this used to import -- the ImportError was swallowed and the catalog
    silently degraded to the 50-entry builtin list while trigger_event could
    still fire all 994).

    AircraftEvents holds inner classes, each with a `list` of
    (b"EVENT_NAME", "description") tuples.
    """
    global _EVENT_CATALOG, _FLAT_EVENTS
    if _EVENT_CATALOG is not None:
        return _EVENT_CATALOG

    catalog: dict[str, list[dict]] = {}
    try:
        from SimConnect.EventList import AircraftEvents

        for attr_name in dir(AircraftEvents):
            inner = getattr(AircraftEvents, attr_name, None)
            if not isinstance(inner, type) or not hasattr(inner, "list"):
                continue
            # Inner classes are name-mangled: _AircraftEvents__Flight_Controls
            category = attr_name.split("__", 1)[-1].replace("_", " ").strip()
            entries = []
            for item in inner.list:
                raw_name, description = item[0], item[1] if len(item) > 1 else ""
                name = raw_name.decode() if isinstance(raw_name, bytes) else str(raw_name)
                entries.append(
                    {"name": name, "category": category, "description": description}
                )
            if entries:
                catalog[category] = entries
    except Exception:
        logger.warning("Could not load SimConnect event catalog", exc_info=True)
        catalog = {}

    # Fall back when parsing yields nothing, not only when the import raises.
    if not catalog:
        catalog = _builtin_event_catalog()

    _EVENT_CATALOG = catalog
    _FLAT_EVENTS = [e for entries in catalog.values() for e in entries]
    return catalog


def _builtin_event_catalog() -> dict[str, list[dict]]:
    return {
        "Aircraft Controls": [
            {
                "name": "PARKING_BRAKES",
                "category": "Aircraft Controls",
                "description": "Toggle parking brakes",
            },
            {
                "name": "TOGGLE_FLIGHT_DIRECTOR",
                "category": "Aircraft Controls",
                "description": "Toggle flight director",
            },
            {
                "name": "FLAPS_UP",
                "category": "Aircraft Controls",
                "description": "Retract flaps fully",
            },
            {
                "name": "FLAPS_DOWN",
                "category": "Aircraft Controls",
                "description": "Extend flaps one notch",
            },
            {
                "name": "FLAPS_1",
                "category": "Aircraft Controls",
                "description": "Set flaps to position 1",
            },
            {
                "name": "FLAPS_2",
                "category": "Aircraft Controls",
                "description": "Set flaps to position 2",
            },
            {
                "name": "FLAPS_3",
                "category": "Aircraft Controls",
                "description": "Set flaps to position 3",
            },
            {
                "name": "GEAR_TOGGLE",
                "category": "Aircraft Controls",
                "description": "Toggle landing gear",
            },
            {
                "name": "SPOILERS_TOGGLE",
                "category": "Aircraft Controls",
                "description": "Toggle spoilers",
            },
            {
                "name": "SPOILERS_ARM_TOGGLE",
                "category": "Aircraft Controls",
                "description": "Toggle spoiler arm",
            },
        ],
        "Autopilot": [
            {
                "name": "AP_MASTER",
                "category": "Autopilot",
                "description": "Toggle autopilot master",
            },
            {"name": "AP_HDG_HOLD", "category": "Autopilot", "description": "Toggle heading hold"},
            {"name": "AP_ALT_HOLD", "category": "Autopilot", "description": "Toggle altitude hold"},
            {"name": "AP_NAV1_HOLD", "category": "Autopilot", "description": "Toggle NAV1 hold"},
            {"name": "AP_APR_HOLD", "category": "Autopilot", "description": "Toggle approach hold"},
            {
                "name": "AP_VS_HOLD",
                "category": "Autopilot",
                "description": "Toggle vertical speed hold",
            },
            {
                "name": "AP_SPD_VAR_SET",
                "category": "Autopilot",
                "description": "Set autopilot speed (parameter: speed in knots)",
            },
            {
                "name": "AP_ALT_VAR_SET_ENGLISH",
                "category": "Autopilot",
                "description": "Set autopilot altitude (parameter: altitude in feet)",
            },
            {
                "name": "HEADING_BUG_SET",
                "category": "Autopilot",
                "description": "Set heading bug (parameter: heading in degrees)",
            },
        ],
        "Electrical": [
            {
                "name": "TOGGLE_MASTER_BATTERY",
                "category": "Electrical",
                "description": "Toggle master battery",
            },
            {
                "name": "TOGGLE_MASTER_ALTERNATOR",
                "category": "Electrical",
                "description": "Toggle master alternator",
            },
            {
                "name": "TOGGLE_AVIONICS_MASTER",
                "category": "Electrical",
                "description": "Toggle avionics master",
            },
        ],
        "Engine": [
            {
                "name": "TOGGLE_STARTER1",
                "category": "Engine",
                "description": "Toggle engine 1 starter",
            },
            {
                "name": "TOGGLE_STARTER2",
                "category": "Engine",
                "description": "Toggle engine 2 starter",
            },
            {
                "name": "ENGINE_AUTO_START",
                "category": "Engine",
                "description": "Auto-start engines",
            },
            {
                "name": "ENGINE_AUTO_SHUTDOWN",
                "category": "Engine",
                "description": "Auto-shutdown engines",
            },
            {
                "name": "THROTTLE_SET",
                "category": "Engine",
                "description": "Set throttle (parameter: 0-16383)",
            },
            {
                "name": "MIXTURE_SET",
                "category": "Engine",
                "description": "Set mixture (parameter: 0-16383)",
            },
            {
                "name": "PROPELLER_SET",
                "category": "Engine",
                "description": "Set propeller (parameter: 0-16383)",
            },
        ],
        "Lights": [
            {
                "name": "LANDING_LIGHTS_TOGGLE",
                "category": "Lights",
                "description": "Toggle landing lights",
            },
            {"name": "STROBES_TOGGLE", "category": "Lights", "description": "Toggle strobe lights"},
            {
                "name": "TOGGLE_BEACON_LIGHTS",
                "category": "Lights",
                "description": "Toggle beacon lights",
            },
            {
                "name": "TOGGLE_NAV_LIGHTS",
                "category": "Lights",
                "description": "Toggle navigation lights",
            },
            {
                "name": "TOGGLE_TAXI_LIGHTS",
                "category": "Lights",
                "description": "Toggle taxi lights",
            },
        ],
        "Radio": [
            {
                "name": "COM_RADIO_SET",
                "category": "Radio",
                "description": "Set COM1 frequency (parameter: BCD16 encoded frequency)",
            },
            {"name": "COM2_RADIO_SET", "category": "Radio", "description": "Set COM2 frequency"},
            {"name": "NAV1_RADIO_SET", "category": "Radio", "description": "Set NAV1 frequency"},
            {"name": "NAV2_RADIO_SET", "category": "Radio", "description": "Set NAV2 frequency"},
            {
                "name": "XPNDR_SET",
                "category": "Radio",
                "description": "Set transponder code (parameter: BCD16 encoded)",
            },
        ],
        "Simulation": [
            {"name": "PAUSE_TOGGLE", "category": "Simulation", "description": "Toggle pause"},
            {
                "name": "SIM_RATE_INCR",
                "category": "Simulation",
                "description": "Increase simulation rate",
            },
            {
                "name": "SIM_RATE_DECR",
                "category": "Simulation",
                "description": "Decrease simulation rate",
            },
            {
                "name": "FREEZE_LATITUDE_LONGITUDE_TOGGLE",
                "category": "Simulation",
                "description": "Freeze position",
            },
            {
                "name": "FREEZE_ALTITUDE_TOGGLE",
                "category": "Simulation",
                "description": "Freeze altitude",
            },
        ],
    }


def _matching_events(keyword: str, category: str | None = None) -> list[dict]:
    """All events matching keyword/category, uncapped -- callers paginate."""
    _load_event_catalog()
    assert _FLAT_EVENTS is not None
    keyword_lower = keyword.lower()
    results = []
    for evt in _FLAT_EVENTS:
        if category and evt.get("category", "").lower() != category.lower():
            continue
        searchable = f"{evt.get('name', '')} {evt.get('description', '')}".lower()
        if keyword_lower in searchable:
            results.append(evt)
    return results


def _to_dword(parameter: int) -> int:
    """SimConnect event parameters are unsigned DWORDs.

    Events such as AP_VS_VAR_SET_ENGLISH take negative values (descent rates),
    which must be sent as two's complement.
    """
    return parameter & 0xFFFFFFFF


@handle_simconnect_errors
@require_connection
async def trigger_event(
    name: Annotated[
        str,
        Field(description="Event name, e.g. 'PARKING_BRAKES', 'AP_MASTER', "
                          "'THROTTLE_SET'", min_length=1, max_length=128),
    ],
    parameter: Annotated[
        int | None,
        Field(description="Integer parameter for events that take one. Negative "
                          "values are supported (e.g. AP_VS_VAR_SET_ENGLISH)."),
    ] = None,
) -> EventResult | ToolError:
    """Fire a SimConnect event.

    Resolves through the library's 994-event catalog first, then falls back to
    mapping the name directly, so third-party and newer MSFS events work too.

    When using the mapping fallback on a dispatcher-equipped connection,
    exceptions from SimConnect (NAME_UNRECOGNIZED, ERROR) are correlated back
    to the map and send packets to detect non-existent events.
    """
    manager = SimConnectManager()
    name = name.strip().upper()
    payload = _to_dword(parameter) if parameter is not None else None

    def _fire() -> str:
        event = manager.ae.find(name)
        if event is not None:
            event(payload) if payload is not None else event()
            return "catalog"

        # Not in the static catalog -- map it directly.
        # If the dispatcher's registry is available, correlate exceptions
        # to detect non-existent events (MapClientEventToSimEvent succeeds
        # for any string, so a non-None return proves nothing).
        if hasattr(manager.sm, "registry"):
            pending = PendingRequest(request_id=None)
            manager.sm.registry.register(pending)

            try:
                # Hold the lock across both the map and send DLL calls.
                # Either can independently raise an exception that correlates
                # to its own send ID, so both must be bound before the
                # dispatcher thread can deliver exceptions for them.
                with manager.sm.registry.pending_lock:
                    # Map the event name
                    mapped = manager.sm.map_to_sim_event(name.encode("ascii"))
                    if mapped is None:
                        raise LookupError(name)
                    map_send_id = DWORD(0)
                    manager.sm.dll.GetLastSentPacketID(manager.sm.hSimConnect, map_send_id)
                    manager.sm.registry.bind_send_id(pending, map_send_id.value, _locked=True)

                    # Send the event
                    manager.sm.send_event(mapped, DWORD(payload if payload is not None else 0))
                    send_send_id = DWORD(0)
                    manager.sm.dll.GetLastSentPacketID(manager.sm.hSimConnect, send_send_id)
                    manager.sm.registry.bind_send_id(pending, send_send_id.value, _locked=True)

                # Wait for an exception or success
                if pending.done.wait(0.2) and pending.exception is not None:
                    exc = pending.exception
                    # Treat NAME_UNRECOGNIZED and ERROR as "event not found"
                    if exc in (
                        "SIMCONNECT_EXCEPTION_NAME_UNRECOGNIZED",
                        "SIMCONNECT_EXCEPTION_ERROR",
                    ):
                        raise LookupError(name)
                    # Other exceptions should propagate
                    raise RuntimeError(f"SimConnect exception: {exc}")
            finally:
                manager.sm.registry.discard(pending)
        else:
            # Plain SimConnect without dispatcher registry: skip correlation
            # and use the optimistic path (mapping may succeed or fail, but we
            # have no exception correlation so we can't tell).
            mapped = manager.sm.map_to_sim_event(name.encode("ascii"))
            if mapped is None:
                raise LookupError(name)
            manager.sm.send_event(mapped, DWORD(payload if payload is not None else 0))

        return "mapped"

    try:
        resolved_via = await manager.run_sync(_fire)
    except LookupError:
        return ToolError(
            error="EVENT_NOT_FOUND",
            message=f"SimConnect could not map event '{name}'.",
            suggestion=(
                "Check the name with msfs_search_events. For aircraft-specific "
                "controls use msfs_trigger_custom_event or msfs_execute_calculator_code."
            ),
        )

    return EventResult(
        event=name,
        parameter=parameter,
        resolved_via=resolved_via,
        message=f"Event '{name}' triggered successfully",
    )


EVENT_COLUMNS = [("name", "Event"), ("category", "Category"), ("description", "Description")]


@handle_simconnect_errors
async def search_events(
    keyword: Annotated[
        str, Field(description="Search term, e.g. 'autopilot', 'light', 'engine'",
                   min_length=1, max_length=100)
    ],
    category: Annotated[
        str | None, Field(description="Restrict to one category, e.g. 'Autopilot'")
    ] = None,
    limit: Annotated[int, Field(description="Maximum results", ge=1, le=200)] = DEFAULT_LIMIT,
    offset: Annotated[int, Field(description="Results to skip, for paging", ge=0)] = 0,
    response_format: Annotated[
        ResponseFormat, Field(description="'markdown' for a compact table, 'json' for rows")
    ] = ResponseFormat.MARKDOWN,
) -> SearchResult | ToolError:
    """Search SimConnect events by keyword, optionally filtered by category.

    Spans the library's full 994-event catalog, not just the builtin
    fallback list. Results are paginated.
    """
    rows = _matching_events(keyword, category)
    return build_search_result(
        rows, offset, limit, response_format, EVENT_COLUMNS,
        title=f"Events matching '{keyword}'",
        query=keyword, filters={"category": category},
    )


@handle_simconnect_errors
@require_connection
async def trigger_custom_event(
    name: Annotated[
        str,
        Field(description="Custom event name, e.g. "
                          "'MobiFlight.AS1000_PFD_SOFTKEYS_1'",
              min_length=1, max_length=128),
    ],
    parameter: Annotated[
        int | None, Field(description="Optional integer parameter")
    ] = None,
) -> EventResult | ToolError:
    """Fire a custom event as a key event through the MobiFlight WASM module.

    Requires the MobiFlight WASM module. The event is delivered via the WASM
    module's RPN interface -- `(>K:NAME)`, or `PARAM (>K:NAME)` when a
    parameter is given -- not through native SimConnect event mapping (that
    is msfs_trigger_event). This reaches events outside the standard
    SimConnect catalog.

    An aircraft with its own event system (PMDG, Fenix) may silently ignore
    a default key event in favour of its own SDK: measured live against a
    PMDG 737, `PARKING_BRAKES` delivered through this path had no effect on
    the aircraft's brake state, while a sim-level event (no aircraft can
    intercept it) delivered correctly. A no-op result on such aircraft means
    the aircraft ignored the event, not that this tool failed.
    """
    manager = SimConnectManager()

    if not manager.mobiflight_available:
        return ToolError(
            error="MOBIFLIGHT_NOT_AVAILABLE",
            message="MobiFlight WASM extension is not available.",
            suggestion=(
                "Install MSFSPythonSimConnectMobiFlightExtension and the MobiFlight WASM "
                "module in MSFS."
            ),
        )

    def _fire() -> None:
        rpn = f"{parameter} (>K:{name})" if parameter is not None else f"(>K:{name})"
        manager.mobiflight.set(rpn)

    await manager.run_sync(_fire)
    return EventResult(
        event=name,
        parameter=parameter,
        custom=True,
        # Not "triggered successfully": the MobiFlight WASM bridge writes to
        # a client data area with no response channel read (see
        # MobiFlightVariableRequests.set/send_command in
        # vendor/mobiflight_variable_requests.py), so nothing here confirms
        # the event actually fired -- only that it was sent.
        message=f"Custom event '{name}' sent; delivery is not confirmed",
    )
