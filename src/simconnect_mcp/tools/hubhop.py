"""HubHop community preset search.

Wraps the existing data/hubhop.py client so agents can look up events and
L-vars for aircraft that have no bundled catalog -- HubHop covers far more
aircraft than the three catalogs shipped in data/*.json.

One thing this module deliberately does NOT touch in data/hubhop.py: the
"msfs2020" segment in API_URL. That is HubHop's own naming for an endpoint
whose presets apply across MSFS 2020 and 2024 both -- not a stale
reference to fix.

HubHop is an HTTP API, not SimConnect, which changes how these tools reach
it in several ways:

* Calls go through `run_in_executor` directly, in `_run()` below, rather
  than SimConnectManager.run_sync. run_sync holds `_sim_lock` for the
  duration of the call -- correct for the DLL, which is not thread-safe,
  but a multi-second network round trip behind that same lock would stall
  every other tool on the server, including ones that have nothing to do
  with HubHop.
* Neither tool here is decorated with @require_connection: both work with
  MSFS closed. Only @handle_simconnect_errors is kept, as the safety net
  every tool in this package uses to guarantee it returns ToolError rather
  than raising -- not because these failures are SimConnect-shaped, but
  because nothing else in tools/__init__.py fits an HTTP client and the
  envelope contract still applies.
* `_TIMEOUT_S` is passed all the way down to `urlopen()` (via
  HubHopClient.fetch_presets/list_aircraft/fetch_all's `timeout` argument),
  not just wrapped around the await in `_run()`. `asyncio.wait_for`
  cancels the *await*, not a blocking call already running in a worker
  thread -- if the socket call itself only ever saw the client's own
  120s default, a timed-out fetch would keep occupying a thread in the
  default executor for up to 120s after this module had already given up
  and returned HUBHOP_TIMEOUT to the caller. That executor is shared with
  every other `run_in_executor(None, ...)` call on the server, including
  SimConnect's, so a stuck HubHop fetch could starve unrelated tools of
  worker threads. Passing the same _TIMEOUT_S down means the socket read
  itself gives up at approximately the same time the await does, instead
  of up to 90s later.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Annotated, Any

from pydantic import Field

from simconnect_mcp.data.hubhop import HubHopClient
from simconnect_mcp.tools import handle_simconnect_errors
from simconnect_mcp.tools.formatting import (
    DEFAULT_LIMIT,
    ResponseFormat,
    build_search_result,
)
from simconnect_mcp.tools.models import SearchResult, ToolError

# `code` is listed second, right after the human-readable label, rather
# than last: it is the payload an agent actually wants (RPN it can hand
# straight to msfs_execute_calculator_code or msfs_trigger_custom_event),
# so it should not be the column a caller has to scroll to.
PRESET_COLUMNS = [
    ("label", "Preset"),
    ("code", "RPN Code"),
    ("vendor", "Vendor"),
    ("aircraft", "Aircraft"),
    ("system", "System"),
    ("presetType", "Type"),
]

AIRCRAFT_COLUMNS = [
    ("aircraft", "Aircraft"),
    ("vendor", "Vendor"),
    ("systems", "Systems"),
]

# Measured live against the real API (see this task's addendum): fetching
# the full preset database took 7.8s for 17.4 MB / 32,570 presets. 30s
# gives roughly 4x headroom over that observed cost while still bounding
# an agent's wait to something it can sit through -- the client's own
# `timeout=120` default is not an acceptable worst case for a single tool
# call, so this value is used both for _run()'s own wait_for and passed
# down to the client's underlying urlopen() call (see module docstring),
# rather than relying on either default.
_TIMEOUT_S = 30.0

# One client for this server process's lifetime, not one per call. HubHop's
# own HubHopClient caches the fetched database in memory on the instance,
# behind a lock and a 6-hour TTL (see fetch_all in data/hubhop.py) -- a
# fresh HubHopClient() per tool call would discard that cache immediately
# after building it, turning "the first call is slow" into "every call is
# slow". Sharing one instance here means msfs_search_hubhop and
# msfs_list_hubhop_aircraft both draw on the same in-memory copy after
# whichever of them runs first pays the fetch, and the shared lock means
# two such calls landing at once still only trigger one download.
_client = HubHopClient()


def _hubhop_unavailable() -> ToolError:
    """Fresh ToolError per call -- see tools/__init__.py's
    _accessor_unavailable for why this package never shares one mutable
    ToolError instance across call sites. Used for a genuine connectivity
    failure (DNS, refused connection, ...); see _hubhop_timeout for the
    slow-but-maybe-reachable case, which gets different advice."""
    return ToolError(
        error="HUBHOP_UNAVAILABLE",
        message="Could not reach the HubHop API.",
        suggestion="HubHop needs internet access. Check your connection, or "
                   "work offline with msfs_search_lvars against the bundled catalogs.",
    )


def _hubhop_timeout() -> ToolError:
    """Fresh ToolError per call. Kept distinct from _hubhop_unavailable:
    retrying makes sense for an API that was merely slow, but not for one
    that could not be reached at all -- collapsing both into one message
    would give an agent the wrong advice for whichever case didn't happen."""
    return ToolError(
        error="HUBHOP_TIMEOUT",
        message=f"The HubHop API did not respond within {_TIMEOUT_S:g}s.",
        suggestion="The API may be slow or temporarily down. Try again shortly, "
                   "or work offline with msfs_search_lvars against the bundled catalogs.",
    )


