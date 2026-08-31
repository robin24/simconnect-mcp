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

import asyncio
import re
import threading
import time
from types import SimpleNamespace

import pytest

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
    # The mock accessor answers every probe immediately (see conftest.py's
    # simulated_read_seconds default), so the sim recovery wait resolves on
    # its first read and must not attach a warning.
    assert result.warning is None
    assert isinstance(result.duration_s, float)
    assert result.duration_s >= 0


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
    # The mock accessor answers every probe immediately, so the sim
    # recovery wait resolves on its first read and must not warn.
    assert result.warning is None
    assert isinstance(result.duration_s, float)
    assert result.duration_s >= 0


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
    assert result.warning is None
    assert isinstance(result.duration_s, float)
    assert result.duration_s >= 0


async def test_load_flight_plan_reports_load_failed_when_the_library_returns_false(
    mock_simconnect, tmp_path
):
    pln = tmp_path / "test.PLN"
    pln.write_text("<FlightPlan/>")
    mock_simconnect["sm"].load_flight_plan.return_value = False

    result = await load_flight_plan(str(pln))
    assert isinstance(result, ToolError)
    assert result.error == "LOAD_FAILED"


# --- sim recovery wait (shared by load_flight/save_flight/load_flight_plan) ---
#
# Task 9 live verification: FlightSave writes its file in a fraction of a
# second but then leaves MSFS unable to answer SimConnect at all for ~14s
# (measured live -- see tools/flight.py's _SIM_RECOVERY_TIMEOUT_S comment).
# The old save_flight returned as soon as the file existed, so the agent's
# NEXT tool call failed against a sim that could not yet respond. These
# tests cover _wait_for_sim_responsive directly (fast, via monkeypatched
# constants -- the same technique test_facilities_tools.py's
# _COLLECT_TIMEOUT/_POLL_INTERVAL tests use) and how the three flight tools
# wire its result into FlightResult.warning/duration_s.


async def test_wait_for_sim_responsive_returns_true_as_soon_as_a_probe_succeeds(
    mock_simconnect,
):
    from simconnect_mcp.tools.flight import _wait_for_sim_responsive

    # conftest.py's mock accessor answers immediately by default
    # (simulated_read_seconds=0.0), so this must resolve on the first probe.
    responsive, waited = await _wait_for_sim_responsive(mock_simconnect["manager"])
    assert responsive is True
    assert waited >= 0.0


async def test_wait_for_sim_responsive_does_not_wait_without_an_accessor(mock_simconnect):
    """The plain-SimConnect fallback (connection.py's connect(), when the
    dispatcher fails to build) never creates a SimVarAccessor. The flight
    tools' primary save/load action does not need one either (same rule
    require_connection's needs_accessor docstring documents), so a missing
    accessor must degrade to "don't wait" rather than hang or refuse."""
    from simconnect_mcp.tools.flight import _wait_for_sim_responsive

    manager = mock_simconnect["manager"]
    manager.accessor = None
    responsive, waited = await _wait_for_sim_responsive(manager)
    assert responsive is True
    assert waited == 0.0


async def test_wait_for_sim_responsive_gives_up_after_its_bound(mock_simconnect, monkeypatch):
    """If every probe keeps timing out, this must give up at
    _SIM_RECOVERY_TIMEOUT_S rather than waiting forever -- shrunk here via
    monkeypatch so the test runs in milliseconds instead of the production
    30s. Also checks the returned `waited` reflects the actual elapsed time
    (the fix brief asked the warning to state "how long was waited", not
    just the configured bound)."""
    from simconnect_mcp.tools import flight as flight_module

    monkeypatch.setattr(flight_module, "_SIM_RECOVERY_TIMEOUT_S", 0.2)
    monkeypatch.setattr(flight_module, "_SIM_RECOVERY_PROBE_TIMEOUT_S", 0.05)
    monkeypatch.setattr(flight_module, "_SIM_RECOVERY_POLL_INTERVAL_S", 0.02)

    manager = mock_simconnect["manager"]
    manager.accessor.simulated_read_seconds = 999.0  # every probe times out

    responsive, waited = await flight_module._wait_for_sim_responsive(manager)
    assert responsive is False
    assert waited >= 0.2


