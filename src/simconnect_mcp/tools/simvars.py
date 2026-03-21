"""SimVar tools — get, set, search, bulk read, watch."""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

from simconnect_mcp.connection import SimConnectManager
from simconnect_mcp.tools import handle_simconnect_errors, require_connection

# --- SimVar catalog (loaded once from embedded JSON-like data) ---

_SIMVAR_CATALOG: dict[str, list[dict]] | None = None
_FLAT_SIMVARS: list[dict] | None = None


def _load_catalog() -> dict[str, list[dict]]:
    """Load SimVar catalog from bundled JSON, SimConnect package, or builtin fallback."""
    global _SIMVAR_CATALOG, _FLAT_SIMVARS
    if _SIMVAR_CATALOG is not None:
        return _SIMVAR_CATALOG

    catalog: dict[str, list[dict]] = {}

    # 1. Try bundled JSON catalog (comprehensive, ~850 vars)
    json_path = Path(__file__).parent.parent / "data" / "simvars_catalog.json"
    if json_path.exists():
        try:
            raw = json.loads(json_path.read_text(encoding="utf-8"))
            for category_name, vars_list in raw.items():
                entries = []
                for v in vars_list:
                    entries.append({
                        "name": v["name"],
                        "category": category_name,
                        "description": v.get("description", ""),
                        "units": v.get("unit", ""),
                        "settable": v.get("settable", False),
                    })
                if entries:
                    catalog[category_name] = entries
        except Exception:
            catalog = {}

    # 2. Fallback: try SimConnect package's AircraftRequests data
    if not catalog:
        try:
            from SimConnect.RequestList import AircraftRequests
            import re as _re

            # Parse the inner class list dicts without instantiating (needs no connection)
            import inspect
            source = inspect.getsource(AircraftRequests)
            for match in _re.finditer(
                r'"(\w[\w:]*)":\s*\["([^"]*)",\s*b\'([^\']*)\',\s*b\'([^\']*)\',\s*\'([YN])\'\]',
                source,
            ):
                name, desc, _simvar, unit, settable = match.groups()
                cat = "SimConnect"
                entry = {
                    "name": name,
                    "category": cat,
                    "description": desc,
                    "units": unit,
                    "settable": settable == "Y",
                }
                catalog.setdefault(cat, []).append(entry)
        except Exception:
            pass

    # 3. Last resort: minimal builtin catalog
    if not catalog:
        catalog = _builtin_catalog()

    _SIMVAR_CATALOG = catalog
    _FLAT_SIMVARS = [v for cats in catalog.values() for v in cats]
    return catalog


