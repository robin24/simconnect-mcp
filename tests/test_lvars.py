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

from simconnect_mcp.tools.lvars import (
    browse_lvar_catalog,
    execute_calculator_code,
    get_lvar,
    list_lvars,
    set_lvar,
)
from simconnect_mcp.tools.models import CalculatorResult, LVarValue, LVarWriteResult, ToolError


def _enable_mobiflight(mock_simconnect):
    """mock_simconnect leaves _mobiflight_available False and mobiflight None
    by default (see test_events.py's identical pattern for trigger_custom_event).
    """
    mock_simconnect["manager"]._mobiflight_available = True
    mock_simconnect["manager"].mobiflight = MagicMock()
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
    AttributeError here -- this is not just an absence-of-call assertion."""
    result = await set_lvar("A32NX_TEST", 1.0)

    assert isinstance(result, LVarWriteResult)
    assert result.name == "A32NX_TEST"
    assert result.value_set == 1.0
    mock_simconnect["sm"].dll.SetDataOnSimObject.assert_called_once()


async def test_set_lvar_does_not_require_mobiflight(mock_simconnect):
    """Native writes work even when the WASM bridge never loaded."""
    assert mock_simconnect["manager"].mobiflight_available is False
    result = await set_lvar("L:A32NX_TEST", 5.0)
    assert result.status == "ok"


# ---------------------------------------------------------------------------
# list_lvars -- Phase 2 Task 4 implements real enumeration. Until then this
# must return an honest error, never a fabricated success.
# ---------------------------------------------------------------------------


async def test_list_lvars_without_mobiflight_returns_error(mock_simconnect):
    result = await list_lvars()
    assert isinstance(result, ToolError)
    assert result.error == "MOBIFLIGHT_NOT_AVAILABLE"


async def test_list_lvars_reports_not_implemented_rather_than_fake_success(mock_simconnect):
    """Current code returns status='ok' with a message claiming a WASM list
    command was sent -- nothing is ever read back, so that 'ok' is
    fabricated. Fails against current code: result.error would be missing
    entirely (status is 'ok', not 'error')."""
    mobiflight = _enable_mobiflight(mock_simconnect)

    result = await list_lvars()

    assert isinstance(result, ToolError)
    assert result.error == "NOT_IMPLEMENTED"
    mobiflight.send_command.assert_not_called()


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
    test_mcp.tool()(browse_lvar_catalog)
    with pytest.raises(MCPToolError, match="200"):
        await test_mcp.call_tool("browse_lvar_catalog", {"limit": 5000})
