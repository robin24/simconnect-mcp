"""Tests for shared, locked aircraft-title/model detection.

Covers two defects:

1. Thread-safety: `search_lvars`, `list_lvar_panels`, `_detect_pmdg_variant`
   and the `simconnect://state/aircraft` resource used to call
   `manager.aq.get("TITLE")` directly on the event loop with no lock.
2. A live-verified detection failure: a PMDG 777F's TITLE is the terse
   "777F" and its ATC_MODEL is the generic "ATCCOM.AC_MODEL B77L.0.text" --
   neither matches any bundled catalog's title_pattern, so catalog-scoped
   tools used to silently search everything or silently guess "pmdg_777"
   with no way for a caller to tell a guess from a detection.
"""

from __future__ import annotations

import time

import pytest

from simconnect_mcp.connection import SimConnectManager

# ---------------------------------------------------------------------------
# SimConnectManager.detect_aircraft_title / detect_aircraft_identity
# ---------------------------------------------------------------------------


async def test_detect_title_returns_a_string(mock_simconnect):
    title = await SimConnectManager().detect_aircraft_title()
    assert title == "Boeing 747-8i"


async def test_detect_title_decodes_bytes_from_the_sim(mock_simconnect):
    """The sim returns bytes for string SimVars."""
    mock_simconnect["accessor"].read.side_effect = lambda *a, **k: b"Fenix A320"
    manager = SimConnectManager()
    manager._title_cache = None
    assert await manager.detect_aircraft_title() == "Fenix A320"


async def test_detect_title_is_cached_within_the_ttl(mock_simconnect):
    manager = SimConnectManager()
    manager._title_cache = None
    await manager.detect_aircraft_title()
    calls = mock_simconnect["accessor"].read.call_count
    await manager.detect_aircraft_title()
    assert mock_simconnect["accessor"].read.call_count == calls


async def test_detect_title_returns_none_when_disconnected():
    manager = SimConnectManager()
    assert await manager.detect_aircraft_title() is None


async def test_detect_identity_returns_title_and_model(mock_simconnect):
    """New in this task: detect_aircraft_title() alone can't see ATC_MODEL,
    which is what a PMDG 777F carries no useful data in either -- but other
    add-ons do carry vendor branding in ATC_MODEL. detect_aircraft_identity
    exposes both from a single cached round trip."""
    manager = SimConnectManager()
    title, model = await manager.detect_aircraft_identity()
    assert title == "Boeing 747-8i"
    assert model == "ATCCOM.AC_MODEL B747.0.text"


async def test_detect_identity_caches_both_under_one_ttl(mock_simconnect):
    manager = SimConnectManager()
    manager._title_cache = None
    await manager.detect_aircraft_identity()
    calls = mock_simconnect["accessor"].read.call_count
    title, model = await manager.detect_aircraft_identity()
    assert mock_simconnect["accessor"].read.call_count == calls
    assert (title, model) == ("Boeing 747-8i", "ATCCOM.AC_MODEL B747.0.text")


async def test_detect_identity_returns_none_none_when_disconnected():
    manager = SimConnectManager()
    assert await manager.detect_aircraft_identity() == (None, None)


async def test_detect_identity_survives_one_field_failing(mock_simconnect):
    """ATC_MODEL failing to read must not also blank out a TITLE that
    succeeded -- a single combined try/except around both reads would
    regress detect_aircraft_title() for every aircraft where ATC_MODEL
    happens to be unavailable."""

    def _read(name, unit=None, index=None, timeout=2.0):
        if name == "ATC_MODEL":
            raise RuntimeError("simulated SimVar error")
        return "Boeing 747-8i"

    mock_simconnect["accessor"].read.side_effect = _read
    manager = SimConnectManager()
    manager._title_cache = None

    title, model = await manager.detect_aircraft_identity()
    assert title == "Boeing 747-8i"
    assert model is None


# ---------------------------------------------------------------------------
# data.catalog.detect_catalog — now also checks ATC_MODEL
# ---------------------------------------------------------------------------


def test_detect_catalog_no_false_positive_on_pmdg_777f_telemetry():
    """Live-verified defect: on a real PMDG 777F, TITLE='777F' and
    ATC_MODEL='ATCCOM.AC_MODEL B77L.0.text'. Neither carries PMDG branding,
    so detection must honestly return None rather than guess."""
    from simconnect_mcp.data.catalog import detect_catalog

    assert detect_catalog("777F", "ATCCOM.AC_MODEL B77L.0.text") is None


def test_detect_catalog_matches_via_atc_model_when_title_is_generic():
    """Some add-ons put their vendor name in ATC_MODEL while TITLE is terse."""
    from simconnect_mcp.data.catalog import detect_catalog

    assert detect_catalog("777F", "PMDG 777-300ER") == "pmdg_777"
    assert detect_catalog(None, "PMDG 737-800 NG3") == "pmdg_737"


def test_detect_catalog_title_still_takes_precedence():
    """Backward compatible: a single positional title argument still works."""
    from simconnect_mcp.data.catalog import detect_catalog

    assert detect_catalog("PMDG 777-300ER") == "pmdg_777"


