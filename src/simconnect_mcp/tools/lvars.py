"""L-Var tools — read, write, list, and execute calculator code via MobiFlight.

The MobiFlight WASM bridge uses RPN-style variable strings:
  - Read: vr.get("(L:VarName)")  returns float
  - Write: vr.set("value (>L:VarName)")  sends RPN command
  - Calculator: vr.set("rpn code here")  executes arbitrary RPN
  - SimVars via RPN: vr.get("(A:PLANE ALTITUDE,Feet)")
"""

from __future__ import annotations

from simconnect_mcp.connection import SimConnectManager
from simconnect_mcp.tools import handle_simconnect_errors, require_connection


def _require_mobiflight() -> dict | None:
    """Check MobiFlight availability. Returns error dict or None."""
    manager = SimConnectManager()
    if not manager.mobiflight_available:
        return {
            "status": "error",
            "error": "MOBIFLIGHT_NOT_AVAILABLE",
            "message": "MobiFlight WASM extension is not available. L-var operations require it.",
            "suggestion": (
                "Ensure the MobiFlight WASM module is installed in your MSFS Community folder "
                "and that the vendored SimConnectMobiFlight extension loaded successfully."
            ),
        }
    return None


@handle_simconnect_errors
@require_connection
async def get_lvar(name: str) -> dict:
    """Read an L-var (local variable) value from the current aircraft.

    L-vars are aircraft-specific local variables used by add-on developers.
    Requires MobiFlight WASM extension.

    Args:
        name: L-var name (e.g., 'A32NX_EFIS_L_OPTION', 'WT_CJ4_HDG_ON').
              The 'L:' prefix and RPN wrapping are added automatically if missing.

    Returns:
        Dict with the L-var name and value.
    """
    err = _require_mobiflight()
    if err:
        return err

    manager = SimConnectManager()

    # Normalize to RPN format: (L:VarName)
    rpn_key = _to_lvar_rpn(name)

    def _read() -> float | None:
        return manager.mobiflight.get(rpn_key)

    value = await manager.run_sync(_read)
    return {
        "status": "ok",
        "name": name,
        "rpn": rpn_key,
        "value": value,
    }


@handle_simconnect_errors
@require_connection
async def set_lvar(name: str, value: float) -> dict:
    """Write a value to an L-var on the current aircraft.

    Uses native SimConnect data definitions (AddToDataDefinition +
    SetDataOnSimObject) which works with proprietary aircraft like the
    Fenix A320/A321. Does NOT require MobiFlight.

    Args:
        name: L-var name (the 'L:' prefix is optional)
        value: Numeric value to set

    Returns:
        Confirmation dict.
    """
    manager = SimConnectManager()
    bare_name = _bare_lvar_name(name)

    def _write() -> None:
        manager.set_lvar(bare_name, value)

    await manager.run_sync(_write)
    return {
        "status": "ok",
        "name": bare_name,
        "value_set": value,
    }


@handle_simconnect_errors
@require_connection
async def list_lvars() -> dict:
    """Enumerate active L-vars on the current aircraft.

    Uses the MobiFlight WASM module's ListLVars command to request the
    sim to list all registered L-vars. Note: this depends on the WASM
    module version supporting this command.

    Requires MobiFlight WASM extension.

    Returns:
        Dict with list of L-var names and count, or instructions if
        the listing command is not supported.
    """
    err = _require_mobiflight()
    if err:
        return err

    manager = SimConnectManager()

    def _list() -> None:
        # Send the list command — results come back asynchronously
        # via the WASM response channel
        manager.mobiflight.send_command("MF.LVars.List")

    await manager.run_sync(_list)

    return {
        "status": "ok",
        "message": (
            "L-var list command sent to WASM module. "
            "Note: The MobiFlight WASM module may not support listing in all versions. "
            "If no list is returned, try reading specific L-vars by name using get_lvar(). "
            "Common prefixes for popular aircraft: A32NX_ (FBW A320), WT_CJ4_ (Working Title CJ4), "
            "AS1000_ (G1000), ASCRJ_ (Aerosoft CRJ)."
        ),
    }


