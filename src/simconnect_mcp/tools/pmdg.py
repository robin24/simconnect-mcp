"""PMDG 777 tools -- read aircraft state, CDU screens, send events."""

from __future__ import annotations

import asyncio
from typing import Any

from simconnect_mcp.connection import SimConnectManager
from simconnect_mcp.tools import handle_simconnect_errors, require_connection


def _ensure_pmdg_manager():
    """Get or create PmdgDataManager. Returns (manager, error_dict)."""
    from simconnect_mcp.pmdg import PmdgDataManager

    sm_mgr = SimConnectManager()

    if not hasattr(sm_mgr.sm, "register_client_data_handler"):
        return None, {
            "status": "error",
            "error": "MOBIFLIGHT_REQUIRED",
            "message": "PMDG SDK tools require SimConnectMobiFlight.",
            "suggestion": "Ensure the MobiFlight WASM module is installed.",
        }

    if sm_mgr.pmdg is None:
        sm_mgr.pmdg = PmdgDataManager(sm_mgr.sm)
        sm_mgr.sm.register_client_data_handler(sm_mgr.pmdg.client_data_handler)

    return sm_mgr.pmdg, None


@handle_simconnect_errors
@require_connection
async def get_pmdg_var(name: str) -> dict:
    """Read a PMDG 777 aircraft data field by name.

    Uses the PMDG SDK data broadcast to read switch positions, annunciators,
    knob positions, MCP values, fuel quantities, FMC data, and more.

    Requires EnableDataBroadcast=1 in 777_Options.ini.

    Args:
        name: Variable name from the PMDG 777 catalog. Use search_lvars()
              to discover available variables. Examples:
              'ELEC_Battery_Sw_ON', 'MCP_IASMach', 'FUEL_QtyCenter'

    Returns:
        Dict with variable name, value, display name, and category.
    """
    from simconnect_mcp.data.catalog import get_catalog

    pmdg, err = _ensure_pmdg_manager()
    if err:
        return err

    catalog = get_catalog("pmdg_777")
    if catalog is None:
        return {
            "status": "error",
            "error": "CATALOG_NOT_FOUND",
            "message": "PMDG 777 catalog not loaded.",
        }

    # Find the variable entry
    var_entry = None
    for var in catalog["variables"]:
        if var["name"] == name:
            var_entry = var
            break

    if var_entry is None:
        return {
            "status": "error",
            "error": "FIELD_NOT_FOUND",
            "message": f"Variable '{name}' not found in PMDG 777 catalog.",
            "suggestion": "Use search_lvars() to find available variables.",
        }

    sdk_field = var_entry.get("sdk_field")
    sdk_index = var_entry.get("sdk_index")
    sdk_type = var_entry.get("sdk_type")

    if sdk_field is None or sdk_type in ("event", "lvar"):
        return {
            "status": "error",
            "error": "NOT_A_DATA_FIELD",
            "message": f"'{name}' is a {sdk_type}, not a readable data field. "
                       "Use send_pmdg_event for events, or get_lvar for L-vars.",
        }

    manager = SimConnectManager()

    def _subscribe_and_request():
        pmdg.subscribe_data()
        pmdg.request_data()

    await manager.run_sync(_subscribe_and_request)

    # Wait for data to arrive
    for _ in range(20):
        if pmdg.data_age < 5.0:
            break
        await asyncio.sleep(0.1)

    if pmdg.data_age == float("inf"):
        return {
            "status": "error",
            "error": "NO_DATA",
            "message": "No data received from PMDG 777.",
            "suggestion": "Ensure EnableDataBroadcast=1 is set in 777_Options.ini and restart the sim.",
        }

    def _read():
        return pmdg.read_field(sdk_field, index=sdk_index)

    value = await manager.run_sync(_read)

    # Convert bytes for JSON
    if isinstance(value, bytes):
        value = value.decode("ascii", errors="replace").rstrip("\x00")

    result = {
        "status": "ok",
        "name": name,
        "value": value,
        "display_name": var_entry.get("display_name", name),
        "category": var_entry.get("category", ""),
    }

    values_map = var_entry.get("values")
    if values_map and str(value) in values_map:
        result["value_description"] = values_map[str(value)]

    if pmdg.data_age > 5.0:
        result["warning"] = f"Data may be stale ({pmdg.data_age:.1f}s since last update)"

    return result