async def test_save_flight_attaches_a_warning_when_the_sim_stays_unresponsive(
    mock_simconnect, tmp_path, monkeypatch
):
    """The file existing is not the same as the sim being usable again. If
    MSFS has not resumed answering within the wait bound, save_flight must
    still report success -- the file really is on disk -- but flag it, so
    the caller knows the very next tool call could be slow or fail."""
    from simconnect_mcp.tools import flight as flight_module

    monkeypatch.setattr(flight_module, "_SIM_RECOVERY_TIMEOUT_S", 0.2)
    monkeypatch.setattr(flight_module, "_SIM_RECOVERY_PROBE_TIMEOUT_S", 0.05)
    monkeypatch.setattr(flight_module, "_SIM_RECOVERY_POLL_INTERVAL_S", 0.02)
    mock_simconnect["accessor"].simulated_read_seconds = 999.0

    target = tmp_path / "saved.FLT"

    def _fake_save(path, title, description, *a, **k):
        target.write_text("[Main]")
        return False

    mock_simconnect["sm"].save_flight.side_effect = _fake_save

    result = await flight_module.save_flight(str(target), title="T", description="D")

    assert result.status == "ok", getattr(result, "message", result)
    assert target.exists()
    assert result.warning is not None
    assert "resumed answering" in result.warning
    assert "save" in result.warning
    assert result.duration_s >= 0.2
    # The fix brief asked the warning to say "how long was waited", not
    # just the configured bound -- so a real measured duration (close to
    # the monkeypatched 0.2s bound, not exactly it: the loop can overshoot
    # by up to one poll interval) must appear in the text, extracted rather
    # than matched as a literal substring since exact wall-clock timing is
    # not deterministic enough to assert on directly.
    waited_match = re.search(r"waiting (\d+\.\d+)s", result.warning)
    assert waited_match, f"warning does not state how long was waited: {result.warning!r}"
    assert float(waited_match.group(1)) >= 0.2


async def test_load_flight_attaches_a_warning_when_the_sim_stays_unresponsive(
    mock_simconnect, tmp_path, monkeypatch
):
    from simconnect_mcp.tools import flight as flight_module

    monkeypatch.setattr(flight_module, "_SIM_RECOVERY_TIMEOUT_S", 0.2)
    monkeypatch.setattr(flight_module, "_SIM_RECOVERY_PROBE_TIMEOUT_S", 0.05)
    monkeypatch.setattr(flight_module, "_SIM_RECOVERY_POLL_INTERVAL_S", 0.02)
    mock_simconnect["accessor"].simulated_read_seconds = 999.0

    flt = tmp_path / "test.FLT"
    flt.write_text("[Main]")
    mock_simconnect["sm"].load_flight.return_value = True

    result = await flight_module.load_flight(str(flt))

    assert result.status == "ok", getattr(result, "message", result)
    assert result.warning is not None
    assert "resumed answering" in result.warning
    assert "load" in result.warning


async def test_load_flight_plan_attaches_a_warning_when_the_sim_stays_unresponsive(
    mock_simconnect, tmp_path, monkeypatch
):
    from simconnect_mcp.tools import flight as flight_module

    monkeypatch.setattr(flight_module, "_SIM_RECOVERY_TIMEOUT_S", 0.2)
    monkeypatch.setattr(flight_module, "_SIM_RECOVERY_PROBE_TIMEOUT_S", 0.05)
    monkeypatch.setattr(flight_module, "_SIM_RECOVERY_POLL_INTERVAL_S", 0.02)
    mock_simconnect["accessor"].simulated_read_seconds = 999.0

    pln = tmp_path / "test.PLN"
    pln.write_text("<FlightPlan/>")
    mock_simconnect["sm"].load_flight_plan.return_value = True

    result = await flight_module.load_flight_plan(str(pln))

    assert result.status == "ok", getattr(result, "message", result)
    assert result.warning is not None
    assert "resumed answering" in result.warning
    assert "flight-plan load" in result.warning


