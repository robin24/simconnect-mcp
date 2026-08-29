"""PMDG tools — read aircraft state, CDU screens, send events.

Dispatches to either the PMDG 777 SDK or the PMDG 737 NG3 SDK depending on
which aircraft is currently loaded. Detection checks the ``TITLE`` and
``ATC_MODEL`` SimVars against each catalog's ``title_pattern``.
"""

from __future__ import annotations

import asyncio

from simconnect_mcp.connection import SimConnectManager
from simconnect_mcp.tools import handle_simconnect_errors, require_connection


# ---------------------------------------------------------------------------
# Variant detection and manager dispatch
# ---------------------------------------------------------------------------


async def _detect_pmdg_variant() -> str | None:
    """Return ``"pmdg_777"`` or ``"pmdg_737"`` based on the loaded aircraft.

    Checks both TITLE and ATC_MODEL against the PMDG catalogs' title
    patterns -- some liveries carry the vendor name only in ATC_MODEL while
    TITLE stays terse (e.g. a PMDG 777F's TITLE is just "777F"). Returns
    None if neither candidate matches either catalog; callers must not
    guess when this happens (see ``_resolve_pmdg_catalog``'s ``"fallback"``
    source).
    """
    title, model = await SimConnectManager().detect_aircraft_identity()
    for candidate in (title, model):
        if not candidate:
            continue
        candidate_lower = candidate.lower()
        if "pmdg 737" in candidate_lower or "pmdg b737" in candidate_lower:
            return "pmdg_737"
        if "pmdg 777" in candidate_lower or "pmdg b777" in candidate_lower:
            return "pmdg_777"
    return None


async def _resolve_pmdg_catalog(
    name: str | None, explicit_variant: str | None
) -> tuple[str | None, str | None]:
    """Pick a catalog key for the given variable/event name.

    Resolution order:
    1. If ``explicit_variant`` is given (``"pmdg_777"`` / ``"pmdg_737"``), use it.
    2. Otherwise use the variant detected from the loaded aircraft TITLE/ATC_MODEL.
    3. Otherwise, if ``name`` is provided, search both catalogs for the name.
       Returns the catalog key of the first match. If neither matches,
       fall back to ``"pmdg_777"`` so error messages stay consistent.

    Returns ``(catalog_key, source)`` where ``source`` is one of
    ``"explicit"``, ``"detected"``, ``"name_match"``, ``"fallback"``, or None.
    ``"fallback"`` is a guess, not a detection -- callers must keep surfacing
    ``source`` (as ``variant_source`` in tool responses) so a caller can tell
    the two apart instead of silently trusting a guessed SDK.
    """
    if explicit_variant in ("pmdg_777", "pmdg_737"):
        return explicit_variant, "explicit"

    detected = await _detect_pmdg_variant()
    if detected:
        return detected, "detected"

    if name is not None:
        from simconnect_mcp.data.catalog import get_catalog
        for key in ("pmdg_777", "pmdg_737"):
            cat = get_catalog(key)
            if cat is None:
                continue
            for var in cat["variables"]:
                if var["name"] == name:
                    return key, "name_match"
                for evt in var.get("events", []):
                    if evt["name"] == name:
                        return key, "name_match"

    return "pmdg_777", "fallback"


def _ensure_pmdg_manager(variant: str = "pmdg_777"):
    """Get or create the right PMDG data manager for the variant.

    Returns ``(manager, error_dict)``. The manager is lazily attached to the
    SimConnect singleton.
    """
    sm_mgr = SimConnectManager()

    if not hasattr(sm_mgr.sm, "register_client_data_handler"):
        return None, {
            "status": "error",
            "error": "MOBIFLIGHT_REQUIRED",
            "message": "PMDG SDK tools require SimConnectMobiFlight.",
            "suggestion": "Ensure the MobiFlight WASM module is installed.",
        }

    if variant == "pmdg_737":
        from simconnect_mcp.pmdg_ng3 import PmdgNG3DataManager
        if sm_mgr.pmdg_ng3 is None:
            sm_mgr.pmdg_ng3 = PmdgNG3DataManager(sm_mgr.sm)
            sm_mgr.sm.register_client_data_handler(
                sm_mgr.pmdg_ng3.client_data_handler
            )
        return sm_mgr.pmdg_ng3, None

    # Default: PMDG 777
    from simconnect_mcp.pmdg import PmdgDataManager
    if sm_mgr.pmdg is None:
        sm_mgr.pmdg = PmdgDataManager(sm_mgr.sm)
        sm_mgr.sm.register_client_data_handler(sm_mgr.pmdg.client_data_handler)
    return sm_mgr.pmdg, None


