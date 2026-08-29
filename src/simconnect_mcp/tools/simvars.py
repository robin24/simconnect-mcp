"""SimVar tools — get, set, search, bulk read, watch."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from simconnect_mcp.connection import SimConnectManager
from simconnect_mcp.data.simvar_catalog import (
    load_catalog,
    resolve_unit,
    search_catalog,
    suggest_names,
)
from simconnect_mcp.simvar_access import (
    SimVarNotFoundError,
    SimVarTimeoutError,
    UnitMismatchError,
)
from simconnect_mcp.tools import handle_simconnect_errors, require_connection


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