async def test_a_cancelled_save_flight_leaves_the_lock_free(
    mock_simconnect, tmp_path, monkeypatch
):
    """IMPORTANT (task-9-fix-brief.md): the recovery wait is long enough in
    production (~14-30s) for the caller's MCP request to be cancelled
    mid-wait (notifications/cancelled cancels this coroutine's task). Each
    probe is one complete, independent SimVarAccessor.read() call -- unlike
    tools/facilities.py's subscription or tools/lvars.py's response
    handler, nothing here is registered before the poll loop's `await
    asyncio.sleep()` and torn down after it, so there is no handler to
    leak -- but SimConnectManager._sim_lock is real, and this proves a
    cancellation genuinely leaves it free rather than merely arguing it by
    inspection. Mirrors
    test_facilities_tools.py's test_a_cancelled_collection_still_unsubscribes
    and test_lvar_listing.py's test_a_cancelled_call_still_removes_its_handler.
    """
    from simconnect_mcp.tools import flight as flight_module

    monkeypatch.setattr(flight_module, "_SIM_RECOVERY_TIMEOUT_S", 5.0)
    monkeypatch.setattr(flight_module, "_SIM_RECOVERY_PROBE_TIMEOUT_S", 0.2)
    monkeypatch.setattr(flight_module, "_SIM_RECOVERY_POLL_INTERVAL_S", 0.05)

    manager = mock_simconnect["manager"]
    # Never "recovers" -- every probe times out -- so the wait loop is
    # guaranteed to still be polling (asleep between probes, lock released)
    # when this test cancels the task.
    manager.accessor.simulated_read_seconds = 999.0

    target = tmp_path / "cancel_test.FLT"

    def _fake_save(path, title, description, *a, **k):
        target.write_text("[Main]")
        return False

    mock_simconnect["sm"].save_flight.side_effect = _fake_save

    task = asyncio.create_task(
        flight_module.save_flight(str(target), title="T", description="D")
    )

    # Let the task save the file (fast) and reach the recovery wait's poll
    # loop, which -- at a 5.0s bound -- is guaranteed to still be running.
    await asyncio.sleep(0.15)
    assert not task.done(), "the recovery wait should still be polling"

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2.0)

    # Nothing left locked. This is a bounded wait rather than a non-blocking
    # acquire on purpose: cancelling an `await loop.run_in_executor(...)`
    # cannot cancel the thread that is already running it. If the cancel
    # lands mid-probe instead of during the sleep between probes, that
    # thread still has to finish its read and leave `with self._sim_lock`
    # before the lock frees -- measured at ~156ms with a deliberately
    # stalled probe. Asserting on a non-blocking acquire made this test
    # depend on which of the two the cancel happened to hit, which is
    # exactly the kind of timing a loaded CI runner decides differently
    # (observed failing on the Windows/3.10 job while 3.13 passed).
    # A genuine leak never releases the lock at all and still fails here.
    assert manager._sim_lock.acquire(timeout=5.0), (
        "a cancelled save_flight left SimConnectManager._sim_lock held"
    )
    manager._sim_lock.release()

    # A follow-up call must still work normally -- proves the cancellation
    # didn't wedge the manager or leave a stale registry entry behind.
    manager.accessor.simulated_read_seconds = 0.0
    follow_up_target = tmp_path / "cancel_test_followup.FLT"

    def _fake_save_followup(path, title, description, *a, **k):
        follow_up_target.write_text("[Main]")
        return False

    mock_simconnect["sm"].save_flight.side_effect = _fake_save_followup

    follow_up = await flight_module.save_flight(
        str(follow_up_target), title="T2", description="D2"
    )
    assert follow_up.status == "ok", getattr(follow_up, "message", follow_up)


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
    assert not mock_simconnect["sm"].dll.AICreateSimulatedObject.called


async def test_create_ai_object_confirms_a_real_object_id_on_success(mock_simconnect):
    """L1 fix: create_ai_object now consumes SimConnect's
    ASSIGNED_OBJECT_ID reply instead of discarding it unread. The default
    fixture's registry auto-resolves that reply immediately and cleanly
    (conftest.py) -- the common/success case -- so a plain successful call
    must come back with a real object_id and wording that says so, not the
    old "accepted, not confirmed" hedge, which is now reserved for when
    confirmation genuinely does not arrive (see the two tests below)."""
    result = await create_ai_object(title="Boeing 747-8i", latitude=47.6, longitude=-122.3)
    assert isinstance(result, AiObjectResult)
    assert result.status == "ok"
    assert result.title == "Boeing 747-8i"
    assert result.object_id == mock_simconnect["mock_assigned_object_id"]
    assert "confirming" in result.message.lower()
    assert "silently" not in result.message.lower()


async def test_create_ai_object_no_reply_leaves_object_id_none(mock_simconnect, monkeypatch):
    """A title MSFS ignores (or any other reason no ASSIGNED_OBJECT_ID ever
    arrives) must not invent an id or claim creation -- the whole point of
    the L1 fix is staying honest rather than assuming success just because
    one now COULD be reported. Timeout constants shrunk via monkeypatch so
    this runs in milliseconds instead of the production wait."""
    from simconnect_mcp.tools import flight as flight_module

    monkeypatch.setattr(flight_module, "_AI_OBJECT_REPLY_TIMEOUT_S", 0.05)
    monkeypatch.setattr(flight_module, "_AI_OBJECT_POLL_INTERVAL_S", 0.01)
    # Undo the default fixture's auto-resolve (conftest.py) so the
    # ASSIGNED_OBJECT_ID wait genuinely times out, as it would live for a
    # title matching no installed aircraft.
    mock_simconnect["sm"].registry.register.side_effect = None

    result = await create_ai_object(
        title="Definitely Not An Installed Aircraft", latitude=47.6, longitude=-122.3
    )

    assert result.status == "ok"
    assert result.object_id is None
    assert "silently" in result.message.lower()
    assert "not confirmation" in result.message.lower()