def _hubhop_bad_response() -> ToolError:
    """Fresh ToolError per call. HubHop answering with HTTP 200 and a body
    that isn't valid JSON (truncated response, an HTML error page, ...) is
    a different failure from an unreachable or slow API -- distinct from
    both _hubhop_unavailable and _hubhop_timeout so it doesn't inherit
    either's advice. Without this, a bare json.JSONDecodeError (a
    ValueError) would fall through to handle_simconnect_errors' catch-all,
    which suggests checking whether MSFS is running -- nonsensical for a
    response-parsing failure that has nothing to do with the simulator."""
    return ToolError(
        error="HUBHOP_BAD_RESPONSE",
        message="HubHop responded, but the response body was not valid JSON.",
        suggestion="This is likely a transient issue on HubHop's end. Try again "
                   "shortly, or work offline with msfs_search_lvars against the "
                   "bundled catalogs.",
    )


async def _run(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run a blocking HubHop client call off the event loop, with a timeout.

    Goes straight through the default executor rather than
    SimConnectManager.run_sync -- see the module docstring. run_sync's
    `_sim_lock` exists to serialize calls into the non-thread-safe
    SimConnect DLL; holding it for an HTTP round trip would block every
    other tool on the server for as long as HubHop takes to answer.
    """
    loop = asyncio.get_running_loop()
    return await asyncio.wait_for(
        loop.run_in_executor(None, lambda: fn(*args, **kwargs)), timeout=_TIMEOUT_S
    )


@handle_simconnect_errors
async def search_hubhop(
    query: Annotated[
        str | None,
        Field(description="Text to match against preset labels and RPN code, "
                          "e.g. 'autopilot', 'landing light'"),
    ] = None,
    vendor: Annotated[
        str | None, Field(description="Vendor name, e.g. 'FenixSim', 'PMDG', 'Asobo'")
    ] = None,
    aircraft: Annotated[
        str | None, Field(description="Aircraft model, e.g. 'A320', 'B737-800'")
    ] = None,
    system: Annotated[
        str | None, Field(description="System, e.g. 'Autopilot', 'Lights', 'Electrical'")
    ] = None,
    limit: Annotated[int, Field(description="Maximum results", ge=1, le=200)] = DEFAULT_LIMIT,
    offset: Annotated[int, Field(description="Results to skip, for paging", ge=0)] = 0,
    response_format: Annotated[
        ResponseFormat, Field(description="'markdown' for a table, 'json' for rows")
    ] = ResponseFormat.MARKDOWN,
    refresh: Annotated[
        bool,
        Field(description="Bypass the cached preset database and re-fetch from "
                          "HubHop before searching. Use this if you just added or "
                          "changed a preset on HubHop and want to see it "
                          "immediately, rather than waiting for the normal cache "
                          "refresh."),
    ] = False,
) -> SearchResult | ToolError:
    """Search the MobiFlight HubHop community preset database.

    HubHop covers far more aircraft than the bundled catalogs, so this is
    the place to look when msfs_search_lvars finds nothing for the loaded
    aircraft. Each result's `code` field holds RPN -- typically `(>K:...)`
    or `(>H:...)` -- that you can pass directly to
    msfs_execute_calculator_code or msfs_trigger_custom_event, so a match
    here is immediately actionable, not just informational.

    The first call in this server's session downloads the full preset
    database (roughly 32,000 presets, ~17 MB) and keeps it in memory; that
    call alone can take several seconds. This is expected, not a hang.
    Later calls, including to msfs_list_hubhop_aircraft, reuse the same
    in-memory copy and return quickly, for up to 6 hours -- after that the
    next call re-fetches automatically, since HubHop is a community
    database that keeps growing. Pass refresh=True to force an immediate
    re-fetch instead of waiting on that, e.g. right after publishing a new
    preset yourself.

    Requires internet access. Supply at least one of query, vendor,
    aircraft or system -- the database is too large to browse unfiltered.
    """
    if not any((query, vendor, aircraft, system)):
        return ToolError(
            error="NO_FILTER",
            message="Supply at least one of: query, vendor, aircraft, system.",
            suggestion="The database holds tens of thousands of presets. "
                       "Try vendor='FenixSim' or query='autopilot'.",
        )

    try:
        presets = await _run(
            _client.fetch_presets, vendor=vendor, aircraft=aircraft, system=system,
            timeout=_TIMEOUT_S, force_refresh=refresh,
        )
    except asyncio.TimeoutError:
        return _hubhop_timeout()
    except OSError:
        return _hubhop_unavailable()
    except ValueError:
        return _hubhop_bad_response()

    if query:
        needle = query.lower()
        presets = [
            p for p in presets
            if needle in str(p.get("label", "")).lower()
            or needle in str(p.get("code", "")).lower()
        ]

    return build_search_result(
        presets, offset, limit, response_format, PRESET_COLUMNS,
        title="HubHop presets",
        query=query,
        filters={"vendor": vendor, "aircraft": aircraft, "system": system},
    )


@handle_simconnect_errors
async def list_hubhop_aircraft(
    vendor: Annotated[
        str | None, Field(description="Restrict to one vendor, e.g. 'FenixSim'")
    ] = None,
    limit: Annotated[int, Field(description="Maximum results", ge=1, le=200)] = DEFAULT_LIMIT,
    offset: Annotated[int, Field(description="Results to skip, for paging", ge=0)] = 0,
    response_format: Annotated[
        ResponseFormat, Field(description="'markdown' for a table, 'json' for rows")
    ] = ResponseFormat.MARKDOWN,
) -> SearchResult | ToolError:
    """List the aircraft that HubHop has presets for.

    Use this to find the exact vendor and aircraft spelling to pass to
    msfs_search_hubhop -- HubHop matches those filters exactly, so getting
    the spelling from here first avoids a filtered search that silently
    finds nothing because of a mismatched name.

    Like msfs_search_hubhop, the first call in this server's session
    downloads the full preset database (roughly 32,000 presets, ~17 MB),
    which can take several seconds; the two tools share the same in-memory
    copy afterwards, refreshed automatically every 6 hours. Requires
    internet access.
    """
    try:
        aircraft = await _run(
            _client.list_aircraft, vendor=vendor, timeout=_TIMEOUT_S
        )
    except asyncio.TimeoutError:
        return _hubhop_timeout()
    except OSError:
        return _hubhop_unavailable()
    except ValueError:
        return _hubhop_bad_response()

    return build_search_result(
        aircraft, offset, limit, response_format, AIRCRAFT_COLUMNS,
        title="Aircraft with HubHop presets",
        filters={"vendor": vendor},
    )
