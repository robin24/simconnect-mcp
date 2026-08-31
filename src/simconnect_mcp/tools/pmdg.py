"""PMDG tools — read aircraft state, CDU screens, send events.

Dispatches to either the PMDG 777 SDK or the PMDG 737 NG3 SDK depending on
which aircraft is currently loaded. Resolution tries, in order: an explicit
``variant`` argument, the ``TITLE``/``ATC_MODEL`` SimVars against each
catalog's ``title_pattern``, a live probe of each SDK's client data area
(see ``pmdg_detect.probe_pmdg_variant``), a name lookup against both
catalogs, and finally a guess -- see ``_resolve_pmdg_catalog`` for the full
order and how ``variant_source`` reports which one actually fired.

Variant detection and probing themselves live in ``simconnect_mcp.pmdg_detect``,
not here -- ``tools/lvars.py``'s catalog auto-detection needs the exact same
probe, and lifting the shared piece into a module neither tools package
depends on keeps that a downward dependency for both, rather than one tool
module reaching into another.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field

from simconnect_mcp.connection import SimConnectManager
from simconnect_mcp.pmdg_detect import detect_pmdg_variant as _detect_pmdg_variant
from simconnect_mcp.pmdg_detect import get_or_create_pmdg_manager
from simconnect_mcp.pmdg_detect import probe_pmdg_variant as _probe_pmdg_variant
from simconnect_mcp.tools import handle_simconnect_errors, require_connection
from simconnect_mcp.tools.models import PmdgCduResult, PmdgEventResult, PmdgVarResult, ToolError

if TYPE_CHECKING:
    from simconnect_mcp.pmdg import PmdgDataManager
    from simconnect_mcp.pmdg_ng3 import PmdgNG3DataManager

_PmdgVariant = Literal["pmdg_777", "pmdg_737"]

# ---------------------------------------------------------------------------
# Variant detection and manager dispatch
#
# _detect_pmdg_variant and _probe_pmdg_variant above are re-exported from
# pmdg_detect under their historical names: existing tests import and patch
# them at these names (simconnect_mcp.tools.pmdg._detect_pmdg_variant /
# _probe_pmdg_variant), and _resolve_pmdg_catalog below calls them as module
# globals, so patching either name here continues to intercept every caller
# in this module exactly as before this file split.
# ---------------------------------------------------------------------------


async def _resolve_pmdg_catalog(
    name: str | None, explicit_variant: str | None
) -> tuple[str | None, str | None]:
    """Pick a catalog key for the given variable/event name.

    Resolution order:
    1. If ``explicit_variant`` is given (``"pmdg_777"`` / ``"pmdg_737"``), use it.
    2. Otherwise use the variant detected from the loaded aircraft TITLE/ATC_MODEL.
    3. Otherwise, probe each SDK's client data area and use whichever responds
       (see ``_probe_pmdg_variant``) -- authoritative, but a real SimConnect
       round trip, so it only runs once title/model detection has failed.
    4. Otherwise, if ``name`` is provided, search both catalogs for the name.
       Returns the catalog key of the first match. If neither matches,
       fall back to ``"pmdg_777"`` so error messages stay consistent.

    Returns ``(catalog_key, source)`` where ``source`` is one of
    ``"explicit"``, ``"detected"``, ``"probed"``, ``"name_match"``,
    ``"fallback"``, or None. ``"fallback"`` is a guess, not a detection --
    callers must keep surfacing ``source`` (as ``variant_source`` in tool
    responses) so a caller can tell the two apart instead of silently
    trusting a guessed SDK.
    """
    if explicit_variant in ("pmdg_777", "pmdg_737"):
        return explicit_variant, "explicit"

    detected = await _detect_pmdg_variant()
    if detected:
        return detected, "detected"

    probed = await _probe_pmdg_variant()
    if probed:
        return probed, "probed"

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


# variant_source values that carry no real confirmation the resolved catalog
# actually matches the loaded aircraft -- see _unassured_variant_warning.
_UNCONFIRMED_VARIANT_SOURCES = frozenset({"fallback", "name_match"})


def _unassured_variant_warning(catalog_key: str | None, source: str | None) -> str | None:
    """Warning for send_pmdg_event when the catalog was assumed, not detected.

    get_pmdg_var/get_pmdg_cdu self-correct on a wrong guess: the client data
    area for a catalog nothing is loaded for simply never responds, so a bad
    guess surfaces as NO_DATA and the caller learns. send_pmdg_event has no
    such feedback loop -- it writes to whichever SDK's control area
    (control_data) or fires whichever RPN code (rotor_brake) the guessed
    catalog names, and that can reach a real, wrong aircraft system with no
    error of any kind. "explicit", "detected", and "probed" all have a real
    signal behind them (a caller's own say-so, the loaded aircraft's own
    TITLE/ATC_MODEL, or its client data area actually answering) and need no
    warning; "fallback" (no signal at all) and "name_match" (the event name
    merely happened to exist in one catalog, with nothing confirming that
    aircraft is the one loaded) do.
    """
    if source not in _UNCONFIRMED_VARIANT_SOURCES:
        return None
    detail = (
        "the event name matched neither catalog"
        if source == "fallback"
        else "the event name matched only that catalog"
    )
    return (
        f"No PMDG aircraft was detected, so '{catalog_key}' was assumed ({detail}) "
        "with nothing confirming it is the aircraft actually loaded. If this is "
        "wrong, pass variant='pmdg_737' or variant='pmdg_777' explicitly."
    )


def _ensure_pmdg_manager(
    variant: str = "pmdg_777",
) -> tuple[PmdgDataManager | PmdgNG3DataManager | None, ToolError | None]:
    """Get or create the right PMDG data manager for the variant.

    Returns ``(manager, error)``. The manager is lazily attached to the
    SimConnect singleton via ``pmdg_detect.get_or_create_pmdg_manager``,
    which reports unavailability as a plain ``None`` since it is also
    consulted (via ``probe_pmdg_variant``) from contexts with no tool-facing
    error to return. This wrapper adds the ``ToolError`` the PMDG tools
    below need.
    """
    mgr = get_or_create_pmdg_manager(variant)
    if mgr is None:
        # MOBIFLIGHT_NOT_AVAILABLE, not a separate MOBIFLIGHT_REQUIRED code --
        # this and the MobiFlightVariableRequests-based check in
        # tools/lvars.py/_require_mobiflight and tools/events.py both mean
        # the same thing to a caller: the MobiFlight WASM bridge is not
        # there. An agent that learned to branch on one code must not
        # silently miss the other.
        return None, ToolError(
            error="MOBIFLIGHT_NOT_AVAILABLE",
            message="PMDG SDK tools require SimConnectMobiFlight.",
            suggestion="Ensure the MobiFlight WASM module is installed.",
        )
    return mgr, None


@handle_simconnect_errors
@require_connection
async def get_pmdg_var(
    name: Annotated[
        str,
        Field(
            description="Variable name from the PMDG catalog. Use msfs_search_lvars() to "
            "discover available variables. Examples: 'ELEC_Battery_Sw_ON', "
            "'MCP_IASMach', 'FUEL_QtyCenter'.",
            min_length=1,
        ),
    ],
    variant: Annotated[
        _PmdgVariant | None,
        Field(
            description="Optional aircraft SDK variant. When omitted, it is "
            "auto-detected from the loaded aircraft."
        ),
    ] = None,
) -> PmdgVarResult | ToolError:
    """Read a PMDG aircraft data field by name (777 or 737 NG3).

    Uses the PMDG SDK data broadcast to read switch positions, annunciators,
    knob positions, MCP values, fuel quantities, FMC data, and more.

    Requires ``EnableDataBroadcast=1`` in the aircraft's options.ini
    (``777_Options.ini`` or ``737NG3_Options.ini``).
    """
    from simconnect_mcp.data.catalog import get_catalog

    catalog_key, source = await _resolve_pmdg_catalog(name, variant)
    catalog = get_catalog(catalog_key)
    if catalog is None:
        return ToolError(
            error="CATALOG_NOT_FOUND",
            message=f"{catalog_key} catalog not loaded.",
            suggestion=(
                f"{catalog_key} is one of the two built-in PMDG catalogs, so this "
                "points at a packaging problem rather than a bad argument -- check "
                "that the installed package includes "
                "src/simconnect_mcp/data/pmdg_777.json and pmdg_737.json."
            ),
        )

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
        return ToolError(
            error="FIELD_NOT_FOUND",
            message=f"Variable '{name}' not found in {catalog_key} catalog.",
            suggestion="Use msfs_search_lvars() to find available variables.",
        )

    sdk_field = var_entry.get("sdk_field")
    sdk_index = var_entry.get("sdk_index")
    sdk_type = var_entry.get("sdk_type")

    if sdk_field is None or sdk_type in ("event", "lvar"):
        return ToolError(
            error="NOT_A_DATA_FIELD",
            message=f"'{name}' is a {sdk_type}, not a readable data field.",
            suggestion="Use msfs_send_pmdg_event for events, or msfs_get_lvar for L-vars.",
        )

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
        return ToolError(
            error="NO_DATA",
            message=f"No data received from {catalog_key}.",
            suggestion=(
                f"Ensure EnableDataBroadcast=1 is set in {options_file} and restart the sim."
            ),
        )

    def _read():
        return pmdg.read_field(sdk_field, index=sdk_index)

    value = await manager.run_sync(_read)

    # Convert bytes for JSON
    if isinstance(value, bytes):
        value = value.decode("ascii", errors="replace").rstrip("\x00")

    value_description = None
    values_map = var_entry.get("values")
    if values_map and str(value) in values_map:
        value_description = values_map[str(value)]

    warning = None
    if pmdg.data_age > 5.0:
        warning = f"Data may be stale ({pmdg.data_age:.1f}s since last update)"

    return PmdgVarResult(
        name=name,
        value=value,
        display_name=var_entry.get("display_name", name),
        category=var_entry.get("category", ""),
        catalog=catalog_key,
        variant_source=source,
        value_description=value_description,
        warning=warning,
    )


@handle_simconnect_errors
@require_connection
async def get_pmdg_cdu(
    cdu: Annotated[
        int,
        Field(
            description="CDU unit number. 777: 0=left (Captain), 1=center, "
            "2=right (F/O). 737 NG3: 0=Captain, 1=F/O.",
            ge=0, le=2,
        ),
    ] = 0,
    variant: Annotated[
        _PmdgVariant | None,
        Field(description="Optional aircraft SDK variant. Defaults to auto-detect."),
    ] = None,
) -> PmdgCduResult | ToolError:
    """Read a PMDG CDU screen (777 has 3 CDUs, 737 NG3 has 2).

    Returns the CDU display as text rows and an optional structured grid
    with per-cell color and formatting information.

    Requires ``EnableCDUBroadcast.N=1`` in the aircraft's options.ini.
    """
    catalog_key, source = await _resolve_pmdg_catalog(None, variant)

    # Validate CDU index per variant. The Field bound above (0-2) already
    # rejects anything outside either variant's range at the schema
    # boundary; this catches the narrower 737 case (only 0-1) with a
    # message naming the actual limit, which a single shared bound can't
    # express.
    if catalog_key == "pmdg_737" and cdu not in (0, 1):
        return ToolError(
            error="INVALID_CDU",
            message=f"PMDG 737 NG3 has only 2 CDUs (0=Captain, 1=F/O). Got {cdu}.",
            suggestion="Pass cdu=0 for Captain or cdu=1 for F/O.",
        )
    if catalog_key == "pmdg_777" and cdu not in (0, 1, 2):
        return ToolError(
            error="INVALID_CDU",
            message=f"PMDG 777 CDU must be 0 (left), 1 (center), or 2 (right). Got {cdu}.",
            suggestion="Pass cdu=0 (left), cdu=1 (center), or cdu=2 (right).",
        )

    if catalog_key == "pmdg_737":
        from simconnect_mcp.pmdg_ng3 import render_cdu_grid, render_cdu_text
        cdu_names = {0: "Left (Captain)", 1: "Right (F/O)"}
        options_file = "737NG3_Options.ini"
    else:
        from simconnect_mcp.pmdg import render_cdu_grid, render_cdu_text
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
        return ToolError(
            error="NO_CDU_DATA",
            message=f"No CDU {cdu} data received.",
            suggestion=(
                f"Ensure EnableCDUBroadcast.{cdu}=1 is set in {options_file} and restart the sim."
            ),
        )

    screen = pmdg.read_cdu(cdu)
    if screen is None:
        return ToolError(
            error="NO_CDU_DATA",
            message="CDU screen not available.",
            suggestion=(
                f"Ensure EnableCDUBroadcast.{cdu}=1 is set in {options_file} and restart the sim."
            ),
        )

    rows = render_cdu_text(screen)
    if rows is None:
        return PmdgCduResult(
            cdu=cdu,
            cdu_name=cdu_names[cdu],
            powered=False,
            rows=None,
            grid=None,
            catalog=catalog_key,
            variant_source=source,
        )

    grid = render_cdu_grid(screen)

    warning = None
    if pmdg.cdu_age(cdu) > 5.0:
        warning = f"Data may be stale ({pmdg.cdu_age(cdu):.1f}s since last update)"

    return PmdgCduResult(
        cdu=cdu,
        cdu_name=cdu_names[cdu],
        powered=True,
        rows=rows,
        grid=grid,
        catalog=catalog_key,
        variant_source=source,
        warning=warning,
    )


@handle_simconnect_errors
@require_connection
async def send_pmdg_event(
    event_name: Annotated[
        str,
        Field(
            description="PMDG event name (e.g., 'EVT_OH_ELEC_BATTERY_SWITCH').",
            min_length=1,
        ),
    ],
    parameter: Annotated[
        int | None,
        Field(
            description="Optional position value. For toggle switches, omit this. "
            "For selectors, pass the desired position (0, 1, 2, etc)."
        ),
    ] = None,
    variant: Annotated[
        _PmdgVariant | None,
        Field(
            description="Optional aircraft SDK variant. When omitted, the variant is "
            "detected from the loaded aircraft; if detection fails, the event name is "
            "looked up in both catalogs and the first match wins (PMDG 777 takes "
            "priority on ambiguity)."
        ),
    ] = None,
) -> PmdgEventResult | ToolError:
    """Send a PMDG control event (777 or 737 NG3).

    Triggers cockpit controls (switches, buttons, knobs) using the PMDG SDK
    event system. Use msfs_search_lvars() to find events — look for entries with
    an 'events' field.
    """
    catalog_key, source = await _resolve_pmdg_catalog(event_name, variant)

    if catalog_key == "pmdg_737":
        from simconnect_mcp.pmdg_ng3 import resolve_pmdg_event
    else:
        from simconnect_mcp.pmdg import resolve_pmdg_event

    try:
        dispatch = resolve_pmdg_event(event_name, parameter)
    except ValueError as e:
        return ToolError(
            error="PMDG_EVENT_NOT_FOUND",
            message=str(e),
            suggestion="Use msfs_search_lvars to find PMDG events for this aircraft.",
        )

    manager = SimConnectManager()

    if dispatch["method"] == "control_data":
        # Direct-set events (e.g., EVT_MCP_ALT_SET) — write to the Control area
        pmdg, err = _ensure_pmdg_manager(catalog_key)
        if err:
            return err

        def _send_control():
            return pmdg.send_control(dispatch["event_id"], dispatch["parameter"])

        accepted, exception_name = await manager.run_sync(_send_control)
        if accepted is False:
            # A real, observed rejection -- not silence. SimConnect itself
            # (not the aircraft) refused this write; correlated via the
            # dispatcher's request registry exactly like tools/events.py's
            # trigger_event correlates MapClientEventToSimEvent/
            # TransmitClientEvent failures. See PmdgDataManager.send_control
            # (pmdg.py) for the live evidence that this correlation is real.
            return ToolError(
                error="PMDG_CONTROL_REJECTED",
                message=(
                    f"SimConnect rejected the control-area write for "
                    f"'{event_name}': {exception_name}."
                ),
                suggestion=(
                    "This is SimConnect refusing the write itself, not the aircraft "
                    "ignoring the event. Reconnect with msfs_connect and confirm the "
                    "PMDG aircraft is fully loaded (not just spawned), then retry."
                ),
            )
        # Not "sent successfully": send_control() reports only what
        # SimConnect's own exception correlation can observe. accepted=True
        # means SimConnect accepted the packet -- it does not mean PMDG's
        # own code acted on it, so this stops short of "successfully".
        # accepted=None means no request registry was available (plain
        # SimConnect fallback) and nothing here can say either way.
        message = (
            f"Event '{event_name}' sent; SimConnect accepted the packet"
            if accepted
            else f"Event '{event_name}' sent; delivery is not confirmed"
        )
    else:
        # Standard cockpit events — use ROTOR_BRAKE via MobiFlight RPN
        if not manager.mobiflight_available:
            return ToolError(
                error="MOBIFLIGHT_NOT_AVAILABLE",
                message="MobiFlight WASM extension required for PMDG events.",
                suggestion=(
                    "Install the MobiFlight WASM module in your MSFS Community folder "
                    "and reconnect with msfs_connect."
                ),
            )

        def _execute():
            manager.mobiflight.set(dispatch["code"])

        await manager.run_sync(_execute)
        # Not "sent successfully": manager.mobiflight.set() sends
        # MF.SimVars.Set.* over the same command channel as the two sites
        # Phase 1 already corrected in tools/lvars.py and tools/events.py.
        # Now that the WASM response channel is readable (see
        # MobiFlightVariableRequests.add_response_handler in
        # vendor/mobiflight_variable_requests.py), this was re-checked live
        # rather than assumed still true: MF.LVars.List produces response
        # traffic, but an MF.SimVars.Set.* command does not, even though
        # the write itself lands (confirmed via readback). Nothing here
        # observes the event actually firing -- only that it was sent.
        message = f"Event '{event_name}' sent; delivery is not confirmed"

    return PmdgEventResult(
        event=event_name,
        parameter=parameter,
        catalog=catalog_key,
        variant_source=source,
        message=message,
        warning=_unassured_variant_warning(catalog_key, source),
    )