@handle_simconnect_errors
@require_connection
async def get_pmdg_cdu(cdu: int = 0) -> dict:
    """Read a PMDG 777 CDU screen.

    Returns the CDU display as text rows and an optional structured grid
    with per-cell color and formatting information.

    Requires EnableCDUBroadcast.N=1 in 777_Options.ini.

    Args:
        cdu: CDU unit number. 0=left (captain), 1=center, 2=right (F/O).

    Returns:
        Dict with 'rows' (list of 14 strings, 24 chars each) and 'grid'
        (structured per-cell data with color and flags).
    """
    from simconnect_mcp.pmdg import render_cdu_text, render_cdu_grid

    if cdu not in (0, 1, 2):
        return {
            "status": "error",
            "error": "INVALID_CDU",
            "message": f"CDU must be 0 (left), 1 (center), or 2 (right). Got {cdu}.",
        }

    pmdg, err = _ensure_pmdg_manager()
    if err:
        return err

    manager = SimConnectManager()

    def _subscribe_and_request():
        pmdg.subscribe_cdu(cdu)
        pmdg.request_cdu(cdu)

    await manager.run_sync(_subscribe_and_request)

    for _ in range(20):
        if pmdg.cdu_age(cdu) < 5.0:
            break
        await asyncio.sleep(0.1)

    if pmdg.cdu_age(cdu) == float("inf"):
        return {
            "status": "error",
            "error": "NO_CDU_DATA",
            "message": f"No CDU {cdu} data received.",
            "suggestion": f"Ensure EnableCDUBroadcast.{cdu}=1 is set in 777_Options.ini and restart the sim.",
        }

    screen = pmdg.read_cdu(cdu)
    if screen is None:
        return {"status": "error", "error": "NO_CDU_DATA", "message": "CDU screen not available."}

    rows = render_cdu_text(screen)
    if rows is None:
        return {
            "status": "ok",
            "cdu": cdu,
            "powered": False,
            "rows": None,
            "grid": None,
        }

    grid = render_cdu_grid(screen)

    cdu_names = {0: "Left (Captain)", 1: "Center", 2: "Right (F/O)"}
    result = {
        "status": "ok",
        "cdu": cdu,
        "cdu_name": cdu_names[cdu],
        "powered": True,
        "rows": rows,
        "grid": grid,
    }

    if pmdg.cdu_age(cdu) > 5.0:
        result["warning"] = f"Data may be stale ({pmdg.cdu_age(cdu):.1f}s since last update)"

    return result


@handle_simconnect_errors
@require_connection
async def send_pmdg_event(event_name: str, parameter: int | None = None) -> dict:
    """Send a PMDG 777 control event.

    Triggers cockpit controls (switches, buttons, knobs) using the PMDG SDK
    event system. Use search_lvars() to find events -- look for entries with
    an 'events' field.

    Args:
        event_name: PMDG event name (e.g., 'EVT_OH_ELEC_BATTERY_SWITCH').
        parameter: Optional position value. For toggle switches, omit this.
                   For selectors, pass the desired position (0, 1, 2, etc).

    Returns:
        Confirmation dict.
    """
    from simconnect_mcp.pmdg import resolve_pmdg_event

    dispatch = resolve_pmdg_event(event_name, parameter)

    manager = SimConnectManager()

    if dispatch["method"] == "control_data":
        # Direct-set events (e.g., EVT_MCP_ALT_SET) — write to PMDG_777X_Control
        pmdg, err = _ensure_pmdg_manager()
        if err:
            return err

        def _send_control():
            pmdg.send_control(dispatch["event_id"], dispatch["parameter"])

        await manager.run_sync(_send_control)
    else:
        # Standard cockpit events — use ROTOR_BRAKE via MobiFlight RPN
        if not manager.mobiflight_available:
            return {
                "status": "error",
                "error": "MOBIFLIGHT_NOT_AVAILABLE",
                "message": "MobiFlight WASM extension required for PMDG events.",
            }

        def _execute():
            manager.mobiflight.set(dispatch["code"])

        await manager.run_sync(_execute)

    return {
        "status": "ok",
        "event": event_name,
        "parameter": parameter,
        "message": f"Event '{event_name}' sent successfully",
    }
