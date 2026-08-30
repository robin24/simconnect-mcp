"""Tests for search functionality across SimVars and events."""

from __future__ import annotations

import pytest

from simconnect_mcp.data.simvar_catalog import search_catalog, suggest_names
from simconnect_mcp.tools.events import _search_events, search_events
from simconnect_mcp.tools.simvars import search_simvars


@pytest.mark.asyncio
async def test_simvar_search_case_insensitive():
    """Search is case-insensitive."""
    result1 = await search_simvars("ALTITUDE")
    result2 = await search_simvars("altitude")
    assert result1.page.count == result2.page.count


@pytest.mark.asyncio
async def test_simvar_search_no_results():
    """Search with no matches returns empty results."""
    result = await search_simvars("xyznonexistent123")
    assert result.status == "ok"
    assert result.page.count == 0


@pytest.mark.asyncio
async def test_event_search_case_insensitive():
    """Event search is case-insensitive."""
    result1 = await search_events("LIGHT")
    result2 = await search_events("light")
    assert result1.page.count == result2.page.count


def test_fuzzy_suggest():
    """Fuzzy suggestions return similar variable names."""
    suggestions = suggest_names("PLANE_LAT")
    assert len(suggestions) > 0
    assert any("LATITUDE" in s or "PLANE" in s for s in suggestions)


def test_search_catalog_uncapped():
    """Search results are uncapped — callers paginate."""
    # Search for something very broad
    results = search_catalog("a")
    # The actual catalog should have many more than 50 variables containing 'a'
    assert len(results) > 50


def test_event_search_capped():
    """Event search results are capped at 50."""
    results = _search_events("a")
    assert len(results) <= 50


def test_simvar_catalog_is_not_loaded_as_an_aircraft_catalog():
    """data/*.json also matches simvars_catalog.json, which has a different
    schema and would appear as a phantom aircraft with zero variables."""
    from simconnect_mcp.data.catalog import list_catalogs

    keys = {c["key"] for c in list_catalogs()}
    assert "simvars_catalog" not in keys
    assert all(c["variable_count"] > 0 for c in list_catalogs())
