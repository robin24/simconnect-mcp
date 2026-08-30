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
    """Also proves the radius filter runs over the whole set before
    pagination: page.total must reflect all 3 in-range matches even though
    only 1 comes back in this window."""
    from simconnect_mcp.tools.facilities import get_nearby_airports

    result = await get_nearby_airports(
        latitude=47.45, longitude=-122.31, radius_nm=200, limit=1,
        response_format=ResponseFormat.JSON,
    )
    assert result.page.count == 1
    assert result.page.has_more is True
    assert result.page.total == 3


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
    while the tool was still a stub."""
    from simconnect_mcp.tools.facilities import get_nearby_airports

    await get_nearby_airports(
        latitude=47.45, longitude=-122.31, radius_nm=200,
        response_format=ResponseFormat.JSON,
    )
    # The mock fixture never sets manager.fr (stays None from __init__), so
    # any attribute access on it would raise -- reaching this assertion at
    # all is itself proof fr was never touched.
    assert facility_sim["manager"].fr is None


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
    assert result.error == "POSITION_UNAVAILABLE"
    assert "latitude and longitude" in result.suggestion.lower()


async def test_second_call_is_served_from_cache_without_resubscribing(facility_sim):
    """Addendum point 1: the world list does not change during a session,
    so a second call for the same kind must not re-subscribe or re-run the
    filter's setup against fresh SimConnect traffic."""
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


async def test_concurrent_calls_do_not_race_the_collector_reset(facility_sim):
    """Addendum point 2's race: the brief's collector.reset(kind)-then-
    subscribe is not atomic with the wait that follows it, so a second
    caller arriving mid-collection must not reset the buffer the first is
    still waiting to fill. Proven deterministically (not via real-thread
    timing races) by holding the first call inside its SubscribeToFacilities
    call with a threading.Event until a second call has had a chance to
    reach (and block on) the per-kind lock."""
    from simconnect_mcp.tools.facilities import get_nearby_airports

    collector = facility_sim["sm"].facilities
    reset_calls = []
    real_reset = collector.reset

    def _spy_reset(kind):
        reset_calls.append(kind)
        real_reset(kind)

    collector.reset = _spy_reset

    proceed = threading.Event()
    real_side_effect = facility_sim["sm"].dll.SubscribeToFacilities.side_effect

    def _gated_subscribe(*args):
        proceed.wait(timeout=2.0)
        return real_side_effect(*args)

    facility_sim["sm"].dll.SubscribeToFacilities.side_effect = _gated_subscribe

    task1 = asyncio.create_task(get_nearby_airports(
        latitude=47.45, longitude=-122.31, radius_nm=200,
        response_format=ResponseFormat.JSON,
    ))

    # Wait for task1 to actually reach the gated subscribe call (it resets
    # the collector immediately beforehand, on the same executor thread).
    for _ in range(50):
        await asyncio.sleep(0.01)
        if reset_calls:
            break
    assert reset_calls, "task1 never reached the subscribe step"

    task2 = asyncio.create_task(get_nearby_airports(
        latitude=47.45, longitude=-122.31, radius_nm=200,
        response_format=ResponseFormat.JSON,
    ))
    # Give task2 a real chance to run up to (and block on) the per-kind lock.
    await asyncio.sleep(0.05)
    assert len(reset_calls) == 1, "a concurrent caller reset the collector mid-flight"

    proceed.set()
    result1, result2 = await asyncio.gather(task1, task2)

    assert len(reset_calls) == 1, "task2 should have been served from cache, not resubscribed"
    assert facility_sim["sm"].dll.SubscribeToFacilities.call_count == 1
    assert {a["icao"] for a in result1.results} == {a["icao"] for a in result2.results}
