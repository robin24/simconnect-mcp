"""Tests for the facilities tools (get_nearby_airports, get_facility_info).

_collect() resets the collector and (re)subscribes on every cache miss, so a
fixture that just pre-loads the collector once (and stops there) would have
its data wiped by that reset before the tool under test ever saw it. Instead,
`facility_sim` wires the mocked SubscribeToFacilities call itself to populate
the collector, mirroring how the real dispatch thread delivers data shortly
after a real subscribe call -- close enough that the poll loop's first check
already finds the list complete.
"""
from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from simconnect_mcp.facilities import FacilityKind
from simconnect_mcp.tools.formatting import ResponseFormat


class _Header:
    dwEntryNumber, dwOutOf, dwArraySize = 0, 1, 3


_AIRPORTS = [
    {"icao": "KSEA", "kind": "airport", "latitude": 47.4502,
     "longitude": -122.3088, "altitude_ft": 433.0},
    {"icao": "KBFI", "kind": "airport", "latitude": 47.5300,
     "longitude": -122.3020, "altitude_ft": 21.0},
    {"icao": "KPDX", "kind": "airport", "latitude": 45.5898,
     "longitude": -122.5951, "altitude_ft": 31.0},
]


@pytest.fixture
def facility_sim(mock_simconnect):
    """Give the mock a facility collector that fills in as soon as
    SubscribeToFacilities is called, so a cache-miss collection completes
    on the poll loop's very first check instead of timing out."""
    from simconnect_mcp.facilities import FacilityCollector

    collector = FacilityCollector()

    def _fake_subscribe(_hsim, _list_type, _request_id):
        collector.handle(FacilityKind.AIRPORT, _Header(), _AIRPORTS)

    mock_simconnect["sm"].dll.SubscribeToFacilities.side_effect = _fake_subscribe
    mock_simconnect["sm"].facilities = collector
    return mock_simconnect


async def test_nearby_airports_filters_by_radius(facility_sim):
    from simconnect_mcp.tools.facilities import get_nearby_airports

    result = await get_nearby_airports(
        latitude=47.45, longitude=-122.31, radius_nm=25,
        response_format=ResponseFormat.JSON,
    )
    icaos = [a["icao"] for a in result.results]
    assert "KSEA" in icaos
    assert "KBFI" in icaos
    assert "KPDX" not in icaos, "KPDX is ~129 nm away"


async def test_nearby_airports_are_sorted_by_distance(facility_sim):
    from simconnect_mcp.tools.facilities import get_nearby_airports

    result = await get_nearby_airports(
        latitude=47.45, longitude=-122.31, radius_nm=200,
        response_format=ResponseFormat.JSON,
    )
    distances = [a["distance_nm"] for a in result.results]
    assert distances == sorted(distances)


async def test_nearby_airports_paginate(facility_sim):
    """page.total must reflect all 3 in-range matches even though only 1
    comes back in this window (radius_nm=200, limit=1)."""
    from simconnect_mcp.tools.facilities import get_nearby_airports

    result = await get_nearby_airports(
        latitude=47.45, longitude=-122.31, radius_nm=200, limit=1,
        response_format=ResponseFormat.JSON,
    )
    assert result.page.count == 1
    assert result.page.has_more is True
    assert result.page.total == 3