def _builtin_catalog() -> dict[str, list[dict]]:
    """Minimal built-in SimVar catalog for common variables."""
    return {
        "Aircraft Position": [
            {"name": "PLANE_LATITUDE", "category": "Aircraft Position", "units": "degrees", "settable": True, "description": "Latitude of aircraft"},
            {"name": "PLANE_LONGITUDE", "category": "Aircraft Position", "units": "degrees", "settable": True, "description": "Longitude of aircraft"},
            {"name": "PLANE_ALTITUDE", "category": "Aircraft Position", "units": "feet", "settable": True, "description": "Altitude of aircraft"},
            {"name": "PLANE_HEADING_DEGREES_TRUE", "category": "Aircraft Position", "units": "degrees", "settable": False, "description": "Heading relative to true north"},
            {"name": "PLANE_HEADING_DEGREES_MAGNETIC", "category": "Aircraft Position", "units": "degrees", "settable": False, "description": "Heading relative to magnetic north"},
            {"name": "GROUND_ALTITUDE", "category": "Aircraft Position", "units": "feet", "settable": False, "description": "Altitude of ground below aircraft"},
            {"name": "SIM_ON_GROUND", "category": "Aircraft Position", "units": "bool", "settable": False, "description": "Whether aircraft is on the ground"},
        ],
        "Aircraft Speed": [
            {"name": "AIRSPEED_INDICATED", "category": "Aircraft Speed", "units": "knots", "settable": False, "description": "Indicated airspeed"},
            {"name": "AIRSPEED_TRUE", "category": "Aircraft Speed", "units": "knots", "settable": False, "description": "True airspeed"},
            {"name": "GROUND_VELOCITY", "category": "Aircraft Speed", "units": "knots", "settable": False, "description": "Ground speed"},
            {"name": "VERTICAL_SPEED", "category": "Aircraft Speed", "units": "feet per minute", "settable": False, "description": "Vertical speed"},
        ],
        "Aircraft Controls": [
            {"name": "ELEVATOR_POSITION", "category": "Aircraft Controls", "units": "position", "settable": True, "description": "Elevator position"},
            {"name": "AILERON_POSITION", "category": "Aircraft Controls", "units": "position", "settable": True, "description": "Aileron position"},
            {"name": "RUDDER_POSITION", "category": "Aircraft Controls", "units": "position", "settable": True, "description": "Rudder position"},
            {"name": "FLAPS_HANDLE_INDEX", "category": "Aircraft Controls", "units": "number", "settable": True, "description": "Flaps handle position index"},
            {"name": "GEAR_HANDLE_POSITION", "category": "Aircraft Controls", "units": "bool", "settable": True, "description": "Landing gear handle position"},
            {"name": "SPOILERS_HANDLE_POSITION", "category": "Aircraft Controls", "units": "percent", "settable": True, "description": "Spoilers handle position"},
        ],
        "Aircraft Engine": [
            {"name": "GENERAL_ENG_THROTTLE_LEVER_POSITION:index", "category": "Aircraft Engine", "units": "percent", "settable": True, "description": "Throttle lever position (indexed by engine)"},
            {"name": "ENG_N1_RPM:index", "category": "Aircraft Engine", "units": "percent", "settable": False, "description": "Engine N1 RPM percentage"},
            {"name": "ENG_N2_RPM:index", "category": "Aircraft Engine", "units": "percent", "settable": False, "description": "Engine N2 RPM percentage"},
            {"name": "FUEL_TOTAL_QUANTITY", "category": "Aircraft Engine", "units": "gallons", "settable": False, "description": "Total fuel quantity"},
            {"name": "FUEL_TOTAL_QUANTITY_WEIGHT", "category": "Aircraft Engine", "units": "pounds", "settable": False, "description": "Total fuel weight"},
        ],
        "Aircraft Electrical": [
            {"name": "ELECTRICAL_MASTER_BATTERY", "category": "Aircraft Electrical", "units": "bool", "settable": False, "description": "Master battery switch"},
            {"name": "ELECTRICAL_AVIONICS_BUS_VOLTAGE", "category": "Aircraft Electrical", "units": "volts", "settable": False, "description": "Avionics bus voltage"},
            {"name": "GENERAL_ENG_GENERATOR_ACTIVE:index", "category": "Aircraft Electrical", "units": "bool", "settable": False, "description": "Generator active (indexed)"},
        ],
        "Autopilot": [
            {"name": "AUTOPILOT_MASTER", "category": "Autopilot", "units": "bool", "settable": False, "description": "Autopilot master switch"},
            {"name": "AUTOPILOT_HEADING_LOCK_DIR", "category": "Autopilot", "units": "degrees", "settable": True, "description": "Autopilot heading bug direction"},
            {"name": "AUTOPILOT_ALTITUDE_LOCK_VAR", "category": "Autopilot", "units": "feet", "settable": True, "description": "Autopilot target altitude"},
            {"name": "AUTOPILOT_VERTICAL_HOLD_VAR", "category": "Autopilot", "units": "feet per minute", "settable": True, "description": "Autopilot target vertical speed"},
            {"name": "AUTOPILOT_AIRSPEED_HOLD_VAR", "category": "Autopilot", "units": "knots", "settable": True, "description": "Autopilot target airspeed"},
        ],
        "Environment": [
            {"name": "AMBIENT_TEMPERATURE", "category": "Environment", "units": "celsius", "settable": False, "description": "Outside air temperature"},
            {"name": "AMBIENT_WIND_VELOCITY", "category": "Environment", "units": "knots", "settable": False, "description": "Wind speed"},
            {"name": "AMBIENT_WIND_DIRECTION", "category": "Environment", "units": "degrees", "settable": False, "description": "Wind direction"},
            {"name": "BAROMETER_PRESSURE", "category": "Environment", "units": "millibars", "settable": False, "description": "Barometric pressure"},
            {"name": "SEA_LEVEL_PRESSURE", "category": "Environment", "units": "millibars", "settable": False, "description": "Sea level pressure"},
        ],
        "Miscellaneous": [
            {"name": "TITLE", "category": "Miscellaneous", "units": "string", "settable": False, "description": "Aircraft title"},
            {"name": "ATC_TYPE", "category": "Miscellaneous", "units": "string", "settable": False, "description": "ATC aircraft type"},
            {"name": "ATC_ID", "category": "Miscellaneous", "units": "string", "settable": False, "description": "ATC aircraft ID"},
            {"name": "SIMULATION_RATE", "category": "Miscellaneous", "units": "number", "settable": False, "description": "Current simulation rate"},
            {"name": "ABSOLUTE_TIME", "category": "Miscellaneous", "units": "seconds", "settable": False, "description": "Absolute simulation time"},
            {"name": "ZULU_TIME", "category": "Miscellaneous", "units": "seconds", "settable": False, "description": "Zulu time of day"},
            {"name": "LOCAL_TIME", "category": "Miscellaneous", "units": "seconds", "settable": False, "description": "Local time of day"},
        ],
    }


