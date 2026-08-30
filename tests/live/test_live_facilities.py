"""Live verification of the facilities tools.

Run with MSFS running and an aircraft loaded:  uv run pytest -m live

The mocked suite (tests/test_facilities_tools.py) simulates SimConnect's
facility delivery with a hand-wired collector. These tests exercise the same
code paths against the real dispatcher, where SubscribeToFacilities really
does hand back the whole world (measured at 85,249 airports -- see the
addendum this task was built from) rather than a fixture's three rows.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.live


async def test_nearby_airports_returns_real_data(live_manager):
    from simconnect_mcp.tools.facilities import get_nearby_airports
    from simconnect_mcp.tools.formatting import ResponseFormat

    result = await get_nearby_airports(radius_nm=100, response_format=ResponseFormat.JSON)
    assert result.page.total > 0, "expected at least one loaded airport"
    assert all("icao" in a and "distance_nm" in a for a in result.results)


async def test_facility_lookup_round_trips(live_manager):
    """Take an ICAO from the nearby list and look it up directly."""
    from simconnect_mcp.tools.facilities import get_facility_info, get_nearby_airports
    from simconnect_mcp.tools.formatting import ResponseFormat

    nearby = await get_nearby_airports(radius_nm=100, limit=1,
                                       response_format=ResponseFormat.JSON)
    icao = nearby.results[0]["icao"]
    found = await get_facility_info(icao)
    assert found.facility["icao"] == icao


async def test_katl_and_alliv_appear_near_the_gate(live_manager):
    """KATL (33.6367, -84.4281) and the waypoint ALLIV (~33.674, -84.080)
    are this project's documented live anchors for a radius query."""
    from simconnect_mcp.tools.facilities import get_facility_info, get_nearby_airports
    from simconnect_mcp.tools.formatting import ResponseFormat

    nearby = await get_nearby_airports(
        latitude=33.6367, longitude=-84.4281, radius_nm=50,
        response_format=ResponseFormat.JSON,
    )
    assert "KATL" in [a["icao"] for a in nearby.results]

    alliv = await get_facility_info("ALLIV", facility_type="waypoint")
    assert alliv.facility["icao"] == "ALLIV"
    assert alliv.facility["latitude"] == pytest.approx(33.674, abs=0.05)
    assert alliv.facility["longitude"] == pytest.approx(-84.080, abs=0.05)


async def test_airport_list_is_cached_after_first_call(live_manager):
    """Addendum point 1, against the real dispatcher: after one successful
    collection, SimConnectManager should hold the whole parsed world list,
    which is what lets a later call skip re-subscribing entirely."""
    from simconnect_mcp.tools.facilities import get_nearby_airports
    from simconnect_mcp.tools.formatting import ResponseFormat

    result = await get_nearby_airports(radius_nm=50, response_format=ResponseFormat.JSON)
    assert result.page.total >= 0

    cached = live_manager.get_cached_facilities("airport")
    assert cached is not None
    assert len(cached) > 1000, "expected the whole-world airport list to be cached"
