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
    """
    from simconnect_mcp.tools.lvars import list_lvars

    if not live_manager.mobiflight_available:
        pytest.skip("MobiFlight WASM module not installed")

    # See test_repeated_identical_list_request_gets_no_response below: the
    # WASM module drops MF.LVars.List when it repeats the immediately
    # preceding command on this channel, and live_manager is
    # session-scoped, so a prior test elsewhere could leave this "stuck"
    # before this one even runs. Reset first so this test's own result
    # reflects the aircraft, not test ordering.
    live_manager.mobiflight.clear_sim_variables()

    result = await list_lvars()
    if getattr(result, "error", None) == "MOBIFLIGHT_NOT_AVAILABLE":
        pytest.skip("MobiFlight WASM module not installed")

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


async def test_repeated_identical_list_request_gets_no_response(live_manager, monkeypatch):
    """Pins a real WASM module quirk discovered while building this tool
    (see task-4-report.md): MF.LVars.List gets no response at all when it
    is byte-identical to the command immediately preceding it on the
    MobiFlight.Command channel. Confirmed NOT a time-based cooldown --
    waiting 20s with nothing else sent still produced zero messages; only
    a different command in between (even one with no response of its own,
    like MF.SimVars.Clear) restored it. msfs_list_lvars's own
    NO_LVARS_RETURNED error mentions this so a caller isn't left thinking
    the aircraft or the WASM module broke.

    Timeout/settle constants are patched down after the first (expected to
    succeed) call, since confirming the second call gets nothing would
    otherwise cost the full production wait for no reason.
    """
    if not live_manager.mobiflight_available:
        pytest.skip("MobiFlight WASM module not installed")

    from simconnect_mcp.tools import lvars as lvars_module

    # Guarantees this test's own first call isn't itself a dup of whatever
    # a previous test last sent on this channel.
    live_manager.mobiflight.clear_sim_variables()

    first = await lvars_module.list_lvars()
    assert first.status == "ok", f"prerequisite call itself failed: {first!r}"

    monkeypatch.setattr(lvars_module, "_LIST_TIMEOUT_S", 1.0)
    monkeypatch.setattr(lvars_module, "_LIST_SETTLE_S", 0.2)

    second = await lvars_module.list_lvars()

    assert getattr(second, "error", None) == "NO_LVARS_RETURNED", (
        "expected the WASM module's confirmed repeat-suppression to produce "
        f"NO_LVARS_RETURNED on an immediate identical repeat, got {second!r}"
    )
