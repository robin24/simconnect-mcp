"""Tests for flight/scenario tools: load_flight, save_flight,
load_flight_plan, create_ai_object.

load_flight, load_flight_plan, and create_ai_object are only tested against
mocks here -- loading a flight or flight plan would discard the live
session's aircraft state, and create_ai_object's effect (a spawned AI
aircraft) is visible and persistent in a real session. save_flight also has
mock coverage below; a real-sim check lives in tests/live/test_live_flight.py
since writing a flight file to a temp path is not disruptive.
"""
from __future__ import annotations

import threading
import time

from simconnect_mcp.tools.flight import (
    create_ai_object,
    load_flight,
    load_flight_plan,
    save_flight,
)
from simconnect_mcp.tools.models import AiObjectResult, FlightResult, ToolError

# --- load_flight ---


async def test_load_flight_rejects_a_relative_path(mock_simconnect):
    result = await load_flight("flights/test.FLT")
    assert result.error == "INVALID_PATH"
    assert "absolute" in result.suggestion.lower()


async def test_load_flight_rejects_a_missing_file(mock_simconnect, tmp_path):
    result = await load_flight(str(tmp_path / "nope.FLT"))
    assert result.error == "FILE_NOT_FOUND"


async def test_load_flight_rejects_the_wrong_extension(mock_simconnect, tmp_path):
    wrong = tmp_path / "test.txt"
    wrong.write_text("x")
    result = await load_flight(str(wrong))
    assert result.error == "INVALID_PATH"
    assert ".FLT" in result.suggestion


async def test_load_flight_calls_the_library(mock_simconnect, tmp_path):
    flt = tmp_path / "test.FLT"
    flt.write_text("[Main]")
    mock_simconnect["sm"].load_flight.return_value = True

    result = await load_flight(str(flt))
    assert isinstance(result, FlightResult)
    assert result.status == "ok"
    mock_simconnect["sm"].load_flight.assert_called_once_with(str(flt))


async def test_load_flight_reports_load_failed_when_the_library_returns_false(
    mock_simconnect, tmp_path
):
    flt = tmp_path / "test.FLT"
    flt.write_text("[Main]")
    mock_simconnect["sm"].load_flight.return_value = False

    result = await load_flight(str(flt))
    assert isinstance(result, ToolError)
    assert result.error == "LOAD_FAILED"


# --- save_flight ---


async def test_save_flight_verifies_the_file_rather_than_the_return_value(
    mock_simconnect, tmp_path
):
    """The library's save_flight ends with an unconditional `return False`."""
    target = tmp_path / "saved.FLT"

    def _fake_save(path, title, description, *a, **k):
        target.write_text("[Main]")
        return False  # what the library actually returns

    mock_simconnect["sm"].save_flight.side_effect = _fake_save

    result = await save_flight(str(target), title="T", description="D")
    assert result.status == "ok"


async def test_save_flight_reports_a_genuine_failure(mock_simconnect, tmp_path):
    mock_simconnect["sm"].save_flight.return_value = False  # writes nothing
    result = await save_flight(str(tmp_path / "never.FLT"), title="T", description="D")
    assert result.error == "SAVE_FAILED"


async def test_save_flight_recovers_from_a_readback_race_if_the_file_lands(
    mock_simconnect, tmp_path
):
    """The library's save_flight calls flight_to_dic(flt_path) immediately
    after an asynchronous FlightSave; if MSFS has not finished writing the
    file yet, that read raises instead of returning False (see
    task-6-addendum.md). If the file is actually there, this must still be
    reported as a successful save, not an exception leaking through as a
    generic UNEXPECTED error."""
    target = tmp_path / "saved.FLT"

    def _fake_save(path, title, description, *a, **k):
        target.write_text("[Main]")
        raise KeyError("Main")  # what flight_to_dic raises on an incomplete read

    mock_simconnect["sm"].save_flight.side_effect = _fake_save

    result = await save_flight(str(target), title="T", description="D")
    assert isinstance(result, FlightResult)
    assert result.status == "ok"


async def test_save_flight_reports_save_failed_not_unexpected_when_nothing_lands(
    mock_simconnect, tmp_path
):
    """Same raise as above, but this time genuinely nothing gets written --
    must resolve to the specific SAVE_FAILED code, not the decorator's
    generic UNEXPECTED catch-all."""

    def _raise_without_writing(path, title, description, *a, **k):
        raise KeyError("Main")

    mock_simconnect["sm"].save_flight.side_effect = _raise_without_writing

    result = await save_flight(str(tmp_path / "never.FLT"), title="T", description="D")
    assert isinstance(result, ToolError)
    assert result.error == "SAVE_FAILED"


async def test_save_flight_polls_rather_than_checking_the_file_once(mock_simconnect, tmp_path):
    """A single immediate Path.exists() check can land in the window before
    an asynchronous save finishes and report a save that succeeds a moment
    later as a failure. Simulate that timing with a background thread that
    writes the file shortly after the library call returns."""
    target = tmp_path / "delayed.FLT"

    def _write_soon():
        time.sleep(0.1)
        target.write_text("[Main]")

    def _fake_save(path, title, description, *a, **k):
        threading.Thread(target=_write_soon).start()
        return False

    mock_simconnect["sm"].save_flight.side_effect = _fake_save

    result = await save_flight(str(target), title="T", description="D")
    assert result.status == "ok", getattr(result, "message", result)


