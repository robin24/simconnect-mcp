import pytest

from simconnect_mcp.simvar_access import (
    SimVarNotFoundError,
    SimVarNotSettableError,
    SimVarTimeoutError,
    UnitMismatchError,
)
from simconnect_mcp.tools.models import Page, SimVarValue, ToolError, error_from


def test_tool_error_keeps_the_legacy_field_names():
    err = ToolError(error="SIMVAR_NOT_FOUND", message="nope", suggestion="try search")
    dumped = err.model_dump()
    assert dumped["status"] == "error"
    assert set(dumped) >= {"status", "error", "message", "suggestion"}


@pytest.mark.parametrize(
    "exc,code",
    [
        (SimVarNotFoundError("x"), "SIMVAR_NOT_FOUND"),
        (SimVarNotSettableError("x"), "SIMVAR_NOT_SETTABLE"),
        (UnitMismatchError("x"), "UNIT_MISMATCH"),
        (SimVarTimeoutError("x"), "SIM_TIMEOUT"),
    ],
)
def test_error_from_maps_each_accessor_exception(exc, code):
    assert error_from(exc).error == code


def test_error_from_always_supplies_a_suggestion():
    assert error_from(SimVarNotFoundError("x")).suggestion


def test_page_reports_has_more_and_next_offset():
    page = Page.build(total=100, offset=0, count=25)
    assert page.has_more is True
    assert page.next_offset == 25


def test_page_on_the_last_slice_has_no_next_offset():
    page = Page.build(total=30, offset=25, count=5)
    assert page.has_more is False
    assert page.next_offset is None


def test_simvar_value_accepts_a_string_value():
    """TITLE and friends return str, not float."""
    value = SimVarValue(name="TITLE", value="Boeing 747-8i", unit="string")
    assert value.value == "Boeing 747-8i"
