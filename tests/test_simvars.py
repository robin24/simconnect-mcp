"""Tests for SimVar tools."""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from simconnect_mcp.simvar_access import (
    SimVarNotFoundError,
    SimVarNotSettableError,
    SimVarTimeoutError,
)
from simconnect_mcp.tools.simvars import (
    MAX_BULK_VARIABLES,
    get_simvar,
    get_simvar_bulk,
    list_simvar_categories,
    search_simvars,
    set_simvar,
    watch_simvar,
)


@pytest.mark.asyncio
async def test_get_simvar(mock_simconnect):
    """Reading a SimVar returns value."""
    result = await get_simvar("PLANE_LATITUDE")
    assert result["status"] == "ok"
    assert result["value"] == 47.6062
    assert result["name"] == "PLANE_LATITUDE"


@pytest.mark.asyncio
async def test_get_simvar_not_found(mock_simconnect):
    """Reading a nonexistent SimVar returns error with suggestions."""
    mock_simconnect["accessor"].read.side_effect = SimVarNotFoundError("not found")
    result = await get_simvar("NONEXISTENT_VAR")
    assert result["status"] == "error"
    assert result["error"] == "SIMVAR_NOT_FOUND"


@pytest.mark.asyncio
async def test_set_simvar(mock_simconnect):
    """Writing a SimVar succeeds."""
    mock_simconnect["accessor"].write.return_value = True
    result = await set_simvar("PLANE_LATITUDE", 48.0)
    assert result["status"] == "ok"
    assert result["value_set"] == 48.0
    assert result["verified"] is True


@pytest.mark.asyncio
async def test_set_simvar_reports_failure_instead_of_faking_success(mock_simconnect):
    """Regression: the old code discarded aq.set()'s False and returned ok."""
    mock_simconnect["accessor"].write.side_effect = SimVarNotSettableError(
        "SimVar 'AIRSPEED_INDICATED' cannot be written"
    )
    result = await set_simvar("AIRSPEED_INDICATED", 250.0)
    assert result["status"] == "error"
    assert result["error"] == "SIMVAR_NOT_SETTABLE"


@pytest.mark.asyncio
async def test_set_simvar_success_reports_the_unit_used(mock_simconnect):
    mock_simconnect["accessor"].write.return_value = True
    result = await set_simvar("AUTOPILOT_ALTITUDE_LOCK_VAR", 12000.0, unit="feet")
    assert result["status"] == "ok"
    assert result["unit"] == "feet"
    assert result["value_set"] == 12000.0


@pytest.mark.asyncio
async def test_set_simvar_unknown_name_returns_not_found(mock_simconnect):
    mock_simconnect["accessor"].write.side_effect = SimVarNotFoundError("nope")
    result = await set_simvar("NOT_A_REAL_VAR", 1.0)
    assert result["error"] == "SIMVAR_NOT_FOUND"


@pytest.mark.asyncio
async def test_set_simvar_passes_index_zero(mock_simconnect):
    mock_simconnect["accessor"].write.return_value = True
    await set_simvar("GENERAL_ENG_THROTTLE_LEVER_POSITION", 50.0, index=0)
    assert mock_simconnect["accessor"].write.call_args.kwargs["index"] == 0


@pytest.mark.asyncio
async def test_set_simvar_bad_unit_on_a_known_var_reports_unit_mismatch(mock_simconnect):
    """When SimConnect rejects a write with NAME_UNRECOGNIZED, the catalog
    disambiguates: if the name exists and the caller supplied a unit, the unit
    is at fault, not the name."""
    mock_simconnect["accessor"].write.side_effect = SimVarNotFoundError("nope")
    result = await set_simvar("PLANE_ALTITUDE", 1000.0, unit="bananas")
    assert result["error"] == "UNIT_MISMATCH"
    assert "bananas" in result["message"]
    assert "suggestions" not in result


@pytest.mark.asyncio
async def test_bad_name_still_reports_not_found(mock_simconnect):
    """A typo in the name should report SIMVAR_NOT_FOUND with suggestions."""
    mock_simconnect["accessor"].write.side_effect = SimVarNotFoundError("nope")
    result = await set_simvar("PLANE_ALTITUD", 1.0, unit="feet")
    assert result["error"] == "SIMVAR_NOT_FOUND"
    assert "PLANE_ALTITUDE" in result["suggestions"]


