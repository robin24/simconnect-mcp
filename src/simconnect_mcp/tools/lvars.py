"""L-Var tools — read, write, search, and browse the aircraft catalog via
MobiFlight, plus RPN calculator code execution.

The MobiFlight WASM bridge uses RPN-style variable strings:
  - Read: vr.get("(L:VarName)")  returns float
  - Write: vr.set("value (>L:VarName)")  sends RPN command
  - Calculator: vr.set("rpn code here")  executes arbitrary RPN
  - SimVars via RPN: vr.get("(A:PLANE ALTITUDE,Feet)")

set_lvar is the one exception: it writes through native SimConnect data
definitions (AddToDataDefinition + SetDataOnSimObject), not MobiFlight RPN --
proprietary aircraft such as the Fenix A320/A321 ignore RPN set() commands
but do respond to native SimConnect writes.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from simconnect_mcp.connection import SimConnectManager
from simconnect_mcp.tools import handle_simconnect_errors, require_connection
from simconnect_mcp.tools.formatting import (
    DEFAULT_LIMIT,
    ResponseFormat,
    build_search_result,
    paginate,
    render_paginated_table,
)
from simconnect_mcp.tools.models import (
    CalculatorResult,
    CatalogBrowse,
    LVarList,
    LVarValue,
    LVarWriteResult,
    SearchResult,
    ToolError,
)


def _require_mobiflight() -> ToolError | None:
    """Check MobiFlight availability. Returns a ToolError, or None if fine."""
    if SimConnectManager().mobiflight_available:
        return None
    return ToolError(
        error="MOBIFLIGHT_NOT_AVAILABLE",
        message="The MobiFlight WASM extension is not available; L-var "
        "operations require it.",
        suggestion="Install the MobiFlight WASM module in your MSFS Community "
        "folder and reconnect with msfs_connect.",
    )


@handle_simconnect_errors
@require_connection
async def get_lvar(
    name: Annotated[
        str,
        Field(
            description="L-var name, e.g. 'A32NX_EFIS_L_OPTION', 'WT_CJ4_HDG_ON'. "
            "The 'L:' prefix and RPN wrapping are added automatically if missing.",
            min_length=1,
            max_length=128,
        ),
    ],
) -> LVarValue | ToolError:
    """Read an L-var (local variable) value from the current aircraft.

    L-vars are aircraft-specific local variables used by add-on developers.
    Requires the MobiFlight WASM extension.
    """
    err = _require_mobiflight()
    if err:
        return err

    manager = SimConnectManager()
    rpn_key = _to_lvar_rpn(name)

    def _read() -> float | None:
        return manager.mobiflight.get(rpn_key)

    value = await manager.run_sync(_read)
    return LVarValue(name=name, rpn=rpn_key, value=value)


@handle_simconnect_errors
@require_connection
async def set_lvar(
    name: Annotated[
        str, Field(description="L-var name; the 'L:' prefix is optional", min_length=1,
                   max_length=128)
    ],
    value: Annotated[float, Field(description="Numeric value to write")],
) -> LVarWriteResult | ToolError:
    """Write a value to an L-var on the current aircraft.

    Uses native SimConnect data definitions (AddToDataDefinition +
    SetDataOnSimObject), which works with proprietary aircraft like the
    Fenix A320/A321. Does NOT require MobiFlight and does NOT use the
    MobiFlight RPN set() command.
    """
    manager = SimConnectManager()
    bare_name = _bare_lvar_name(name)

    def _write() -> None:
        manager.set_lvar(bare_name, value)

    await manager.run_sync(_write)
    return LVarWriteResult(name=bare_name, value_set=value)


@handle_simconnect_errors
@require_connection
async def list_lvars() -> LVarList | ToolError:
    """Enumerate active L-vars on the current aircraft.

    Not yet implemented. The MobiFlight WASM module's list command responds
    asynchronously on a channel this server does not capture yet, so this
    honestly reports that instead of claiming a listing was produced.

    Requires the MobiFlight WASM extension.
    """
    err = _require_mobiflight()
    if err:
        return err

    return ToolError(
        error="NOT_IMPLEMENTED",
        message="Enumerating active L-vars is not implemented yet.",
        suggestion=(
            "Use msfs_get_lvar to read a specific L-var by name, or msfs_search_lvars "
            "/ msfs_browse_lvar_catalog to find known variable names for the loaded "
            "aircraft. "
            "Common prefixes for popular aircraft: A32NX_ (FBW A320), WT_CJ4_ (Working "
            "Title CJ4), AS1000_ (G1000), ASCRJ_ (Aerosoft CRJ)."
        ),
    )


@handle_simconnect_errors
@require_connection
async def execute_calculator_code(
    code: Annotated[
        str,
        Field(
            description="RPN calculator code, e.g. '(A:PLANE ALTITUDE, feet)' to read a "
            "SimVar, '(L:MyCustomVar) 1 + (>L:MyCustomVar)' to increment an L-var, or "
            "'1 (>K:PARKING_BRAKES)' to trigger an event.",
            min_length=1,
            max_length=2000,
        ),
    ],
    mode: Annotated[
        Literal["auto", "read", "execute"],
        Field(
            description="'read' returns a value, 'execute' runs the code for effect. "
            "'auto' guesses from the syntax, which is unreliable for "
            "compound expressions."
        ),
    ] = "auto",
) -> CalculatorResult | ToolError:
    """Execute RPN calculator code in the simulator.

    Runs arbitrary RPN (Reverse Polish Notation) calculator code via the
    MobiFlight WASM bridge. Can read or write any variable type and perform
    complex operations. Requires the MobiFlight WASM extension.

    The 'auto' mode's heuristic -- code starts with '(', ends with ')', and
    contains no '(>' -- misclassifies compound read expressions such as
    '(L:A) (L:B) max' as an execute, because they don't end in ')'. Pass
    mode='read' explicitly for those.
    """
    err = _require_mobiflight()
    if err:
        return err

    manager = SimConnectManager()
    code_stripped = code.strip()

    if mode == "auto":
        resolved_mode: Literal["read", "execute"] = (
            "read"
            if code_stripped.startswith("(")
            and code_stripped.endswith(")")
            and "(>" not in code_stripped
            else "execute"
        )
    else:
        resolved_mode = mode

    if resolved_mode == "read":
        def _read() -> float | None:
            return manager.mobiflight.get(code_stripped)

        value = await manager.run_sync(_read)
        return CalculatorResult(code=code, mode="read", value=value)

    def _execute() -> None:
        manager.mobiflight.set(code_stripped)

    await manager.run_sync(_execute)
    return CalculatorResult(
        code=code, mode="execute", message="Calculator code executed successfully"
    )


def _no_detection_message(covered: str, valid_keys: set[str]) -> str:
    """Actionable explanation for when aircraft auto-detection finds nothing.

    Never silently guess a catalog: say what happened and how to fix it.
    """
    available = ", ".join(sorted(valid_keys))
    return (
        f"No aircraft catalog was auto-detected, so {covered}. "
        f"Pass catalog=<key> to scope it (available: {available}), or call "
        "msfs_browse_lvar_catalog() with no arguments for details."
    )


def _unknown_catalog_error(catalog: str, valid_keys: set[str]) -> ToolError:
    """Error for an explicit but unrecognized `catalog` argument.

    An invalid explicit key must error, not silently fall back to searching
    everything under the caller's requested (wrong) label.
    """
    return ToolError(
        error="CATALOG_NOT_FOUND",
        message=f"Unknown catalog '{catalog}'.",
        suggestion=(
            f"Use one of: {', '.join(sorted(valid_keys))} "
            "(call msfs_browse_lvar_catalog() with no arguments to list them)."
        ),
    )


LVAR_COLUMNS = [
    ("name", "L-Var"),
    ("display_name", "Description"),
    ("category", "Category"),
    ("writable", "Writable"),
]


@handle_simconnect_errors
async def search_lvars(
    keyword: Annotated[
        str,
        Field(
            description="Search term, e.g. 'seatbelt', 'autopilot', 'heading', 'fuel'",
            min_length=1,
            max_length=100,
        ),
    ],
    category: Annotated[
        str | None,
        Field(description="Filter by panel/system category, e.g. 'Signs', 'FCU', 'Electrical'"),
    ] = None,
    writable_only: Annotated[
        bool, Field(description="Only return variables that can be written to")
    ] = False,
    prefix: Annotated[
        str | None,
        Field(
            description="Filter by Fenix prefix type: S (switch), N (numeric readout), "
            "E (event counter), I (indicator), A (analog), B (boolean indicator)"
        ),
    ] = None,
    catalog: Annotated[
        str | None,
        Field(
            description="Explicit catalog key to search, e.g. 'fenix_a320', 'pmdg_737', "
            "'pmdg_777', overriding auto-detection. Use this when the loaded aircraft "
            "isn't auto-detected, or to search a specific aircraft's catalog regardless "
            "of what's loaded."
        ),
    ] = None,
    limit: Annotated[int, Field(description="Maximum results", ge=1, le=200)] = DEFAULT_LIMIT,
    offset: Annotated[int, Field(description="Results to skip, for paging", ge=0)] = 0,
    response_format: Annotated[
        ResponseFormat, Field(description="'markdown' for a compact table, 'json' for rows")
    ] = ResponseFormat.MARKDOWN,
) -> SearchResult | ToolError:
    """Search known aircraft L-vars by keyword.

    Searches the embedded L-var catalog for the current aircraft (auto-detected
    from TITLE/ATC_MODEL), or every known aircraft catalog if none is loaded or
    auto-detected. Results are paginated.

    When no aircraft catalog could be auto-detected and 'catalog' was not
    given, this searches every catalog ('filters.catalog' reads "all") and
    'message' explains how to scope the search instead.
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

    rows = search_catalog(
        keyword,
        catalog_key=catalog_key,
        category=category,
        writable_only=writable_only,
        prefix=prefix,
    )
    result = build_search_result(
        rows,
        offset,
        limit,
        response_format,
        LVAR_COLUMNS,
        title=f"L-vars matching '{keyword}'",
        query=keyword,
        filters={
            "category": category,
            "writable_only": writable_only,
            "prefix": prefix,
            "catalog": catalog_key or "all",
        },
    )
    if auto_detect_failed:
        result.message = _no_detection_message("this searched all catalogs", valid_keys)
        if result.markdown is not None:
            result.markdown += f"\n\n_{result.message}_"
    return result