async def test_radius_filter_runs_before_pagination_not_after(facility_sim):
    """MINOR 3 from review: the test above alone doesn't prove filter-
    before-paginate, since all 3 fixture airports are in range -- a
    filter-after-paginate bug would slice the same raw window and produce
    an identical result. This puts an out-of-range airport (KFAR, Fargo ND,
    ~1036 nm from the search centre) FIRST in raw arrival order, ahead of
    the in-range ones. A paginate-then-filter bug would slice raw[0:1] =
    [KFAR] before ever applying the radius check, returning an empty (or
    KFAR-tainted) window and the wrong total instead of the nearest
    in-range airport."""
    from simconnect_mcp.tools.facilities import get_nearby_airports

    collector = facility_sim["sm"].facilities
    out_of_range_first = [
        {"icao": "KFAR", "kind": "airport", "latitude": 46.9207,
         "longitude": -96.8206, "altitude_ft": 902.0},
        *_AIRPORTS,
    ]

    def _fake_subscribe(_hsim, _list_type, _request_id):
        collector.handle(FacilityKind.AIRPORT, _Header(), out_of_range_first)

    facility_sim["sm"].dll.SubscribeToFacilities.side_effect = _fake_subscribe

    result = await get_nearby_airports(
        latitude=47.45, longitude=-122.31, radius_nm=200, limit=1,
        response_format=ResponseFormat.JSON,
    )

    assert result.page.total == 3, "KFAR is ~1036 nm away and must not count as a match"
    assert result.results[0]["icao"] == "KSEA"
    assert result.page.has_more is True


async def test_nearby_airports_markdown_names_withheld_rows(facility_sim):
    """CLAUDE.md's whole-surface rule: markdown must go through
    render_paginated_table so a truncated page still discloses what it
    withheld, rather than silently reading as a complete listing."""
    from simconnect_mcp.tools.facilities import get_nearby_airports

    result = await get_nearby_airports(
        latitude=47.45, longitude=-122.31, radius_nm=200, limit=1,
        response_format=ResponseFormat.MARKDOWN,
    )
    assert result.results is None
    assert "2 more result" in result.markdown or "more result(s)" in result.markdown


async def test_nearby_airports_never_touches_the_stdout_printing_facilities_requests(
    facility_sim,
):
    """Carried over from the pre-implementation stub's guard (formerly
    tests/test_facilities.py, removed by this rewrite): manager.fr is the
    SimConnect library's own FacilitiesRequests, whose iteration prints to
    stdout and corrupts the JSON-RPC stream on a real MCP server (see
    dispatch.py's module docstring). The real implementation must reach
    facility data only through manager.sm.facilities/manager.sm.dll, never
    through manager.fr, including on a fully successful call -- not just
    while the tool was still a stub.

    IMPORTANT review finding: asserting only `manager.fr is None` afterward
    cannot fail. manager.fr is None from __init__ and nothing ever writes
    it, so that assertion passes whether or not the code under test touches
    it -- and it was only ever meaningful while the tool was a stub that
    executed nothing at all. Now that @handle_simconnect_errors wraps the
    tool with a bare `except Exception`, a hypothetical
    `manager.fr.get(...)` would raise AttributeError on a real None,
    which the decorator converts into an ordinary-looking ToolError rather
    than letting it surface -- so even a reintroduced reference to fr would
    not raise past this test and would not change fr's value either. Giving
    manager.fr a MagicMock makes a call to it observable via method_calls;
    asserting on the tool's own return value catches the case where a
    stray AttributeError gets silently absorbed into a ToolError instead
    of raising."""
    from simconnect_mcp.tools.facilities import get_nearby_airports

    facility_sim["manager"].fr = MagicMock()

    result = await get_nearby_airports(
        latitude=47.45, longitude=-122.31, radius_nm=200,
        response_format=ResponseFormat.JSON,
    )

    assert result.status == "ok"
    assert facility_sim["manager"].fr.method_calls == []


async def test_facility_info_finds_an_airport_case_insensitively(facility_sim):
    from simconnect_mcp.tools.facilities import get_facility_info

    result = await get_facility_info("ksea")
    assert result.facility["icao"] == "KSEA"


async def test_facility_info_reports_a_miss(facility_sim):
    from simconnect_mcp.tools.facilities import get_facility_info

    result = await get_facility_info("ZZZZ")
    assert result.error == "FACILITY_NOT_FOUND"
    assert "radius" in result.suggestion.lower() or "loaded" in result.suggestion.lower()


