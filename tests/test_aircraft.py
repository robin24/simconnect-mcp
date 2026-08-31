"""Tests for the consolidated aircraft snapshot tool.

Before this file, three separate tools (get_aircraft_state,
get_aircraft_position, get_aircraft_systems) each read through
manager.aq (SimConnect.AircraftRequests) via a local _read_vars helper.
get_aircraft_snapshot replaces all three and migrates to
manager.accessor.read_many -- the same accessor every other tool module
was migrated to in Phase 0 / Tasks 3-5. See tools/aircraft.py's module
docstring for why that migration matters (unit-locked table, 828-entry
cap, bytes-for-strings, None-on-failure).
"""

from __future__ import annotations

from simconnect_mcp.tools.aircraft import SECTIONS, get_aircraft_snapshot
from simconnect_mcp.tools.models import AircraftSnapshot, ToolError


async def test_snapshot_defaults_to_every_section(mock_simconnect):
    """No `sections` argument -> every section in SECTIONS is included.

    Fails against an implementation that hardcodes a default list separate
    from SECTIONS' own keys (two sources of truth), or one that requires
    `sections` to be supplied.
    """
    result = await get_aircraft_snapshot()
    assert isinstance(result, AircraftSnapshot)
    assert set(result.sections) == set(SECTIONS)


async def test_snapshot_can_be_narrowed_to_one_section(mock_simconnect):
    """Fails against an implementation that ignores `sections` and always
    returns everything (FUEL_TOTAL_QUANTITY, which only lives in the
    'engines' section, would leak into a 'position'-only request)."""
    result = await get_aircraft_snapshot(sections=["position"])
    assert result.sections == ["position"]
    assert "PLANE_LATITUDE" in result.data
    assert "FUEL_TOTAL_QUANTITY" not in result.data


async def test_unknown_section_is_rejected_with_the_valid_list(mock_simconnect):
    """Fails against an implementation that skips validation entirely (which
    would KeyError on SECTIONS['nonsense'] instead of returning a ToolError),
    or one that validates but reports a generic message without actually
    naming the valid sections in `suggestion`."""
    result = await get_aircraft_snapshot(sections=["nonsense"])
    assert isinstance(result, ToolError)
    assert result.error == "INVALID_SECTION"
    assert "position" in result.suggestion


async def test_snapshot_reads_every_variable_in_one_batch(mock_simconnect):
    """One read_many call, not one read per variable, and not one read_many
    call per section either.

    Fails against a per-variable-read implementation (call_count would be 0
    on accessor.read_many, since accessor.read would be used instead), a
    per-section-batch implementation (call_count would be 2 for two
    sections), and -- most importantly for this task -- against any
    implementation that still reads through the legacy manager.aq path
    (accessor.read_many would never be called at all: count 0, not 1).
    """
    await get_aircraft_snapshot(sections=["position", "engines"])
    assert mock_simconnect["accessor"].read_many.call_count == 1


async def test_snapshot_never_touches_the_legacy_aq_path(mock_simconnect):
    """The point of this task: tools/aircraft.py must stop reading through
    manager.aq (SimConnect.AircraftRequests) entirely.

    Fails against the current module (and against any reimplementation that
    keeps a manager.aq.get(...) fallback anywhere): mock_simconnect['aq'] is
    a MagicMock standing in for the legacy AircraftRequests object, and the
    current _read_vars helper calls aq.get() once per variable, so this
    assertion trips immediately against unmigrated code.
    """
    await get_aircraft_snapshot()
    mock_simconnect["aq"].get.assert_not_called()


async def test_sections_are_deduplicated(mock_simconnect):
    """Fails against an implementation that passes `sections` straight
    through without deduplicating, which would leave
    result.sections == ["position", "position"]."""
    result = await get_aircraft_snapshot(sections=["position", "position"])
    assert result.sections == ["position"]


async def test_position_section_pins_explicit_units_not_catalog_defaults(mock_simconnect):
    """PLANE_HEADING_DEGREES_TRUE/_MAGNETIC default to Radians in the SimVar
    catalog, and GROUND_ALTITUDE defaults to Meters -- resolve_unit() would
    silently fall back to those if aircraft.py passed unit=None for them
    instead of pinning an explicit unit. That silent fallback is exactly
    the reproducibility bug this task's SECTIONS table exists to fix.

    Fails against a reimplementation that (correctly, by the old tools'
    behaviour) omits the unit and lets the catalog default apply: the
    request tuples would carry unit=None instead of 'degrees'/'feet'.
    """
    await get_aircraft_snapshot(sections=["position"])
    requests = mock_simconnect["accessor"].read_many.call_args.args[0]
    units_by_name = {name: unit for name, unit, _index in requests}
    assert units_by_name["PLANE_HEADING_DEGREES_TRUE"] == "degrees"
    assert units_by_name["PLANE_HEADING_DEGREES_MAGNETIC"] == "degrees"
    assert units_by_name["GROUND_ALTITUDE"] == "feet"


async def test_snapshot_diagnoses_a_failed_entry_instead_of_leaving_a_raw_string(
    mock_simconnect,
):
    """get_simvar_bulk ran read_many's per-entry failures through
    _simvar_error_envelope; get_aircraft_snapshot did not, so the identical
    dict surfaced here as an undiagnosed exception string with no error code
    and no suggestion.

    Fails against an implementation that returns read_many's raw dicts:
    'error_code' and 'suggestion' would both be absent.
    """
    mock_simconnect["accessor"].simulated_read_seconds = 1000.0

    result = await get_aircraft_snapshot(sections=["position"])

    entry = result.data["PLANE_LATITUDE"]
    assert entry["error_code"] == "BATCH_BUDGET_EXCEEDED"
    assert entry["suggestion"]
    assert "paused" not in entry["suggestion"].lower()


async def test_snapshot_reports_ok_and_error_counts(mock_simconnect):
    """Partial failure must be visible above the per-variable dicts."""
    ok = await get_aircraft_snapshot(sections=["position"])
    assert ok.error_count == 0
    assert ok.ok_count == len(SECTIONS["position"])

    mock_simconnect["accessor"].simulated_read_seconds = 1000.0
    failed = await get_aircraft_snapshot(sections=["position"])
    assert failed.ok_count == 0
    assert failed.error_count == len(SECTIONS["position"])
