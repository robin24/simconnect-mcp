"""Tests for event tools."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError as MCPToolError

from simconnect_mcp.tools.events import search_events, trigger_custom_event, trigger_event
from simconnect_mcp.tools.formatting import ResponseFormat
from simconnect_mcp.tools.models import EventResult, SearchResult
from simconnect_mcp.vendor.mobiflight_variable_requests import MobiFlightVariableRequests


@pytest.mark.asyncio
async def test_trigger_event_returns_a_model(mock_simconnect):
    """Brief's own test: trigger_event must return the EventResult model,
    not a dict, and the default mock (ae.find succeeds) resolves via the
    static catalog."""
    result = await trigger_event("PARKING_BRAKES")
    assert isinstance(result, EventResult)
    assert result.resolved_via == "catalog"


@pytest.mark.asyncio
async def test_trigger_event(mock_simconnect):
    """Triggering an event succeeds.

    The catalog branch now correlates through map_to_sim_event/send_event
    like the mapped branch does (see events.py's _fire()), rather than
    calling the found Event object directly -- so this asserts on the DLL
    call, not on ae.find()'s returned mock being invoked."""
    result = await trigger_event("PARKING_BRAKES")
    assert result.status == "ok"
    assert result.event == "PARKING_BRAKES"
    mock_simconnect["ae"].find.assert_called_with("PARKING_BRAKES")
    mock_simconnect["sm"].send_event.assert_called_once()


@pytest.mark.asyncio
async def test_trigger_event_with_parameter(mock_simconnect):
    """Triggering an event with parameter passes it through."""
    result = await trigger_event("THROTTLE_SET", parameter=8192)
    assert result.status == "ok"
    assert result.parameter == 8192
    sent = mock_simconnect["sm"].send_event.call_args.args[1]
    assert sent.value == 8192


@pytest.mark.asyncio
async def test_search_events_paginates_over_the_full_catalog(mock_simconnect):
    """Brief's own test: real pagination must span the library's full
    994-event catalog, not the 50-entry builtin fallback list."""
    result = await search_events("set", limit=10, response_format=ResponseFormat.JSON)
    assert isinstance(result, SearchResult)
    assert result.page.total > 50, "should span the 994-event catalog, not 50 builtins"
    assert result.page.count == 10


@pytest.mark.asyncio
async def test_search_events_defaults_to_markdown(mock_simconnect):
    result = await search_events("autopilot")
    assert result.markdown is not None
    assert result.results is None


@pytest.mark.asyncio
async def test_search_events_json_format_returns_rows(mock_simconnect):
    result = await search_events("autopilot", response_format=ResponseFormat.JSON)
    assert result.results is not None
    assert result.markdown is None


@pytest.mark.asyncio
async def test_search_events(mock_simconnect):
    """Search events returns results."""
    result = await search_events("autopilot")
    assert result.status == "ok"
    assert result.page.count > 0


@pytest.mark.asyncio
async def test_search_events_with_category(mock_simconnect):
    """Search events with category filter."""
    result = await search_events(
        "master", category="Autopilot", response_format=ResponseFormat.JSON
    )
    assert result.status == "ok"
    for r in result.results:
        assert r["category"] == "Autopilot"


@pytest.mark.asyncio
async def test_search_events_paginates_instead_of_truncating(mock_simconnect):
    """The old code sliced [:50] with no total and no signal to the caller;
    real pagination must expose has_more/next_offset and each page must
    actually advance through the catalog rather than repeating results."""
    first = await search_events("a", limit=10, offset=0, response_format=ResponseFormat.JSON)
    assert first.page.total > 50
    assert first.page.count == 10
    assert first.page.has_more is True

    second = await search_events("a", limit=10, offset=10, response_format=ResponseFormat.JSON)
    assert second.results[0] != first.results[0]


async def test_search_events_limit_over_bound_is_rejected_by_fastmcp():
    """le=200 must be enforced by FastMCP's own generated schema, not a soft
    clamp inside the tool body -- a direct Python call bypasses validation
    completely and would pass even with the Field bound deleted (confirmed:
    the pre-conversion search_events(keyword, category=None) has no `limit`
    parameter at all, so FastMCP's arg model silently drops the extra
    `limit` field -- Pydantic's default extra="ignore" -- and the call
    succeeds instead of raising, which is exactly why this must go through
    mcp.call_tool rather than a direct await).

    search_events carries no @require_connection, so routing this through a
    real FastMCP instance cannot open a SimConnect connection -- unlike
    trigger_event/trigger_custom_event, which do and must never be used for
    this kind of check (a previous task inadvertently connected to the
    user's live sim doing exactly that).
    """
    test_mcp = FastMCP("test-events")
    test_mcp.tool(name="msfs_search_events")(search_events)
    with pytest.raises(MCPToolError, match="200"):
        await test_mcp.call_tool("msfs_search_events", {"keyword": "a", "limit": 5000})


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
    found = events._matching_events("pushback")
    assert any("PUSHBACK" in e["name"].upper() for e in found)


@pytest.mark.asyncio
async def test_trigger_falls_back_to_map_to_sim_event(mock_simconnect):
    """Third-party and newer MSFS events are not in the library's static list."""
    mock_simconnect["ae"].find.return_value = None
    mock_simconnect["sm"].map_to_sim_event.return_value = MagicMock(value=99)

    result = await trigger_event("SOME_THIRD_PARTY_EVENT")

    assert result.status == "ok"
    assert result.resolved_via == "mapped"
    mock_simconnect["sm"].map_to_sim_event.assert_called_once_with(b"SOME_THIRD_PARTY_EVENT")


@pytest.mark.asyncio
async def test_negative_parameter_is_sent_as_twos_complement(mock_simconnect):
    """AP_VS_VAR_SET_ENGLISH needs negative values; send_event takes a DWORD."""
    await trigger_event("AP_VS_VAR_SET_ENGLISH", parameter=-1800)
    sent = mock_simconnect["sm"].send_event.call_args.args[1]
    assert sent.value == (-1800) & 0xFFFFFFFF


@pytest.mark.asyncio
async def test_positive_parameter_is_unchanged(mock_simconnect):
    await trigger_event("HEADING_BUG_SET", parameter=270)
    sent = mock_simconnect["sm"].send_event.call_args.args[1]
    assert sent.value == 270


@pytest.mark.asyncio
async def test_unmappable_event_returns_event_not_found(mock_simconnect):
    mock_simconnect["ae"].find.return_value = None
    mock_simconnect["sm"].map_to_sim_event.return_value = None

    result = await trigger_event("DEFINITELY_NOT_AN_EVENT")

    assert result.error == "EVENT_NOT_FOUND"
    assert "search_events" in result.suggestion


@pytest.mark.asyncio
async def test_known_event_reports_catalog_resolution(mock_simconnect):
    result = await trigger_event("PARKING_BRAKES")
    assert result.resolved_via == "catalog"


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

    assert result.status == "error"
    assert result.error == "EVENT_NOT_FOUND"


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

    assert result.status == "ok"
    assert result.resolved_via == "mapped"


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

    assert result.status == "ok"
    assert result.resolved_via == "mapped"


@pytest.mark.asyncio
async def test_custom_event_without_mobiflight_returns_error(mock_simconnect):
    mock_simconnect["manager"]._mobiflight_available = False
    result = await trigger_custom_event("MobiFlight.TEST")
    assert result.error == "MOBIFLIGHT_NOT_AVAILABLE"


@pytest.mark.asyncio
async def test_trigger_custom_event_success_returns_a_model(mock_simconnect):
    """The success path was previously untested -- mock_simconnect leaves
    _mobiflight_available False by default, so this wires up the MobiFlight
    mock explicitly rather than relying on fixture defaults."""
    mock_simconnect["manager"]._mobiflight_available = True
    mock_simconnect["manager"].mobiflight = MagicMock(spec=MobiFlightVariableRequests)

    result = await trigger_custom_event("MobiFlight.TEST", parameter=5)

    assert isinstance(result, EventResult)
    assert result.status == "ok"
    assert result.custom is True
    assert result.parameter == 5
    mock_simconnect["manager"].mobiflight.set.assert_called_once_with(
        "5 (>K:MobiFlight.TEST)"
    )


@pytest.mark.asyncio
async def test_trigger_custom_event_message_does_not_overclaim_confirmation(mock_simconnect):
    """B9: the MobiFlight WASM bridge writes to a client data area with no
    response channel read, so nothing here actually confirms the event
    fired -- the message must say it was sent, not that it "triggered
    successfully"."""
    mock_simconnect["manager"]._mobiflight_available = True
    mock_simconnect["manager"].mobiflight = MagicMock(spec=MobiFlightVariableRequests)

    result = await trigger_custom_event("MobiFlight.TEST")

    assert "successfully" not in result.message.lower()
    assert "sent" in result.message.lower()


@pytest.mark.asyncio
async def test_trigger_custom_event_sends_exact_rpn_without_parameter(mock_simconnect):
    """final-fix-C / C1: msfs_trigger_custom_event called
    manager.mobiflight.trigger_event(), a method that does not exist on
    MobiFlightVariableRequests -- confirmed live, every call raised
    AttributeError. The working mechanism (measured live: SIM_RATE_INCR
    delivered via this exact RPN form) is manager.mobiflight.set() with a
    `(>K:NAME)` key-event RPN string. Fails against the pre-fix code because
    the bare MagicMock().trigger_event(...) call succeeds silently and
    .set is never touched.
    """
    mock_simconnect["manager"]._mobiflight_available = True
    mobiflight = MagicMock(spec=MobiFlightVariableRequests)
    mock_simconnect["manager"].mobiflight = mobiflight

    await trigger_custom_event("PARKING_BRAKES")

    mobiflight.set.assert_called_once_with("(>K:PARKING_BRAKES)")


@pytest.mark.asyncio
async def test_trigger_custom_event_sends_exact_rpn_with_parameter(mock_simconnect):
    """Parameterised form: the value precedes the key-event RPN token, e.g.
    '8192 (>K:THROTTLE_SET)' -- the same convention execute_calculator_code
    already uses for direct-set events (see test_lvars.py)."""
    mock_simconnect["manager"]._mobiflight_available = True
    mobiflight = MagicMock(spec=MobiFlightVariableRequests)
    mock_simconnect["manager"].mobiflight = mobiflight

    await trigger_custom_event("THROTTLE_SET", parameter=8192)

    mobiflight.set.assert_called_once_with("8192 (>K:THROTTLE_SET)")