async def test_create_ai_object_without_a_registry_leaves_object_id_none(mock_simconnect):
    """Plain SimConnect fallback: no dispatcher, so correlating
    ASSIGNED_OBJECT_ID was never possible at all -- distinct from a timeout,
    and worded differently (see create_ai_object's message-building)."""
    delattr(mock_simconnect["sm"], "registry")

    result = await create_ai_object(title="Boeing 747-8i", latitude=47.6, longitude=-122.3)

    assert result.status == "ok"
    assert result.object_id is None
    assert "no request registry" in result.message.lower()


async def test_create_ai_object_calls_the_dll_directly_not_the_wrapper(mock_simconnect):
    """Regression: createSimulatedObject() (SimConnect.py in the installed
    library) builds this exact DLL call but discards the HRESULT
    SimConnect_AICreateSimulatedObject returns, so a request MSFS rejected
    outright was indistinguishable from one it accepted -- this tool
    reported success either way. The tool must call
    self.dll.AICreateSimulatedObject(...) directly so the return code
    survives to be checked with IsHR, exactly as send_sim_text does for
    SimConnect_Text."""
    mock_simconnect["sm"].dll.AICreateSimulatedObject.return_value = 0
    mock_simconnect["sm"].IsHR.return_value = True

    await create_ai_object(
        title="Boeing 747-8i",
        latitude=47.6,
        longitude=-122.3,
        altitude_ft=1500.0,
        heading=270.0,
        on_ground=False,
        airspeed=120,
    )

    assert not mock_simconnect["sm"].createSimulatedObject.called
    mock_simconnect["sm"].dll.AICreateSimulatedObject.assert_called_once()
    args = mock_simconnect["sm"].dll.AICreateSimulatedObject.call_args.args
    assert args[1] == b"Boeing 747-8i"
    init_pos = args[2]
    assert init_pos.Latitude == 47.6
    assert init_pos.Longitude == -122.3
    assert init_pos.Altitude == 1500.0
    assert init_pos.Heading == 270.0
    assert init_pos.OnGround == 0
    assert init_pos.Airspeed == 120
    mock_simconnect["sm"].IsHR.assert_called_once_with(0, 0)


async def test_create_ai_object_reports_failure_when_the_hresult_is_not_ok(mock_simconnect):
    """If SimConnect_AICreateSimulatedObject's HRESULT says the call failed
    -- a stale handle, E_INVALIDARG, a connection dropped after
    ensure_connected -- this must return a ToolError rather than the
    reassuring 'requested' envelope. The tenth instance of this project's
    signature defect: a success envelope that was assumed, not earned."""
    mock_simconnect["sm"].dll.AICreateSimulatedObject.return_value = 0x8000FFFF
    mock_simconnect["sm"].IsHR.return_value = False

    result = await create_ai_object(
        title="Boeing 747-8i", latitude=47.6, longitude=-122.3
    )

    assert isinstance(result, ToolError)
    assert result.error == "AI_OBJECT_FAILED"
    assert result.suggestion


async def test_create_ai_object_on_ground_defaults_to_true(mock_simconnect):
    await create_ai_object(title="Boeing 747-8i", latitude=47.6, longitude=-122.3)
    init_pos = mock_simconnect["sm"].dll.AICreateSimulatedObject.call_args.args[2]
    assert init_pos.OnGround == 1


async def test_create_ai_object_reuses_one_reserved_request_id(mock_simconnect):
    """Regression for the Phase 0 allocation pool: new_request_id() rebuilds
    an Enum from every prior member on every call and never reclaims one, so
    calling it per spawn makes cost grow without bound across a session.
    Nothing correlates on this request ID, so one reserved ID serves the
    whole connection -- three spawns must allocate exactly once."""
    ids = iter(range(100, 200))
    mock_simconnect["sm"].new_request_id.side_effect = lambda: SimpleNamespace(
        value=next(ids)
    )
    mock_simconnect["sm"].registry.acquire_request_id.side_effect = (
        lambda allocate: allocate()
    )

    for _ in range(3):
        await create_ai_object(title="Boeing 747-8i", latitude=47.6, longitude=-122.3)

    assert mock_simconnect["sm"].new_request_id.call_count == 1
    used = [
        call.args[3]
        for call in mock_simconnect["sm"].dll.AICreateSimulatedObject.call_args_list
    ]
    assert used == [100, 100, 100]