# ---------------------------------------------------------------------------
# tools.lvars.search_lvars
# ---------------------------------------------------------------------------


async def test_search_lvars_does_not_touch_aq_directly(mock_simconnect):
    """search_lvars used to call aq.get() on the event loop with no lock."""
    from simconnect_mcp.tools.lvars import search_lvars

    await search_lvars("autopilot")
    assert not mock_simconnect["aq"].get.called


async def test_search_lvars_reports_actionable_message_when_undetected(mock_simconnect):
    """The default fixture aircraft (Boeing 747-8i) matches no bundled
    catalog. The old code silently searched everything with no indication
    of why, or how to scope it."""
    from simconnect_mcp.tools.lvars import search_lvars

    result = await search_lvars("autopilot")

    assert result["catalog"] == "all"
    assert "message" in result
    assert "catalog" in result["message"]
    assert "list_lvar_catalogs" in result["message"]


async def test_search_lvars_explicit_catalog_overrides_autodetect(mock_simconnect):
    """An explicit catalog= argument scopes the search without needing a
    sim round trip at all."""
    from simconnect_mcp.tools.lvars import search_lvars

    result = await search_lvars("altitude", catalog="pmdg_777")

    assert result["catalog"] == "pmdg_777"
    assert "message" not in result
    assert not mock_simconnect["accessor"].read.called


async def test_search_lvars_rejects_unknown_explicit_catalog(mock_simconnect):
    """An explicit but invalid catalog must error, not silently fall back to
    searching everything under the caller's requested (wrong) label."""
    from simconnect_mcp.tools.lvars import search_lvars

    result = await search_lvars("altitude", catalog="not_a_real_catalog")

    assert result["status"] == "error"
    assert result["error"] == "CATALOG_NOT_FOUND"


# ---------------------------------------------------------------------------
# tools.lvars.list_lvar_panels
# ---------------------------------------------------------------------------


async def test_list_lvar_panels_does_not_touch_aq_directly(mock_simconnect):
    """list_lvar_panels used to call aq.get() on the event loop with no lock."""
    from simconnect_mcp.tools.lvars import list_lvar_panels

    await list_lvar_panels()
    assert not mock_simconnect["aq"].get.called


async def test_list_lvar_panels_reports_actionable_message_when_undetected(mock_simconnect):
    from simconnect_mcp.tools.lvars import list_lvar_panels

    result = await list_lvar_panels()

    assert result["catalog"] == "all"
    assert "message" in result
    assert "catalog" in result["message"]


async def test_list_lvar_panels_explicit_catalog_overrides_autodetect(mock_simconnect):
    from simconnect_mcp.tools.lvars import list_lvar_panels

    result = await list_lvar_panels(catalog="pmdg_737")

    assert result["catalog"] == "pmdg_737"
    assert not mock_simconnect["accessor"].read.called


async def test_list_lvar_panels_rejects_unknown_explicit_catalog(mock_simconnect):
    from simconnect_mcp.tools.lvars import list_lvar_panels

    result = await list_lvar_panels(catalog="not_a_real_catalog")

    assert result["status"] == "error"
    assert result["error"] == "CATALOG_NOT_FOUND"


async def test_panel_lookup_says_so_when_no_catalog_was_detected(mock_simconnect):
    """Live-verified gap: with category= and no detection, get_panel_variables
    picks whichever catalog iterates first ('Signs' only exists in
    pmdg_777, so that's what comes back on an undetected aircraft) -- the
    caller must be told that was a guess across all catalogs, not a
    detection, exactly as the no-category branch already discloses."""
    from simconnect_mcp.tools.lvars import list_lvar_panels

    result = await list_lvar_panels(category="Signs")

    assert result["status"] == "ok"
    assert result["catalog"] == "pmdg_777"
    assert "message" in result, "a guessed catalog must be disclosed"
    assert "catalog" in result["message"]


async def test_panel_lookup_is_quiet_when_the_catalog_was_explicit(mock_simconnect):
    """No guess was made, so no warning belongs in the response."""
    from simconnect_mcp.tools.lvars import list_lvar_panels

    result = await list_lvar_panels(category="Signs", catalog="pmdg_777")

    assert result["status"] == "ok"
    assert "message" not in result


# ---------------------------------------------------------------------------
# tools.pmdg._detect_pmdg_variant / _resolve_pmdg_catalog
# ---------------------------------------------------------------------------


async def test_detect_pmdg_variant_does_not_touch_aq_directly(mock_simconnect):
    """_detect_pmdg_variant used to call aq.get() on the event loop with no
    lock."""
    from simconnect_mcp.tools.pmdg import _detect_pmdg_variant

    await _detect_pmdg_variant()
    assert not mock_simconnect["aq"].get.called


async def test_detect_pmdg_variant_no_false_positive_on_pmdg_777f_telemetry(mock_simconnect):
    """Live-verified: a PMDG 777F's TITLE ('777F') and ATC_MODEL
    ('ATCCOM.AC_MODEL B77L.0.text') carry no PMDG branding at all --
    detection must fail honestly rather than guess."""
    from simconnect_mcp.tools.pmdg import _detect_pmdg_variant

    mock_simconnect["simvar_values"]["TITLE"] = b"777F"
    mock_simconnect["simvar_values"]["ATC_MODEL"] = b"ATCCOM.AC_MODEL B77L.0.text"
    manager = SimConnectManager()
    manager._title_cache = None

    assert await _detect_pmdg_variant() is None