@pytest.mark.asyncio
async def test_known_var_without_a_caller_unit_stays_not_found(mock_simconnect):
    """When unit=None (using the catalog's own unit), a write failure is a
    genuine name problem, not a unit mismatch."""
    mock_simconnect["accessor"].write.side_effect = SimVarNotFoundError("nope")
    result = await set_simvar("PLANE_ALTITUDE", 1.0)
    assert result["error"] == "SIMVAR_NOT_FOUND"


@pytest.mark.asyncio
async def test_unit_mismatch_message_uses_the_sanitised_unit(mock_simconnect):
    """The raw catalog unit for ENG_N1_RPM is prose; the message must use the
    sanitised unit returned by resolve_unit()."""
    mock_simconnect["accessor"].write.side_effect = SimVarNotFoundError("nope")
    result = await set_simvar("ENG_N1_RPM", 50.0, index=1, unit="bananas")
    assert "(0 to 16384" not in result["suggestion"]
    assert "'Rpm'" in result["suggestion"]


@pytest.mark.asyncio
async def test_not_settable_suggestion_does_not_recommend_the_settable_flag(mock_simconnect):
    """The catalog's settable flag has false negatives; recommending it would
    make agents skip writes that actually work."""
    mock_simconnect["accessor"].write.side_effect = SimVarNotSettableError("read-only")
    result = await set_simvar("ENG_N1_RPM", 50.0, index=1)
    assert "unreliable" in result["suggestion"]


@pytest.mark.parametrize("tool", [get_simvar, set_simvar])
@pytest.mark.asyncio
async def test_both_tools_report_the_sanitised_unit_on_mismatch(tool, mock_simconnect):
    """ENG_N1_RPM's raw catalog unit is prose. Both tools must say 'Rpm'."""
    mock_simconnect["accessor"].read.side_effect = SimVarNotFoundError("nope")
    mock_simconnect["accessor"].write.side_effect = SimVarNotFoundError("nope")
    kwargs = {"index": 1, "unit": "bananas"}
    result = await (tool("ENG_N1_RPM", 50.0, **kwargs) if tool is set_simvar
                    else tool("ENG_N1_RPM", **kwargs))
    assert result["error"] == "UNIT_MISMATCH"
    assert "'Rpm'" in result["suggestion"]
    assert "(0 to 16384" not in result["suggestion"]




@pytest.mark.asyncio
async def test_get_simvar_bulk(mock_simconnect):
    """Bulk read returns multiple values."""
    result = await get_simvar_bulk([
        {"name": "PLANE_LATITUDE"},
        {"name": "AIRSPEED_INDICATED"},
    ])
    assert result["status"] == "ok"
    assert "PLANE_LATITUDE" in result["variables"]
    assert "AIRSPEED_INDICATED" in result["variables"]


@pytest.mark.asyncio
async def test_bulk_read_honours_index_zero(mock_simconnect):
    """Regression: `if idx` dropped index 0, silently reading the wrong var."""
    await get_simvar_bulk([{"name": "GENERAL_ENG_THROTTLE_LEVER_POSITION", "index": 0}])
    requests = mock_simconnect["accessor"].read_many.call_args.args[0]
    assert requests == [("GENERAL_ENG_THROTTLE_LEVER_POSITION", None, 0)]


@pytest.mark.asyncio
async def test_bulk_read_passes_units(mock_simconnect):
    await get_simvar_bulk([{"name": "PLANE_ALTITUDE", "unit": "meters"}])
    requests = mock_simconnect["accessor"].read_many.call_args.args[0]
    assert requests == [("PLANE_ALTITUDE", "meters", None)]


@pytest.mark.asyncio
async def test_bulk_read_returns_a_value_per_variable(mock_simconnect):
    result = await get_simvar_bulk([{"name": "PLANE_ALTITUDE"}, {"name": "AIRSPEED_INDICATED"}])
    assert result["status"] == "ok"
    assert result["variables"]["PLANE_ALTITUDE"]["value"] == 35000.0
    assert "unit" in result["variables"]["PLANE_ALTITUDE"]
    assert result["variables"]["PLANE_ALTITUDE"]["unit"] is not None


