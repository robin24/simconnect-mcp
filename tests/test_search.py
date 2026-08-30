"""Tests for search functionality across SimVars, events, and L-vars."""

from __future__ import annotations

import pytest

from simconnect_mcp.data.simvar_catalog import search_catalog, suggest_names
from simconnect_mcp.tools.events import search_events
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


# ---------------------------------------------------------------------------
# browse_lvar_catalog's markdown must never truncate silently.
#
# Task 2 built build_search_result, whose markdown footer names the
# withheld rows and the offset to continue with. Task 5 reimplemented
# pagination for browse_lvar_catalog with paginate + render_table directly
# and dropped that footer, so all three of its branches truncated in
# silence -- in the DEFAULT format, and while search_lvars, in the same
# module, disclosed the remainder for the same edge.
#
# One test per branch, deliberately: the bug WAS that the branches
# diverged, so a single test over one of them could not have caught it.
# All three fail against the pre-fix code (verified by reverting each
# branch to render_table).
# ---------------------------------------------------------------------------

_MORE_MARKER = "more result(s)"


async def test_panel_branch_markdown_names_the_withheld_variables(mock_simconnect):
    """284 variables against the default limit of 25: 91% withheld.

    A client reading the table sees what looks like a complete listing of
    the COMMUNICATION panel unless the footer says otherwise.
    """
    result = await browse_lvar_catalog(catalog="pmdg_737", panel="COMMUNICATION")
    assert result.page.has_more and result.page.total > result.page.count
    assert _MORE_MARKER in result.markdown
    assert str(result.page.total - result.page.count) in result.markdown
    assert f"offset={result.page.next_offset}" in result.markdown


async def test_panel_list_branch_markdown_names_the_withheld_panels(mock_simconnect):
    """All three bundled catalogs have 26-28 panels against a default limit
    of 25, so the plain catalog listing hits this on every ordinary call."""
    result = await browse_lvar_catalog(catalog="pmdg_777")
    assert result.page.has_more, "pmdg_777 should have more panels than the default limit"
    assert _MORE_MARKER in result.markdown
    assert f"offset={result.page.next_offset}" in result.markdown


async def test_catalog_list_branch_markdown_names_the_withheld_catalogs(mock_simconnect):
    """The no-arguments branch. Only three catalogs ship, so this forces the
    edge with an explicit limit rather than waiting for a fourth to be
    added -- the branch's rendering is what is under test, not the fixture
    data's size."""
    result = await browse_lvar_catalog(limit=1)
    assert result.page.has_more
    assert _MORE_MARKER in result.markdown
    assert f"offset={result.page.next_offset}" in result.markdown


async def test_browse_markdown_has_no_footer_when_nothing_is_withheld(mock_simconnect):
    """The footer is a disclosure, not decoration: a complete page must not
    claim there is more to fetch."""
    result = await browse_lvar_catalog(limit=200)
    assert not result.page.has_more
    assert _MORE_MARKER not in result.markdown
