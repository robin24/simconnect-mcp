"""Event tools — trigger standard and custom SimConnect events."""

from __future__ import annotations

import logging
from typing import Any

from simconnect_mcp.connection import SimConnectManager
from simconnect_mcp.tools import handle_simconnect_errors, require_connection

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
            {"name": "PARKING_BRAKES", "category": "Aircraft Controls", "description": "Toggle parking brakes"},
            {"name": "TOGGLE_FLIGHT_DIRECTOR", "category": "Aircraft Controls", "description": "Toggle flight director"},
            {"name": "FLAPS_UP", "category": "Aircraft Controls", "description": "Retract flaps fully"},
            {"name": "FLAPS_DOWN", "category": "Aircraft Controls", "description": "Extend flaps one notch"},
            {"name": "FLAPS_1", "category": "Aircraft Controls", "description": "Set flaps to position 1"},
            {"name": "FLAPS_2", "category": "Aircraft Controls", "description": "Set flaps to position 2"},
            {"name": "FLAPS_3", "category": "Aircraft Controls", "description": "Set flaps to position 3"},
            {"name": "GEAR_TOGGLE", "category": "Aircraft Controls", "description": "Toggle landing gear"},
            {"name": "SPOILERS_TOGGLE", "category": "Aircraft Controls", "description": "Toggle spoilers"},
            {"name": "SPOILERS_ARM_TOGGLE", "category": "Aircraft Controls", "description": "Toggle spoiler arm"},
        ],
        "Autopilot": [
            {"name": "AP_MASTER", "category": "Autopilot", "description": "Toggle autopilot master"},
            {"name": "AP_HDG_HOLD", "category": "Autopilot", "description": "Toggle heading hold"},
            {"name": "AP_ALT_HOLD", "category": "Autopilot", "description": "Toggle altitude hold"},
            {"name": "AP_NAV1_HOLD", "category": "Autopilot", "description": "Toggle NAV1 hold"},
            {"name": "AP_APR_HOLD", "category": "Autopilot", "description": "Toggle approach hold"},
            {"name": "AP_VS_HOLD", "category": "Autopilot", "description": "Toggle vertical speed hold"},
            {"name": "AP_SPD_VAR_SET", "category": "Autopilot", "description": "Set autopilot speed (parameter: speed in knots)"},
            {"name": "AP_ALT_VAR_SET_ENGLISH", "category": "Autopilot", "description": "Set autopilot altitude (parameter: altitude in feet)"},
            {"name": "HEADING_BUG_SET", "category": "Autopilot", "description": "Set heading bug (parameter: heading in degrees)"},
        ],
        "Electrical": [
            {"name": "TOGGLE_MASTER_BATTERY", "category": "Electrical", "description": "Toggle master battery"},
            {"name": "TOGGLE_MASTER_ALTERNATOR", "category": "Electrical", "description": "Toggle master alternator"},
            {"name": "TOGGLE_AVIONICS_MASTER", "category": "Electrical", "description": "Toggle avionics master"},
        ],
        "Engine": [
            {"name": "TOGGLE_STARTER1", "category": "Engine", "description": "Toggle engine 1 starter"},
            {"name": "TOGGLE_STARTER2", "category": "Engine", "description": "Toggle engine 2 starter"},
            {"name": "ENGINE_AUTO_START", "category": "Engine", "description": "Auto-start engines"},
            {"name": "ENGINE_AUTO_SHUTDOWN", "category": "Engine", "description": "Auto-shutdown engines"},
            {"name": "THROTTLE_SET", "category": "Engine", "description": "Set throttle (parameter: 0-16383)"},
            {"name": "MIXTURE_SET", "category": "Engine", "description": "Set mixture (parameter: 0-16383)"},
            {"name": "PROPELLER_SET", "category": "Engine", "description": "Set propeller (parameter: 0-16383)"},
        ],
        "Lights": [
            {"name": "LANDING_LIGHTS_TOGGLE", "category": "Lights", "description": "Toggle landing lights"},
            {"name": "STROBES_TOGGLE", "category": "Lights", "description": "Toggle strobe lights"},
            {"name": "TOGGLE_BEACON_LIGHTS", "category": "Lights", "description": "Toggle beacon lights"},
            {"name": "TOGGLE_NAV_LIGHTS", "category": "Lights", "description": "Toggle navigation lights"},
            {"name": "TOGGLE_TAXI_LIGHTS", "category": "Lights", "description": "Toggle taxi lights"},
        ],
        "Radio": [
            {"name": "COM_RADIO_SET", "category": "Radio", "description": "Set COM1 frequency (parameter: BCD16 encoded frequency)"},
            {"name": "COM2_RADIO_SET", "category": "Radio", "description": "Set COM2 frequency"},
            {"name": "NAV1_RADIO_SET", "category": "Radio", "description": "Set NAV1 frequency"},
            {"name": "NAV2_RADIO_SET", "category": "Radio", "description": "Set NAV2 frequency"},
            {"name": "XPNDR_SET", "category": "Radio", "description": "Set transponder code (parameter: BCD16 encoded)"},
        ],
        "Simulation": [
            {"name": "PAUSE_TOGGLE", "category": "Simulation", "description": "Toggle pause"},
            {"name": "SIM_RATE_INCR", "category": "Simulation", "description": "Increase simulation rate"},
            {"name": "SIM_RATE_DECR", "category": "Simulation", "description": "Decrease simulation rate"},
            {"name": "FREEZE_LATITUDE_LONGITUDE_TOGGLE", "category": "Simulation", "description": "Freeze position"},
            {"name": "FREEZE_ALTITUDE_TOGGLE", "category": "Simulation", "description": "Freeze altitude"},
        ],
    }


