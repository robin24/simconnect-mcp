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


async def test_save_flight_refuses_to_overwrite_an_existing_file_by_default(
    live_manager, tmp_path
):
    from simconnect_mcp.tools.flight import save_flight

    target = tmp_path / "simconnect_mcp_live_test_overwrite.FLT"

    first = await save_flight(str(target), title="T1", description="D1")
    assert first.status == "ok", getattr(first, "message", first)

    second = await save_flight(str(target), title="T2", description="D2")
    assert second.error == "ALREADY_EXISTS"


async def test_save_flight_overwrites_when_asked(live_manager, tmp_path):
    from simconnect_mcp.tools.flight import save_flight

    target = tmp_path / "simconnect_mcp_live_test_replace.FLT"

    first = await save_flight(str(target), title="T1", description="D1")
    assert first.status == "ok", getattr(first, "message", first)
    first_mtime = target.stat().st_mtime

    second = await save_flight(str(target), title="T2", description="D2", overwrite=True)
    assert second.status == "ok", getattr(second, "message", second)
    assert target.stat().st_mtime >= first_mtime
