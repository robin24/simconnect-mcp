"""Tests for msfs_list_lvars -- Phase 2 Task 4.

Consumes Task 3's add_response_handler/remove_response_handler to collect
the MobiFlight WASM module's MF.LVars.List response in real time.

The 1000-name cap tests are the point of this task: measured live
(task-3-4-addendum.md), MF.LVars.List returns *exactly* 1000 names and
still sends its .End sentinel, so a truncated response is indistinguishable
from a complete one by the sentinel alone. A naive implementation would
report total:1000/has_more:false for a list that is neither -- the exact
fabricated-completeness pattern this project has spent Phase 0 and Phase 1
removing. See test_list_lvars_reports_truncated_at_the_cap below.

The fake bridges below only answer MF.LVars.List specifically, never any
other command (including the re-arm command list_lvars now sends first) --
matching the live-confirmed fact that MF.SimVars.Set.* produces zero
response-channel messages of its own (task-3-report.md). A fake that fired
on every send_command call regardless of the command string would silently
double-count every response once the re-arm landed.
"""

from __future__ import annotations

import asyncio
import logging
import threading

import pytest

from simconnect_mcp.tools.lvars import _REARM_COMMAND, list_lvars


@pytest.fixture
def mobiflight_sim(mock_simconnect):
    """A MobiFlight bridge that answers MF.LVars.List with three names."""
    manager = mock_simconnect["manager"]
    manager._mobiflight_available = True

    class FakeBridge:
        def __init__(self):
            self.response_handlers = []
            self.commands = []

        def add_response_handler(self, fn):
            self.response_handlers.append(fn)

        def remove_response_handler(self, fn):
            if fn in self.response_handlers:
                self.response_handlers.remove(fn)

        def send_command(self, command):
            self.commands.append(command)
            if command != "MF.LVars.List":
                return  # e.g. the re-arm command -- real WASM sends no echo
            for handler in list(self.response_handlers):
                for name in ("A32NX_AUTOPILOT_1_ACTIVE", "A32NX_FCU_HDG", "XMLVAR_Baro"):
                    handler(name)
                handler("MF.LVars.List.End")

    bridge = FakeBridge()
    manager.mobiflight = bridge
    mock_simconnect["bridge"] = bridge
    return mock_simconnect


class _CountingBridge:
    """Answers MF.LVars.List with `count` generated names plus the
    end-of-list sentinel -- used to probe the 1000-name cap boundary."""

    def __init__(self, count: int):
        self.count = count
        self.response_handlers = []
        self.commands = []

    def add_response_handler(self, fn):
        self.response_handlers.append(fn)

    def remove_response_handler(self, fn):
        if fn in self.response_handlers:
            self.response_handlers.remove(fn)

    def send_command(self, command):
        self.commands.append(command)
        if command != "MF.LVars.List":
            return  # e.g. the re-arm command -- real WASM sends no echo
        for handler in list(self.response_handlers):
            for i in range(self.count):
                handler(f"ZZZ_LVAR_{i:04d}")
            handler("MF.LVars.List.End")


async def test_list_lvars_returns_real_names(mobiflight_sim):
    result = await list_lvars()
    assert result.lvars == ["A32NX_AUTOPILOT_1_ACTIVE", "A32NX_FCU_HDG", "XMLVAR_Baro"]
    assert result.page.total == 3


async def test_list_lvars_sends_the_list_command(mobiflight_sim):
    await list_lvars()
    assert "MF.LVars.List" in mobiflight_sim["bridge"].commands


async def test_list_lvars_sends_the_rearm_command_immediately_before_list(mobiflight_sim):
    """Regression guard for the re-arm fix (task-4-report.md): the WASM
    module gives no response to MF.LVars.List when it is byte-identical to
    the command immediately preceding it. list_lvars sends a harmless
    no-op RPN command right before every list request specifically to
    prevent that -- if someone "cleans up" that extra call as dead code,
    this fails.
    """
    await list_lvars()
    commands = mobiflight_sim["bridge"].commands
    assert _REARM_COMMAND in commands
    list_index = commands.index("MF.LVars.List")
    assert commands[list_index - 1] == _REARM_COMMAND, (
        f"expected {_REARM_COMMAND!r} immediately before 'MF.LVars.List', got {commands!r}"
    )


async def test_list_lvars_filters_by_prefix(mobiflight_sim):
    result = await list_lvars(filter_prefix="A32NX")
    assert all(name.startswith("A32NX") for name in result.lvars)
    assert result.page.total == 2


async def test_list_lvars_paginates(mobiflight_sim):
    result = await list_lvars(limit=2, offset=0)
    assert result.page.count == 2
    assert result.page.has_more is True


async def test_list_lvars_unregisters_its_handler(mobiflight_sim):
    """A leaked handler would accumulate on every call."""
    await list_lvars()
    assert mobiflight_sim["bridge"].response_handlers == []


