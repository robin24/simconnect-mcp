"""Live tests for create_ai_object against a running MSFS.

load_flight and load_flight_plan are not exercised live: loading a flight or
flight plan discards the current aircraft/session state, which is exactly
the setup this live suite depends on for every other test file. Both stay
covered by mocks in tests/test_flight.py -- see task-6-addendum.md.

save_flight's three live tests (writes a real file; refuses to overwrite by
default; overwrites when asked) were removed in the 2026-08-29 live-suite
trim: the overwrite-guard logic is pure Python, fully exercised by mocks in
tests/test_flight.py, and the rest of what those tests checked was MSFS's
own save-then-stall timing (see CLAUDE.md's Known Sim Behaviours), which
varies run to run (0.7s-14.5s measured) and is the sim's behaviour, not
this project's code. See
.superpowers/sdd/2026-08-29-mcp-modernization-phase2-capability/
live-trim-report.md for the full reasoning.

create_ai_object was excluded from this file entirely for the same reason
as load_flight/load_flight_plan: a spawned AI aircraft used to be visible
and permanent for the rest of the user's session, with nothing this project
shipped able to clean it up. The live-follow-up task that added `object_id`
(consuming SimConnect's ASSIGNED_OBJECT_ID reply -- see tools/flight.py)
also made that no longer true for the specific spawn-then-immediately-remove
shape used below: AIRemoveObject needs the id object_id now provides, and
removal was confirmed live by a follow-up SimVar request targeted at the
removed object's own id, which came back SIMCONNECT_EXCEPTION_UNRECOGNIZED_ID
-- proof the object is actually gone, not merely that the removal call's own
HRESULT was accepted (the same "accepted vs confirmed" gap this whole task
exists to close). No new tool was added for this -- these tests call
AIRemoveObject directly via manager.sm.dll, exactly as create_ai_object's
own low-level pattern does.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.live


# --- create_ai_object (L1 live-follow-up) ---
#
# Every spawn here is offset well clear of the user's own aircraft and
# airborne (on_ground=False), then removed again within the same test --
# see this module's docstring for why that shape is safe to run
# unattended, unlike ENGINE_AUTO_START-style live checks that can have a
# delayed real-world effect on the user's own aircraft with no bounded
# window to restore it in.


async def _remove_ai_object(live_manager, object_id: int) -> bool:
    """Direct AIRemoveObject DLL call -- mirrors create_ai_object's own
    low-level pattern (tools/flight.py). Not a new tool: test-only cleanup."""
    manager = live_manager

    def _remove() -> bool:
        req_id = manager.reserved_request_id("live_test_ai_object_removal")
        hr = manager.sm.dll.AIRemoveObject(manager.sm.hSimConnect, object_id, req_id)
        return bool(manager.sm.IsHR(hr, 0))

    return await manager.run_sync(_remove)


async def _object_still_answers(live_manager, object_id: int) -> bool:
    """True if SimConnect still answers a PLANE LATITUDE request targeted at
    `object_id` (not SIMCONNECT_OBJECT_ID_USER, which SimVarAccessor.read()
    is hardcoded to -- see its module docstring -- so this makes its own
    minimal, one-off RequestDataOnSimObject call instead). False when
    SimConnect raises an exception against that request -- live-verified
    (see the live-follow-up report) to be SIMCONNECT_EXCEPTION_UNRECOGNIZED_ID
    once an object has actually been removed. This is the actual proof
    AIRemoveObject worked, not just that its own HRESULT was accepted."""
    from ctypes.wintypes import DWORD

    from SimConnect.Constants import SIMCONNECT_UNUSED
    from SimConnect.Enum import SIMCONNECT_DATATYPE, SIMCONNECT_PERIOD

    from simconnect_mcp.dispatch import PendingRequest

    manager = live_manager

    def _probe() -> bool:
        def_id = manager.sm.new_def_id().value
        req_id = manager.sm.new_request_id().value
        pending = PendingRequest(request_id=req_id)
        manager.sm.registry.register(pending)
        try:
            with manager.sm.registry.pending_lock:
                manager.sm.dll.AddToDataDefinition(
                    manager.sm.hSimConnect, def_id,
                    b"PLANE LATITUDE", b"degrees",
                    SIMCONNECT_DATATYPE.SIMCONNECT_DATATYPE_FLOAT64,
                    0.0, SIMCONNECT_UNUSED,
                )
                def_send = DWORD(0)
                manager.sm.dll.GetLastSentPacketID(manager.sm.hSimConnect, def_send)
                manager.sm.registry.bind_send_id(pending, def_send.value, _locked=True)

                manager.sm.dll.RequestDataOnSimObject(
                    manager.sm.hSimConnect, req_id, def_id, object_id,
                    SIMCONNECT_PERIOD.SIMCONNECT_PERIOD_ONCE, 0, 0, 0, 0,
                )
                req_send = DWORD(0)
                manager.sm.dll.GetLastSentPacketID(manager.sm.hSimConnect, req_send)
                manager.sm.registry.bind_send_id(pending, req_send.value, _locked=True)

            got = pending.done.wait(2.0)
            return got and pending.exception is None
        finally:
            manager.sm.registry.discard(pending)

    return await manager.run_sync(_probe)


async def test_create_ai_object_confirms_and_can_be_removed(live_manager):
    """L1: a title read straight from the sim's own TITLE SimVar (so,
    guaranteed to match an installed aircraft) must yield a real object_id
    -- SimConnect's ASSIGNED_OBJECT_ID reply, confirming the object was
    actually created, not merely accepted. That id must then be enough to
    remove the object again with AIRemoveObject, confirmed via
    _object_still_answers rather than trusting AIRemoveObject's own HRESULT
    alone (see that helper's docstring)."""
    from simconnect_mcp.tools.flight import create_ai_object

    title = live_manager.accessor.read("TITLE")
    lat = live_manager.accessor.read("PLANE_LATITUDE", unit="degrees")
    lon = live_manager.accessor.read("PLANE_LONGITUDE", unit="degrees")

    result = await create_ai_object(
        title=title,
        latitude=lat + 0.045,  # ~2.7nm away -- clear of the user's own aircraft
        longitude=lon,
        altitude_ft=3000.0,
        on_ground=False,
        airspeed=150,
    )
    assert result.status == "ok", getattr(result, "message", result)
    assert result.object_id is not None, (
        "expected a real object_id for a title read straight off the sim's "
        "own TITLE SimVar"
    )

    try:
        assert await _object_still_answers(live_manager, result.object_id), (
            "the newly created object did not answer a targeted SimVar "
            "request -- object_id may not be real"
        )
    finally:
        removed = await _remove_ai_object(live_manager, result.object_id)
        assert removed, "AIRemoveObject rejected removing the object this test spawned"

    assert not await _object_still_answers(live_manager, result.object_id), (
        "the object still answers after AIRemoveObject -- removal did not "
        "actually take effect"
    )


async def test_create_ai_object_bogus_title_yields_no_object_id(live_manager):
    """The honest path: MSFS ignores a title matching no installed aircraft
    silently -- no error, no ASSIGNED_OBJECT_ID reply -- so object_id must
    stay null and the message must not claim creation."""
    from simconnect_mcp.tools.flight import create_ai_object

    lat = live_manager.accessor.read("PLANE_LATITUDE", unit="degrees")
    lon = live_manager.accessor.read("PLANE_LONGITUDE", unit="degrees")

    result = await create_ai_object(
        title="Definitely Not A Real Installed Aircraft Title 98765",
        latitude=lat + 0.09,
        longitude=lon,
        altitude_ft=3000.0,
        on_ground=False,
    )
    assert result.status == "ok", getattr(result, "message", result)
    assert result.object_id is None
    assert "not confirmation" in result.message.lower()