@pytest.mark.asyncio
async def test_bulk_read_disambiguates_a_bad_unit_on_a_known_variable(mock_simconnect):
    """Finding 6: get_simvar_bulk used to surface read_many's raw
    SimVarNotFoundError message for every failure -- "SimConnect does not
    recognise SimVar 'PLANE_ALTITUDE'" -- even when the catalog could tell
    the unit, not the name, was at fault. Must match what get_simvar
    reports for the identical situation (UNIT_MISMATCH naming the unit)."""
    mock_simconnect["accessor"].read_many.side_effect = None
    mock_simconnect["accessor"].read_many.return_value = {
        "PLANE_ALTITUDE": {
            "error": "SimConnect does not recognise SimVar 'PLANE_ALTITUDE'",
            "error_type": "SimVarNotFoundError",
            "unit": "bananas",
        }
    }
    result = await get_simvar_bulk([{"name": "PLANE_ALTITUDE", "unit": "bananas"}])
    entry = result["variables"]["PLANE_ALTITUDE"]
    assert entry["error_code"] == "UNIT_MISMATCH"
    assert "bananas" in entry["error"]
    assert "suggestions" not in entry


@pytest.mark.asyncio
async def test_bulk_read_reports_not_found_with_suggestions_for_a_typo(mock_simconnect):
    """A genuine bad name (no caller unit) must still get SIMVAR_NOT_FOUND
    with name suggestions, not a bare unit-mismatch guess."""
    mock_simconnect["accessor"].read_many.side_effect = None
    mock_simconnect["accessor"].read_many.return_value = {
        "PLANE_ALTITUD": {
            "error": "SimConnect does not recognise SimVar 'PLANE_ALTITUD'",
            "error_type": "SimVarNotFoundError",
            "unit": "Feet",
        }
    }
    result = await get_simvar_bulk([{"name": "PLANE_ALTITUD"}])
    entry = result["variables"]["PLANE_ALTITUD"]
    assert entry["error_code"] == "SIMVAR_NOT_FOUND"
    assert "PLANE_ALTITUDE" in entry["suggestions"]


@pytest.mark.asyncio
async def test_get_simvar_bulk_rejects_more_than_the_max_variables(mock_simconnect):
    """Finding 3: an uncapped caller-supplied list makes read_many hold
    _sim_lock for up to len(variables) * timeout against a hung sim."""
    variables = [{"name": "PLANE_ALTITUDE"}] * (MAX_BULK_VARIABLES + 1)
    result = await get_simvar_bulk(variables)
    assert result["status"] == "error"
    assert result["error"] == "TOO_MANY_VARIABLES"
    assert not mock_simconnect["accessor"].read_many.called


@pytest.mark.asyncio
async def test_get_simvar_bulk_accepts_exactly_the_max(mock_simconnect):
    variables = [{"name": "PLANE_ALTITUDE"}] * MAX_BULK_VARIABLES
    result = await get_simvar_bulk(variables)
    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_search_simvars_reports_total_and_truncated():
    """Minor: [:50] silently truncated with no way to tell 50-of-50 apart
    from 50-of-many."""
    result = await search_simvars("e")  # broad enough to exceed 50 matches
    assert result["count"] == 50
    assert result["total"] > 50
    assert result["truncated"] is True


@pytest.mark.asyncio
async def test_search_simvars_reports_not_truncated_when_under_the_cap():
    result = await search_simvars("xyznonexistent123")
    assert result["total"] == 0
    assert result["truncated"] is False


@pytest.mark.asyncio
async def test_watch_simvar_honours_index_zero(mock_simconnect):
    await watch_simvar("ENG_N1_RPM", index=0, interval_ms=50, duration_s=1)
    assert mock_simconnect["accessor"].read.call_args.kwargs["index"] == 0


@pytest.mark.asyncio
async def test_watch_simvar_reports_the_catalog_unit_when_none_given(mock_simconnect):
    """The old code reported the literal 'default' when no unit was passed.
    Passing a truthy unit cannot detect that, so this asserts the None case."""
    result = await watch_simvar("PLANE_ALTITUDE", interval_ms=100, duration_s=1)
    assert result["unit"].lower() == "feet"
    assert result["unit"] != "default"
    assert len(result["samples"]) >= 1


