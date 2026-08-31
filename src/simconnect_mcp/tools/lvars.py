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

import asyncio
import logging
from typing import Annotated, Literal

from pydantic import Field

from simconnect_mcp.connection import SimConnectManager
from simconnect_mcp.simvar_access import (
    SimVarError,
    SimVarNotFoundError,
    SimVarNotSettableError,
)
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
    error_from,
)

logger = logging.getLogger(__name__)


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

    Reads the variable back afterwards and reports 'verified': true if the
    value landed, false if it did not, null if the read-back could not be
    completed. A false or null is never reported as success.
    """
    manager = SimConnectManager()
    if manager.accessor is None:
        return ToolError(
            error="ACCESSOR_UNAVAILABLE",
            message=(
                "This connection fell back to plain SimConnect, which cannot "
                "write L-vars through a data definition."
            ),
            suggestion="Reconnect with msfs_connect and check msfs_get_connection_status.",
        )

    bare_name = _bare_lvar_name(name)

    # verify=True re-reads after the write. SimConnect raises nothing when
    # an aircraft simply ignores a write, so a read-back is the only
    # evidence the value landed -- the same reason msfs_set_simvar does it.
    try:
        verified = await manager.run_sync(
            lambda: manager.set_lvar(bare_name, value, verify=True)
        )
    except SimVarNotFoundError as e:
        return ToolError(
            error="LVAR_NAME_INVALID",
            message=str(e),
            suggestion=(
                "L-var names must be plain ASCII. Check the name with "
                "msfs_search_lvars or msfs_browse_lvar_catalog."
            ),
        )
    except SimVarNotSettableError as e:
        return ToolError(
            error="LVAR_NOT_SETTABLE",
            message=str(e),
            suggestion=(
                f"SimConnect rejected the write to '{bare_name}'. Check the name with "
                "msfs_search_lvars, or use msfs_execute_calculator_code to set it "
                "through RPN instead."
            ),
        )
    except SimVarError as e:
        return error_from(e)

    warning = None
    if verified is False:
        warning = (
            f"The write was sent but '{bare_name}' did not read back as {value}. "
            "SimConnect does not reject a write the aircraft ignores, so this "
            "usually means the variable is not writable, or the aircraft "
            "immediately overrode the value."
        )
    elif verified is None:
        warning = (
            f"The write to '{bare_name}' was sent, but the read-back could not be "
            "completed, so whether it landed is unknown. This is not a report of "
            "success."
        )

    return LVarWriteResult(
        name=bare_name, value_set=value, verified=verified, warning=warning
    )


_LIST_TERMINATORS = ("MF.LVars.List.End", "MF.LVars.List.Complete")
_LIST_SETTLE_S = 1.5
_LIST_TIMEOUT_S = 10.0

# Re-arm command sent immediately before every MF.LVars.List -- see the
# extended comment at its call site below for the full story (repeat
# suppression measured live, ruled out as a byte-comparison dedupe, ruled
# out as client-side/connection-scoped state).
_REARM_COMMAND = "MF.SimVars.Set.1"

# Measured live against MSFS 2024 + the MobiFlight WASM module
# (task-3-4-addendum.md): MF.LVars.List returned *exactly* 1000 names and
# still sent its .End sentinel. Proven, not inferred -- an L-var created to
# sort after every returned name read back fine (42.0) but was absent from
# a re-list that still claimed to have ended. A response this large is
# therefore reported as presumptively truncated rather than trusted as a
# complete inventory; the module gives no other signal that it cut anything.
_LIST_CAP = 1000
_TRUNCATION_MESSAGE = (
    f"The MobiFlight WASM module caps MF.LVars.List at {_LIST_CAP} names and "
    "still reports the list as complete, so this is likely not every L-var "
    "the aircraft has registered -- other add-ons (e.g. GSX) can crowd the "
    "aircraft's own variables out of the response entirely. msfs_get_lvar "
    "reads any name by value whether or not it appears in this list."
)


@handle_simconnect_errors
@require_connection
async def list_lvars(
    filter_prefix: Annotated[
        str | None,
        Field(
            description="Only return names starting with this prefix, e.g. "
            "'A32NX', 'WT_CJ4', 'XMLVAR'. Case-insensitive."
        ),
    ] = None,
    limit: Annotated[int, Field(description="Maximum results", ge=1, le=200)] = DEFAULT_LIMIT,
    offset: Annotated[int, Field(description="Results to skip", ge=0)] = 0,
) -> LVarList | ToolError:
    """Enumerate the L-vars registered by the currently loaded aircraft.

    Asks the MobiFlight WASM module for its L-var list and collects the
    response. The module caps its reply at 1000 names but still reports the
    list as complete when it does -- see 'truncated' in the result, which is
    set whenever that cap was hit. A busy add-on setup (e.g. GSX) can crowd
    an aircraft's own L-vars out of a capped response entirely; msfs_get_lvar
    reads any name directly regardless of whether it showed up here.

    Returns bare names only -- no description, category, or writability per
    variable, unlike msfs_search_lvars' catalogued rows. Use msfs_search_lvars
    / msfs_browse_lvar_catalog for aircraft with a bundled catalog when that
    detail is what you need; use this tool for aircraft that have none, or
    to see everything currently registered regardless of catalog coverage.

    Internally sends a harmless no-op RPN command immediately before the
    WASM request, to re-arm the module against a quirk where it otherwise
    gives no response to a request byte-identical to the one it just
    answered (see _send_list_request's own docstring below for the full
    story). This creates no variable and has no effect on the aircraft, so
    calling this repeatedly is safe -- but the underlying quirk is a
    third-party module behavior this project does not control, so
    NO_LVARS_RETURNED below stays the honest report for the rare case
    where even the re-arm doesn't help, rather than this call ever
    assuming success. Requires the MobiFlight WASM extension.

    A listing that stops without the module's end-of-list marker returns
    LVAR_LIST_INCOMPLETE rather than the names collected so far: an
    arbitrary prefix of the list is indistinguishable from a complete
    listing of that size once returned, so it is refused the same way
    msfs_get_nearby_airports refuses a timed-out facility collection.
    """
    err = _require_mobiflight()
    if err:
        return err

    manager = SimConnectManager()
    bridge = manager.mobiflight

    names: list[str] = []
    finished = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _on_response(text: str) -> None:
        """Called from SimConnect's dispatch thread (Phase 2 Task 3's
        add_response_handler). The vendored fan-out that invokes this only
        logs a raising handler at DEBUG -- invisible at this server's
        default WARNING level, and deliberately left that way rather than
        widening vendor/'s local-change footprint for a diagnostic (see
        task-3-4-addendum.md). So this handler is responsible for surfacing
        its own failures visibly rather than trusting that silent catch.
        """
        try:
            if text in _LIST_TERMINATORS:
                loop.call_soon_threadsafe(finished.set)
                return
            if text.startswith("MF."):
                return  # command echo/sentinel, not an L-var name
            names.append(text)
        except Exception:
            logger.warning(
                "msfs_list_lvars response handler failed on %r", text, exc_info=True
            )

    def _send_list_request() -> None:
        """Re-arm, then ask for the list -- both under one run_sync call so
        no other tool call's DLL access can land between them.

        Measured live (task-4-report.md): MF.LVars.List gets NO response
        at all when it is byte-identical to the command immediately
        preceding it on MobiFlight.Command -- reproduced 0/4 on back-to-
        back identical requests, and NOT a time-based cooldown (a 20s wait
        with nothing else sent never cleared it). Two things ruled out
        that would otherwise look like an obvious fix:

        * NOT a raw byte-comparison dedupe. "MF.LVars.List " (trailing
          space -- different payload bytes, same intent) still got zero
          responses, so whatever gates this is keyed to the command
          itself, not a content diff.
        * NOT client- or connection-scoped state. A brand-new process with
          a brand-new SimConnect connection reproduced the same stuck
          state 4/4 on its very first call, right after a previous
          process's run left the channel "stuck" -- the state lives in
          the WASM module (or something in the client-data layer it
          uses), not in this Python object or this connection, so
          reconnecting is not an escape either.

        What does work: sending any OTHER command first. This sends a bare
        RPN literal with no (>L:...) write target -- MobiFlight's executor
        evaluates "1" and discards it, so nothing is read, written, or
        subscribed. Confirmed 4/4 across two independent live runs (fresh
        process each time) immediately before this exact MF.LVars.List
        call, with zero footprint on the aircraft -- prefer this over a
        scratch-L-var write for exactly that reason. If some future WASM
        build stops answering to this specific no-op, that is not silently
        papered over: NO_LVARS_RETURNED below still fires honestly.
        """
        bridge.send_command(_REARM_COMMAND)
        bridge.send_command("MF.LVars.List")

    # Review finding (task-4-report.md addendum): run_sync only holds
    # _sim_lock for the synchronous send, releasing it well before
    # `finished.wait()` returns -- so without a lock spanning the whole
    # register -> send -> wait -> cleanup cycle, a second concurrent call
    # can register its own handler and fire its own MF.LVars.List burst
    # while this one is still collecting. The vendored fan-out
    # (_deliver_response) delivers every response to every registered
    # handler with no per-call correlation, so that second burst lands in
    # THIS call's `names` list too -- inflating the raw count and able to
    # trip `truncated` for a response nowhere near the real cap. Same bug
    # class this codebase already found and fixed one module over
    # (SimConnectManager.facility_lock, tools/facilities.py) -- mirrored
    # here rather than reinvented.
    async with manager.list_lvars_lock():
        bridge.add_response_handler(_on_response)
        try:
            await manager.run_sync(_send_list_request)
            try:
                await asyncio.wait_for(finished.wait(), timeout=_LIST_TIMEOUT_S)
            except asyncio.TimeoutError:
                # No MF.LVars.List.End inside the window. Handled after the
                # lock is released -- see the `terminated` check below.
                pass
            # Give any trailing names a chance to land before reading `names`.
            if not finished.is_set():
                await asyncio.sleep(_LIST_SETTLE_S)
            # Read AFTER the settle sleep, not before: a terminator that
            # lands during that extra 1.5s means the listing did finish, and
            # this is the last moment it can be observed before the handler
            # comes off the bridge.
            terminated = finished.is_set()
        finally:
            bridge.remove_response_handler(_on_response)

    if not names:
        return ToolError(
            error="NO_LVARS_RETURNED",
            message="The MobiFlight WASM module returned no L-var names.",
            suggestion=(
                "Ensure an aircraft is fully loaded and that the MobiFlight WASM "
                "module supports MF.LVars.List. This call already sends a re-arm "
                "command before every request specifically to prevent the WASM "
                "module from silently dropping an immediately repeated identical "
                "request, so seeing this even on a fast repeated call is "
                "unexpected; if it persists, msfs_get_lvar, msfs_search_lvars, and "
                "msfs_browse_lvar_catalog remain unaffected in the meantime."
            ),
        )

    if not terminated:
        # The listing never reached its MF.LVars.List.End sentinel, so
        # `names` is whatever happened to arrive before the stream stopped
        # -- not an inventory. Nothing about that count distinguishes it
        # from a complete listing of the same size, so returning it with
        # truncated=False/has_more=False/message=None would present a cut-
        # off stream as an exhaustive one: the fabricated-success pattern
        # this project has removed nine times over. An earlier version of
        # this branch accepted the partial list on the theory that some
        # WASM build might send no terminator; that premise was checked
        # live (task-3-4-addendum.md) and the module always sends .End,
        # even for a capped 1000-name response. What actually reaches here
        # is a stream disrupted mid-flight -- list_lvars_lock serialises
        # this tool against itself but holds no _sim_lock across the 10s
        # wait, so msfs_get_lvar, msfs_execute_calculator_code and
        # msfs_send_pmdg_event can all push commands onto the
        # order-sensitive MobiFlight.Command channel mid-listing -- which
        # is exactly the case where the result is partial.
        #
        # This refuses rather than flagging, matching tools/facilities.py's
        # FACILITY_COLLECTION_TIMEOUT for the identical question one module
        # over: an agent that learns "an unterminated collection here is an
        # error envelope" from one of these tools must not be silently
        # handed a truncated inventory by the other. `truncated` stays
        # reserved for the different, live-measured case it documents -- a
        # response the module DID terminate but capped at _LIST_CAP, where
        # the partial list is all that exists to serve and the flag is the
        # only signal available.
        return ToolError(
            error="LVAR_LIST_INCOMPLETE",
            message=(
                f"The L-var listing stopped after {len(names)} names without "
                "the MobiFlight WASM module's end-of-list marker."
            ),
            suggestion=(
                "Those names are an arbitrary prefix of the real list, not a "
                "shorter version of it, so nothing is returned rather than "
                "risk being read as a complete inventory. Another tool "
                "writing to the WASM command channel mid-listing can cause "
                "this; retry with no other sim calls in flight. "
                "msfs_get_lvar reads any name directly meanwhile, and "
                "msfs_search_lvars works off the bundled catalogs."
            ),
        )

    # The cap is judged on what the module actually sent, before any local
    # filtering -- filter_prefix narrowing the view afterward must not hide
    # the fact that the underlying response was itself incomplete (names
    # arrive sorted, so a cap can crowd out an entire late-alphabet prefix).
    truncated = len(names) >= _LIST_CAP

    if filter_prefix:
        needle = filter_prefix.strip().upper()
        names = [n for n in names if n.upper().startswith(needle)]

    names = sorted(dict.fromkeys(names))
    window, page = paginate([{"name": n} for n in names], offset, limit)
    return LVarList(
        page=page,
        lvars=[row["name"] for row in window],
        truncated=truncated,
        message=_TRUNCATION_MESSAGE if truncated else None,
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
        code=code,
        mode="execute",
        # Not "executed successfully": manager.mobiflight.set() writes to a
        # client data area with no response channel read (see
        # MobiFlightVariableRequests.set/send_command in
        # vendor/mobiflight_variable_requests.py), so nothing here confirms
        # the WASM module actually ran this code -- only that it was sent.
        message="Calculator code sent to the sim; execution is not confirmed",
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


# list_lvars above deliberately has no response_format/markdown, unlike
# search_lvars and browse_lvar_catalog below (both render LVAR_COLUMNS
# through render_paginated_table). Adjudicated, not an oversight: its rows
# are bare WASM-reported names with no description/category/writable to
# put in other columns, so a one-column markdown table would be strictly
# worse than the JSON array it already returns -- and with no markdown
# format to default to, there is no footer to omit either, which is the
# Phase 1 defect (browse_lvar_catalog's dropped "more results" footer)
# this convention otherwise guards against. Page.has_more/truncated are
# handed to the caller directly instead.
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
            description="Filter by the catalog's variable-type prefix, matched exactly "
            "against each entry's 'prefix' field, e.g. PMDG's 'MCP', 'ELEC', 'EVT'. A "
            "catalog you build yourself (see msfs_browse_lvar_catalog) may use a "
            "different convention -- browse a panel first if you're unsure what a "
            "given catalog uses."
        ),
    ] = None,
    catalog: Annotated[
        str | None,
        Field(
            description="Explicit catalog key to search, e.g. 'pmdg_737', 'pmdg_777', "
            "overriding auto-detection. Use this when the loaded aircraft isn't "
            "auto-detected, or to search a specific aircraft's catalog regardless of "
            "what's loaded. Call msfs_browse_lvar_catalog() with no arguments for the "
            "full list of keys currently bundled or dropped into data/ locally."
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
            description="Catalog key, e.g. 'pmdg_737', 'pmdg_777'. "
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