@handle_simconnect_errors
async def browse_lvar_catalog(
    catalog: Annotated[
        str | None,
        Field(
            description="Catalog key, e.g. 'fenix_a320', 'pmdg_737', 'pmdg_777'. "
            "Omit to auto-detect from the loaded aircraft, or to "
            "list all available catalogs."
        ),
    ] = None,
    panel: Annotated[
        str | None,
        Field(
            description="Panel name to open, e.g. 'Signs', 'FCU', 'Electrical'. "
            "Omit to list the panels in the catalog."
        ),
    ] = None,
    limit: Annotated[int, Field(description="Maximum results", ge=1, le=200)] = DEFAULT_LIMIT,
    offset: Annotated[int, Field(description="Results to skip", ge=0)] = 0,
    response_format: Annotated[
        ResponseFormat, Field(description="'markdown' for a table, 'json' for rows")
    ] = ResponseFormat.MARKDOWN,
) -> CatalogBrowse | ToolError:
    """Browse the aircraft L-var catalogs.

    Three levels, narrowing as you supply arguments:
      * no catalog resolved -- every available aircraft catalog
      * catalog only        -- the panels in that catalog
      * catalog+panel       -- the variables on that panel, with their valid values

    With no 'catalog', the loaded aircraft is auto-detected from its TITLE
    and ATC_MODEL; a successful detection acts as if that catalog had been
    passed explicitly. When detection fails, 'catalog' comes back None,
    'message' explains why, and (with 'panel' also given) the panel is
    looked up across every catalog -- the first match is returned, but that
    is a guess, not a detection, and 'message' says so.
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

    if panel:
        found = get_panel_variables(panel, catalog_key)
        if found is None:
            return ToolError(
                error="PANEL_NOT_FOUND",
                message=f"No panel matching '{panel}' found.",
                suggestion=(
                    "Call msfs_browse_lvar_catalog without 'panel' to list "
                    "available panels."
                ),
            )
        rows, page = paginate(found["variables"], offset, limit)
        result = CatalogBrowse(
            catalog=found["catalog"],
            panel=found["panel"],
            page=page,
            variables=None if response_format is ResponseFormat.MARKDOWN else rows,
            markdown=(
                render_paginated_table(
                    rows, page, LVAR_COLUMNS, title=f"Panel: {found['panel']}"
                )
                if response_format is ResponseFormat.MARKDOWN
                else None
            ),
        )
        if auto_detect_failed:
            # get_panel_variables(panel, None) above returned the first
            # matching panel from whichever catalog iterates first -- a
            # silent guess unless disclosed here.
            result.message = _no_detection_message(
                "this returned the first matching panel found across all catalogs", valid_keys
            )
            if result.markdown is not None:
                result.markdown += f"\n\n_{result.message}_"
        return result

    if catalog_key:
        rows, page = paginate(list_panels(catalog_key), offset, limit)
        return CatalogBrowse(
            catalog=catalog_key,
            page=page,
            panels=None if response_format is ResponseFormat.MARKDOWN else rows,
            markdown=(
                render_paginated_table(
                    rows,
                    page,
                    [("panel", "Panel"), ("variable_count", "Variables")],
                    title=f"Panels in {catalog_key}",
                )
                if response_format is ResponseFormat.MARKDOWN
                else None
            ),
        )

    rows, page = paginate(list_catalogs(), offset, limit)
    result = CatalogBrowse(
        catalog=None,
        page=page,
        catalogs=None if response_format is ResponseFormat.MARKDOWN else rows,
        markdown=(
            render_paginated_table(
                rows,
                page,
                [
                    ("key", "Key"),
                    ("aircraft", "Aircraft"),
                    ("variable_count", "Variables"),
                    ("panel_count", "Panels"),
                ],
                title="Available aircraft L-var catalogs",
            )
            if response_format is ResponseFormat.MARKDOWN
            else None
        ),
    )
    if auto_detect_failed:
        result.message = _no_detection_message("every catalog is listed instead", valid_keys)
        if result.markdown is not None:
            result.markdown += f"\n\n_{result.message}_"
    return result


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
