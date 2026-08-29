"""SimVar tools — get, set, search, bulk read, watch."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from simconnect_mcp.connection import SimConnectManager
from simconnect_mcp.data.simvar_catalog import (
    load_catalog,
    lookup,
    resolve_unit,
    search_catalog,
    suggest_names,
)
from simconnect_mcp.simvar_access import (
    SimVarError,
    SimVarNotFoundError,
    SimVarNotSettableError,
    SimVarTimeoutError,
    UnitMismatchError,
)
from simconnect_mcp.tools import handle_simconnect_errors, require_connection


def _disambiguate_not_found(name: str, unit: str | None) -> dict | None:
    """Tell a bad unit apart from a bad name.

    SimConnect raises the same NAME_UNRECOGNIZED for an unknown variable and
    for a known variable with an invalid unit (verified against a live sim),
    so the exception alone cannot distinguish them. The catalog can: if we
    know this variable and the caller supplied a unit, the name is fine and
    the unit is what was rejected.

    Returns a UNIT_MISMATCH envelope, or None to fall through to
    SIMVAR_NOT_FOUND. `unit` must be truthy -- with no caller unit we used
    the catalog's own, so a failure there is a genuine name problem.
    """
    if not unit or lookup(name) is None:
        return None
    return {
        "status": "error",
        "error": "UNIT_MISMATCH",
        "message": f"SimConnect rejected unit '{unit}' for SimVar '{name}'.",
        "suggestion": (
            f"'{name}' is measured in '{resolve_unit(name, None)}'. Omit the unit "
            "argument to use that, or pass a compatible SimConnect unit."
        ),
    }


@handle_simconnect_errors
@require_connection
async def get_simvar(name: str, unit: str | None = None, index: int | None = None) -> dict:
    """Read a SimVar value by name.

    Args:
        name: SimVar name (e.g., 'PLANE_LATITUDE', 'AIRSPEED_INDICATED')
        unit: Unit to read in (e.g., 'feet', 'meters', 'knots'). Defaults to
              the catalog unit for this variable, then to 'number'.
        index: Index for indexed SimVars (e.g., engine number). Index 0 is valid.

    Returns:
        Dict with the variable name, value, and the unit actually used.
    """
    manager = SimConnectManager()
    resolved_unit = resolve_unit(name, unit)

    try:
        value = await manager.run_sync(
            lambda: manager.accessor.read(name, unit=unit, index=index)
        )
    except SimVarNotFoundError:
        mismatch = _disambiguate_not_found(name, unit)
        if mismatch is not None:
            return mismatch
        result: dict[str, Any] = {
            "status": "error",
            "error": "SIMVAR_NOT_FOUND",
            "message": f"SimConnect does not recognise SimVar '{name}'",
            "suggestion": "Use search_simvars to find the correct name.",
        }
        suggestions = suggest_names(name)
        if suggestions:
            result["suggestions"] = suggestions
        return result
    except UnitMismatchError as e:
        return {
            "status": "error",
            "error": "UNIT_MISMATCH",
            "message": str(e),
            "suggestion": (
                f"Check the units for '{name}' with search_simvars, "
                "or omit the unit argument to use the catalog default."
            ),
        }
    except SimVarTimeoutError as e:
        return {
            "status": "error",
            "error": "SIM_TIMEOUT",
            "message": str(e),
            "suggestion": "The sim may be paused or loading. Try again shortly.",
        }

    return {
        "status": "ok",
        "name": name,
        "value": value,
        "unit": resolved_unit,
        "index": index,
    }


@handle_simconnect_errors
@require_connection
async def set_simvar(
    name: str, value: float, unit: str | None = None, index: int | None = None
) -> dict:
    """Write a value to a settable SimVar.

    Args:
        name: SimVar name (must be settable)
        value: Value to write
        unit: Unit the value is expressed in. Defaults to the catalog unit.
        index: Index for indexed SimVars. Index 0 is valid.

    Returns:
        Confirmation dict, or an error if the sim rejected the write.
    """
    manager = SimConnectManager()
    resolved_unit = resolve_unit(name, unit)

    try:
        # verify=True re-reads after the write. SimConnect does NOT raise for a
        # write to a read-only variable -- it silently ignores it -- so read-back
        # is the only way to tell the caller whether the value actually landed.
        verified = await manager.run_sync(
            lambda: manager.accessor.write(
                name, value, unit=unit, index=index, verify=True
            )
        )
    except SimVarNotSettableError as e:
        return {
            "status": "error",
            "error": "SIMVAR_NOT_SETTABLE",
            "message": str(e),
            "suggestion": (
                f"'{name}' appears to be read-only. Look for an event that changes it "
                "with search_events, or an aircraft-specific L-var with search_lvars. "
                "Note the catalog's 'settable' flag is unreliable, so a variable it "
                "marks read-only may still accept writes."
            ),
        }
    except SimVarNotFoundError:
        mismatch = _disambiguate_not_found(name, unit)
        if mismatch is not None:
            return mismatch
        result: dict[str, Any] = {
            "status": "error",
            "error": "SIMVAR_NOT_FOUND",
            "message": f"SimConnect does not recognise SimVar '{name}'",
            "suggestion": "Use search_simvars to find the correct name.",
        }
        suggestions = suggest_names(name)
        if suggestions:
            result["suggestions"] = suggestions
        return result
    except UnitMismatchError as e:
        return {
            "status": "error",
            "error": "UNIT_MISMATCH",
            "message": str(e),
            "suggestion": f"Check the valid units for '{name}' with search_simvars.",
        }
    except SimVarTimeoutError as e:
        return {
            "status": "error",
            "error": "SIM_TIMEOUT",
            "message": str(e),
            "suggestion": "The sim may be paused or loading. Try again shortly.",
        }

    result: dict[str, Any] = {
        "status": "ok",
        "name": name,
        "value_set": value,
        "unit": resolved_unit,
        "index": index,
        "verified": verified,
    }
    if verified is False:
        result["warning"] = (
            f"The write was sent but '{name}' did not change. SimConnect does not "
            "reject writes to read-only variables, so this usually means the "
            "variable is not settable. It can also mean the sim immediately "
            "overrode the value."
        )
    return result


@handle_simconnect_errors
@require_connection
async def get_simvar_bulk(variables: list[dict]) -> dict:
    """Read multiple SimVars in one call.

    Args:
        variables: List of dicts with 'name' and optional 'unit' and 'index'.
                   Example: [{"name": "PLANE_LATITUDE"},
                             {"name": "ENG_N1_RPM", "index": 1, "unit": "percent"}]

    Returns:
        Dict keyed by 'NAME' or 'NAME:index', each holding a value or an error.
        A failure on one variable does not abort the others.
    """
    manager = SimConnectManager()
    requests = [
        (var["name"], var.get("unit"), var.get("index"))  # index 0 must survive
        for var in variables
    ]
    results = await manager.run_sync(lambda: manager.accessor.read_many(requests))
    return {"status": "ok", "count": len(results), "variables": results}


@handle_simconnect_errors
async def search_simvars(keyword: str, category: str | None = None) -> dict:
    """Search SimVars by keyword, optionally filtered by category.

    Args:
        keyword: Search term (e.g., 'altitude', 'engine', 'heading')
        category: Optional category filter (e.g., 'Aircraft Position')

    Returns:
        Dict with matching SimVars.
    """
    results = search_catalog(keyword, category)[:50]
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
    catalog = load_catalog()
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
    """Sample a SimVar over time, returning a time series for debugging.

    Args:
        name: SimVar name to watch
        unit: Unit to read in. Defaults to the catalog unit.
        index: Index for indexed SimVars. Index 0 is valid.
        interval_ms: Polling interval in milliseconds (minimum 50)
        duration_s: Total duration in seconds (maximum 30)

    Returns:
        Time series of values with elapsed timestamps.
    """
    duration_s = min(duration_s, 30)
    interval_s = max(interval_ms / 1000.0, 0.05)
    manager = SimConnectManager()
    resolved_unit = resolve_unit(name, unit)

    samples: list[dict] = []
    errors = 0
    start = time.monotonic()

    while (time.monotonic() - start) < duration_s:
        try:
            value = await manager.run_sync(
                lambda: manager.accessor.read(name, unit=unit, index=index)
            )
            samples.append({"t": round(time.monotonic() - start, 3), "value": value})
        except SimVarError as e:
            errors += 1
            if not samples and errors == 1:
                # Fail fast on a name that will never work.
                return {
                    "status": "error",
                    "error": "SIMVAR_NOT_READABLE",
                    "message": str(e),
                    "suggestion": "Check the name with search_simvars.",
                }
        await asyncio.sleep(interval_s)

    return {
        "status": "ok",
        "name": name,
        "unit": resolved_unit,
        "index": index,
        "samples": samples,
        "sample_count": len(samples),
        "error_count": errors,
        "duration_s": duration_s,
        "interval_ms": interval_ms,
    }