async def test_missing_position_without_an_accessor_reports_a_clean_error(mock_simconnect):
    """The plain-SimConnect fallback has no accessor. Omitting lat/lon there
    used to be reachable straight into `None.read_many(...)`, which
    handle_simconnect_errors' catch-all turns into a raw AttributeError
    leaking through the envelope -- exactly what require_connection's
    needs_accessor flag exists to prevent for tools whose primary job IS
    the accessor. This tool's primary job is the facility collector, not
    the accessor -- explicit lat/lon skips it entirely -- so the guard is
    inline rather than a blanket refusal via needs_accessor=True."""
    from simconnect_mcp.tools.facilities import get_nearby_airports

    mock_simconnect["manager"].accessor = None

    result = await get_nearby_airports(response_format=ResponseFormat.JSON)
    assert result.error == "POSITION_NOT_AVAILABLE"
    assert "latitude and longitude" in result.suggestion.lower()


async def test_second_call_is_served_from_cache_without_resubscribing(facility_sim):
    """Addendum point 1, AIRPORT only: measured live, the airport list is
    genuinely world-wide and does not change during a session, so a second
    call must not re-subscribe or re-run the filter's setup against fresh
    SimConnect traffic. (WAYPOINT/NDB/VOR are the opposite case -- see
    test_non_airport_kinds_are_never_cached below.)"""
    from simconnect_mcp.tools.facilities import get_nearby_airports

    first = await get_nearby_airports(
        latitude=47.45, longitude=-122.31, radius_nm=200,
        response_format=ResponseFormat.JSON,
    )
    assert facility_sim["sm"].dll.SubscribeToFacilities.call_count == 1

    second = await get_nearby_airports(
        latitude=47.45, longitude=-122.31, radius_nm=200,
        response_format=ResponseFormat.JSON,
    )
    assert facility_sim["sm"].dll.SubscribeToFacilities.call_count == 1
    assert {a["icao"] for a in second.results} == {a["icao"] for a in first.results}


async def test_cached_result_survives_pagination_and_radius_changes(facility_sim):
    """A cache hit must still re-run the radius filter/pagination per call
    -- the cache holds the raw world list, not a stale rendered answer."""
    from simconnect_mcp.tools.facilities import get_nearby_airports

    await get_nearby_airports(
        latitude=47.45, longitude=-122.31, radius_nm=200,
        response_format=ResponseFormat.JSON,
    )
    narrow = await get_nearby_airports(
        latitude=47.45, longitude=-122.31, radius_nm=25,
        response_format=ResponseFormat.JSON,
    )
    assert facility_sim["sm"].dll.SubscribeToFacilities.call_count == 1
    assert "KPDX" not in [a["icao"] for a in narrow.results]


@pytest.mark.parametrize("kind", [FacilityKind.WAYPOINT, FacilityKind.NDB, FacilityKind.VOR])
async def test_non_airport_kinds_are_never_cached(facility_sim, kind):
    """Correction to the addendum: measured live against the same running
    sim, WAYPOINT/NDB/VOR are a position-scoped "reality bubble" (every
    facility within ~193 nm of the aircraft), unlike AIRPORT's genuinely
    world-wide list. Caching them would keep serving facilities from
    wherever the aircraft *used to be* after a reposition (or just flying)
    with no signal to the caller -- the same defect class as Phase 1's PMDG
    variant cache bug. Fails against a version of _collect that caches
    every kind uniformly (this test's first call would leave the second
    served from cache, dropping SubscribeToFacilities.call_count to 1)."""
    from simconnect_mcp.tools.facilities import get_facility_info

    collector = facility_sim["sm"].facilities
    entry = {
        "icao": "TEST1", "kind": kind.value, "latitude": 33.7, "longitude": -84.1,
        "altitude_ft": 0.0,
    }

    def _fake_subscribe(_hsim, _list_type, _request_id):
        collector.handle(kind, _Header(), [entry])

    facility_sim["sm"].dll.SubscribeToFacilities.side_effect = _fake_subscribe

    first = await get_facility_info("TEST1", facility_type=kind.value)
    second = await get_facility_info("TEST1", facility_type=kind.value)

    assert first.facility["icao"] == "TEST1"
    assert second.facility["icao"] == "TEST1"
    assert facility_sim["sm"].dll.SubscribeToFacilities.call_count == 2, (
        f"a {kind.value} lookup must re-collect on every call, never serve a cached result"
    )
    assert facility_sim["manager"].get_cached_facilities(kind.value) is None