@pytest.mark.asyncio
async def test_watch_simvar_reports_explicit_unit_when_given(mock_simconnect):
    """When a unit is explicitly passed, it should be reported."""
    result = await watch_simvar("PLANE_ALTITUDE", unit="meters", interval_ms=50, duration_s=1)
    assert result["unit"] == "meters"
    assert len(result["samples"]) >= 1


@pytest.mark.asyncio
async def test_watch_simvar_fails_fast_when_the_first_read_raises(mock_simconnect):
    """A name that will never work must not loop for the full duration.

    Finding 6: this used to assert the umbrella SIMVAR_NOT_READABLE, which
    watch_simvar returned for every first-sample failure regardless of
    cause. A name with no unit and no catalog entry is a genuine
    SIMVAR_NOT_FOUND -- the same diagnosis get_simvar gives the identical
    situation (see test_unknown_var_without_a_unit_reports_not_found)."""
    import time
    mock_simconnect["accessor"].read.side_effect = SimVarNotFoundError("nope")
    start = time.monotonic()
    result = await watch_simvar("NOT_A_REAL_VAR", interval_ms=200, duration_s=30)
    elapsed = time.monotonic() - start
    assert result["error"] == "SIMVAR_NOT_FOUND"
    assert elapsed < 5, f"should fail fast, took {elapsed:.1f}s"


@pytest.mark.asyncio
async def test_watch_simvar_bad_unit_on_a_known_var_reports_unit_mismatch(mock_simconnect):
    """Finding 6: watch_simvar used to report every first-sample failure as
    SIMVAR_NOT_READABLE with "check the name" -- wrong advice here, since
    PLANE_ALTITUDE is a real, known variable and the unit is what's bad.
    Must match what get_simvar reports for the identical situation (see
    test_bad_unit_on_a_known_var_reports_unit_mismatch)."""
    mock_simconnect["accessor"].read.side_effect = SimVarNotFoundError("nope")
    result = await watch_simvar("PLANE_ALTITUDE", unit="bananas", interval_ms=200, duration_s=30)
    assert result["error"] == "UNIT_MISMATCH"
    assert "bananas" in result["message"]
    assert "suggestions" not in result, "must not suggest names when the name was fine"


@pytest.mark.asyncio
async def test_watch_simvar_timeout_is_reported_as_its_own_error(mock_simconnect):
    mock_simconnect["accessor"].read.side_effect = SimVarTimeoutError("no response")
    result = await watch_simvar("PLANE_ALTITUDE", interval_ms=200, duration_s=30)
    assert result["error"] == "SIM_TIMEOUT"
    assert "suggestion" in result


@pytest.mark.asyncio
async def test_watch_simvar_survives_a_transient_error_after_a_good_sample(mock_simconnect):
    """Fail-fast is first-sample-only; a later blip must not abort the watch."""
    good = 100.0
    # Provide enough side effects for the loop: first sample good, then error, then more good
    side_effects = [good, SimVarNotFoundError("blip")] + [good] * 10
    mock_simconnect["accessor"].read.side_effect = side_effects
    result = await watch_simvar("PLANE_ALTITUDE", interval_ms=50, duration_s=0.5)
    assert result["status"] == "ok"
    assert result["error_count"] >= 1
    assert result["sample_count"] >= 1


@pytest.mark.asyncio
async def test_search_simvars():
    """Search returns matching SimVars."""
    result = await search_simvars("altitude")
    assert result["status"] == "ok"
    assert result["count"] > 0
    names = [r["name"] for r in result["results"]]
    assert any("ALTITUDE" in n for n in names)


@pytest.mark.asyncio
async def test_search_simvars_with_category():
    """Search with category filter narrows results."""
    result = await search_simvars("altitude", category="Aircraft Position")
    assert result["status"] == "ok"
    for r in result["results"]:
        assert r["category"] == "Aircraft Position"


@pytest.mark.asyncio
async def test_list_categories():
    """List categories returns categories with counts."""
    result = await list_simvar_categories()
    assert result["status"] == "ok"
    assert "categories" in result
    assert result["total_variables"] > 0


