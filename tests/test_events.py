"""Tests for event tools."""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from simconnect_mcp.tools.events import trigger_event, search_events


@pytest.mark.asyncio
async def test_trigger_event(mock_simconnect):
    """Triggering an event succeeds."""
    result = await trigger_event("PARKING_BRAKES")
    assert result["status"] == "ok"
    assert result["event"] == "PARKING_BRAKES"
    mock_simconnect["ae"].find.assert_called_with("PARKING_BRAKES")
    mock_simconnect["event"].assert_called_once()


@pytest.mark.asyncio
async def test_trigger_event_with_parameter(mock_simconnect):
    """Triggering an event with parameter passes it through."""
    result = await trigger_event("THROTTLE_SET", parameter=8192)
    assert result["status"] == "ok"
    assert result["parameter"] == 8192
    mock_simconnect["event"].assert_called_once_with(8192)


@pytest.mark.asyncio
async def test_search_events():
    """Search events returns results."""
    result = await search_events("autopilot")
    assert result["status"] == "ok"
    assert result["count"] > 0


@pytest.mark.asyncio
async def test_search_events_with_category():
    """Search events with category filter."""
    result = await search_events("master", category="Autopilot")
    assert result["status"] == "ok"
    for r in result["results"]:
        assert r["category"] == "Autopilot"


@pytest.mark.asyncio
async def test_trigger_event_not_connected():
    """Triggering event without connection returns error when sim is unavailable."""
    with patch.dict(sys.modules, {"SimConnect": None}):
        result = await trigger_event("PARKING_BRAKES")
        assert result["status"] == "error"
