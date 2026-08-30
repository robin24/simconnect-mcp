"""Live verification of L-var writes through the SimVar accessor.

Run with MSFS running and an aircraft loaded:  uv run pytest -m live

A mocked test cannot distinguish a working L-var write from one that
encodes the wrong datum name or writes nothing at all -- a self-consistent
mock agrees with itself either way. Everything here targets a claim only a
real SimConnect DLL can settle.

Scratch variables
-----------------
These tests write to L-vars named CLAUDE_LIVE_* rather than to any
aircraft control. Writing a name the aircraft never registered creates a
new local variable that nothing in the aircraft's code reads, so the
loaded aircraft's state is untouched; there is no SimConnect call that
deletes an L-var, so each test zeroes the ones it created on the way out.
"""
from __future__ import annotations

import asyncio

import pytest

from simconnect_mcp.simvar_access import SimVarNotFoundError

pytestmark = pytest.mark.live

PROBE_A = "CLAUDE_LIVE_PROBE_A"
PROBE_B = "CLAUDE_LIVE_PROBE_B"


@pytest.fixture
def zero_probes(live_manager):
    """Leave both scratch variables at 0.0 whatever the test did."""
    yield
    for name in (PROBE_A, PROBE_B):
        try:
            live_manager.set_lvar(name, 0.0)
        except Exception:  # noqa: BLE001 - teardown must not mask a test failure
            pass


def test_two_distinct_lvars_do_not_collide(live_manager, zero_probes):
    """The encoding test a mock cannot make.

    A round trip on ONE name proves nothing about encoding: if the datum
    were mangled, the write and the read-back would target the same wrong
    variable and agree with each other. simconnect_name -- correct for a
    SimVar, and what this path used before raw_name existed -- reduces both
    'L:CLAUDE_LIVE_PROBE_A' and 'L:CLAUDE_LIVE_PROBE_B' to b'L', so under
    it the second write lands on the first's variable and PROBE_A reads
    back 22.0.
    """
    live_manager.set_lvar(PROBE_A, 11.0)
    live_manager.set_lvar(PROBE_B, 22.0)

    read_a = live_manager.accessor.read(f"L:{PROBE_A}", unit="number", raw_name=True)
    read_b = live_manager.accessor.read(f"L:{PROBE_B}", unit="number", raw_name=True)

    assert read_a == pytest.approx(11.0), (
        f"expected 11.0, got {read_a} -- the two datum names collided, so the "
        "L-var name is not reaching SimConnect intact"
    )
    assert read_b == pytest.approx(22.0)


def test_write_verify_confirms_a_value_that_landed(live_manager, zero_probes):
    assert live_manager.set_lvar(PROBE_A, 33.0, verify=True) is True
    assert live_manager.accessor.read(
        f"L:{PROBE_A}", unit="number", raw_name=True
    ) == pytest.approx(33.0)


def test_write_verify_reports_zero_honestly(live_manager, zero_probes):
    """Writing 0.0 must verify as True, not be confused with "no value".

    A native read of an L-var that was never set also returns 0.0
    (measured), so this pins that a genuine zero write is still reported as
    landed rather than as a failure.
    """
    live_manager.set_lvar(PROBE_A, 44.0)
    assert live_manager.set_lvar(PROBE_A, 0.0, verify=True) is True


def test_repeated_writes_reuse_one_definition(live_manager):
    """The leak: connection.set_lvar called new_def_id() on every write,
    and CLAUDE.md's documented Fenix FCU procedure issues one write per
    knob click at 15 ms intervals.

    Measured against the real accessor's own cache, so this fails against
    any reimplementation that builds a definition per write (delta 5).

    The variable name is unique to this test because `live_manager` is
    session-scoped: a name another test already wrote would still be in
    the cache, making the delta 0 and the assertion order-dependent.
    """
    accessor = live_manager.accessor
    name = "CLAUDE_LIVE_DEFCACHE"
    before = len(accessor._definitions)

    for value in (1.0, 2.0, 3.0, 4.0, 5.0):
        live_manager.set_lvar(name, value)

    # One definition for the write path; the read path is not exercised here.
    assert len(accessor._definitions) - before == 1, (
        "five writes to one L-var must share a single data definition, got "
        f"{len(accessor._definitions) - before}"
    )
    live_manager.set_lvar(name, 0.0)


def test_non_ascii_lvar_name_raises_a_typed_error(live_manager):
    """The old path let name.encode("ascii") raise a bare
    UnicodeEncodeError into handle_simconnect_errors' catch-all, producing
    an UNEXPECTED envelope leaking Python exception text. Confirmed live
    against that path before the fix.
    """
    with pytest.raises(SimVarNotFoundError):
        live_manager.set_lvar("CLAUDE_LIVE_DEGREE_\N{DEGREE SIGN}", 1.0)


async def test_set_lvar_tool_returns_a_verified_envelope(live_manager, zero_probes):
    """End to end through the MCP tool, against the real sim."""
    from simconnect_mcp.tools.lvars import set_lvar

    result = await set_lvar(PROBE_A, 55.0)

    assert result.status == "ok"
    assert result.verified is True, f"unverified write: {result}"
    assert result.warning is None
    assert result.value_set == 55.0