async def test_unsubscribes_after_a_complete_collection(facility_sim):
    """Addendum point 2: a completed subscription must be torn down, or a
    later scenery change could re-fire ~85k facilities into a collector
    nothing is watching any more mid-way through an unrelated call."""
    from simconnect_mcp.tools.facilities import get_nearby_airports

    await get_nearby_airports(
        latitude=47.45, longitude=-122.31, radius_nm=200,
        response_format=ResponseFormat.JSON,
    )
    assert facility_sim["sm"].dll.UnsubscribeToFacilities.call_count == 1


async def test_a_cancelled_collection_still_unsubscribes(facility_sim):
    """IMPORTANT 1 from review: the poll loop awaits asyncio.sleep() on
    every iteration and can legitimately run for up to _COLLECT_TIMEOUT
    (5s) -- a real window for the caller's MCP request to be cancelled
    (notifications/cancelled cancels this coroutine's task). Without a
    try/finally around subscribe -> poll -> unsubscribe, a cancellation
    here skips _unsubscribe entirely, leaving an orphaned subscription that
    can go on delivering chunks nobody is watching -- which, without the
    request-id correlation fix, could silently contaminate a LATER,
    unrelated collection for the same kind. Fails against a version of
    _collect with no try/finally: UnsubscribeToFacilities is never called
    when the CancelledError is raised at the sleep and propagates straight
    out."""
    from simconnect_mcp.tools.facilities import get_nearby_airports

    # Never completes -- nothing calls collector.handle(), so the poll loop
    # is guaranteed to still be sleeping when this test cancels the task.
    facility_sim["sm"].dll.SubscribeToFacilities.side_effect = None

    task = asyncio.create_task(get_nearby_airports(
        latitude=47.45, longitude=-122.31, radius_nm=200,
        response_format=ResponseFormat.JSON,
    ))

    # Let the task actually start and reach the poll loop's first sleep.
    await asyncio.sleep(0.05)
    assert not task.done(), "the collection should still be polling"

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert facility_sim["sm"].dll.UnsubscribeToFacilities.call_count == 1

    # A follow-up call must still work normally -- proves the per-kind lock
    # was released (facility_lock's `async with` already guaranteed this)
    # and the collector/cache don't wedge after a cancellation.
    def _fake_subscribe(_hsim, _list_type, _request_id):
        facility_sim["sm"].facilities.handle(FacilityKind.AIRPORT, _Header(), _AIRPORTS)

    facility_sim["sm"].dll.SubscribeToFacilities.side_effect = _fake_subscribe

    follow_up = await get_nearby_airports(
        latitude=47.45, longitude=-122.31, radius_nm=200,
        response_format=ResponseFormat.JSON,
    )
    assert follow_up.status == "ok"