@pytest.mark.asyncio
async def test_get_simvar_not_connected():
    """Reading without connection returns error when sim is unavailable."""
    with patch.dict(sys.modules, {"SimConnect": None}):
        result = await get_simvar("PLANE_LATITUDE")
        assert result["status"] == "error"


def test_title_mock_returns_bytes_like_the_real_sim(mock_simconnect):
    """The sim returns bytes for string SimVars; the mock must match or
    bytes-handling bugs stay invisible to the suite."""
    assert isinstance(mock_simconnect["aq"].get("TITLE"), bytes)


@pytest.mark.asyncio
async def test_get_simvar_reports_the_unit_actually_used(mock_simconnect):
    result = await get_simvar("PLANE_ALTITUDE", unit="meters")
    assert result["status"] == "ok"
    assert result["unit"] == "meters", "the old code echoed 'default' and ignored the unit"


@pytest.mark.asyncio
async def test_get_simvar_passes_unit_through_to_the_accessor(mock_simconnect):
    await get_simvar("PLANE_ALTITUDE", unit="meters")
    mock_simconnect["accessor"].read.assert_called_once()
    assert mock_simconnect["accessor"].read.call_args.kwargs["unit"] == "meters"


@pytest.mark.asyncio
async def test_unknown_simvar_returns_not_found_with_suggestions(mock_simconnect):
    """Previously unreachable: the branch above it raised ImportError."""
    mock_simconnect["accessor"].read.side_effect = SimVarNotFoundError("nope")
    result = await get_simvar("PLANE_ALTITUD")
    assert result["error"] == "SIMVAR_NOT_FOUND"
    assert "PLANE_ALTITUDE" in result["suggestions"]


@pytest.mark.asyncio
async def test_timeout_is_reported_as_its_own_error(mock_simconnect):
    mock_simconnect["accessor"].read.side_effect = SimVarTimeoutError("no response")
    result = await get_simvar("PLANE_ALTITUDE")
    assert result["error"] == "SIM_TIMEOUT"
    assert "suggestion" in result


@pytest.mark.asyncio
async def test_index_zero_is_passed_through(mock_simconnect):
    await get_simvar("GENERAL_ENG_THROTTLE_LEVER_POSITION", index=0)
    assert mock_simconnect["accessor"].read.call_args.kwargs["index"] == 0


@pytest.mark.asyncio
async def test_bad_unit_on_a_known_var_reports_unit_mismatch(mock_simconnect):
    """SimConnect raises NAME_UNRECOGNIZED for a bad unit AND a bad name.
    The catalog disambiguates: PLANE_ALTITUDE exists, so the unit is at fault."""
    mock_simconnect["accessor"].read.side_effect = SimVarNotFoundError("nope")
    result = await get_simvar("PLANE_ALTITUDE", unit="bananas")
    assert result["error"] == "UNIT_MISMATCH"
    assert "bananas" in result["message"]
    assert "Feet" in result["suggestion"] or "feet" in result["suggestion"].lower()
    assert "suggestions" not in result, "must not suggest names when the name was fine"


@pytest.mark.asyncio
async def test_bad_name_still_reports_not_found_with_suggestions(mock_simconnect):
    mock_simconnect["accessor"].read.side_effect = SimVarNotFoundError("nope")
    result = await get_simvar("PLANE_ALTITUD", unit="feet")
    assert result["error"] == "SIMVAR_NOT_FOUND"
    assert "PLANE_ALTITUDE" in result["suggestions"]


@pytest.mark.asyncio
async def test_unknown_var_without_a_unit_reports_not_found(mock_simconnect):
    mock_simconnect["accessor"].read.side_effect = SimVarNotFoundError("nope")
    result = await get_simvar("TOTALLY_MADE_UP_VAR")
    assert result["error"] == "SIMVAR_NOT_FOUND"


@pytest.mark.asyncio
async def test_known_var_failing_without_a_caller_unit_is_not_blamed_on_the_unit(mock_simconnect):
    """unit=None means we used the catalog's own unit, so a failure there is
    a real name/catalog problem, not the caller's mistake."""
    mock_simconnect["accessor"].read.side_effect = SimVarNotFoundError("nope")
    result = await get_simvar("PLANE_ALTITUDE")
    assert result["error"] == "SIMVAR_NOT_FOUND"