async def test_list_lvars_enumerates_the_loaded_aircraft(live_manager):
    """Requires the MobiFlight WASM module in the Community folder.

    Not asserting an exact count or that any particular aircraft prefix
    shows up -- task-3-4-addendum.md measured this exact live setup (GSX at
    KATL) returning exactly 1000 FSDT_GSX_* names with the aircraft's own
    L-vars crowded out entirely, so what a fresh run sees here depends on
    whatever add-ons happen to be installed. What's pinned instead is the
    honesty contract: whenever the cap is actually hit, 'truncated' must
    say so, and never the opposite.

    No defensive reset needed before this call (there was one here
    previously): list_lvars sends its own re-arm command immediately
    before every MF.LVars.List, so it no longer matters what a previous
    test left on the MobiFlight.Command channel. See
    test_repeated_list_lvars_calls_both_succeed and
    test_repeated_identical_raw_list_command_gets_no_response below for the
    two halves of that story.
    """
    from simconnect_mcp.tools.lvars import list_lvars

    if not live_manager.mobiflight_available:
        pytest.skip("MobiFlight WASM module not installed")

    result = await list_lvars()
    if getattr(result, "error", None) == "MOBIFLIGHT_NOT_AVAILABLE":
        pytest.skip("MobiFlight WASM module not installed")

    # Deliberately a failure, not a skip. The addendum established live that
    # the module always sends .End, even for a capped list, so seeing this
    # would mean the response stream was disrupted mid-listing -- worth
    # knowing about loudly rather than stepping around. Checked before
    # result.page below so it reports the envelope instead of dying on an
    # AttributeError against a ToolError.
    assert getattr(result, "error", None) != "LVAR_LIST_INCOMPLETE", (
        f"MF.LVars.List ended with no .End sentinel: {result!r}"
    )

    assert result.page.total > 0
    assert all(isinstance(name, str) and name for name in result.lvars)

    if result.page.total >= 1000:
        assert result.truncated is True, (
            "a response of 1000+ names must be flagged as presumptively "
            "truncated -- the WASM module sends .End for a capped list too"
        )
        assert result.message is not None
    else:
        assert result.truncated is False
        assert result.message is None


async def test_repeated_list_lvars_calls_both_succeed(live_manager):
    """Confirms the re-arm fix end to end, through the real tool.

    Before the fix (task-4-report.md), two back-to-back msfs_list_lvars
    calls reliably failed on the second: the WASM module gives no response
    to MF.LVars.List when it is byte-identical to the command immediately
    preceding it on MobiFlight.Command. list_lvars now sends a harmless
    no-op re-arm command first every time, specifically so an agent calling
    it twice in a row -- a perfectly ordinary thing to do -- doesn't hit
    that. No delay and no cleanup between the two calls: this is
    deliberately the tightest, most adversarial back-to-back timing, the
    same shape that reproduced the bug 0/4 while building the fix.
    """
    from simconnect_mcp.tools.lvars import list_lvars

    if not live_manager.mobiflight_available:
        pytest.skip("MobiFlight WASM module not installed")

    first = await list_lvars()
    second = await list_lvars()

    for label, result in (("first", first), ("second", second)):
        if getattr(result, "error", None) == "MOBIFLIGHT_NOT_AVAILABLE":
            pytest.skip("MobiFlight WASM module not installed")
        assert result.status == "ok", f"{label} call failed: {result!r}"
        assert result.page.total > 0, f"{label} call returned no names: {result!r}"


async def test_repeated_identical_raw_list_command_gets_no_response(live_manager):
    """Pins the underlying WASM module quirk itself (task-4-report.md),
    independent of msfs_list_lvars's own workaround: two MF.LVars.List
    commands sent back to back, with nothing in between, get a response
    only for the first. This talks to the bridge directly rather than
    through list_lvars, specifically so it keeps proving the quirk exists
    rather than proving the tool's fix works (that's
    test_repeated_list_lvars_calls_both_succeed above) -- once list_lvars
    always re-arms first, two list_lvars calls no longer reproduce this.

    Self-contained on both ends: re-arms before its own first ("prerequisite")
    call too, not just its last one -- live_manager is session-scoped, and a
    previous test's own trailing MF.LVars.List (e.g.
    test_repeated_list_lvars_calls_both_succeed above, which necessarily
    ends on one) would otherwise make even this test's first call collide,
    which is exactly the failure mode this test exists to demonstrate, just
    arriving one call too early to be measuring it on purpose. So: re-arm,
    confirm a list succeeds, send an identical list with nothing in
    between and confirm THAT one gets nothing, then re-arm again in a
    finally so whatever test runs next isn't left holding this one's mess
    either.
    """
    if not live_manager.mobiflight_available:
        pytest.skip("MobiFlight WASM module not installed")

    from simconnect_mcp.tools.lvars import _REARM_COMMAND

    async def _list_once(wait_s: float) -> list[str]:
        seen: list[str] = []
        live_manager.mobiflight.add_response_handler(seen.append)
        try:
            await live_manager.run_sync(live_manager.mobiflight.send_command, "MF.LVars.List")
            loop = asyncio.get_running_loop()
            deadline = loop.time() + wait_s
            while loop.time() < deadline:
                if seen and seen[-1] == "MF.LVars.List.End":
                    break
                await asyncio.sleep(0.05)
        finally:
            live_manager.mobiflight.remove_response_handler(seen.append)
        return seen

    try:
        await live_manager.run_sync(live_manager.mobiflight.send_command, _REARM_COMMAND)

        first = await _list_once(6.0)
        assert first and first[-1] == "MF.LVars.List.End", (
            f"prerequisite call itself got no response: {first!r}"
        )

        second = await _list_once(1.0)
        assert second == [], (
            "expected the WASM module's confirmed repeat-suppression to "
            f"produce no response on an immediate identical repeat, got {second!r}"
        )
    finally:
        await live_manager.run_sync(live_manager.mobiflight.send_command, _REARM_COMMAND)
