"""L-Var tools — read, write, list, and execute calculator code via MobiFlight.

The MobiFlight WASM bridge uses RPN-style variable strings:
  - Read: vr.get("(L:VarName)")  returns float
  - Write: vr.set("value (>L:VarName)")  sends RPN command
  - Calculator: vr.set("rpn code here")  executes arbitrary RPN
  - SimVars via RPN: vr.get("(A:PLANE ALTITUDE,Feet)")
"""

from __future__ import annotations

from typing import Any

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


@handle_simconnect_errors
async def search_lvars(
    keyword: str,
    category: str | None = None,
    writable_only: bool = False,
    prefix: str | None = None,
) -> dict:
    """Search known aircraft L-vars by keyword.

    Searches the embedded L-var catalog for the current aircraft (auto-detected)
    or all known aircraft. Much more reliable than guessing L-var names.

    Args:
        keyword: Search term (e.g., 'seatbelt', 'autopilot', 'heading', 'fuel')
        category: Filter by panel/system category (e.g., 'Signs', 'FCU', 'Electrical')
        writable_only: Only return variables that can be written to
        prefix: Filter by Fenix prefix type: S (switch), N (numeric readout),
                E (event counter), I (indicator), A (analog), B (boolean indicator)

    Returns:
        Matching L-vars with names, display names, categories, and valid values.
    """
    from simconnect_mcp.data.catalog import search_catalog, detect_catalog

    # Try to auto-detect aircraft catalog
    catalog_key = None
    manager = SimConnectManager()
    if manager.is_connected:
        try:
            title = manager.aq.get("TITLE")
            if title:
                title_str = title.decode() if isinstance(title, bytes) else str(title)
                catalog_key = detect_catalog(title_str)
        except Exception:
            pass

    results = search_catalog(
        keyword,
        catalog_key=catalog_key,
        category=category,
        writable_only=writable_only,
        prefix=prefix,
    )
    return {
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


@handle_simconnect_errors
async def list_lvar_panels(category: str | None = None) -> dict:
    """List available L-var panels/categories for the current aircraft.

    If a category name is provided, returns all variables in that panel
    with their full definitions and valid values.

    Args:
        category: Optional panel name to get details for (e.g., 'Signs', 'FCU',
                  'Electrical', 'Lights'). Omit to list all panels.

    Returns:
        Panel listing or detailed panel variable info.
    """
    from simconnect_mcp.data.catalog import list_panels, get_panel_variables, detect_catalog

    # Try to auto-detect aircraft catalog
    catalog_key = None
    manager = SimConnectManager()
    if manager.is_connected:
        try:
            title = manager.aq.get("TITLE")
            if title:
                title_str = title.decode() if isinstance(title, bytes) else str(title)
                catalog_key = detect_catalog(title_str)
        except Exception:
            pass

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
        return {
            "status": "ok",
            "catalog": catalog_key or "all",
            "count": len(panels),
            "panels": panels,
        }


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