async def test_detect_pmdg_variant_checks_atc_model(mock_simconnect):
    """A PMDG variant named only in ATC_MODEL (TITLE stays terse) must still
    be detected."""
    from simconnect_mcp.tools.pmdg import _detect_pmdg_variant

    mock_simconnect["simvar_values"]["TITLE"] = b"777F"
    mock_simconnect["simvar_values"]["ATC_MODEL"] = b"PMDG 777-300ER"
    manager = SimConnectManager()
    manager._title_cache = None

    assert await _detect_pmdg_variant() == "pmdg_777"


async def test_resolve_pmdg_catalog_marks_fallback_as_fallback_not_detected(mock_simconnect):
    """Live defect: with no PMDG branding in TITLE or ATC_MODEL, resolution
    falls back to pmdg_777 -- callers must be able to tell this apart from a
    real detection via variant_source."""
    from simconnect_mcp.tools.pmdg import _resolve_pmdg_catalog

    mock_simconnect["simvar_values"]["TITLE"] = b"777F"
    mock_simconnect["simvar_values"]["ATC_MODEL"] = b"ATCCOM.AC_MODEL B77L.0.text"
    manager = SimConnectManager()
    manager._title_cache = None

    catalog_key, source = await _resolve_pmdg_catalog(None, None)
    assert catalog_key == "pmdg_777"
    assert source == "fallback"


async def test_resolve_pmdg_catalog_marks_detected_when_title_matches(mock_simconnect):
    from simconnect_mcp.tools.pmdg import _resolve_pmdg_catalog

    mock_simconnect["simvar_values"]["TITLE"] = b"PMDG 777-300ER"
    manager = SimConnectManager()
    manager._title_cache = None

    catalog_key, source = await _resolve_pmdg_catalog(None, None)
    assert catalog_key == "pmdg_777"
    assert source == "detected"


async def test_get_pmdg_cdu_unpowered_response_includes_variant_source(mock_simconnect):
    """Regression: the 'powered: False' branch of get_pmdg_cdu omitted
    variant_source, hiding whether the catalog it used was a detection or a
    guess -- the one PMDG response shape that had dropped the field."""
    from simconnect_mcp.pmdg import PMDG_777X_CDU_Screen, PmdgDataManager
    from simconnect_mcp.tools.pmdg import get_pmdg_cdu

    manager = mock_simconnect["manager"]
    pmdg = PmdgDataManager(sm=manager.sm)
    pmdg.cdu_subscribed[0] = True
    pmdg._cdu_screens[0] = PMDG_777X_CDU_Screen()  # Powered defaults to False
    pmdg._cdu_timestamps[0] = time.time()
    manager.pmdg = pmdg

    result = await get_pmdg_cdu(cdu=0)

    assert result["status"] == "ok"
    assert result["powered"] is False
    assert "variant_source" in result


# ---------------------------------------------------------------------------
# resources.state.state_aircraft
# ---------------------------------------------------------------------------


async def test_state_aircraft_resource_does_not_touch_aq_directly(mock_simconnect):
    """The simconnect://state/aircraft resource used to call aq.get() on the
    event loop with no lock."""
    import json

    from mcp.server.fastmcp import FastMCP

    from simconnect_mcp.resources.state import register_state_resources

    mcp = FastMCP("test")
    register_state_resources(mcp)

    contents = await mcp.read_resource("simconnect://state/aircraft")
    result = json.loads(contents[0].content)

    assert result["status"] == "ok"
    assert result["aircraft"]["TITLE"]["value"] == "Boeing 747-8i"
    assert not mock_simconnect["aq"].get.called


async def test_state_aircraft_gives_read_many_a_total_budget_sized_for_six_reads(mock_simconnect):
    """read_many's `timeout` is a TOTAL budget for the whole batch (Finding
    3), not per item. This resource reads 6 names; passing the default
    (sized for one read) through unchanged would give all 6 the budget one
    used to get alone, so a merely sluggish (not hung) sim could spuriously
    time out the later names. Must pass an explicit, larger total budget."""
    from mcp.server.fastmcp import FastMCP

    from simconnect_mcp.resources.state import register_state_resources
    from simconnect_mcp.simvar_access import DEFAULT_TIMEOUT

    mcp = FastMCP("test")
    register_state_resources(mcp)

    await mcp.read_resource("simconnect://state/aircraft")

    call = mock_simconnect["accessor"].read_many.call_args
    names_requested = call.args[0]
    timeout_used = call.kwargs.get("timeout", call.args[1] if len(call.args) > 1 else None)

    assert timeout_used is not None and timeout_used > DEFAULT_TIMEOUT, (
        f"expected a total timeout scaled for {len(names_requested)} reads, "
        f"got {timeout_used!r} (the single-read default is {DEFAULT_TIMEOUT})"
    )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