def _search_catalog(keyword: str, category: str | None = None) -> list[dict]:
    """Search the SimVar catalog by keyword, optionally filtered by category."""
    _load_catalog()
    assert _FLAT_SIMVARS is not None

    keyword_lower = keyword.lower()
    results = []
    for var in _FLAT_SIMVARS:
        if category and var.get("category", "").lower() != category.lower():
            continue
        searchable = f"{var.get('name', '')} {var.get('description', '')}".lower()
        if keyword_lower in searchable:
            results.append(var)
    return results[:50]  # Cap results


def _fuzzy_suggest(name: str) -> list[str]:
    """Return up to 5 similar SimVar names for typo suggestions."""
    _load_catalog()
    assert _FLAT_SIMVARS is not None

    name_lower = name.lower().replace(" ", "_")
    suggestions = []
    for var in _FLAT_SIMVARS:
        var_name = var["name"].lower()
        # Simple substring match for suggestions
        if name_lower[:4] in var_name or var_name[:4] in name_lower:
            suggestions.append(var["name"])
            if len(suggestions) >= 5:
                break
    return suggestions


@handle_simconnect_errors
@require_connection
async def get_simvar(name: str, unit: str | None = None, index: int | None = None) -> dict:
    """Read a SimVar value by name.

    Args:
        name: SimVar name (e.g., 'PLANE_LATITUDE', 'AIRSPEED_INDICATED')
        unit: Optional unit override (e.g., 'degrees', 'knots', 'feet')
        index: Optional index for indexed SimVars (e.g., engine number 1-4)

    Returns:
        Dict with the variable name, value, and unit.
    """
    manager = SimConnectManager()

    def _read() -> Any:
        # Build the request key
        key = name
        if index is not None:
            key = f"{name}:{index}"

        # Try via AircraftRequests first
        try:
            value = manager.aq.get(key)
            if value is not None:
                return value
        except Exception:
            pass

        # Fallback: direct SimConnect data request
        from SimConnect.Constants import DATATYPE_FLOAT64
        req_name = key.replace(":", "_")
        manager.sm.add_to_data_definition(
            manager.sm.new_data_definition(), key, None, DATATYPE_FLOAT64
        )
        return None

    value = await manager.run_sync(_read)

    if value is None:
        suggestions = _fuzzy_suggest(name)
        result: dict[str, Any] = {
            "status": "error",
            "error": "SIMVAR_NOT_FOUND",
            "message": f"Could not read SimVar '{name}'",
        }
        if suggestions:
            result["suggestions"] = suggestions
        return result

    return {
        "status": "ok",
        "name": name,
        "value": value,
        "unit": unit or "default",
        "index": index,
    }