async def test_timeout_reports_an_error_not_a_partial_success(mock_simconnect, monkeypatch):
    """Addendum point 3: a collection that never completes must surface as
    a ToolError, never as a FacilityList quietly built from whatever
    partial data happened to be in the collector when the poll gave up."""
    from simconnect_mcp.facilities import FacilityCollector
    from simconnect_mcp.tools import facilities as facilities_module
    from simconnect_mcp.tools.facilities import get_nearby_airports

    monkeypatch.setattr(facilities_module, "_COLLECT_TIMEOUT", 0.2)
    monkeypatch.setattr(facilities_module, "_POLL_INTERVAL", 0.05)

    # A fresh collector that nothing ever populates -- SubscribeToFacilities
    # is left as a plain no-op mock, so is_complete() can never turn True.
    mock_simconnect["sm"].facilities = FacilityCollector()

    result = await get_nearby_airports(
        latitude=47.45, longitude=-122.31, radius_nm=200,
        response_format=ResponseFormat.JSON,
    )

    assert result.status == "error"
    assert result.error == "FACILITY_COLLECTION_TIMEOUT"
    # And it must still clean up the subscription on the timeout path.
    assert mock_simconnect["sm"].dll.UnsubscribeToFacilities.call_count == 1
    # A timed-out collection must not be cached as though it succeeded.
    assert mock_simconnect["manager"].get_cached_facilities("airport") is None


@pytest.mark.parametrize(
    "kind", [FacilityKind.AIRPORT, FacilityKind.WAYPOINT, FacilityKind.NDB, FacilityKind.VOR]
)
async def test_concurrent_calls_do_not_race_the_collector_reset(facility_sim, kind):
    """Addendum point 2's race: the brief's collector.reset(kind)-then-
    subscribe is not atomic with the wait that follows it, so a second
    caller arriving mid-collection must not reset the buffer the first is
    still waiting to fill. facility_lock wraps _collect's whole body
    unconditionally, so this guard is identical for every kind regardless
    of whether it is cacheable (MINOR 5 from review) -- parametrized so a
    future change that only locks the cacheable path can't silently
    regress WAYPOINT/NDB/VOR.

    Proven deterministically (not via real-thread timing races) by holding
    the first call inside its SubscribeToFacilities call with a
    threading.Event until a second call has had a chance to reach (and
    block on) the per-kind lock. Uses get_facility_info rather than
    get_nearby_airports so the same test body works for all four kinds.

    What "task2 didn't race" looks like differs by cacheability, both
    correctly: for AIRPORT, task2 must find task1's result already cached
    and never subscribe again. For WAYPOINT/NDB/VOR (never cached, by
    design -- see _CACHEABLE_KINDS), task2 legitimately re-collects once
    task1's finally block releases the lock; what matters for those three
    is only that task2's own reset did not happen while task1 was still
    mid-flight (asserted below, before the gate opens), and that task2's
    own collection still comes back correct once it does run."""
    from simconnect_mcp.tools.facilities import _CACHEABLE_KINDS, get_facility_info

    collector = facility_sim["sm"].facilities
    entry = {
        "icao": "TEST1", "kind": kind.value, "latitude": 33.7, "longitude": -84.1,
        "altitude_ft": 0.0,
    }
    reset_calls = []
    real_reset = collector.reset

    def _spy_reset(k, request_id=None):
        reset_calls.append(k)
        real_reset(k, request_id)

    collector.reset = _spy_reset

    proceed = threading.Event()

    def _gated_subscribe(_hsim, _list_type, _request_id):
        proceed.wait(timeout=2.0)
        collector.handle(kind, _Header(), [entry])

    facility_sim["sm"].dll.SubscribeToFacilities.side_effect = _gated_subscribe

    task1 = asyncio.create_task(get_facility_info("TEST1", facility_type=kind.value))

    # Wait for task1 to actually reach the gated subscribe call (it resets
    # the collector immediately beforehand, on the same executor thread).
    for _ in range(50):
        await asyncio.sleep(0.01)
        if reset_calls:
            break
    assert reset_calls, "task1 never reached the subscribe step"

    task2 = asyncio.create_task(get_facility_info("TEST1", facility_type=kind.value))
    # Give task2 a real chance to run up to (and block on) the per-kind lock.
    await asyncio.sleep(0.05)
    assert len(reset_calls) == 1, "a concurrent caller reset the collector mid-flight"

    proceed.set()
    result1, result2 = await asyncio.gather(task1, task2)

    if kind in _CACHEABLE_KINDS:
        assert len(reset_calls) == 1, "task2 should have been served from cache, not resubscribed"
        assert facility_sim["sm"].dll.SubscribeToFacilities.call_count == 1
    else:
        # Correctly uncached (see _CACHEABLE_KINDS): task2 re-collects for
        # real once unblocked. The race guard is already proven above --
        # this just confirms task2's own, later collection wasn't corrupted.
        assert len(reset_calls) == 2
        assert facility_sim["sm"].dll.SubscribeToFacilities.call_count == 2

    assert result1.facility["icao"] == "TEST1"
    assert result2.facility["icao"] == "TEST1"
