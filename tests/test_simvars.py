"""Tests for SimVar tools."""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from simconnect_mcp.simvar_access import SimVarNotFoundError, SimVarTimeoutError
from simconnect_mcp.tools.simvars import (
    get_simvar,
    get_simvar_bulk,
    list_simvar_categories,
    search_simvars,
    set_simvar,
)


@pytest.mark.asyncio
async def test_get_simvar(mock_simconnect):
    """Reading a SimVar returns value."""
    result = await get_simvar("PLANE_LATITUDE")
    assert result["status"] == "ok"
    assert result["value"] == 47.6062
    assert result["name"] == "PLANE_LATITUDE"


@pytest.mark.asyncio
async def test_get_simvar_not_found(mock_simconnect):
    """Reading a nonexistent SimVar returns error with suggestions."""
    mock_simconnect["accessor"].read.side_effect = SimVarNotFoundError("not found")
    result = await get_simvar("NONEXISTENT_VAR")
    assert result["status"] == "error"
    assert result["error"] == "SIMVAR_NOT_FOUND"


@pytest.mark.asyncio
async def test_set_simvar(mock_simconnect):
    """Writing a SimVar succeeds."""
    result = await set_simvar("PLANE_LATITUDE", 48.0)
    assert result["status"] == "ok"
    assert result["value_set"] == 48.0
    mock_simconnect["aq"].set.assert_called_with("PLANE_LATITUDE", 48.0)


@pytest.mark.asyncio
async def test_get_simvar_bulk(mock_simconnect):
    """Bulk read returns multiple values."""
    result = await get_simvar_bulk([
        {"name": "PLANE_LATITUDE"},
        {"name": "AIRSPEED_INDICATED"},
    ])
    assert result["status"] == "ok"
    assert "PLANE_LATITUDE" in result["variables"]
    assert "AIRSPEED_INDICATED" in result["variables"]


@pytest.mark.asyncio
async def test_search_simvars():
    """Search returns matching SimVars."""
    result = await search_simvars("altitude")
    assert result["status"] == "ok"
    assert result["count"] > 0
    names = [r["name"] for r in result["results"]]
    assert any("ALTITUDE" in n for n in names)


@pytest.mark.asyncio
async def test_search_simvars_with_category():
    """Search with category filter narrows results."""
    result = await search_simvars("altitude", category="Aircraft Position")
    assert result["status"] == "ok"
    for r in result["results"]:
        assert r["category"] == "Aircraft Position"


@pytest.mark.asyncio
async def test_list_categories():
    """List categories returns categories with counts."""
    result = await list_simvar_categories()
    assert result["status"] == "ok"
    assert "categories" in result
    assert result["total_variables"] > 0


@pytest.mark.asyncio
async def test_get_simvar_not_connected():
    """Reading without connection returns error when sim is unavailable."""
    with patch.dict(sys.modules, {"SimConnect": None}):
        result = await get_simvar("PLANE_LATITUDE")
        assert result["status"] == "error"


def test_title_mock_returns_bytes_like_the_real_sim(mock_simconnect):
    """The sim returns bytes for string SimVars; the mock must match or
    bytes-handling bugs stay invisible to the suite."""
    assert isinstance(mock_simconnect["aq"].get("TITLE"), bytes)


@pytest.mark.asyncio
async def test_get_simvar_reports_the_unit_actually_used(mock_simconnect):
    result = await get_simvar("PLANE_ALTITUDE", unit="meters")
    assert result["status"] == "ok"
    assert result["unit"] == "meters", "the old code echoed 'default' and ignored the unit"


@pytest.mark.asyncio
async def test_get_simvar_passes_unit_through_to_the_accessor(mock_simconnect):
    await get_simvar("PLANE_ALTITUDE", unit="meters")
    mock_simconnect["accessor"].read.assert_called_once()
    assert mock_simconnect["accessor"].read.call_args.kwargs["unit"] == "meters"


@pytest.mark.asyncio
async def test_unknown_simvar_returns_not_found_with_suggestions(mock_simconnect):
    """Previously unreachable: the branch above it raised ImportError."""
    mock_simconnect["accessor"].read.side_effect = SimVarNotFoundError("nope")
    result = await get_simvar("PLANE_ALTITUD")
    assert result["error"] == "SIMVAR_NOT_FOUND"
    assert "PLANE_ALTITUDE" in result["suggestions"]


@pytest.mark.asyncio
async def test_timeout_is_reported_as_its_own_error(mock_simconnect):
    mock_simconnect["accessor"].read.side_effect = SimVarTimeoutError("no response")
    result = await get_simvar("PLANE_ALTITUDE")
    assert result["error"] == "SIM_TIMEOUT"
    assert "suggestion" in result


@pytest.mark.asyncio
async def test_index_zero_is_passed_through(mock_simconnect):
    await get_simvar("GENERAL_ENG_THROTTLE_LEVER_POSITION", index=0)
    assert mock_simconnect["accessor"].read.call_args.kwargs["index"] == 0


@pytest.mark.asyncio
async def test_bad_unit_on_a_known_var_reports_unit_mismatch(mock_simconnect):
    """SimConnect raises NAME_UNRECOGNIZED for a bad unit AND a bad name.
    The catalog disambiguates: PLANE_ALTITUDE exists, so the unit is at fault."""
    mock_simconnect["accessor"].read.side_effect = SimVarNotFoundError("nope")
    result = await get_simvar("PLANE_ALTITUDE", unit="bananas")
    assert result["error"] == "UNIT_MISMATCH"
    assert "bananas" in result["message"]
    assert "Feet" in result["suggestion"] or "feet" in result["suggestion"].lower()
    assert "suggestions" not in result, "must not suggest names when the name was fine"


@pytest.mark.asyncio
async def test_bad_name_still_reports_not_found_with_suggestions(mock_simconnect):
    mock_simconnect["accessor"].read.side_effect = SimVarNotFoundError("nope")
    result = await get_simvar("PLANE_ALTITUD", unit="feet")
    assert result["error"] == "SIMVAR_NOT_FOUND"
    assert "PLANE_ALTITUDE" in result["suggestions"]


@pytest.mark.asyncio
async def test_unknown_var_without_a_unit_reports_not_found(mock_simconnect):
    mock_simconnect["accessor"].read.side_effect = SimVarNotFoundError("nope")
    result = await get_simvar("TOTALLY_MADE_UP_VAR")
    assert result["error"] == "SIMVAR_NOT_FOUND"


@pytest.mark.asyncio
async def test_known_var_failing_without_a_caller_unit_is_not_blamed_on_the_unit(mock_simconnect):
    """unit=None means we used the catalog's own unit, so a failure there is
    a real name/catalog problem, not the caller's mistake."""
    mock_simconnect["accessor"].read.side_effect = SimVarNotFoundError("nope")
    result = await get_simvar("PLANE_ALTITUDE")
    assert result["error"] == "SIMVAR_NOT_FOUND"
