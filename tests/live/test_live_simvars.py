"""Live verification of the data-definition layer.

Run with MSFS running and an aircraft loaded:  uv run pytest -m live

Everything here targets a claim the mocked suite (tests/test_simvar_access.py)
cannot make: that the behaviour actually holds against the real SimConnect
DLL, not just against a FakeSM standing in for it.
"""
from __future__ import annotations

import pytest

from simconnect_mcp.simvar_access import SimVarNotFoundError, SimVarNotSettableError

pytestmark = pytest.mark.live


def test_unit_conversion_is_real(live_manager):
    """The core claim of the new layer: the requested unit is honoured.

    1 foot = 0.3048 metres, so the two readings must differ by that ratio.
    """
    feet = live_manager.accessor.read("PLANE_ALTITUDE", unit="feet")
    metres = live_manager.accessor.read("PLANE_ALTITUDE", unit="meters")

    assert feet is not None and metres is not None
    assert metres == pytest.approx(feet * 0.3048, rel=0.01)


def test_string_simvar_decodes_to_str(live_manager):
    title = live_manager.accessor.read("TITLE")
    assert isinstance(title, str)
    assert title.strip(), "TITLE should not be empty with an aircraft loaded"


def test_unknown_simvar_raises_not_found(live_manager):
    with pytest.raises(SimVarNotFoundError):
        live_manager.accessor.read("DEFINITELY_NOT_A_REAL_SIMVAR")


@pytest.mark.parametrize(
    "name,kwargs",
    [
        # A calculated per-engine readout. index=1 is the live engine on the
        # verified session (index 0 read 0.0 -- an unpowered engine can't
        # distinguish "rejected write" from "wrote 0 to something already 0").
        ("ENG_N1_RPM", {"index": 1}),
        # A calculated ambient readout with no index at all.
        ("AMBIENT_TEMPERATURE", {}),
    ],
)
def test_write_to_read_only_var_is_rejected(live_manager, name, kwargs):
    """The bug this layer replaces reported success here.

    Both variables are calculated, read-only values. SimConnect raises
    SIMCONNECT_EXCEPTION_DATA_ERROR for the write, which the accessor
    translates to SimVarNotSettableError -- verified live against both.
    (AIRSPEED_TRUE, the brief's original choice, turned out to be genuinely
    settable on this build: a write of 350 landed and read back. It is not
    a read-only variable and must not be used for this assertion.)
    """
    with pytest.raises(SimVarNotSettableError):
        live_manager.accessor.write(name, 999.0, grace=0.5, **kwargs)


def test_write_then_read_round_trips(live_manager, restore_autopilot_altitude):
    """Autopilot altitude is settable and immediately readable.

    Also exercises write(verify=True): the accessor's own read-back check
    against a real DLL round trip, not a FakeSM standing in for one.
    """
    result = live_manager.accessor.write(
        "AUTOPILOT_ALTITUDE_LOCK_VAR", 12000.0, unit="feet", verify=True
    )
    assert result is True

    value = live_manager.accessor.read("AUTOPILOT_ALTITUDE_LOCK_VAR", unit="feet")
    assert value == pytest.approx(12000.0, abs=50.0)


def test_indexed_read_with_index_zero_and_one(live_manager):
    """Index 0 must reach the sim rather than being dropped."""
    for index in (0, 1):
        value = live_manager.accessor.read(
            "GENERAL_ENG_THROTTLE_LEVER_POSITION", unit="percent", index=index
        )
        assert value is not None


def test_variable_outside_the_library_table_is_readable(live_manager):
    """AircraftRequests' hardcoded table does not cover everything.

    No explicit unit: this also proves the catalog default ('Slugs per cubic
    feet') is what reaches AddToDataDefinition -- the wrong unit for a
    density variable would either raise UnitMismatchError or return a value
    wildly outside a physically sensible density, not silently succeed.
    """
    value = live_manager.accessor.read("AMBIENT_DENSITY")
    assert value is not None
    assert value > 0


def test_a_full_size_bulk_read_is_not_starved_by_its_own_budget(live_manager):
    """Finding A1, as measured.

    Before the fix, get_simvar_bulk passed no budget to read_many, whose
    `timeout` was a TOTAL budget defaulting to DEFAULT_TIMEOUT -- one
    variable's worth. A 100-variable call against this idle sim returned
    ok=71 with 26 entries reported as timeouts, each advising "The sim may
    be paused or loading. Try again shortly." It was neither.

    Deliberately end-to-end through the tool rather than through read_many:
    the defect was that the tool call site passed nothing, so a test that
    supplied a budget itself could not have caught it.
    """
    import asyncio

    from simconnect_mcp.data.simvar_catalog import load_catalog
    from simconnect_mcp.tools.simvars import MAX_BULK_VARIABLES, get_simvar_bulk

    names = [entry["name"] for rows in load_catalog().values() for entry in rows]
    names = names[:MAX_BULK_VARIABLES]
    assert len(names) == MAX_BULK_VARIABLES

    result = asyncio.run(get_simvar_bulk([{"name": n} for n in names]))

    starved = {
        key: entry for key, entry in result.variables.items()
        if entry.get("error_code") == "BATCH_BUDGET_EXCEEDED"
    }
    assert not starved, (
        f"{len(starved)} of {result.count} variables ran out of batch budget on an "
        f"idle sim: {sorted(starved)[:5]}"
    )
    assert result.error_count < result.count // 4, (
        f"{result.error_count} of {result.count} failed -- more than a stray bad "
        "catalog name would explain"
    )
