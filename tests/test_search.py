"""Tests for search functionality across SimVars and events."""

from __future__ import annotations

import pytest

from simconnect_mcp.tools.simvars import search_simvars, _search_catalog, _fuzzy_suggest
from simconnect_mcp.tools.events import search_events, _search_events


@pytest.mark.asyncio
async def test_simvar_search_case_insensitive():
    """Search is case-insensitive."""
    result1 = await search_simvars("ALTITUDE")
    result2 = await search_simvars("altitude")
    assert result1["count"] == result2["count"]


@pytest.mark.asyncio
async def test_simvar_search_no_results():
    """Search with no matches returns empty results."""
    result = await search_simvars("xyznonexistent123")
    assert result["status"] == "ok"
    assert result["count"] == 0


@pytest.mark.asyncio
async def test_event_search_case_insensitive():
    """Event search is case-insensitive."""
    result1 = await search_events("LIGHT")
    result2 = await search_events("light")
    assert result1["count"] == result2["count"]


def test_fuzzy_suggest():
    """Fuzzy suggestions return similar variable names."""
    suggestions = _fuzzy_suggest("PLANE_LAT")
    assert len(suggestions) > 0
    assert any("LATITUDE" in s or "PLANE" in s for s in suggestions)


def test_search_catalog_capped():
    """Search results are capped at 50."""
    # Search for something very broad
    results = _search_catalog("a")
    assert len(results) <= 50


def test_event_search_capped():
    """Event search results are capped at 50."""
    results = _search_events("a")
    assert len(results) <= 50