def _search_events(keyword: str, category: str | None = None) -> list[dict]:
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
    return results[:50]


@handle_simconnect_errors
@require_connection
async def trigger_event(name: str, parameter: int | None = None) -> dict:
    """Fire a standard SimConnect event.

    Args:
        name: Event name (e.g., 'PARKING_BRAKES', 'AP_MASTER', 'THROTTLE_SET')
        parameter: Optional integer parameter for events that require one

    Returns:
        Confirmation dict.
    """
    manager = SimConnectManager()

    def _fire() -> None:
        event = manager.ae.find(name)
        if event is None:
            raise ValueError(f"Event '{name}' not found")
        if parameter is not None:
            event(parameter)
        else:
            event()

    await manager.run_sync(_fire)
    return {
        "status": "ok",
        "event": name,
        "parameter": parameter,
        "message": f"Event '{name}' triggered successfully",
    }


@handle_simconnect_errors
async def search_events(keyword: str, category: str | None = None) -> dict:
    """Search SimConnect events by keyword, optionally filtered by category.

    Args:
        keyword: Search term (e.g., 'autopilot', 'light', 'engine')
        category: Optional category filter

    Returns:
        Matching events.
    """
    results = _search_events(keyword, category)
    return {
        "status": "ok",
        "count": len(results),
        "results": results,
        "keyword": keyword,
        "category": category,
    }


@handle_simconnect_errors
@require_connection
async def trigger_custom_event(name: str, parameter: int | None = None) -> dict:
    """Fire a MobiFlight/custom event by name.

    Requires MobiFlight WASM module. Used for custom aircraft events that
    are not in the standard SimConnect event list.

    Args:
        name: Custom event name (e.g., 'MobiFlight.AS1000_PFD_SOFTKEYS_1')
        parameter: Optional integer parameter

    Returns:
        Confirmation dict.
    """
    manager = SimConnectManager()

    if not manager.mobiflight_available:
        return {
            "status": "error",
            "error": "MOBIFLIGHT_NOT_AVAILABLE",
            "message": "MobiFlight WASM extension is not available.",
            "suggestion": "Install MSFSPythonSimConnectMobiFlightExtension and the MobiFlight WASM module in MSFS.",
        }

    def _fire() -> None:
        if parameter is not None:
            manager.mobiflight.trigger_event(name, parameter)
        else:
            manager.mobiflight.trigger_event(name)

    await manager.run_sync(_fire)
    return {
        "status": "ok",
        "event": name,
        "parameter": parameter,
        "custom": True,
        "message": f"Custom event '{name}' triggered successfully",
    }
