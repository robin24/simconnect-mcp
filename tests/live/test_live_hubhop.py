"""Live verification of the HubHop tools against the real API.

Run with:  uv run pytest -m live

CATEGORY NOTE: every other file under this `live` marker needs a running
MSFS instance -- that is the marker's documented meaning (see its
declaration in pyproject.toml: "requires a running MSFS instance with an
aircraft loaded") and what tests/live/conftest.py's `live_manager` fixture
provides. These two tests need neither MSFS nor SimConnect -- only
internet access to HubHop's API. Reusing `live` here to mean "don't run
this by default" is a slight category error rather than a genuine fit;
it is done anyway because a marker that means "opt-in, needs an external
resource this suite can't fake" is close enough, and adding a second
marker for exactly one file felt like more machinery than the mismatch
is worth. Flagged here rather than quietly stretching what `live` means
for the next person who greps for it expecting "needs MSFS".

These tests intentionally avoid pinning exact counts (preset totals,
vendor lists) -- HubHop is a community database that grows over time, and
a test asserting today's exact number would fail on nothing but the
database's own healthy growth. They assert shape and filter correctness
instead: at least one aircraft has presets, filtering by a real vendor
actually narrows the results, and each preset carries the
fields the tool's markdown/JSON output depends on.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.live


async def test_search_hubhop_hits_the_real_api():
    from simconnect_mcp.tools.formatting import ResponseFormat
    from simconnect_mcp.tools.hubhop import search_hubhop

    result = await search_hubhop(
        vendor="FenixSim", limit=5, response_format=ResponseFormat.JSON
    )
    assert result.status == "ok", getattr(result, "message", result)
    assert result.page.total > 0
    assert len(result.results) > 0
    for preset in result.results:
        assert preset["vendor"] == "FenixSim"
        assert "code" in preset
        assert "label" in preset


async def test_list_hubhop_aircraft_hits_the_real_api():
    from simconnect_mcp.tools.hubhop import list_hubhop_aircraft

    result = await list_hubhop_aircraft(limit=5)
    assert result.status == "ok", getattr(result, "message", result)
    assert result.page.total > 0
    assert result.markdown is not None


async def test_search_hubhop_reports_missing_filter_without_network():
    """Sanity check that the NO_FILTER short-circuit still fires before any
    request goes out, even when this file's other tests prove the network
    path itself works."""
    from simconnect_mcp.tools.hubhop import search_hubhop

    result = await search_hubhop()
    assert result.error == "NO_FILTER"