@handle_simconnect_errors
@require_connection
async def set_simvar(name: str, value: float, unit: str | None = None, index: int | None = None) -> dict:
    """Write a value to a settable SimVar.

    Args:
        name: SimVar name (must be settable)
        value: Value to write
        unit: Optional unit (e.g., 'degrees', 'feet')
        index: Optional index for indexed SimVars

    Returns:
        Confirmation dict.
    """
    manager = SimConnectManager()

    def _write() -> bool:
        key = name
        if index is not None:
            key = f"{name}:{index}"
        try:
            manager.aq.set(key, value)
            return True
        except Exception as e:
            raise RuntimeError(f"Failed to set SimVar '{name}': {e}") from e

    success = await manager.run_sync(_write)

    return {
        "status": "ok",
        "name": name,
        "value_set": value,
        "unit": unit or "default",
        "index": index,
    }


@handle_simconnect_errors
@require_connection
async def get_simvar_bulk(variables: list[dict]) -> dict:
    """Read multiple SimVars at once.

    Args:
        variables: List of dicts, each with 'name' and optional 'unit', 'index'.
                   Example: [{"name": "PLANE_LATITUDE"}, {"name": "AIRSPEED_INDICATED", "unit": "knots"}]

    Returns:
        Dict with results for each variable.
    """
    manager = SimConnectManager()
    results = {}

    def _read_all() -> dict:
        out = {}
        for var in variables:
            var_name = var["name"]
            idx = var.get("index")
            key = f"{var_name}:{idx}" if idx else var_name
            try:
                val = manager.aq.get(key)
                out[var_name] = {"value": val, "unit": var.get("unit", "default")}
            except Exception as e:
                out[var_name] = {"error": str(e)}
        return out

    results = await manager.run_sync(_read_all)
    return {"status": "ok", "variables": results}


@handle_simconnect_errors
async def search_simvars(keyword: str, category: str | None = None) -> dict:
    """Search SimVars by keyword, optionally filtered by category.

    Args:
        keyword: Search term (e.g., 'altitude', 'engine', 'heading')
        category: Optional category filter (e.g., 'Aircraft Position')

    Returns:
        Dict with matching SimVars.
    """
    results = _search_catalog(keyword, category)
    return {
        "status": "ok",
        "count": len(results),
        "results": results,
        "keyword": keyword,
        "category": category,
    }


@handle_simconnect_errors
async def list_simvar_categories() -> dict:
    """List all SimVar categories with variable counts.

    Returns:
        Dict mapping category names to their variable count.
    """
    catalog = _load_catalog()
    categories = {name: len(vars_) for name, vars_ in catalog.items()}
    return {
        "status": "ok",
        "categories": categories,
        "total_variables": sum(categories.values()),
    }


@handle_simconnect_errors
@require_connection
async def watch_simvar(
    name: str,
    unit: str | None = None,
    index: int | None = None,
    interval_ms: int = 500,
    duration_s: int = 5,
) -> dict:
    """Monitor a SimVar over time, returning a time-series for debugging.

    Args:
        name: SimVar name to watch
        unit: Optional unit override
        index: Optional index for indexed SimVars
        interval_ms: Polling interval in milliseconds (default 500)
        duration_s: Total duration in seconds (default 5, max 30)

    Returns:
        Time-series of values with timestamps.
    """
    duration_s = min(duration_s, 30)
    interval_s = max(interval_ms / 1000.0, 0.05)
    manager = SimConnectManager()

    samples: list[dict] = []
    start = time.monotonic()

    while (time.monotonic() - start) < duration_s:
        def _read() -> Any:
            key = f"{name}:{index}" if index else name
            return manager.aq.get(key)

        value = await manager.run_sync(_read)
        samples.append({
            "t": round(time.monotonic() - start, 3),
            "value": value,
        })
        await asyncio.sleep(interval_s)

    return {
        "status": "ok",
        "name": name,
        "unit": unit or "default",
        "samples": samples,
        "duration_s": duration_s,
        "interval_ms": interval_ms,
    }