async def test_list_lvars_without_mobiflight_errors(mock_simconnect):
    mock_simconnect["manager"]._mobiflight_available = False
    result = await list_lvars()
    assert result.error == "MOBIFLIGHT_NOT_AVAILABLE"


async def test_list_lvars_small_response_is_not_truncated(mobiflight_sim):
    """Three names, nowhere near the cap: no truncation caveat earned."""
    result = await list_lvars()
    assert result.truncated is False
    assert result.message is None


# ---------------------------------------------------------------------------
# The 1000-name cap (task-3-4-addendum.md) -- the addendum's own words:
# "As written, msfs_list_lvars would report total:1000/has_more:false for a
# list that is neither total nor complete." These two tests are the guard
# against shipping exactly that.
# ---------------------------------------------------------------------------


async def test_list_lvars_reports_truncated_at_the_cap(mock_simconnect):
    manager = mock_simconnect["manager"]
    manager._mobiflight_available = True
    manager.mobiflight = _CountingBridge(1000)

    result = await list_lvars()

    assert result.truncated is True, (
        "a 1000-name response must be flagged as presumptively truncated -- "
        "the WASM module still sends .End for a capped list, so silence here "
        "is exactly the fabricated-completeness defect this task removes"
    )
    assert result.message is not None
    assert "1000" in result.message


async def test_list_lvars_one_below_the_cap_is_not_truncated(mock_simconnect):
    """The boundary must not be off by one in either direction: 999 names
    is a response the module was never forced to cut short."""
    manager = mock_simconnect["manager"]
    manager._mobiflight_available = True
    manager.mobiflight = _CountingBridge(999)

    result = await list_lvars()

    assert result.truncated is False
    assert result.message is None


async def test_list_lvars_truncation_survives_prefix_filtering(mock_simconnect):
    """filter_prefix narrows the *view*, not the underlying WASM response --
    a capped response stays capped even if only a handful of names match."""
    manager = mock_simconnect["manager"]
    manager._mobiflight_available = True
    manager.mobiflight = _CountingBridge(1000)

    result = await list_lvars(filter_prefix="ZZZ_LVAR_00")
    assert result.truncated is True


# ---------------------------------------------------------------------------
# Concurrency (CRITICAL from review): run_sync only holds _sim_lock for the
# synchronous send, releasing it well before `finished.wait()` returns -- so
# without a lock spanning register -> send -> wait -> cleanup, a second
# concurrent call registers its own handler and fires its own MF.LVars.List
# burst while the first is still collecting. The vendored fan-out
# (_deliver_response) delivers every response to every registered handler
# with no per-call correlation, so the two calls' bursts land in each
# other's `names` list too. Reproduced by the reviewer against a 600-name
# fake bridge: both calls' raw counts inflated to 1200, tripping
# `truncated=True` for a response nowhere near the real 1000-name cap --
# `page.total=600, truncated=True` is an internally inconsistent result,
# exactly the silently-wrong signal this task exists to prevent, arriving
# through concurrency instead of a single-call path. Same bug class as
# facility_lock (tools/facilities.py, connection.py); guarded here the same
# way: SimConnectManager.list_lvars_lock() wraps the whole body.
# ---------------------------------------------------------------------------


class _GatedBridge:
    """Answers MF.LVars.List with `count` names, but blocks inside
    send_command on a threading.Event first -- deterministic control over
    when a call's burst fires, so a second concurrent call has a real
    window to attempt (and, with the fix, be blocked by) list_lvars_lock
    before the first call's response is delivered. Mirrors
    test_facilities_tools.py's test_concurrent_calls_do_not_race_the_
    collector_reset, which proves the analogous facility_lock the same way.
    """

    def __init__(self, count: int, gate: threading.Event, reached_gate: list):
        self.count = count
        self.gate = gate
        self.reached_gate = reached_gate
        self.response_handlers: list = []
        self.commands: list[str] = []

    def add_response_handler(self, fn):
        self.response_handlers.append(fn)

    def remove_response_handler(self, fn):
        if fn in self.response_handlers:
            self.response_handlers.remove(fn)

    def send_command(self, command):
        self.commands.append(command)
        if command != "MF.LVars.List":
            return  # the re-arm command -- real WASM sends no echo
        self.reached_gate.append(command)
        self.gate.wait(timeout=2.0)
        for handler in list(self.response_handlers):
            for i in range(self.count):
                handler(f"ZZZ_LVAR_{i:04d}")
            handler("MF.LVars.List.End")


