"""Facilities tools -- nearby airports and facility lookup.

Data arrives through the dispatcher's FacilityCollector. The SimConnect
library's own FacilitiesRequests is unusable here: its get() returns None and
its results only ever reach dump(), which prints to stdout -- fatal on a
stdio MCP server.

SubscribeToFacilities' scope is not the same for every kind -- measured live
against MSFS 2024, aircraft at KATL (see .superpowers/sdd/
2026-08-29-mcp-modernization-phase2-capability/task-2-addendum.md and its
facility_request_cost_probe.py for the first pass, and the controller's
follow-up correction for the numbers below):

    AIRPORT   85,249 facilities   249 regions   max 9,837 nm from KATL
              79% beyond 1000 nm                          -> WORLD-WIDE
    WAYPOINT   2,517 facilities     2 regions   max   193 nm
    NDB           30 facilities     1 region    max   184 nm
    VOR          115 facilities    18 regions   max   192 nm
              0% beyond 1000 nm for any of the three        -> REGIONAL

AIRPORT is genuinely the entire world, unrelated to the aircraft's position
(RequestFacilitiesList returns the identical list, so there is no cheaper
"nearby" variant to call instead). WAYPOINT/NDB/VOR are a "reality bubble"
scoped to wherever the aircraft currently is -- essentially all in one
ARTCC region around it. The first draft of this module generalised "the
world list does not change during a session" from the AIRPORT measurement
alone to all four kinds without checking the other three -- the same
mistake, on the same project, that put the wrong struct layout in the plan
Task 1 was built from. Two consequences shape this module, corrected for
that:

* Only AIRPORT is collected at most once per connection and cached on
  SimConnectManager afterwards (get_cached_facilities/set_cached_facilities)
  -- see _CACHEABLE_KINDS and _collect(). It is the one kind that is both
  expensive (85k records vs. under 2,700 for the other three combined) and
  actually safe to cache: it cannot go stale, because it was never scoped to
  the aircraft's position to begin with. WAYPOINT/NDB/VOR are re-collected
  on every call and never cached: this server can reposition the aircraft
  itself (msfs_set_aircraft_position), or the user can simply fly, and a
  session-cached navaid list would then keep serving facilities from
  wherever the aircraft *used to be* with no signal to the caller -- stale
  data presented as current, the same defect class as Phase 1's PMDG
  variant cache bug. They cost nothing to skip caching for: the whole
  four-kind collection completes in about 30ms, so re-collecting a list of
  at most 2,517 records on every call is not a real cost. Do not
  reintroduce caching for these three, and do not substitute a
  position-keyed cache either -- that would need a staleness policy, a
  tolerance, and a test for each, to save 30ms on a list of 30 NDBs.
* A per-kind asyncio.Lock (SimConnectManager.facility_lock) serializes
  collection attempts for that kind, regardless of whether the kind is
  cacheable. Without it, a second caller arriving while the first is still
  waiting for its subscription to fill would reset the collector's buffer
  out from under the first -- reset-then-subscribe is not atomic with the
  wait that follows it, so both callers could end up with a torn result.
  For AIRPORT, the cache is checked again after the lock is acquired, so a
  caller that waited for the lock returns the first caller's result instead
  of collecting a second time.

The subscription itself goes quiet once its list completes -- also measured
live, no further traffic 6s after completion -- so this is not a live-
traffic problem. But it does mean a scenery change could still re-fire it at
some arbitrary later moment, feeding a collector nothing here is watching
any more. UnsubscribeToFacilities is therefore called once collection ends,
on both the success and timeout paths, even when the result is cached and
never subscribed to again -- and, per the next paragraph, even when the
caller never gets to run that line at all.

Subscribe -> poll -> unsubscribe runs inside a try/finally, not just three
sequential calls. The poll loop awaits asyncio.sleep() every iteration and
can legitimately run for up to _COLLECT_TIMEOUT (5s) -- a real window for
the caller's MCP request to be cancelled (notifications/cancelled cancels
this coroutine's task), which raises CancelledError straight out of that
sleep. Without the finally, that skips the unsubscribe entirely and leaves
an orphaned subscription free to keep delivering chunks to a collector
nothing is watching any more -- belt and braces, FacilityCollector.reset()
also now takes the SimConnect request id the subscription was issued under
(threaded through from _subscribe below) and FacilityCollector.handle()
discards any chunk whose dwRequestID doesn't match. That second guard
matters even when the finally succeeds: UnsubscribeToFacilities does not
retroactively cancel messages SimConnect already queued before it runs, so
a stray late chunk from an already-abandoned subscription could otherwise
land inside a LATER, unrelated _collect() call for the same kind and
silently flip its is_complete() true on a mix of old and new data --
served as a success, indistinguishable from a correct one to the caller.

If the collector never reaches is_complete() within the timeout, this
reports a ToolError rather than returning the partial list sitting in the
collector: serving a partial world as though it were the complete one is
exactly the fabricated-success pattern the rest of this project has spent
two phases removing. SubscribeToFacilities' own HRESULT is checked first,
so a subscription SimConnect rejected outright is reported as
FACILITY_SUBSCRIBE_FAILED instead of being left to surface five seconds
later as a timeout blamed on a busy sim.

Request IDs are reserved per kind and rotated, not allocated per
collection -- see _REQUEST_ID_RING and connection.py's
reserved_request_id.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Annotated, Any, Literal

from pydantic import Field
from SimConnect.Enum import SIMCONNECT_FACILITY_LIST_TYPE

from simconnect_mcp.connection import SimConnectManager
from simconnect_mcp.facilities import FacilityKind, great_circle_nm
from simconnect_mcp.tools import handle_simconnect_errors, require_connection
from simconnect_mcp.tools.formatting import (
    DEFAULT_LIMIT,
    ResponseFormat,
    paginate,
    render_paginated_table,
)
from simconnect_mcp.tools.models import FacilityInfo, FacilityList, ToolError

logger = logging.getLogger(__name__)

_LIST_TYPES = {
    FacilityKind.AIRPORT: SIMCONNECT_FACILITY_LIST_TYPE.SIMCONNECT_FACILITY_LIST_TYPE_AIRPORT,
    FacilityKind.WAYPOINT: SIMCONNECT_FACILITY_LIST_TYPE.SIMCONNECT_FACILITY_LIST_TYPE_WAYPOINT,
    FacilityKind.NDB: SIMCONNECT_FACILITY_LIST_TYPE.SIMCONNECT_FACILITY_LIST_TYPE_NDB,
    FacilityKind.VOR: SIMCONNECT_FACILITY_LIST_TYPE.SIMCONNECT_FACILITY_LIST_TYPE_VOR,
}

AIRPORT_COLUMNS = [
    ("icao", "ICAO"),
    ("distance_nm", "Distance (nm)"),
    ("latitude", "Latitude"),
    ("longitude", "Longitude"),
    ("altitude_ft", "Elevation (ft)"),
]

# Generous relative to the ~30ms a real SubscribeToFacilities took to deliver
# the entire world (measured live -- see module docstring): this bounds how
# long a stalled or still-loading sim can hang a caller, not the expected
# case.
_COLLECT_TIMEOUT = 5.0
_POLL_INTERVAL = 0.1

# How many request IDs each kind reserves once and then rotates through, in
# place of allocating a fresh one per collection. Two is the minimum that
# still keeps FacilityCollector.handle's dwRequestID correlation meaningful
# (consecutive collections of one kind never share an ID); four is chosen
# instead because the whole cost of the larger ring is eight extra Enum
# members reserved at connect time, and it means a stale chunk would have to
# outlive three complete collections of its own kind -- rather than one --
# before it could be mistaken for a current one. See connection.py's
# reserved_request_id.
_REQUEST_ID_RING = 4

# Only AIRPORT is safe to cache -- see module docstring for the live
# measurement. WAYPOINT/NDB/VOR are a position-scoped "reality bubble" that
# would go silently stale after a reposition if cached here.
_CACHEABLE_KINDS = frozenset({FacilityKind.AIRPORT})


def _position_unavailable() -> ToolError:
    """Fresh ToolError for get_nearby_airports' two missing-position exits
    (no accessor to default from, or the read came back empty) -- a new
    instance per call, matching _accessor_unavailable() in tools/__init__.py
    rather than sharing one mutable model instance across both call sites.
    """
    return ToolError(
        error="POSITION_NOT_AVAILABLE",
        message="Could not read the aircraft position for the search centre.",
        suggestion="Pass latitude and longitude explicitly.",
    )


async def _collect(kind: FacilityKind) -> list[dict[str, Any]] | ToolError:
    """Return the facility list for one kind.

    Cached after the first successful collection for AIRPORT only; every
    other kind is re-collected on every call -- see module docstring and
    _CACHEABLE_KINDS.
    """
    manager = SimConnectManager()
    collector = getattr(manager.sm, "facilities", None)
    if collector is None:
        return ToolError(
            error="FACILITIES_NOT_AVAILABLE",
            message="Facility data requires the SimConnect dispatcher.",
            suggestion="Reconnect with msfs_connect; the plain SimConnect "
                       "fallback cannot deliver facility data.",
        )

    cacheable = kind in _CACHEABLE_KINDS

    if cacheable:
        cached = manager.get_cached_facilities(kind.value)
        if cached is not None:
            return cached

    async with manager.facility_lock(kind.value):
        # Re-check: whoever held the lock before us may have just finished
        # collecting this exact kind, in which case there is nothing left
        # for this call to do. Only meaningful for a cacheable kind -- for
        # WAYPOINT/NDB/VOR there is never anything to find here, by design.
        if cacheable:
            cached = manager.get_cached_facilities(kind.value)
            if cached is not None:
                return cached

        list_type = _LIST_TYPES[kind]

        def _subscribe() -> bool:
            # Not new_request_id(): that rebuilds an Enum from every prior
            # member on every call and never reclaims one, and WAYPOINT/NDB/
            # VOR are re-collected on *every* call by design (see module
            # docstring), so this is a hot path whose per-call cost would
            # otherwise grow without bound across a session -- exactly what
            # RequestRegistry.acquire_request_id was built to stop.
            # reserved_request_id rotates over a small fixed set instead of
            # reusing one stable ID, because a stable ID would defeat the
            # dwRequestID correlation in FacilityCollector.handle: a late
            # chunk from an abandoned subscription for this same kind would
            # match the next collection and silently complete it on stale
            # data. See connection.py's reserved_request_id for the full
            # reasoning and for why _REQUEST_ID_RING is enough.
            request_id = manager.reserved_request_id(
                f"facility:{kind.value}", ring=_REQUEST_ID_RING
            )
            # Told to the collector *before* the DLL call, so a chunk that
            # somehow arrives before this function returns (unlikely, but
            # not this code's place to assume) is still correlated correctly.
            collector.reset(kind, request_id)
            # SimConnect_SubscribeToFacilities' restype is HRESULT
            # (Attributes.py in the installed package) and the previous
            # version of this line threw it away. A failed subscribe does
            # not fabricate success here -- the poll below simply times out
            # -- but it did make this report FACILITY_COLLECTION_TIMEOUT and
            # blame a paused or still-loading sim, a diagnosis this code had
            # the evidence to contradict. Same wrapper-swallowed-HRESULT
            # class as tools/utilities.py's send_sim_text, lower stakes.
            hr = manager.sm.dll.SubscribeToFacilities(
                manager.sm.hSimConnect, list_type, request_id
            )
            return bool(manager.sm.IsHR(hr, 0))

        def _unsubscribe() -> None:
            # Best-effort: a failure here must not turn an otherwise
            # successful (or already-timed-out) collection into an error.
            try:
                manager.sm.dll.UnsubscribeToFacilities(manager.sm.hSimConnect, list_type)
            except Exception:
                logger.debug(
                    "UnsubscribeToFacilities failed for %s", kind.value, exc_info=True
                )

        # try/finally around subscribe -> poll -> unsubscribe, not just
        # sequential calls: the poll loop below awaits asyncio.sleep() on
        # every iteration and can legitimately run for up to
        # _COLLECT_TIMEOUT (5s) -- a real window for the caller's MCP
        # request to be cancelled (notifications/cancelled cancels this
        # coroutine's task). Without this, a cancellation here would skip
        # _unsubscribe entirely and leave an orphaned subscription that can
        # go on delivering chunks nobody is watching -- see this module's
        # and FacilityCollector's docstrings for what an orphaned chunk can
        # do to a later, unrelated collection for the same kind if it lands
        # uncorrelated (the request-id check in FacilityCollector.handle is
        # the other half of that defense, for when even this finally can't
        # run to completion). The facility_lock's `async with` above already
        # releases correctly on cancellation (`__aexit__` always runs); this
        # covers the DLL subscription itself, which has no such automatic
        # cleanup.
        try:
            subscribed = await manager.run_sync(_subscribe)

            # Skip the poll entirely if the subscription was rejected --
            # nothing is coming, so waiting the full _COLLECT_TIMEOUT would
            # only delay a failure we already know about.
            waited = 0.0
            while (
                subscribed
                and waited < _COLLECT_TIMEOUT
                and not collector.is_complete(kind)
            ):
                await asyncio.sleep(_POLL_INTERVAL)
                waited += _POLL_INTERVAL

            complete = collector.is_complete(kind)
            results = collector.results(kind)
        finally:
            # Unconditional, including after a rejected subscribe: the
            # unsubscribe is best-effort and swallows its own failures, and
            # a subscription that partly took effect must still be torn down.
            await manager.run_sync(_unsubscribe)

        if not subscribed:
            return ToolError(
                error="FACILITY_SUBSCRIBE_FAILED",
                message=f"SimConnect rejected the {kind.value} facility subscription.",
                suggestion="The connection may be stale. Reconnect with "
                           "msfs_connect and try again.",
            )

        if not complete:
            return ToolError(
                error="FACILITY_COLLECTION_TIMEOUT",
                message=(
                    f"The {kind.value} list did not finish loading within "
                    f"{_COLLECT_TIMEOUT:.0f}s ({len(results)} facilities "
                    "collected so far)."
                ),
                suggestion=(
                    "Reporting that partial set as the full list would be "
                    "misleading, so nothing is returned. The sim may be "
                    "paused, still loading, or busy -- try again shortly."
                ),
            )

        if cacheable:
            manager.set_cached_facilities(kind.value, results)
        return results


@handle_simconnect_errors
@require_connection
async def get_nearby_airports(
    latitude: Annotated[
        float | None,
        Field(description="Centre latitude. Defaults to the aircraft's position.",
              ge=-90, le=90),
    ] = None,
    longitude: Annotated[
        float | None,
        Field(description="Centre longitude. Defaults to the aircraft's position.",
              ge=-180, le=180),
    ] = None,
    radius_nm: Annotated[
        float, Field(description="Search radius in nautical miles", gt=0, le=500)
    ] = 50.0,
    limit: Annotated[int, Field(description="Maximum results", ge=1, le=200)] = DEFAULT_LIMIT,
    offset: Annotated[int, Field(description="Results to skip, for paging", ge=0)] = 0,
    response_format: Annotated[
        ResponseFormat, Field(description="'markdown' for a table, 'json' for rows")
    ] = ResponseFormat.MARKDOWN,
) -> FacilityList | ToolError:
    """List airports near a point, nearest first.

    Filters SimConnect's airport facility list by great-circle distance from
    the given (or current) position. That list is not scoped to the
    aircraft's location at all -- measured live, it is the entire world
    (85,249 airports) regardless of where the aircraft is -- so a radius
    that finds nothing means there is genuinely no airport that close, not
    that the sim "hasn't loaded" one. The distance filter always runs over
    the complete list before pagination, so a later page can never miss a
    match an earlier page's filtering already found.
    """
    manager = SimConnectManager()

    if latitude is None or longitude is None:
        if manager.accessor is None:
            return _position_unavailable()
        pos = await manager.run_sync(
            lambda: manager.accessor.read_many([
                ("PLANE_LATITUDE", "degrees", None),
                ("PLANE_LONGITUDE", "degrees", None),
            ])
        )
        latitude = latitude if latitude is not None else pos["PLANE_LATITUDE"].get("value")
        longitude = longitude if longitude is not None else pos["PLANE_LONGITUDE"].get("value")
        if latitude is None or longitude is None:
            return _position_unavailable()

    airports = await _collect(FacilityKind.AIRPORT)
    if isinstance(airports, ToolError):
        return airports

    nearby = []
    for airport in airports:
        distance = great_circle_nm(
            latitude, longitude, airport["latitude"], airport["longitude"]
        )
        if distance <= radius_nm:
            nearby.append({**airport, "distance_nm": round(distance, 1)})
    nearby.sort(key=lambda a: a["distance_nm"])

    window, page = paginate(nearby, offset, limit)
    center = {"latitude": latitude, "longitude": longitude}

    if response_format is ResponseFormat.JSON:
        return FacilityList(page=page, center=center, radius_nm=radius_nm, results=window)

    markdown = render_paginated_table(
        window, page, AIRPORT_COLUMNS, title=f"Airports within {radius_nm} nm"
    )
    return FacilityList(page=page, center=center, radius_nm=radius_nm, markdown=markdown)


@handle_simconnect_errors
@require_connection
async def get_facility_info(
    icao: Annotated[
        str,
        Field(description="ICAO identifier, e.g. 'KJFK', 'EGLL', 'SEA'",
              min_length=2, max_length=8),
    ],
    facility_type: Annotated[
        Literal["airport", "waypoint", "ndb", "vor"],
        Field(description="Kind of facility to look up: one of 'airport', "
                          "'waypoint', 'ndb', or 'vor'"),
    ] = "airport",
) -> FacilityInfo | ToolError:
    """Look up one airport, waypoint, NDB or VOR by ICAO identifier.

    Only facilities the sim currently has loaded are visible. Airports are
    the exception: SimConnect's airport facility list is the complete
    worldwide set (measured live -- see module docstring), not scoped to
    the aircraft's location, so an airport miss means the identifier is
    wrong rather than out of range.
    """
    kind = FacilityKind(facility_type)
    entries = await _collect(kind)
    if isinstance(entries, ToolError):
        return entries

    needle = icao.strip().upper()
    for entry in entries:
        if entry["icao"].upper() == needle:
            return FacilityInfo(facility=entry)

    if kind is FacilityKind.AIRPORT:
        suggestion = (
            "Airports are matched against the sim's complete worldwide list, "
            "already loaded regardless of position, so this ICAO likely doesn't "
            "exist or is misspelled. Try msfs_get_nearby_airports with a wide "
            "radius to browse what's available."
        )
    else:
        suggestion = (
            "The sim only publishes facilities it has loaded, typically the "
            "area around the aircraft. Fly closer, or double-check the "
            "identifier's spelling."
        )

    return ToolError(
        error="FACILITY_NOT_FOUND",
        message=f"No {facility_type} '{icao}' among the {len(entries)} loaded.",
        suggestion=suggestion,
    )
