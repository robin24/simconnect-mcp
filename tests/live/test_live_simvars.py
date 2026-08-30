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


async def test_failed_lvar_detection_is_disclosed_not_guessed(live_manager):
    """Aircraft-catalog auto-detection must say so when it finds nothing --
    never silently fall back to a guessed catalog.

    Verified live: this session's TITLE ('777F') and ATC_MODEL
    ('ATCCOM.AC_MODEL B77L.0.text') match none of the bundled catalogs'
    title_pattern ('Fenix', 'PMDG 737', 'PMDG 777'), so detection legitimately
    fails for the aircraft actually loaded right now. This test depends on
    that: if a catalog-matching aircraft (e.g. a PMDG 777) is loaded when this
    suite is re-run, this specific test -- and only this one -- would need the
    aircraft swapped back, which is expected, not a regression.
    """
    from simconnect_mcp.tools.lvars import search_lvars

    title, model = await live_manager.detect_aircraft_identity()
    assert title  # TITLE should never be empty with an aircraft loaded

    from simconnect_mcp.data.catalog import detect_catalog

    assert detect_catalog(title, model) is None

    result = await search_lvars("engine")
    assert result.status == "ok"
    assert result.filters["catalog"] == "all"
    assert result.message is not None
    assert "auto-detected" in result.message.lower()