async def test_concurrent_calls_do_not_cross_contaminate(mock_simconnect):
    """Fails against code with no list_lvars_lock: without it, task2
    registers its handler and reaches the gate alongside task1 (both
    blocked on the SAME threading.Event, since run_sync runs each call's
    send on its own executor thread), so releasing the gate fires both
    bursts into both handlers -- each call ends up with page.total=1200
    and truncated=True for a 600-name response. With the lock, task2
    cannot even register its handler until task1's entire cycle
    (register -> send -> wait -> unregister) has released it.
    """
    manager = mock_simconnect["manager"]
    manager._mobiflight_available = True

    gate = threading.Event()
    reached_gate: list = []
    bridge = _GatedBridge(600, gate, reached_gate)
    manager.mobiflight = bridge

    task1 = asyncio.create_task(list_lvars())

    # Wait for task1 to actually reach the gated send (past registration).
    for _ in range(50):
        await asyncio.sleep(0.01)
        if reached_gate:
            break
    assert reached_gate, "task1 never reached the gated MF.LVars.List send"

    task2 = asyncio.create_task(list_lvars())
    # Give task2 a real chance to run up to (and, with the fix, block on)
    # list_lvars_lock.
    await asyncio.sleep(0.05)

    assert len(bridge.response_handlers) == 1, (
        "task2 registered a response handler before task1 finished -- the "
        "lock should have blocked it at list_lvars_lock, before it ever "
        "reaches add_response_handler"
    )

    gate.set()
    result1, result2 = await asyncio.gather(task1, task2)

    for label, result in (("task1", result1), ("task2", result2)):
        assert result.page.total == 600, f"{label}: {result!r}"
        assert result.truncated is False, (
            f"{label} reported truncated=True for a 600-name response -- "
            f"cross-contamination from the other call's burst: {result!r}"
        )

    assert bridge.response_handlers == [], "both handlers must be unregistered afterward"


# ---------------------------------------------------------------------------
# Cancellation (IMPORTANT from review): a cancelled tool call must still
# remove its handler. Mirrors test_facilities_tools.py's
# test_a_cancelled_collection_still_unsubscribes, which came from the same
# finding against structurally identical code.
# ---------------------------------------------------------------------------


async def test_a_cancelled_call_still_removes_its_handler(mock_simconnect):
    """Fails against code with no try/finally around the handler: cancelling
    while `finished.wait()` is pending would otherwise skip
    remove_response_handler entirely, leaking a handler that keeps
    receiving traffic (and, per the lock above, would permanently deadlock
    every later list_lvars call against list_lvars_lock, since nothing
    would ever release it)."""
    manager = mock_simconnect["manager"]
    manager._mobiflight_available = True

    class _SilentBridge:
        """Never fires a response handler -- guarantees the task is still
        parked at `finished.wait()` when this test cancels it."""

        def __init__(self):
            self.response_handlers: list = []

        def add_response_handler(self, fn):
            self.response_handlers.append(fn)

        def remove_response_handler(self, fn):
            if fn in self.response_handlers:
                self.response_handlers.remove(fn)

        def send_command(self, command):
            pass

    bridge = _SilentBridge()
    manager.mobiflight = bridge

    task = asyncio.create_task(list_lvars())

    # Let the task actually start and reach finished.wait().
    for _ in range(50):
        await asyncio.sleep(0.01)
        if bridge.response_handlers:
            break
    assert bridge.response_handlers, "the task never registered its handler"
    assert not task.done()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert bridge.response_handlers == [], (
        "a cancelled list_lvars call must still remove its response handler"
    )

    # A follow-up call must still work normally -- proves list_lvars_lock
    # was released (asyncio.Lock's __aexit__ always runs) and nothing is
    # left wedged after a cancellation.
    manager.mobiflight = _CountingBridge(5)
    result = await list_lvars()
    assert result.page.total == 5


# ---------------------------------------------------------------------------
# Handler-failure visibility (MINOR from review): Task 3's ruling was that
# the vendored fan-out's own DEBUG-level catch is invisible at this
# server's default WARNING level, so list_lvars's own _on_response must log
# failures itself, at a level someone will actually see. Nothing forced an
# exception through it before this test.
# ---------------------------------------------------------------------------


async def test_response_handler_failure_is_logged_at_warning(mock_simconnect, caplog):
    """text is always a decoded str in practice, so this is a low-probability
    path -- but it is the mechanism standing in for the vendored DEBUG log
    Task 3 deliberately left alone, so it should be pinned rather than
    trusted on inspection alone. A non-string response makes `.startswith`
    raise AttributeError inside _on_response."""
    manager = mock_simconnect["manager"]
    manager._mobiflight_available = True

    class _MisbehavingBridge:
        def __init__(self):
            self.response_handlers: list = []

        def add_response_handler(self, fn):
            self.response_handlers.append(fn)

        def remove_response_handler(self, fn):
            if fn in self.response_handlers:
                self.response_handlers.remove(fn)

        def send_command(self, command):
            if command != "MF.LVars.List":
                return
            for handler in list(self.response_handlers):
                handler(None)  # not a str -- text.startswith(...) raises
                handler("A32NX_TEST")
                handler("MF.LVars.List.End")

    manager.mobiflight = _MisbehavingBridge()

    with caplog.at_level(logging.WARNING, logger="simconnect_mcp.tools.lvars"):
        result = await list_lvars()

    assert result.lvars == ["A32NX_TEST"], "the bad message must not abort the whole collection"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "expected a WARNING when the response handler raised"
    assert "msfs_list_lvars" in warnings[0].getMessage()