@handle_simconnect_errors
@require_connection
async def execute_calculator_code(code: str) -> dict:
    """Execute RPN calculator code in the simulator.

    This runs arbitrary RPN (Reverse Polish Notation) calculator code
    via the MobiFlight WASM bridge. Can read/write any variable type
    and perform complex operations.

    Requires MobiFlight WASM extension.

    Args:
        code: RPN calculator code string.
              Examples:
                - "(A:PLANE ALTITUDE, feet)" — read a SimVar via RPN
                - "(L:MyCustomVar) 1 + (>L:MyCustomVar)" — increment an L-var
                - "1 (>K:PARKING_BRAKES)" — trigger an event

    Returns:
        Dict with execution status. To read a value, use get_lvar()
        or pass a read expression to this function.
    """
    err = _require_mobiflight()
    if err:
        return err

    manager = SimConnectManager()

    # If the code looks like a read expression, use get() to return a value
    code_stripped = code.strip()
    is_read = (
        code_stripped.startswith("(")
        and code_stripped.endswith(")")
        and "(>" not in code_stripped
    )

    if is_read:
        def _read() -> float | None:
            return manager.mobiflight.get(code_stripped)

        value = await manager.run_sync(_read)
        return {
            "status": "ok",
            "code": code,
            "mode": "read",
            "value": value,
        }
    else:
        def _execute() -> None:
            manager.mobiflight.set(code_stripped)

        await manager.run_sync(_execute)
        return {
            "status": "ok",
            "code": code,
            "mode": "execute",
            "message": "Calculator code executed successfully",
        }


def _no_detection_message(covered: str, valid_keys: set[str]) -> str:
    """Actionable explanation for when aircraft auto-detection finds nothing.

    Never silently guess a catalog: say what happened and how to fix it.
    """
    available = ", ".join(sorted(valid_keys))
    return (
        f"No aircraft catalog was auto-detected, so {covered}. "
        f"Pass catalog=<key> to scope it (available: {available}), or call "
        "list_lvar_catalogs() for details."
    )


def _unknown_catalog_error(catalog: str, valid_keys: set[str]) -> dict:
    """Error dict for an explicit but unrecognized `catalog` argument.

    An invalid explicit key must error, not silently fall back to searching
    everything under the caller's requested (wrong) label.
    """
    return {
        "status": "error",
        "error": "CATALOG_NOT_FOUND",
        "message": f"Unknown catalog '{catalog}'.",
        "suggestion": (
            f"Use one of: {', '.join(sorted(valid_keys))} (see list_lvar_catalogs())."
        ),
    }


@handle_simconnect_errors
async def search_lvars(
    keyword: str,
    category: str | None = None,
    writable_only: bool = False,
    prefix: str | None = None,
    catalog: str | None = None,
) -> dict:
    """Search known aircraft L-vars by keyword.

    Searches the embedded L-var catalog for the current aircraft (auto-detected
    from TITLE/ATC_MODEL) or all known aircraft if none is loaded or detected.

    Args:
        keyword: Search term (e.g., 'seatbelt', 'autopilot', 'heading', 'fuel')
        category: Filter by panel/system category (e.g., 'Signs', 'FCU', 'Electrical')
        writable_only: Only return variables that can be written to
        prefix: Filter by Fenix prefix type: S (switch), N (numeric readout),
                E (event counter), I (indicator), A (analog), B (boolean indicator)
        catalog: Explicit catalog key to search (see list_lvar_catalogs()),
                 overriding auto-detection. Use this when the loaded aircraft
                 isn't auto-detected, or to search a specific aircraft's
                 catalog regardless of what's loaded.

    Returns:
        Matching L-vars with names, display names, categories, and valid
        values. When no aircraft catalog could be auto-detected and `catalog`
        was not given, `catalog` is "all" and `message` explains how to
        scope the search.
    """
    from simconnect_mcp.data.catalog import detect_catalog, list_catalogs, search_catalog

    valid_keys = {c["key"] for c in list_catalogs()}
    if catalog is not None and catalog not in valid_keys:
        return _unknown_catalog_error(catalog, valid_keys)

    # Explicit catalog wins outright -- no need to touch the sim at all.
    catalog_key = catalog
    auto_detect_failed = False
    if catalog_key is None:
        manager = SimConnectManager()
        title, model = await manager.detect_aircraft_identity()
        catalog_key = detect_catalog(title, model)
        auto_detect_failed = catalog_key is None

    results = search_catalog(
        keyword,
        catalog_key=catalog_key,
        category=category,
        writable_only=writable_only,
        prefix=prefix,
    )
    response = {
        "status": "ok",
        "count": len(results),
        "results": results,
        "keyword": keyword,
        "catalog": catalog_key or "all",
        "filters": {
            "category": category,
            "writable_only": writable_only,
            "prefix": prefix,
        },
    }
    if auto_detect_failed:
        response["message"] = _no_detection_message("this searched all catalogs", valid_keys)
    return response


