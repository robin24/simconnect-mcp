"""Tests for the shared tool decorators in tools/__init__.py.

handle_simconnect_errors and require_connection wrap nearly every tool.
This covers require_connection's needs_accessor gate directly -- both
decorator forms, and that the connection check still runs first -- and
then, end to end, every real tool site the phase-1 final review found
dereferencing manager.accessor without checking it first
(simvars.py:153,187,290,393, aircraft.py:146, utilities.py:136,151):
before this gate existed, each one turned a plain-SimConnect fallback
connection into `UNEXPECTED: 'NoneType' object has no attribute 'read'` --
a Python traceback leaking through the envelope -- instead of a typed
error. Parametrized over the real tool functions, not reimplemented per
module, so a future accessor-dependent tool that forgets needs_accessor=True
has to be added to ACCESSOR_DEPENDENT_TOOLS to go undetected, rather than
just quietly compiling.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from simconnect_mcp.tools import require_connection
from simconnect_mcp.tools.aircraft import get_aircraft_snapshot
from simconnect_mcp.tools.events import trigger_event
from simconnect_mcp.tools.lvars import get_lvar
from simconnect_mcp.tools.models import ToolError
from simconnect_mcp.tools.simvars import get_simvar, get_simvar_bulk, set_simvar, watch_simvar
from simconnect_mcp.tools.utilities import set_aircraft_position
from simconnect_mcp.vendor.mobiflight_variable_requests import MobiFlightVariableRequests

# ---------------------------------------------------------------------------
# The decorator in isolation
# ---------------------------------------------------------------------------


async def test_bare_decorator_ignores_a_missing_accessor(mock_simconnect):
    """@require_connection with no parens/arguments is how most of the
    surface uses it. needs_accessor must default to False, or every one of
    those tools would start failing on the plain-SimConnect fallback --
    exactly the blanket refusal the brief warns against."""
    mock_simconnect["manager"].accessor = None

    @require_connection
    async def _dummy_tool():
        return "ran"

    assert await _dummy_tool() == "ran"


async def test_needs_accessor_true_blocks_a_missing_accessor(mock_simconnect):
    mock_simconnect["manager"].accessor = None

    @require_connection(needs_accessor=True)
    async def _dummy_tool():
        return "ran"

    result = await _dummy_tool()

    assert isinstance(result, ToolError)
    assert result.error == "ACCESSOR_UNAVAILABLE"


async def test_needs_accessor_true_runs_normally_when_the_accessor_is_present(mock_simconnect):
    """The common case: a real dispatcher connection must be completely
    unaffected by this gate existing at all."""

    @require_connection(needs_accessor=True)
    async def _dummy_tool():
        return "ran"

    assert await _dummy_tool() == "ran"


async def test_connection_check_runs_before_the_accessor_check():
    """Not connected at all must report NOT_CONNECTED, never
    ACCESSOR_UNAVAILABLE -- the accessor question does not even make sense
    until a connection attempt has actually been made. SimConnect is
    patched out of sys.modules so connect() deterministically fails
    (mirroring test_simvars.py's test_get_simvar_not_connected) rather than
    depending on whatever this machine's real environment happens to do."""

    @require_connection(needs_accessor=True)
    async def _dummy_tool():
        return "ran"

    with patch.dict(sys.modules, {"SimConnect": None}):
        result = await _dummy_tool()

    assert isinstance(result, ToolError)
    assert result.error != "ACCESSOR_UNAVAILABLE"


def test_accessor_unavailable_is_a_fresh_instance_each_time():
    """Every other error site in this package builds a new ToolError per
    call; this one must not silently start sharing one mutable model
    instance across unrelated requests."""
    from simconnect_mcp.tools import _accessor_unavailable

    first = _accessor_unavailable()
    second = _accessor_unavailable()

    assert first is not second
    assert first == second


# ---------------------------------------------------------------------------
# Every real tool site the review named
# ---------------------------------------------------------------------------

ACCESSOR_DEPENDENT_TOOLS = [
    ("get_simvar", get_simvar, ("PLANE_ALTITUDE",), {}),
    ("set_simvar", set_simvar, ("PLANE_ALTITUDE", 1000.0), {}),
    ("get_simvar_bulk", get_simvar_bulk, ([{"name": "PLANE_ALTITUDE"}],), {}),
    ("watch_simvar", watch_simvar, ("PLANE_ALTITUDE",), {"duration_s": 1}),
    ("get_aircraft_snapshot", get_aircraft_snapshot, (), {}),
    ("set_aircraft_position", set_aircraft_position, (47.0, -122.0), {}),
]


@pytest.mark.parametrize(
    "name, fn, args, kwargs", ACCESSOR_DEPENDENT_TOOLS, ids=[t[0] for t in ACCESSOR_DEPENDENT_TOOLS]
)
async def test_accessor_dependent_tool_reports_accessor_unavailable(
    mock_simconnect, name, fn, args, kwargs
):
    """Before this gate, each of these raised a bare AttributeError from
    `manager.accessor.read(...)` (or `.write`/`.read_many`) that
    handle_simconnect_errors' catch-all turned into an UNEXPECTED envelope
    leaking Python exception text -- resources/state.py:32 was the only
    site that guarded this itself."""
    mock_simconnect["manager"].accessor = None

    result = await fn(*args, **kwargs)

    assert isinstance(result, ToolError), f"{name} returned {result!r}, not a ToolError"
    assert result.error == "ACCESSOR_UNAVAILABLE", f"{name} returned error={result.error!r}"


# ---------------------------------------------------------------------------
# The fallback must still work for tools that do not need the accessor
# ---------------------------------------------------------------------------


async def test_event_tools_are_unaffected_by_a_missing_accessor(mock_simconnect):
    """Events reach the sim through manager.ae/manager.sm, not the
    accessor -- a missing accessor must not degrade them at all."""
    mock_simconnect["manager"].accessor = None

    result = await trigger_event("PARKING_BRAKES")

    assert result.status == "ok"


async def test_mobiflight_lvar_tools_are_unaffected_by_a_missing_accessor(mock_simconnect):
    """L-var reads go through MobiFlight, not the accessor -- a missing
    accessor must not degrade them either (msfs_set_lvar is the one L-var
    tool that DOES need the accessor, and already guarded itself before
    this task; it is deliberately not part of this fallback check)."""
    mock_simconnect["manager"].accessor = None
    mock_simconnect["manager"]._mobiflight_available = True
    mock_simconnect["manager"].mobiflight = MagicMock(spec=MobiFlightVariableRequests)
    mock_simconnect["manager"].mobiflight.get.return_value = 1.0

    result = await get_lvar("A32NX_TEST")

    assert result.status == "ok"