# ---------------------------------------------------------------------------
# Request-ID allocation (IMPORTANT from the whole-phase review). _subscribe
# used to call manager.sm.new_request_id() per collection. That library
# function rebuilds an Enum from every prior member on every call and never
# reclaims one -- the exact unbounded cost curve
# RequestRegistry.acquire_request_id was built to bound in Phase 0 (measured
# there at ~4.5ms per call after 600 allocations, ~31ms after 2000) and
# pinned by tests/test_simvar_access.py. This module's own cache policy made
# that a hot path rather than a one-off: WAYPOINT/NDB/VOR are deliberately
# re-collected on EVERY call, and the Enum is shared with SimVarAccessor, so
# the growth also slowed every later SimVar allocation that missed the
# free-list.
#
# The fix cannot collapse to one stable ID per kind: FacilityCollector.handle
# correlates chunks on dwRequestID specifically so a late chunk from an
# abandoned subscription cannot complete a LATER collection of the same kind
# on stale data. Hence a rotation -- bounded allocation AND a different ID
# from the collection before it. Both halves are asserted below.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", [FacilityKind.WAYPOINT, FacilityKind.NDB, FacilityKind.VOR])
async def test_repeated_collections_reuse_a_bounded_set_of_request_ids(facility_sim, kind):
    """Fails against the pre-fix _subscribe: six uncached collections called
    new_request_id() six times. Uses an uncached kind precisely because
    those are the ones this module re-collects on every call."""
    from simconnect_mcp.tools.facilities import _REQUEST_ID_RING, get_facility_info

    collector = facility_sim["sm"].facilities
    entry = {
        "icao": "TEST1", "kind": kind.value, "latitude": 33.7, "longitude": -84.1,
        "altitude_ft": 0.0,
    }

    def _fake_subscribe(_hsim, _list_type, _request_id):
        collector.handle(kind, _Header(), [entry])
        return 0

    facility_sim["sm"].dll.SubscribeToFacilities.side_effect = _fake_subscribe

    allocated = iter(range(500, 600))
    facility_sim["sm"].new_request_id.side_effect = lambda: SimpleNamespace(
        value=next(allocated)
    )
    facility_sim["sm"].registry.acquire_request_id.side_effect = (
        lambda allocate: allocate()
    )

    collections = 6
    for _ in range(collections):
        result = await get_facility_info("TEST1", facility_type=kind.value)
        assert result.facility["icao"] == "TEST1"

    assert facility_sim["sm"].dll.SubscribeToFacilities.call_count == collections
    assert facility_sim["sm"].new_request_id.call_count == _REQUEST_ID_RING, (
        f"{collections} collections allocated "
        f"{facility_sim['sm'].new_request_id.call_count} request IDs; the "
        f"reserved ring of {_REQUEST_ID_RING} should be the whole budget"
    )

    used = [
        call.args[2]
        for call in facility_sim["sm"].dll.SubscribeToFacilities.call_args_list
    ]
    assert len(set(used)) == _REQUEST_ID_RING
    assert all(a != b for a, b in zip(used, used[1:], strict=False)), (
        "consecutive collections of one kind reused the same request ID, "
        f"which defeats FacilityCollector.handle's correlation: {used}"
    )


