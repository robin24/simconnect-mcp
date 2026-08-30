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
