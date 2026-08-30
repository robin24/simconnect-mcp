"""Live tests for save_flight against a running MSFS.

Only save_flight is exercised live. load_flight and load_flight_plan are not:
loading a flight or flight plan discards the current aircraft/session state,
which is exactly the setup this live suite depends on for every other test
file. create_ai_object's effect (a spawned AI aircraft) is visible and
persistent in the user's session. All three are covered by mocks in
tests/test_flight.py instead -- see task-6-addendum.md.

save_flight writing to a temp path is not disruptive: it only reads the
current state and writes a file, it does not change anything in the sim.
This also exercises the actual FlightSave / flight_to_dic race described in
the addendum against real MSFS timing, not just a mocked one.

Task 9 live verification found a second, more serious defect on top of that
race: FlightSave writes its file in a fraction of a second, but MSFS then
stops answering SimConnect entirely for ~14s while it finishes the save
(flightsave_stall_probe.py, in the task-9 fix brief's directory). The old
save_flight returned as soon as the file existed, so the file-write tests
below used to leave the sim frozen for whatever ran next -- confirmed by
bisection to be the sole cause of three failures elsewhere in the live
suite. save_flight now waits (bounded) for the sim to answer again before
returning, so no test here needs its own tolerance for that stall -- and
test_save_flight_writes_a_real_file asserts on it directly, rather than
only on other files passing as a side effect.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.live


async def test_save_flight_writes_a_real_file(live_manager, tmp_path):
    from simconnect_mcp.tools.flight import save_flight

    target = tmp_path / "simconnect_mcp_live_test.FLT"

    result = await save_flight(
        str(target), title="simconnect-mcp live test", description="Task 6 live check"
    )

    assert result.status == "ok", getattr(result, "message", result)
    assert target.exists()
    assert target.stat().st_size > 0

    # The contract this call now makes good on: by the time it returns, the
    # sim is answering SimConnect again, not merely "the file is on disk".
    # Measured live (repeatedly, see the task-9 fix brief): MSFS stays
    # completely unresponsive for ~14s after FlightSave before this can be
    # true, so a `duration_s` anywhere near the old (sub-second) file-poll
    # time would mean the wait regressed back to returning too early. 5s is
    # a conservative floor -- comfortably below the ~14s measured, but far
    # enough above "returned immediately" to catch that regression without
    # flaking on ordinary machine-speed variance.
    assert result.warning is None, (
        "the sim did not resume answering within the wait bound -- see "
        f"result.warning: {result.warning}"
    )
    assert result.duration_s > 5.0, (
        "save_flight returned too fast for the wait to have actually run -- "
        f"got duration_s={result.duration_s}"
    )


async def test_save_flight_refuses_to_overwrite_an_existing_file_by_default(
    live_manager, tmp_path
):
    from simconnect_mcp.tools.flight import save_flight

    target = tmp_path / "simconnect_mcp_live_test_overwrite.FLT"

    first = await save_flight(str(target), title="T1", description="D1")
    assert first.status == "ok", getattr(first, "message", first)
    assert first.warning is None

    second = await save_flight(str(target), title="T2", description="D2")
    assert second.error == "ALREADY_EXISTS"


async def test_save_flight_overwrites_when_asked(live_manager, tmp_path):
    from simconnect_mcp.tools.flight import save_flight

    target = tmp_path / "simconnect_mcp_live_test_replace.FLT"

    first = await save_flight(str(target), title="T1", description="D1")
    assert first.status == "ok", getattr(first, "message", first)
    assert first.warning is None
    first_mtime = target.stat().st_mtime

    second = await save_flight(str(target), title="T2", description="D2", overwrite=True)
    assert second.status == "ok", getattr(second, "message", second)
    assert second.warning is None
    assert target.stat().st_mtime >= first_mtime
