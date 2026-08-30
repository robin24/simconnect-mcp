"""Live verification of the WASM response channel (Phase 2 Task 3).

Run with MSFS running and an aircraft loaded:  uv run pytest -m live

The mocked suite (tests/test_mobiflight_responses.py) proves the routing
logic in isolation, with a MagicMock standing in for both the message and
the WASM module. It cannot show that the real MobiFlight WASM module
actually sends definition-ID-0 traffic in response to a real command, or
that a real registered handler survives being called from the real
SimConnectMobiFlight dispatch thread. This exercises all of that for real.
"""
from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.live


@pytest.fixture
def remove_handlers_after(live_manager):
    """response_handlers is a plain list on a session-scoped bridge instance
    (live_manager, and therefore live_manager.mobiflight, lives for the
    whole test session) -- anything appended here must be popped again, or
    it silently keeps receiving traffic and appending to a list from a
    finished test for the rest of the session.
    """
    added: list = []
    yield added
    for handler in added:
        live_manager.mobiflight.remove_response_handler(handler)


async def test_lvars_list_responses_reach_a_registered_handler(
    live_manager, remove_handlers_after
):
    """Regression for the exact defect Task 3 fixes.

    Before routing definition ID 0 to response_handlers, every one of these
    messages was logged as 'DefinitionID 0 not found!' and discarded --
    measured live before this fix shipped (task-3-4-addendum.md: a single
    MF.LVars.List produced 1002 definition-0 messages, all thrown away).
    Sending MF.LVars.List is read-only: it asks the WASM module to
    enumerate L-var names and touches no aircraft state.
    """
    if not live_manager.mobiflight_available:
        pytest.skip("MobiFlight WASM extension not available")

    # Phase 2 Task 4 discovered (task-4-report.md) that the WASM module
    # silently drops MF.LVars.List when it is byte-identical to the
    # immediately preceding command on this channel -- not time-based (a
    # 20s wait alone never clears it), and not fixed by a trailing space
    # either (that changes the payload bytes and still fails), only a
    # different command in between does. live_manager is session-scoped,
    # so another test's own MF.LVars.List (e.g. test_live_lvars.py's,
    # which collects alphabetically first) can leave this channel "stuck"
    # before this test ever runs. This test bypasses msfs_list_lvars's own
    # fix (it talks to the bridge directly, to test Task 3's routing in
    # isolation), so it needs the same re-arm msfs_list_lvars sends
    # internally: a bare RPN literal with no (>L:...) write target, which
    # touches no variable and has no effect on the aircraft or on any
    # other tool's state (unlike MF.SimVars.Clear, which this used
    # previously -- that works too, but wipes tracking state
    # get_lvar/execute_calculator_code depend on, for no benefit over the
    # side-effect-free alternative).
    from simconnect_mcp.tools.lvars import _REARM_COMMAND

    await live_manager.run_sync(live_manager.mobiflight.send_command, _REARM_COMMAND)

    seen: list[str] = []
    live_manager.mobiflight.add_response_handler(seen.append)
    remove_handlers_after.append(seen.append)

    def _send() -> None:
        live_manager.mobiflight.send_command("MF.LVars.List")

    await live_manager.run_sync(_send)

    loop = asyncio.get_running_loop()
    deadline = loop.time() + 10
    while loop.time() < deadline:
        if seen and seen[-1] == "MF.LVars.List.End":
            break
        await asyncio.sleep(0.2)

    assert seen, "no response-channel strings reached the handler at all"
    assert seen[0] == "MF.LVars.List.Start"
    assert seen[-1] == "MF.LVars.List.End"
    # Measured live before this fix (task-3-4-addendum.md): 1002 messages
    # for one MF.LVars.List call (1000 names + Start + End). Not asserting
    # an exact count here -- it depends on what's installed/loaded -- but
    # more than the two sentinels alone proves real names were delivered.
    assert len(seen) > 2


async def test_numeric_lvar_reads_are_unaffected(live_manager):
    """The guard the task brief calls load-bearing: numeric variable
    updates (client_data_callback_handler's branch for definition IDs >= 1)
    must keep working exactly as before, on the real dispatch thread, with
    response handlers now also registered on the same bridge instance.

    Reads SIM ON GROUND rather than an aircraft-specific L-var so this does
    not depend on which aircraft happens to be loaded.
    """
    if not live_manager.mobiflight_available:
        pytest.skip("MobiFlight WASM extension not available")

    from simconnect_mcp.tools.lvars import execute_calculator_code

    result = await execute_calculator_code("(A:SIM ON GROUND, Bool)")

    assert result.status == "ok"
    assert result.mode == "read"
    assert result.value in (0.0, 1.0)
