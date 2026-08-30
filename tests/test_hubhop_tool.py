"""Mocked tests for the HubHop tool surface (tools/hubhop.py).

No network access: every test here patches HubHopClient.fetch_all (or, for
the caching test, the urllib.request.urlopen call underneath it) rather
than hitting the real API. See tests/live/test_live_hubhop.py for coverage
against the real service.
"""
from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import patch

import pytest

from simconnect_mcp.tools.formatting import ResponseFormat

PRESETS = [
    {"label": "Autopilot 1 On", "vendor": "FenixSim", "aircraft": "A320",
     "system": "Autopilot", "presetType": "Input", "code": "1 (>L:S_FCU_AP1)"},
    {"label": "Landing Light On", "vendor": "FenixSim", "aircraft": "A320",
     "system": "Lights", "presetType": "Input", "code": "1 (>L:S_OH_EXT_LT_LAND_L)"},
    {"label": "Battery 1", "vendor": "PMDG", "aircraft": "B737-800",
     "system": "Electrical", "presetType": "Input", "code": "1 (>L:switch_1)"},
]


class _FakeResponse:
    """Minimal stand-in for the context manager urllib.request.urlopen
    returns, for tests that need the real fetch_all/fetch_presets caching
    logic to run rather than mocking HubHopClient.fetch_all away."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False


@pytest.fixture(autouse=True)
def _reset_hubhop_client_cache():
    """tools/hubhop.py holds one HubHopClient for the process's lifetime
    (see its module docstring), not one per call. That singleton persists
    across tests in this file, so its in-memory cache (and the timestamp
    the TTL check reads) is reset before and after every test -- otherwise
    a test that lets the real fetch_all run (the caching/refresh/
    concurrency tests below, which fake urlopen rather than fetch_all
    itself) could leave state behind for whichever test happens to run
    next."""
    from simconnect_mcp.tools import hubhop as hubhop_tool

    hubhop_tool._client._cache = None
    hubhop_tool._client._cache_time = None
    yield
    hubhop_tool._client._cache = None
    hubhop_tool._client._cache_time = None


@pytest.fixture
def hubhop_offline():
    with patch("simconnect_mcp.data.hubhop.HubHopClient.fetch_all", return_value=PRESETS):
        yield


async def test_search_matches_on_label(hubhop_offline):
    from simconnect_mcp.tools.hubhop import search_hubhop

    result = await search_hubhop(query="autopilot", response_format=ResponseFormat.JSON)
    assert result.page.total == 1
    assert result.results[0]["label"] == "Autopilot 1 On"


async def test_search_filters_by_vendor(hubhop_offline):
    from simconnect_mcp.tools.hubhop import search_hubhop

    result = await search_hubhop(vendor="PMDG", response_format=ResponseFormat.JSON)
    assert result.page.total == 1
    assert result.results[0]["vendor"] == "PMDG"


async def test_search_defaults_to_markdown(hubhop_offline):
    from simconnect_mcp.tools.hubhop import search_hubhop

    result = await search_hubhop(query="light")
    assert result.markdown is not None
    assert "Landing Light On" in result.markdown


async def test_search_surfaces_code_prominently_in_markdown(hubhop_offline):
    """The addendum calls out `code` as the field that makes a HubHop
    result actionable (it's RPN for msfs_execute_calculator_code /
    msfs_trigger_custom_event), so it must render near the front of the
    table -- specifically before Vendor -- not merely appear somewhere on
    the page. A substring-presence check alone would still pass with
    `code` moved back to the last column, which is the mistake this
    version of the test fixes: it asserts column order in the header row."""
    from simconnect_mcp.tools.hubhop import search_hubhop

    result = await search_hubhop(query="landing light")
    assert result.markdown is not None
    header_line = next(line for line in result.markdown.splitlines() if line.startswith("|"))
    assert "RPN Code" in header_line
    assert "Vendor" in header_line
    assert header_line.index("RPN Code") < header_line.index("Vendor")


async def test_search_requires_at_least_one_filter(hubhop_offline):
    from simconnect_mcp.tools.hubhop import search_hubhop

    result = await search_hubhop()
    assert result.error == "NO_FILTER"
    assert result.suggestion


async def test_network_failure_is_reported_actionably():
    from simconnect_mcp.tools.hubhop import search_hubhop

    with patch(
        "simconnect_mcp.data.hubhop.HubHopClient.fetch_all",
        side_effect=OSError("getaddrinfo failed"),
    ):
        result = await search_hubhop(query="autopilot")
    assert result.error == "HUBHOP_UNAVAILABLE"
    assert "internet" in result.suggestion.lower() or "offline" in result.suggestion.lower()


async def test_search_timeout_is_reported_distinctly_from_unavailable():
    """A slow-but-maybe-reachable API and a genuinely unreachable one
    warrant different advice (retry shortly vs. check your connection), so
    a timed-out fetch must not collapse into the same HUBHOP_UNAVAILABLE
    code as a DNS/connection failure -- the addendum calls this out
    explicitly: never report a timeout as a generic failure.

    asyncio.TimeoutError is raised directly by the mocked call (rather than
    actually waiting out _TIMEOUT_S) so this stays fast and deterministic;
    the exception is the same one asyncio.wait_for raises on a real
    expiry, so the except clause under test is exercised either way.
    """
    from simconnect_mcp.tools.hubhop import search_hubhop

    with patch(
        "simconnect_mcp.data.hubhop.HubHopClient.fetch_all",
        side_effect=asyncio.TimeoutError("timed out"),
    ):
        result = await search_hubhop(query="autopilot")
    assert result.error == "HUBHOP_TIMEOUT"
    assert result.suggestion
    assert "did not respond" in result.message.lower()


async def test_list_aircraft_groups_by_vendor(hubhop_offline):
    from simconnect_mcp.tools.hubhop import list_hubhop_aircraft

    result = await list_hubhop_aircraft(vendor="FenixSim", response_format=ResponseFormat.JSON)
    assert [a["aircraft"] for a in result.results] == ["A320"]


async def test_client_is_shared_and_cached_across_calls(monkeypatch):
    """Regression test for a bug in an earlier draft of this module: it
    built `client = HubHopClient()` fresh inside each tool call, which
    discarded HubHopClient's own in-memory cache (fetch_all's `self._cache`)
    the instant the call returned -- turning "the first call downloads the
    full database" into "every call downloads the full database". This
    drives the real (unmocked) fetch_all/fetch_presets so the assertion is
    against HubHopClient's own caching logic, not a mock standing in for
    it -- only the network call itself (urlopen) is faked.
    """
    from simconnect_mcp.tools.hubhop import search_hubhop

    calls: list[float | None] = []

    def fake_urlopen(req, timeout=None):
        calls.append(timeout)
        return _FakeResponse(json.dumps(PRESETS).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    first = await search_hubhop(query="autopilot", response_format=ResponseFormat.JSON)
    second = await search_hubhop(query="light", response_format=ResponseFormat.JSON)

    assert len(calls) == 1, "second call re-fetched instead of reusing the cache"
    assert first.page.total == 1
    assert second.page.total == 1


async def test_refresh_bypasses_the_cache_without_disabling_it(monkeypatch):
    """The adjudicated refresh=True parameter must force a re-fetch even
    with a warm cache, but the fetch it triggers still has to repopulate
    the cache -- otherwise every later call (with refresh left at its
    default False) would keep re-fetching too, and refresh would have
    quietly disabled caching instead of just bypassing it once."""
    from simconnect_mcp.tools.hubhop import search_hubhop

    calls: list[float | None] = []

    def fake_urlopen(req, timeout=None):
        calls.append(timeout)
        return _FakeResponse(json.dumps(PRESETS).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    await search_hubhop(query="autopilot", response_format=ResponseFormat.JSON)
    await search_hubhop(query="autopilot", response_format=ResponseFormat.JSON)
    assert len(calls) == 1, "second call should have hit the warm cache"

    await search_hubhop(
        query="autopilot", refresh=True, response_format=ResponseFormat.JSON
    )
    assert len(calls) == 2, "refresh=True should bypass the still-warm cache"

    await search_hubhop(query="autopilot", response_format=ResponseFormat.JSON)
    assert len(calls) == 2, (
        "refresh must not disable caching -- this call should reuse what "
        "the refresh call just repopulated"
    )


async def test_concurrent_search_and_list_share_one_fetch(monkeypatch):
    """The exact scenario Important-1 (Task 5 review) called out: an agent
    harness that pipelines tool calls firing msfs_search_hubhop and
    msfs_list_hubhop_aircraft together must not each pay for a full fetch.
    HubHopClient.fetch_all's lock (data/hubhop.py) is what prevents that;
    this drives it through the real tool coroutines rather than calling
    the client directly, matching the reported scenario end to end.
    """
    from simconnect_mcp.tools.hubhop import list_hubhop_aircraft, search_hubhop

    calls: list[float | None] = []

    def fake_urlopen(req, timeout=None):
        calls.append(timeout)
        time.sleep(0.1)  # hold the lock long enough that the other call must wait
        return _FakeResponse(json.dumps(PRESETS).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    search_result, list_result = await asyncio.gather(
        search_hubhop(query="autopilot", response_format=ResponseFormat.JSON),
        list_hubhop_aircraft(response_format=ResponseFormat.JSON),
    )

    assert len(calls) == 1, "both concurrent calls should share one fetch"
    assert search_result.status == "ok"
    assert list_result.status == "ok"


async def test_malformed_response_reports_bad_response_not_unexpected():
    """A json.JSONDecodeError (HTTP 200 with a truncated or otherwise
    malformed body) is a ValueError. It must not fall through to
    handle_simconnect_errors' catch-all, which would advise checking
    whether MSFS is running -- nonsensical for a response-parsing failure
    that has nothing to do with the simulator (Important-2, Task 5
    review)."""
    from simconnect_mcp.tools.hubhop import search_hubhop

    with patch(
        "simconnect_mcp.data.hubhop.HubHopClient.fetch_all",
        side_effect=json.JSONDecodeError("Expecting value", "", 0),
    ):
        result = await search_hubhop(query="autopilot")
    assert result.error == "HUBHOP_BAD_RESPONSE"
    assert result.suggestion
    assert "json" in result.message.lower()
    assert "MSFS" not in result.suggestion
