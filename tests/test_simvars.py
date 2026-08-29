"""Tests for SimVar tools."""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from simconnect_mcp.tools.simvars import (
    get_simvar,
    set_simvar,
    get_simvar_bulk,
    search_simvars,
    list_simvar_categories,
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
    mock_simconnect["aq"].get.return_value = None
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