@handle_simconnect_errors
async def list_lvar_panels(category: str | None = None, catalog: str | None = None) -> dict:
    """List available L-var panels/categories for the current aircraft.

    If a category name is provided, returns all variables in that panel
    with their full definitions and valid values.

    Args:
        category: Optional panel name to get details for (e.g., 'Signs', 'FCU',
                  'Electrical', 'Lights'). Omit to list all panels.
        catalog: Explicit catalog key to use (see list_lvar_catalogs()),
                 overriding auto-detection from the loaded aircraft.

    Returns:
        Panel listing or detailed panel variable info. When no aircraft
        catalog could be auto-detected and `catalog` was not given, panels
        from all catalogs are listed and `message` explains how to scope it.
    """
    from simconnect_mcp.data.catalog import (
        detect_catalog,
        get_panel_variables,
        list_catalogs,
        list_panels,
    )

    valid_keys = {c["key"] for c in list_catalogs()}
    if catalog is not None and catalog not in valid_keys:
        return _unknown_catalog_error(catalog, valid_keys)

    # Explicit catalog wins outright -- no need to touch the sim at all.
    catalog_key = catalog
    auto_detect_failed = False
    if catalog_key is None:
        manager = SimConnectManager()
        title, model = await manager.detect_aircraft_identity()
        catalog_key = detect_catalog(title, model)
        auto_detect_failed = catalog_key is None

    if category:
        panel = get_panel_variables(category, catalog_key)
        if panel is None:
            return {
                "status": "error",
                "error": "PANEL_NOT_FOUND",
                "message": f"No panel matching '{category}' found.",
                "suggestion": "Use list_lvar_panels() without arguments to see available panels.",
            }
        return {"status": "ok", **panel}
    else:
        panels = list_panels(catalog_key)
        response = {
            "status": "ok",
            "catalog": catalog_key or "all",
            "count": len(panels),
            "panels": panels,
        }
        if auto_detect_failed:
            response["message"] = _no_detection_message(
                "this listed panels from all catalogs", valid_keys
            )
        return response


@handle_simconnect_errors
async def list_lvar_catalogs() -> dict:
    """List all available aircraft L-var catalogs.

    Returns which aircraft have known L-var catalogs with variable counts.
    """
    from simconnect_mcp.data.catalog import list_catalogs
    catalogs = list_catalogs()
    return {
        "status": "ok",
        "catalogs": catalogs,
    }


def _to_lvar_rpn(name: str) -> str:
    """Convert an L-var name to RPN read format: (L:VarName)."""
    name = name.strip()
    # Already in RPN format
    if name.startswith("(") and name.endswith(")"):
        return name
    # Has L: prefix but no parens
    if name.startswith("L:"):
        return f"({name})"
    # Bare name
    return f"(L:{name})"


def _bare_lvar_name(name: str) -> str:
    """Extract bare L-var name from various formats."""
    name = name.strip()
    if name.startswith("(L:") and name.endswith(")"):
        return name[3:-1]
    if name.startswith("L:"):
        return name[2:]
    return name