async def test_each_kind_gets_its_own_reserved_request_ids(facility_sim):
    """Two kinds sharing an ID would let one kind's stale chunk correlate
    against the other's live subscription."""
    from simconnect_mcp.tools.facilities import get_facility_info

    collector = facility_sim["sm"].facilities
    seen: dict[str, list] = {}

    allocated = iter(range(500, 600))
    facility_sim["sm"].new_request_id.side_effect = lambda: SimpleNamespace(
        value=next(allocated)
    )
    facility_sim["sm"].registry.acquire_request_id.side_effect = (
        lambda allocate: allocate()
    )

    for kind in (FacilityKind.WAYPOINT, FacilityKind.NDB, FacilityKind.VOR):
        entry = {
            "icao": "TEST1", "kind": kind.value, "latitude": 33.7,
            "longitude": -84.1, "altitude_ft": 0.0,
        }

        def _fake_subscribe(_hsim, _list_type, request_id, _kind=kind, _entry=entry):
            seen.setdefault(_kind.value, []).append(request_id)
            collector.handle(_kind, _Header(), [_entry])
            return 0

        facility_sim["sm"].dll.SubscribeToFacilities.side_effect = _fake_subscribe
        await get_facility_info("TEST1", facility_type=kind.value)

    flat = [rid for ids in seen.values() for rid in ids]
    assert len(seen) == 3
    assert len(set(flat)) == len(flat), f"kinds shared a request ID: {seen}"


# ---------------------------------------------------------------------------
# SubscribeToFacilities' HRESULT (IMPORTANT from the whole-phase review, the
# lower-stakes half of F1). SimConnect_SubscribeToFacilities' restype is
# HRESULT and _subscribe threw it away. A failed subscribe does not fabricate
# success -- the poll simply times out -- but it then reported
# FACILITY_COLLECTION_TIMEOUT and blamed a paused or still-loading sim, a
# diagnosis the code had the evidence to contradict.
# ---------------------------------------------------------------------------


async def test_a_rejected_subscription_is_not_reported_as_a_busy_sim(
    mock_simconnect, monkeypatch
):
    """Fails against the pre-fix _subscribe, which discarded the HRESULT and
    let this surface as FACILITY_COLLECTION_TIMEOUT after the full wait."""
    from simconnect_mcp.facilities import FacilityCollector
    from simconnect_mcp.tools import facilities as facilities_module
    from simconnect_mcp.tools.facilities import get_nearby_airports

    monkeypatch.setattr(facilities_module, "_COLLECT_TIMEOUT", 3.0)
    monkeypatch.setattr(facilities_module, "_POLL_INTERVAL", 0.05)

    mock_simconnect["sm"].facilities = FacilityCollector()
    mock_simconnect["sm"].dll.SubscribeToFacilities.return_value = 0x80004005
    mock_simconnect["sm"].IsHR.return_value = False

    started = time.monotonic()
    result = await get_nearby_airports(
        latitude=47.45, longitude=-122.31, radius_nm=200,
        response_format=ResponseFormat.JSON,
    )
    elapsed = time.monotonic() - started

    assert result.status == "error"
    assert result.error == "FACILITY_SUBSCRIBE_FAILED"
    assert result.suggestion
    assert "paused" not in (result.message + result.suggestion).lower(), (
        "a rejected subscribe must not be blamed on a paused or busy sim"
    )
    assert elapsed < 1.0, (
        f"waited {elapsed:.2f}s polling for data a rejected subscription was "
        "never going to deliver"
    )
    # The teardown still runs, even though the subscribe was refused.
    assert mock_simconnect["sm"].dll.UnsubscribeToFacilities.call_count == 1
    assert mock_simconnect["manager"].get_cached_facilities("airport") is None
