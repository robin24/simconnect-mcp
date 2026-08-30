"""Tests for the connection lifecycle tools: connect_to_sim, disconnect_from_sim,
get_connection_status.

Before Task 8 these three returned SimConnectManager's dict verbatim; now
they translate it into ConnectionStatus | ToolError -- connect_to_sim
builds a ToolError from connect()'s error envelope, and all three merge
get_status()'s dict with a `message` sourced from a *different* dict
(connect()'s or disconnect()'s own message, not get_status()'s -- which
never sets one). That merge is new code with no other coverage:
test_logging.py's source-text check only greps for `get_running_loop()`,
it never calls these functions, so a future change to connect()'s dict
shape, or a typo in the kwarg merge, would go uncaught without tests like
these.
"""

from __future__ import annotations

from unittest.mock import patch

from simconnect_mcp.connection import SimConnectManager
from simconnect_mcp.tools import connection_tools
from simconnect_mcp.tools.models import ConnectionStatus, ToolError


async def test_connect_to_sim_error_envelope_keeps_the_managers_own_fields():
    """The manager's own error code/message/suggestion must pass through
    unchanged -- not be replaced by an invented one. Uses a made-up error
    code that connection.py's real connect() would never actually produce,
    so a pass here is only possible via genuine pass-through, not
    coincidental agreement with one of the two real codes it does use."""
    manager = SimConnectManager()
    with patch.object(manager, "connect", return_value={
        "status": "error",
        "error": "WEIRD_MADE_UP_CODE",
        "message": "something specific broke",
        "suggestion": "try the specific fix",
    }):
        result = await connection_tools.connect_to_sim()

    assert isinstance(result, ToolError)
    assert result.status == "error"
    assert result.error == "WEIRD_MADE_UP_CODE"
    assert result.message == "something specific broke"
    assert result.suggestion == "try the specific fix"


async def test_connect_to_sim_error_envelope_without_a_suggestion_key():
    """connect() is not contractually required to include a 'suggestion'
    key in its error envelope. ToolError.suggestion must degrade to None
    via .get(), not raise KeyError via [...]."""
    manager = SimConnectManager()
    with patch.object(manager, "connect", return_value={
        "status": "error",
        "error": "NOT_CONNECTED",
        "message": "no suggestion this time",
    }):
        result = await connection_tools.connect_to_sim()

    assert isinstance(result, ToolError)
    assert result.error == "NOT_CONNECTED"
    assert result.suggestion is None


async def test_connect_to_sim_success_returns_ok_status_and_carries_connects_message():
    """On success the returned ConnectionStatus must merge two different
    dicts: get_status()'s fields, plus a `message` that comes from
    connect()'s dict specifically (get_status() never sets one)."""
    manager = SimConnectManager()
    with patch.object(manager, "connect", return_value={
        "status": "ok", "message": "Connected to MSFS", "mobiflight": True,
    }), patch.object(manager, "get_status", return_value={
        "state": "connected", "connected": True, "mobiflight_available": True,
        "sim_paused": False, "sim_running": True,
    }):
        result = await connection_tools.connect_to_sim()

    assert isinstance(result, ConnectionStatus)
    assert result.status == "ok"
    assert result.connected is True
    assert result.mobiflight_available is True
    assert result.sim_paused is False
    assert result.sim_running is True
    assert result.message == "Connected to MSFS"


async def test_disconnect_from_sim_leaves_sim_state_fields_none_when_disconnected():
    """A disconnected get_status() omits sim_paused/sim_running entirely
    (they're only read while connected) -- they must come through as
    None, not be fabricated or raise a missing-key error. `message` must
    come from disconnect()'s dict, not get_status()'s."""
    manager = SimConnectManager()
    with patch.object(manager, "disconnect", return_value={
        "status": "ok", "message": "Disconnected",
    }), patch.object(manager, "get_status", return_value={
        "state": "disconnected", "connected": False, "mobiflight_available": False,
    }):
        result = await connection_tools.disconnect_from_sim()

    assert isinstance(result, ConnectionStatus)
    assert result.status == "ok"
    assert result.connected is False
    assert result.message == "Disconnected"
    assert result.sim_paused is None
    assert result.sim_running is None


async def test_get_connection_status_reports_ok_with_no_message():
    """get_status() never sets a message key -- this is the one tool of
    the three where ConnectionStatus.message is always None."""
    manager = SimConnectManager()
    with patch.object(manager, "get_status", return_value={
        "state": "disconnected", "connected": False, "mobiflight_available": False,
    }):
        result = await connection_tools.get_connection_status()

    assert isinstance(result, ConnectionStatus)
    assert result.status == "ok"
    assert result.connected is False
    assert result.message is None
    assert result.sim_paused is None
    assert result.sim_running is None