async def test_save_flight_refuses_to_overwrite_without_the_flag(mock_simconnect, tmp_path):
    existing = tmp_path / "saved.FLT"
    existing.write_text("[Main]\ntitle=Old\n")

    result = await save_flight(str(existing), title="T", description="D")
    assert isinstance(result, ToolError)
    assert result.error == "ALREADY_EXISTS"
    assert "overwrite" in result.suggestion.lower()
    mock_simconnect["sm"].save_flight.assert_not_called()


async def test_save_flight_overwrites_when_asked(mock_simconnect, tmp_path):
    existing = tmp_path / "saved.FLT"
    existing.write_text("[Main]\ntitle=Old\n")

    def _fake_save(path, title, description, *a, **k):
        existing.write_text("[Main]\ntitle=New\n")
        return False

    mock_simconnect["sm"].save_flight.side_effect = _fake_save

    result = await save_flight(str(existing), title="T", description="D", overwrite=True)
    assert result.status == "ok"
    mock_simconnect["sm"].save_flight.assert_called_once()


# --- load_flight_plan ---


async def test_load_flight_plan_rejects_a_relative_path(mock_simconnect):
    result = await load_flight_plan("plans/test.PLN")
    assert result.error == "INVALID_PATH"
    assert "absolute" in result.suggestion.lower()


async def test_load_flight_plan_rejects_a_missing_file(mock_simconnect, tmp_path):
    result = await load_flight_plan(str(tmp_path / "nope.PLN"))
    assert result.error == "FILE_NOT_FOUND"


async def test_load_flight_plan_rejects_the_wrong_extension(mock_simconnect, tmp_path):
    wrong = tmp_path / "test.txt"
    wrong.write_text("x")
    result = await load_flight_plan(str(wrong))
    assert result.error == "INVALID_PATH"
    assert ".PLN" in result.suggestion


async def test_load_flight_plan_calls_the_library(mock_simconnect, tmp_path):
    pln = tmp_path / "test.PLN"
    pln.write_text("<FlightPlan/>")
    mock_simconnect["sm"].load_flight_plan.return_value = True

    result = await load_flight_plan(str(pln))
    assert isinstance(result, FlightResult)
    assert result.status == "ok"
    mock_simconnect["sm"].load_flight_plan.assert_called_once_with(str(pln))


async def test_load_flight_plan_reports_load_failed_when_the_library_returns_false(
    mock_simconnect, tmp_path
):
    pln = tmp_path / "test.PLN"
    pln.write_text("<FlightPlan/>")
    mock_simconnect["sm"].load_flight_plan.return_value = False

    result = await load_flight_plan(str(pln))
    assert isinstance(result, ToolError)
    assert result.error == "LOAD_FAILED"


# --- create_ai_object ---


async def test_create_ai_object_validates_coordinates(mock_simconnect):
    result = await create_ai_object(title="Boeing 747-8i", latitude=200.0, longitude=0.0)
    assert result.error is not None


async def test_create_ai_object_validates_longitude(mock_simconnect):
    result = await create_ai_object(title="Boeing 747-8i", latitude=0.0, longitude=200.0)
    assert result.error is not None


async def test_create_ai_object_does_not_call_the_library_when_coordinates_are_invalid(
    mock_simconnect,
):
    await create_ai_object(title="Boeing 747-8i", latitude=200.0, longitude=0.0)
    assert not mock_simconnect["sm"].createSimulatedObject.called


async def test_create_ai_object_returns_a_model_on_success(mock_simconnect):
    result = await create_ai_object(title="Boeing 747-8i", latitude=47.6, longitude=-122.3)
    assert isinstance(result, AiObjectResult)
    assert result.status == "ok"
    assert result.title == "Boeing 747-8i"
    assert "silently" in result.message.lower()


async def test_create_ai_object_calls_the_library_with_expected_arguments(mock_simconnect):
    await create_ai_object(
        title="Boeing 747-8i",
        latitude=47.6,
        longitude=-122.3,
        altitude_ft=1500.0,
        heading=270.0,
        on_ground=False,
        airspeed=120,
    )
    mock_simconnect["sm"].createSimulatedObject.assert_called_once()
    args = mock_simconnect["sm"].createSimulatedObject.call_args.args
    kwargs = mock_simconnect["sm"].createSimulatedObject.call_args.kwargs
    assert args[0] == "Boeing 747-8i"
    assert args[1] == 47.6
    assert args[2] == -122.3
    assert kwargs["hdg"] == 270.0
    assert kwargs["gnd"] == 0
    assert kwargs["alt"] == 1500.0
    assert kwargs["speed"] == 120


async def test_create_ai_object_on_ground_defaults_to_true(mock_simconnect):
    await create_ai_object(title="Boeing 747-8i", latitude=47.6, longitude=-122.3)
    kwargs = mock_simconnect["sm"].createSimulatedObject.call_args.kwargs
    assert kwargs["gnd"] == 1
