"""Tests for search functionality across SimVars, events, and L-vars."""

from __future__ import annotations

import pytest

from simconnect_mcp.data.simvar_catalog import search_catalog, suggest_names
from simconnect_mcp.tools.events import _search_events, search_events
from simconnect_mcp.tools.formatting import ResponseFormat
from simconnect_mcp.tools.lvars import browse_lvar_catalog, search_lvars
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


# ---------------------------------------------------------------------------
# tools.lvars.browse_lvar_catalog / search_lvars
#
# Every test below fails against pre-Task-5 code: browse_lvar_catalog does
# not exist (ImportError at module load), and search_lvars does not yet
# accept limit/response_format keywords. mock_simconnect's default aircraft
# ("Boeing 747-8i") matches no bundled catalog's title_pattern, so every
# test that omits `catalog` exercises the auto-detect-failed path.
# ---------------------------------------------------------------------------


async def test_browse_with_no_arguments_lists_catalogs(mock_simconnect):
    result = await browse_lvar_catalog(response_format=ResponseFormat.JSON)
    assert result.catalogs is not None
    assert result.panels is None


async def test_browse_with_a_catalog_lists_its_panels(mock_simconnect):
    result = await browse_lvar_catalog(catalog="fenix_a320", response_format=ResponseFormat.JSON)
    assert result.panels is not None
    assert result.catalog == "fenix_a320"


async def test_browse_with_a_panel_lists_its_variables(mock_simconnect):
    """Adapted from the task brief, which paired catalog='fenix_a320' with
    panel='Signs'. Confirmed against src/simconnect_mcp/data/fenix_a320.json:
    the Fenix catalog has no panel named 'Signs' (its 26 panels are ADIRS,
    AIR CONDITIONING, ... SAFETY, WARNING, WEATHER RADAR) -- only pmdg_777
    has one. Calling it with the brief's literal arguments would return
    PANEL_NOT_FOUND, not the panel-found path this test exists to check, so
    it would not actually discriminate a correct implementation from a
    broken one. pmdg_777/'Signs' is real data (2 variables, verified via
    data.catalog.get_panel_variables) and is also what
    test_title_detection.py's test_panel_lookup_says_so_when_no_catalog_was_detected
    already relies on for the same panel name."""
    result = await browse_lvar_catalog(
        catalog="pmdg_777", panel="Signs", response_format=ResponseFormat.JSON
    )
    assert result.variables is not None
    assert result.panel


async def test_browse_unknown_panel_returns_error(mock_simconnect):
    result = await browse_lvar_catalog(panel="NoSuchPanel")
    assert result.error == "PANEL_NOT_FOUND"


async def test_search_lvars_is_not_capped_at_fifty(mock_simconnect):
    """data.catalog.search_catalog used to return early at 50 matches."""
    result = await search_lvars("s", limit=10, response_format=ResponseFormat.JSON)
    assert result.page.total > 50
    assert result.page.count == 10


async def test_browse_markdown_format_also_surfaces_the_disclosure(mock_simconnect):
    """The undetected-aircraft disclosure is exposed as a structured
    `message` field, but response_format defaults to markdown -- a caller
    reading only the rendered table (not `message`) must still see it.
    Fails against an implementation that sets `.message` but never appends
    it to `.markdown` (the task brief's own pseudocode does not append it;
    this is a Task-5 addition, not literally spelled out there)."""
    result = await browse_lvar_catalog()  # response_format defaults to markdown
    assert result.message is not None
    assert result.markdown is not None
    assert result.message in result.markdown
