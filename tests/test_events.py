"""Tests for event tools."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from simconnect_mcp.tools.events import search_events, trigger_event


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
async def test_search_events_reports_total_and_truncated():
    """Minor: results[:50] returned a count computed after slicing, with no
    way to tell 50-of-50 apart from 50-of-over-900 (the full library
    catalog)."""
    result = await search_events("a")
    assert result["count"] == 50
    assert result["total"] > 50
    assert result["truncated"] is True


@pytest.mark.asyncio
async def test_search_events_reports_not_truncated_when_under_the_cap():
    result = await search_events("xyznonexistenteventxyz")
    assert result["total"] == 0
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_trigger_event_not_connected():
    """Triggering event without connection returns error when sim is unavailable."""
    with patch.dict(sys.modules, {"SimConnect": None}):
        result = await trigger_event("PARKING_BRAKES")
        assert result.status == "error"


def test_event_catalog_loads_the_real_library_catalog():
    """The import was from the wrong module, silently degrading to 50 events."""
    from simconnect_mcp.tools import events

    events._EVENT_CATALOG = None
    events._FLAT_EVENTS = None
    catalog = events._load_event_catalog()

    total = sum(len(v) for v in catalog.values())
    assert total > 900, f"expected the full library catalog, got {total} events"
    assert len(catalog) >= 20


def test_search_events_finds_an_event_absent_from_the_builtin_list():
    """TOGGLE_PUSHBACK is in the library catalog but not the 50 builtins."""
    from simconnect_mcp.tools import events

    events._EVENT_CATALOG = None
    events._FLAT_EVENTS = None
    found = events._search_events("pushback")
    assert any("PUSHBACK" in e["name"].upper() for e in found)


@pytest.mark.asyncio
async def test_trigger_falls_back_to_map_to_sim_event(mock_simconnect):
    """Third-party and newer MSFS events are not in the library's static list."""
    mock_simconnect["ae"].find.return_value = None
    mock_simconnect["sm"].map_to_sim_event.return_value = MagicMock(value=99)

    result = await trigger_event("SOME_THIRD_PARTY_EVENT")

    assert result["status"] == "ok"
    assert result["resolved_via"] == "mapped"
    mock_simconnect["sm"].map_to_sim_event.assert_called_once_with(b"SOME_THIRD_PARTY_EVENT")


@pytest.mark.asyncio
async def test_negative_parameter_is_sent_as_twos_complement(mock_simconnect):
    """AP_VS_VAR_SET_ENGLISH needs negative values; send_event takes a DWORD."""
    await trigger_event("AP_VS_VAR_SET_ENGLISH", parameter=-1800)
    sent = mock_simconnect["event"].call_args.args[0]
    assert sent == (-1800) & 0xFFFFFFFF


@pytest.mark.asyncio
async def test_positive_parameter_is_unchanged(mock_simconnect):
    await trigger_event("HEADING_BUG_SET", parameter=270)
    assert mock_simconnect["event"].call_args.args[0] == 270


@pytest.mark.asyncio
async def test_unmappable_event_returns_event_not_found(mock_simconnect):
    mock_simconnect["ae"].find.return_value = None
    mock_simconnect["sm"].map_to_sim_event.return_value = None

    result = await trigger_event("DEFINITELY_NOT_AN_EVENT")

    assert result["error"] == "EVENT_NOT_FOUND"
    assert "search_events" in result["suggestion"]


@pytest.mark.asyncio
async def test_known_event_reports_catalog_resolution(mock_simconnect):
    result = await trigger_event("PARKING_BRAKES")
    assert result["resolved_via"] == "catalog"


@pytest.mark.asyncio
async def test_unknown_mapped_event_is_reported_not_faked(mock_simconnect):
    """MapClientEventToSimEvent succeeds for any string, so a non-None return
    proves nothing. The sim raises NAME_UNRECOGNIZED against the map packet."""
    import threading

    # Create a minimal mock registry with pending lock
    pending_ref = {"pending": None}

    class MockRegistry:
        def __init__(self):
            self.pending_lock = threading.Lock()
            self._by_send = {}

        def register(self, req):
            pending_ref["pending"] = req

        def bind_send_id(self, req, send_id, _locked=False):
            self._by_send[send_id] = req
            # Simulate exception being resolved after send_id is bound
            if send_id > 0:  # Second call (send_event)
                req.exception = "SIMCONNECT_EXCEPTION_NAME_UNRECOGNIZED"
                req.done.set()

        def discard(self, req):
            pass

    # Set up a mock registry with correlation
    mock_registry = MockRegistry()
    mock_simconnect["sm"].registry = mock_registry

    # ae.find misses, mapping "succeeds"
    mock_simconnect["ae"].find.return_value = None
    mock_simconnect["sm"].map_to_sim_event.return_value = MagicMock(value=99)

    # Mock GetLastSentPacketID to return incrementing send IDs
    send_id_counter = [0]
    def mock_get_last_packet_id(hSimConnect, out_dword):
        send_id_counter[0] += 1
        out_dword.value = send_id_counter[0]

    mock_simconnect["sm"].dll.GetLastSentPacketID = mock_get_last_packet_id

    result = await trigger_event("A_TOTALLY_MADE_UP_EVENT_XYZ")

    assert result["status"] == "error"
    assert result["error"] == "EVENT_NOT_FOUND"


@pytest.mark.asyncio
async def test_valid_mapped_event_still_succeeds(mock_simconnect):
    """No exception arrives -> the event is real and was sent."""
    import threading

    # Create a minimal mock registry with pending lock
    class MockRegistry:
        def __init__(self):
            self.pending_lock = threading.Lock()
            self._by_send = {}

        def register(self, req):
            pass

        def bind_send_id(self, req, send_id, _locked=False):
            self._by_send[send_id] = req
            # Signal success by setting done without exception
            req.done.set()

        def discard(self, req):
            pass

    # Set up a mock registry with correlation
    mock_registry = MockRegistry()
    mock_simconnect["sm"].registry = mock_registry

    # ae.find misses, mapping succeeds, no exception
    mock_simconnect["ae"].find.return_value = None
    mock_simconnect["sm"].map_to_sim_event.return_value = MagicMock(value=99)

    # Mock GetLastSentPacketID to return incrementing send IDs
    send_id_counter = [0]
    def mock_get_last_packet_id(hSimConnect, out_dword):
        send_id_counter[0] += 1
        out_dword.value = send_id_counter[0]

    mock_simconnect["sm"].dll.GetLastSentPacketID = mock_get_last_packet_id

    result = await trigger_event("SOME_THIRD_PARTY_EVENT")

    assert result["status"] == "ok"
    assert result["resolved_via"] == "mapped"


@pytest.mark.asyncio
async def test_missing_registry_falls_back_without_crashing(mock_simconnect):
    """Plain SimConnect fallback has no registry; correlation is skipped."""
    # Remove registry from the mock to simulate plain SimConnect
    if hasattr(mock_simconnect["sm"], "registry"):
        delattr(mock_simconnect["sm"], "registry")

    # ae.find misses, mapping succeeds, no exception (optimistic path)
    mock_simconnect["ae"].find.return_value = None
    mock_simconnect["sm"].map_to_sim_event.return_value = MagicMock(value=99)

    result = await trigger_event("SOME_EVENT")

    assert result["status"] == "ok"
    assert result["resolved_via"] == "mapped"
