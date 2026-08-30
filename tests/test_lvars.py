"""Tests for the L-var CRUD tools: get_lvar, set_lvar, list_lvars,
execute_calculator_code, and the FastMCP Field-bound enforcement for
browse_lvar_catalog.

Before this file, none of these four tools had direct test coverage --
only their auto-detection/thread-safety siblings (search_lvars,
list_lvar_panels) were exercised, in test_title_detection.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError as MCPToolError

from simconnect_mcp.simvar_access import (
    SimVarNotFoundError,
    SimVarNotSettableError,
    SimVarTimeoutError,
)
from simconnect_mcp.tools.lvars import (
    browse_lvar_catalog,
    execute_calculator_code,
    get_lvar,
    list_lvars,
    set_lvar,
)
from simconnect_mcp.tools.models import CalculatorResult, LVarValue, LVarWriteResult, ToolError
from simconnect_mcp.vendor.mobiflight_variable_requests import MobiFlightVariableRequests


def _enable_mobiflight(mock_simconnect):
    """mock_simconnect leaves _mobiflight_available False and mobiflight None
    by default (see test_events.py's identical pattern for trigger_custom_event).

    spec=MobiFlightVariableRequests so a call to a method the real bridge
    does not have (e.g. the nonexistent trigger_event(), final-fix-C) raises
    AttributeError here exactly as it does against the sim, instead of a
    bare MagicMock silently auto-creating the attribute.
    """
    mock_simconnect["manager"]._mobiflight_available = True
    mock_simconnect["manager"].mobiflight = MagicMock(spec=MobiFlightVariableRequests)
    return mock_simconnect["manager"].mobiflight


# ---------------------------------------------------------------------------
# get_lvar
# ---------------------------------------------------------------------------


async def test_get_lvar_without_mobiflight_returns_error(mock_simconnect):
    result = await get_lvar("A32NX_TEST")
    assert isinstance(result, ToolError)
    assert result.error == "MOBIFLIGHT_NOT_AVAILABLE"


async def test_get_lvar_returns_a_model(mock_simconnect):
    """Fails against current code: get_lvar returns a plain dict today, so
    isinstance(result, LVarValue) is False and result.rpn raises AttributeError."""
    mobiflight = _enable_mobiflight(mock_simconnect)
    mobiflight.get.return_value = 42.0

    result = await get_lvar("A32NX_TEST")

    assert isinstance(result, LVarValue)
    assert result.name == "A32NX_TEST"
    assert result.rpn == "(L:A32NX_TEST)"
    assert result.value == 42.0
    mobiflight.get.assert_called_once_with("(L:A32NX_TEST)")


# ---------------------------------------------------------------------------
# set_lvar -- must keep using the native data-definition path, never
# MobiFlight RPN (the Fenix and other proprietary aircraft ignore RPN set()).
# ---------------------------------------------------------------------------


async def test_set_lvar_uses_the_native_data_definition_path(mock_simconnect):
    """mock_simconnect leaves manager.mobiflight as None (mobiflight_available
    is False by default), so a regression to the RPN path would raise
    AttributeError here -- this is not just an absence-of-call assertion.

    The write goes through the accessor's raw-datum path now rather than a
    hand-rolled copy of AddToDataDefinition + SetDataOnSimObject in
    connection.py, so this asserts on the accessor call. What reaches
    SetDataOnSimObject -- the encoded name b"L:A32NX_TEST" and the payload
    double -- is pinned against a real accessor in
    tests/test_simvar_access.py, which a MagicMock accessor cannot check.
    """
    result = await set_lvar("A32NX_TEST", 1.0)

    assert isinstance(result, LVarWriteResult)
    assert result.name == "A32NX_TEST"
    assert result.value_set == 1.0

    call = mock_simconnect["accessor"].write.call_args
    assert call.args[0] == "L:A32NX_TEST", "the L: prefix must reach SimConnect"
    assert call.args[1] == 1.0
    assert call.kwargs["raw_name"] is True, (
        "without raw_name the accessor would run the datum through "
        "simconnect_name and register a definition for a variable called 'L'"
    )


async def test_set_lvar_does_not_require_mobiflight(mock_simconnect):
    """Native writes work even when the WASM bridge never loaded."""
    assert mock_simconnect["manager"].mobiflight_available is False
    result = await set_lvar("L:A32NX_TEST", 5.0)
    assert result.status == "ok"


# --- set_lvar's success envelope has to be earned ------------------------
#
# The tool used to call manager.set_lvar (which returned None) and then
# return LVarWriteResult(status="ok", value_set=value) unconditionally --
# no verification of any kind, on exactly the aircraft class (Fenix, PMDG)
# this server exists to serve. msfs_set_simvar had verify=True, a
# `verified` field and a warning; an agent generalising from one write
# tool to the other silently lost the whole honest-failure mechanism.


async def test_set_lvar_reports_verified_true_when_the_value_lands(mock_simconnect):
    result = await set_lvar("A32NX_TEST", 3.0)
    assert result.verified is True
    assert result.warning is None
    assert mock_simconnect["lvar_values"]["L:A32NX_TEST"] == 3.0


async def test_set_lvar_reports_verified_false_when_the_value_does_not_land(
    mock_simconnect,
):
    """An aircraft that ignores the write. SimConnect raises nothing for
    this, so the read-back is the only evidence -- and reporting it as a
    plain success is the fabricated-success pattern this phase removes.

    Fails against the pre-fix tool, which had no `verified` field at all
    and returned status="ok" here.
    """
    mock_simconnect["accessor"].read.side_effect = (
        lambda name, unit=None, index=None, timeout=2.0, raw_name=False: 0.0
    )

    result = await set_lvar("A32NX_TEST", 3.0)

    assert result.verified is False
    assert result.warning is not None
    assert "did not read back" in result.warning


async def test_set_lvar_reports_verified_none_when_verification_is_impossible(
    mock_simconnect,
):
    """Tri-state: None means "could not verify", never "succeeded".

    A read-back that itself fails is not evidence the write failed, so
    False would be a claim this call cannot support.
    """
    mock_simconnect["accessor"].read.side_effect = SimVarTimeoutError("sim not answering")

    result = await set_lvar("A32NX_TEST", 3.0)

    assert result.verified is None
    assert result.warning is not None
    assert "not a report of success" in result.warning.lower()


async def test_set_lvar_never_reports_an_unverified_write_as_verified(mock_simconnect):
    """The one invariant that matters: whatever happens, `verified` is
    True only when a read-back actually confirmed the value."""
    for readback, expected in ((3.0, True), (0.0, False)):
        mock_simconnect["accessor"].read.side_effect = (
            lambda name, unit=None, index=None, timeout=2.0, raw_name=False, _v=readback: _v
        )
        result = await set_lvar("A32NX_TEST", 3.0)
        assert result.verified is expected


async def test_set_lvar_with_a_non_ascii_name_returns_a_typed_error(mock_simconnect):
    """connection.set_lvar's name.encode("ascii") raised a bare
    UnicodeEncodeError straight into handle_simconnect_errors' catch-all,
    producing an UNEXPECTED envelope leaking Python exception text --
    verified live against the pre-fix path. It must be a typed error now.
    """
    mock_simconnect["accessor"].write.side_effect = SimVarNotFoundError(
        "Variable name 'L:DEGREE_\N{DEGREE SIGN}' is not valid ASCII"
    )

    result = await set_lvar("DEGREE_\N{DEGREE SIGN}", 1.0)

    assert isinstance(result, ToolError)
    assert result.error == "LVAR_NAME_INVALID"
    assert result.error != "UNEXPECTED"
    assert result.suggestion


async def test_set_lvar_reports_a_rejected_write_instead_of_success(mock_simconnect):
    """Send-ID correlation reaches the tool: a write SimConnect rejected
    used to be invisible, because nothing bound the packet."""
    mock_simconnect["accessor"].write.side_effect = SimVarNotSettableError(
        "SimConnect rejected the write to 'L:A32NX_TEST'"
    )

    result = await set_lvar("A32NX_TEST", 1.0)

    assert isinstance(result, ToolError)
    assert result.error == "LVAR_NOT_SETTABLE"


async def test_set_lvar_without_an_accessor_says_so(mock_simconnect):
    """The plain-SimConnect fallback has no data-definition layer. Saying
    so beats an AttributeError in an UNEXPECTED envelope."""
    mock_simconnect["manager"].accessor = None

    result = await set_lvar("A32NX_TEST", 1.0)

    assert isinstance(result, ToolError)
    assert result.error == "ACCESSOR_UNAVAILABLE"


# ---------------------------------------------------------------------------
# list_lvars -- Phase 2 Task 4 implements real enumeration by consuming
# Task 3's add_response_handler. The primary behavioural coverage (real
# names, pagination, prefix filtering, handler cleanup, the 1000-name
# truncation cap) lives in tests/test_lvar_listing.py; these two guard the
# edges that module's fixtures don't reach: no MobiFlight bridge at all, and
# a bridge that answers nothing.
# ---------------------------------------------------------------------------


async def test_list_lvars_without_mobiflight_returns_error(mock_simconnect):
    result = await list_lvars()
    assert isinstance(result, ToolError)
    assert result.error == "MOBIFLIGHT_NOT_AVAILABLE"


async def test_list_lvars_with_no_response_reports_no_lvars_returned(
    mock_simconnect, monkeypatch
):
    """Replaces this suite's old stub-pinning test now that list_lvars is
    real: it must actually ask the WASM module (send_command is called),
    and when nothing comes back it must report that honestly rather than an
    empty-but-successful list.

    Timeout/settle constants are patched down so a bridge that never fires
    a response handler doesn't cost this test the full ~11.5s production
    wait.
    """
    from simconnect_mcp.tools import lvars as lvars_module

    monkeypatch.setattr(lvars_module, "_LIST_TIMEOUT_S", 0.05)
    monkeypatch.setattr(lvars_module, "_LIST_SETTLE_S", 0.01)

    mobiflight = _enable_mobiflight(mock_simconnect)

    result = await list_lvars()

    assert isinstance(result, ToolError)
    assert result.error == "NO_LVARS_RETURNED"
    mobiflight.send_command.assert_called_once_with("MF.LVars.List")


# ---------------------------------------------------------------------------
# execute_calculator_code -- explicit mode parameter
# ---------------------------------------------------------------------------


async def test_execute_calculator_code_mode_read_forces_get(mock_simconnect):
    """Fails against current code: execute_calculator_code() takes no `mode`
    keyword at all (TypeError: unexpected keyword argument 'mode')."""
    mobiflight = _enable_mobiflight(mock_simconnect)
    mobiflight.get.return_value = 7.0

    result = await execute_calculator_code("(L:A) (L:B) max", mode="read")

    assert isinstance(result, CalculatorResult)
    assert result.mode == "read"
    assert result.value == 7.0
    mobiflight.get.assert_called_once_with("(L:A) (L:B) max")
    mobiflight.set.assert_not_called()


async def test_execute_calculator_code_mode_auto_misclassifies_compound_reads(mock_simconnect):
    """Documents the known limitation named in the task brief: auto's
    heuristic requires the expression to both start with '(' and end with
    ')'. '(L:A) (L:B) max' ends in 'x', not ')', so auto treats a compound
    read as an execute -- exactly why mode='read' exists as an escape hatch.
    This nails the CURRENT heuristic down as a regression guard: if a future
    change to the heuristic accidentally fixes or further breaks this
    specific case, this test will catch the change either way."""
    mobiflight = _enable_mobiflight(mock_simconnect)

    result = await execute_calculator_code("(L:A) (L:B) max", mode="auto")

    assert result.mode == "execute"
    mobiflight.set.assert_called_once_with("(L:A) (L:B) max")
    mobiflight.get.assert_not_called()


async def test_execute_calculator_code_mode_auto_still_detects_simple_reads(mock_simconnect):
    """auto must stay backward compatible for the common case it already
    classified correctly."""
    mobiflight = _enable_mobiflight(mock_simconnect)
    mobiflight.get.return_value = 123.0

    result = await execute_calculator_code("(A:PLANE ALTITUDE, feet)", mode="auto")

    assert result.mode == "read"
    assert result.value == 123.0


async def test_execute_calculator_code_mode_execute_forces_set(mock_simconnect):
    mobiflight = _enable_mobiflight(mock_simconnect)

    result = await execute_calculator_code("(L:MyVar) 1 + (>L:MyVar)", mode="execute")

    assert isinstance(result, CalculatorResult)
    assert result.mode == "execute"
    mobiflight.set.assert_called_once_with("(L:MyVar) 1 + (>L:MyVar)")
    mobiflight.get.assert_not_called()


async def test_execute_calculator_code_message_does_not_overclaim_confirmation(mock_simconnect):
    """B9: mobiflight.set()/send_command() write to a client data area with
    no response channel read, so nothing here actually confirms the WASM
    module ran this code -- the message must say it was sent, not that it
    "executed successfully"."""
    _enable_mobiflight(mock_simconnect)

    result = await execute_calculator_code("1 (>K:PARKING_BRAKES)", mode="execute")

    assert "successfully" not in result.message.lower()
    assert "sent" in result.message.lower()


async def test_execute_calculator_code_without_mobiflight_returns_error(mock_simconnect):
    result = await execute_calculator_code("(L:MyVar)")
    assert isinstance(result, ToolError)
    assert result.error == "MOBIFLIGHT_NOT_AVAILABLE"


# ---------------------------------------------------------------------------
# Field-bound enforcement through a real FastMCP instance (standing
# controller decision from Task 3: a Field bound must be verified through
# mcp.call_tool, not direct invocation, on a tool with no @require_connection
# so the check cannot open a live SimConnect connection).
# ---------------------------------------------------------------------------


async def test_browse_lvar_catalog_limit_over_bound_is_rejected_by_fastmcp():
    """le=200 must be enforced by FastMCP's generated schema, not a soft
    clamp inside paginate() -- a direct await bypasses validation entirely
    (ArgModelBase's extra='ignore' would not even raise for an unknown
    kwarg, let alone an out-of-range one). browse_lvar_catalog carries no
    @require_connection, so this cannot open a live SimConnect connection --
    unlike a connecting tool, which must never be used for this kind of
    check (a previous task inadvertently connected to the user's live sim
    doing exactly that)."""
    test_mcp = FastMCP("test-lvars")
    test_mcp.tool(name="msfs_browse_lvar_catalog")(browse_lvar_catalog)
    with pytest.raises(MCPToolError, match="200"):
        await test_mcp.call_tool("msfs_browse_lvar_catalog", {"limit": 5000})