@handle_simconnect_errors
@require_connection
async def get_pmdg_var(name: str, variant: str | None = None) -> dict:
    """Read a PMDG aircraft data field by name (777 or 737 NG3).

    Uses the PMDG SDK data broadcast to read switch positions, annunciators,
    knob positions, MCP values, fuel quantities, FMC data, and more. The
    correct SDK is selected based on the loaded aircraft, or via the
    ``variant`` argument.

    Requires ``EnableDataBroadcast=1`` in the aircraft's options.ini
    (``777_Options.ini`` or ``737NG3_Options.ini``).

    Args:
        name: Variable name from the PMDG catalog. Use search_lvars() to
              discover available variables. Examples:
              'ELEC_Battery_Sw_ON', 'MCP_IASMach', 'FUEL_QtyCenter'.
        variant: Optional aircraft SDK variant: 'pmdg_777' or 'pmdg_737'.
                 When omitted, auto-detects from the loaded aircraft.

    Returns:
        Dict with variable name, value, display name, category, and the
        catalog key that was used.
    """
    from simconnect_mcp.data.catalog import get_catalog

    catalog_key, source = await _resolve_pmdg_catalog(name, variant)
    catalog = get_catalog(catalog_key)
    if catalog is None:
        return {
            "status": "error",
            "error": "CATALOG_NOT_FOUND",
            "message": f"{catalog_key} catalog not loaded.",
        }

    pmdg, err = _ensure_pmdg_manager(catalog_key)
    if err:
        return err

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
            "message": f"Variable '{name}' not found in {catalog_key} catalog.",
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
        options_file = "737NG3_Options.ini" if catalog_key == "pmdg_737" else "777_Options.ini"
        return {
            "status": "error",
            "error": "NO_DATA",
            "message": f"No data received from {catalog_key}.",
            "suggestion": f"Ensure EnableDataBroadcast=1 is set in {options_file} and restart the sim.",
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
        "catalog": catalog_key,
        "variant_source": source,
    }

    values_map = var_entry.get("values")
    if values_map and str(value) in values_map:
        result["value_description"] = values_map[str(value)]

    if pmdg.data_age > 5.0:
        result["warning"] = f"Data may be stale ({pmdg.data_age:.1f}s since last update)"

    return result


@handle_simconnect_errors
@require_connection
async def get_pmdg_cdu(cdu: int = 0, variant: str | None = None) -> dict:
    """Read a PMDG CDU screen (777 has 3 CDUs, 737 NG3 has 2).

    Returns the CDU display as text rows and an optional structured grid
    with per-cell color and formatting information.

    Requires ``EnableCDUBroadcast.N=1`` in the aircraft's options.ini.

    Args:
        cdu: CDU unit number.
             - 777: 0=left (Captain), 1=center, 2=right (F/O).
             - 737 NG3: 0=Captain, 1=F/O.
        variant: Optional 'pmdg_777' or 'pmdg_737'. Defaults to auto-detect.

    Returns:
        Dict with 'rows' (list of 14 strings, 24 chars each), 'grid'
        (structured per-cell data with color and flags), and the catalog
        key that was used.
    """
    catalog_key, source = await _resolve_pmdg_catalog(None, variant)

    # Validate CDU index per variant
    if catalog_key == "pmdg_737" and cdu not in (0, 1):
        return {
            "status": "error",
            "error": "INVALID_CDU",
            "message": f"PMDG 737 NG3 has only 2 CDUs (0=Captain, 1=F/O). Got {cdu}.",
        }
    if catalog_key == "pmdg_777" and cdu not in (0, 1, 2):
        return {
            "status": "error",
            "error": "INVALID_CDU",
            "message": f"PMDG 777 CDU must be 0 (left), 1 (center), or 2 (right). Got {cdu}.",
        }

    if catalog_key == "pmdg_737":
        from simconnect_mcp.pmdg_ng3 import render_cdu_text, render_cdu_grid
        cdu_names = {0: "Left (Captain)", 1: "Right (F/O)"}
        options_file = "737NG3_Options.ini"
    else:
        from simconnect_mcp.pmdg import render_cdu_text, render_cdu_grid
        cdu_names = {0: "Left (Captain)", 1: "Center", 2: "Right (F/O)"}
        options_file = "777_Options.ini"

    pmdg, err = _ensure_pmdg_manager(catalog_key)
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
            "suggestion": f"Ensure EnableCDUBroadcast.{cdu}=1 is set in {options_file} and restart the sim.",
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
            "catalog": catalog_key,
            "variant_source": source,
        }

    grid = render_cdu_grid(screen)

    result = {
        "status": "ok",
        "cdu": cdu,
        "cdu_name": cdu_names[cdu],
        "powered": True,
        "rows": rows,
        "grid": grid,
        "catalog": catalog_key,
        "variant_source": source,
    }

    if pmdg.cdu_age(cdu) > 5.0:
        result["warning"] = f"Data may be stale ({pmdg.cdu_age(cdu):.1f}s since last update)"

    return result


@handle_simconnect_errors
@require_connection
async def send_pmdg_event(
    event_name: str,
    parameter: int | None = None,
    variant: str | None = None,
) -> dict:
    """Send a PMDG control event (777 or 737 NG3).

    Triggers cockpit controls (switches, buttons, knobs) using the PMDG SDK
    event system. Use search_lvars() to find events — look for entries with
    an 'events' field.

    Args:
        event_name: PMDG event name (e.g., 'EVT_OH_ELEC_BATTERY_SWITCH').
        parameter: Optional position value. For toggle switches, omit this.
                   For selectors, pass the desired position (0, 1, 2, etc).
        variant: Optional 'pmdg_777' or 'pmdg_737'. When omitted, the variant
                 is detected from the loaded aircraft. If detection fails, the
                 event name is looked up in both catalogs and the first match
                 wins (PMDG 777 takes priority on ambiguity).

    Returns:
        Confirmation dict including the catalog used.
    """
    catalog_key, source = await _resolve_pmdg_catalog(event_name, variant)

    if catalog_key == "pmdg_737":
        from simconnect_mcp.pmdg_ng3 import resolve_pmdg_event
    else:
        from simconnect_mcp.pmdg import resolve_pmdg_event

    dispatch = resolve_pmdg_event(event_name, parameter)

    manager = SimConnectManager()

    if dispatch["method"] == "control_data":
        # Direct-set events (e.g., EVT_MCP_ALT_SET) — write to the Control area
        pmdg, err = _ensure_pmdg_manager(catalog_key)
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
        "catalog": catalog_key,
        "variant_source": source,
        "message": f"Event '{event_name}' sent successfully",
    }
