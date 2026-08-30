"""Mocked tests for the HubHop tool surface (tools/hubhop.py).

No network access: every test here patches HubHopClient.fetch_all (or, for
the caching test, the urllib.request.urlopen call underneath it) rather
than hitting the real API. See tests/live/test_live_hubhop.py for coverage
against the real service.
"""
from __future__ import annotations

import asyncio
import json
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


@pytest.fixture(autouse=True)
def _reset_hubhop_client_cache():
    """tools/hubhop.py holds one HubHopClient for the process's lifetime
    (see its module docstring), not one per call. That singleton persists
    across tests in this file, so its in-memory cache is reset before and
    after every test -- otherwise a test that lets the real fetch_all run
    (only test_client_is_shared_and_cached_across_calls does) could leave
    state behind for whichever test happens to run next."""
    from simconnect_mcp.tools import hubhop as hubhop_tool

    hubhop_tool._client._cache = None
    yield
    hubhop_tool._client._cache = None


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
    msfs_trigger_custom_event), so it must actually render in the table,
    not just ride along in the JSON form."""
    from simconnect_mcp.tools.hubhop import search_hubhop

    result = await search_hubhop(query="landing light")
    assert result.markdown is not None
    assert "1 (>L:S_OH_EXT_LT_LAND_L)" in result.markdown


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

    class _FakeResponse:
        def __init__(self, payload: bytes) -> None:
            self._payload = payload

        def read(self) -> bytes:
            return self._payload

        def __enter__(self) -> _FakeResponse:
            return self

        def __exit__(self, *exc_info: object) -> bool:
            return False

    def fake_urlopen(req, timeout=None):
        calls.append(timeout)
        return _FakeResponse(json.dumps(PRESETS).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    first = await search_hubhop(query="autopilot", response_format=ResponseFormat.JSON)
    second = await search_hubhop(query="light", response_format=ResponseFormat.JSON)

    assert len(calls) == 1, "second call re-fetched instead of reusing the cache"
    assert first.page.total == 1
    assert second.page.total == 1
