"""Tests for shared, locked aircraft-title/model detection.

Covers three defects:

1. Thread-safety: `search_lvars`, `browse_lvar_catalog` (formerly
   `list_lvar_panels`), `_detect_pmdg_variant` and the
   `simconnect://state/aircraft` resource used to call
   `manager.aq.get("TITLE")` directly on the event loop with no lock.
2. A live-verified detection failure: a PMDG 777F's TITLE is the terse
   "777F" and its ATC_MODEL is the generic "ATCCOM.AC_MODEL B77L.0.text" --
   neither matches any bundled catalog's title_pattern, so catalog-scoped
   tools used to silently search everything or silently guess "pmdg_777"
   with no way for a caller to tell a guess from a detection.
3. A second live-verified detection failure, on a real PMDG 737-600: TITLE
   is "737-600 PAX TC" and ATC_MODEL is "ATCCOM.AC_MODEL B736.0.text" --
   again no PMDG branding anywhere, but this time on a 737, so the old
   "guess pmdg_777" fallback silently selected the *wrong* SDK entirely
   rather than merely an undetected one. `_resolve_pmdg_catalog` now probes
   each SDK's client data area (PMDG_777X_Data / PMDG_NG3_Data) when
   title/model matching fails -- only the loaded variant's area ever
   responds, which is authoritative where a title substring is not. See
   the "_resolve_pmdg_catalog probing" section below.
4. catalog-detection-brief.md's live-verified defect: `search_lvars` and
   `browse_lvar_catalog` never consulted that same probe at all -- only
   `data.catalog.detect_catalog`'s plain title_pattern match, which fails
   on both real, unbranded PMDG titles above ("777F" and "737-600 PAX TC"),
   and on a real PMDG 737-800 ("737-800 PAX SSW TC"). `_detect_lvar_catalog`
   (tools/lvars.py) now probes first, exactly like `_resolve_pmdg_catalog`,
   and falls back to title_pattern matching only after that -- see the
   "tools.lvars catalog auto-detection" section below.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

import pytest

from simconnect_mcp.connection import SimConnectManager


@pytest.fixture(autouse=True)
def _skip_pmdg_probe_wait():
    """Every test below that reaches a failing PMDG probe (the fixture
    aircraft matches no PMDG signal unless a test overrides TITLE/ATC_MODEL
    or arms a responding data manager) would otherwise pay its up-to-0.3s
    real wait. Patched globally for this file so that cost doesn't apply to
    tests that aren't about the wait itself; a test that IS about the wait
    (see the "_probe_pmdg_variant" section) re-patches the same target
    locally, which nests harmlessly on top of this.
    """
    with patch("simconnect_mcp.pmdg_detect.asyncio.sleep", new=AsyncMock()):
        yield


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

    assert result.filters["catalog"] == "all"
    assert result.message is not None
    assert "catalog" in result.message
    assert "browse_lvar_catalog" in result.message


async def test_search_lvars_explicit_catalog_overrides_autodetect(mock_simconnect):
    """An explicit catalog= argument scopes the search without needing a
    sim round trip at all."""
    from simconnect_mcp.tools.lvars import search_lvars

    result = await search_lvars("altitude", catalog="pmdg_777")

    assert result.filters["catalog"] == "pmdg_777"
    assert result.message is None
    assert not mock_simconnect["accessor"].read.called


async def test_search_lvars_rejects_unknown_explicit_catalog(mock_simconnect):
    """An explicit but invalid catalog must error, not silently fall back to
    searching everything under the caller's requested (wrong) label."""
    from simconnect_mcp.tools.lvars import search_lvars

    result = await search_lvars("altitude", catalog="not_a_real_catalog")

    assert result.status == "error"
    assert result.error == "CATALOG_NOT_FOUND"


# ---------------------------------------------------------------------------
# tools.lvars.browse_lvar_catalog (formerly list_lvar_panels + list_lvar_catalogs)
# ---------------------------------------------------------------------------


async def test_browse_lvar_catalog_does_not_touch_aq_directly(mock_simconnect):
    """list_lvar_panels, browse_lvar_catalog's predecessor, used to call
    aq.get() on the event loop with no lock."""
    from simconnect_mcp.tools.lvars import browse_lvar_catalog

    await browse_lvar_catalog()
    assert not mock_simconnect["aq"].get.called


async def test_browse_lvar_catalog_reports_actionable_message_when_undetected(mock_simconnect):
    from simconnect_mcp.tools.formatting import ResponseFormat
    from simconnect_mcp.tools.lvars import browse_lvar_catalog

    result = await browse_lvar_catalog(response_format=ResponseFormat.JSON)

    assert result.catalog is None
    assert result.catalogs is not None
    assert result.message is not None
    assert "catalog" in result.message


async def test_browse_lvar_catalog_explicit_catalog_overrides_autodetect(mock_simconnect):
    from simconnect_mcp.tools.lvars import browse_lvar_catalog

    result = await browse_lvar_catalog(catalog="pmdg_737")

    assert result.catalog == "pmdg_737"
    assert not mock_simconnect["accessor"].read.called


async def test_browse_lvar_catalog_rejects_unknown_explicit_catalog(mock_simconnect):
    from simconnect_mcp.tools.lvars import browse_lvar_catalog

    result = await browse_lvar_catalog(catalog="not_a_real_catalog")

    assert result.status == "error"
    assert result.error == "CATALOG_NOT_FOUND"


async def test_panel_lookup_says_so_when_no_catalog_was_detected(mock_simconnect):
    """Live-verified gap: with panel= and no detection, get_panel_variables
    picks whichever catalog iterates first ('Signs' only exists in
    pmdg_777, so that's what comes back on an undetected aircraft) -- the
    caller must be told that was a guess across all catalogs, not a
    detection, exactly as the no-panel branch already discloses."""
    from simconnect_mcp.tools.lvars import browse_lvar_catalog

    result = await browse_lvar_catalog(panel="Signs")

    assert result.status == "ok"
    assert result.catalog == "pmdg_777"
    assert result.message is not None, "a guessed catalog must be disclosed"
    assert "catalog" in result.message


async def test_panel_lookup_is_quiet_when_the_catalog_was_explicit(mock_simconnect):
    """No guess was made, so no warning belongs in the response."""
    from simconnect_mcp.tools.lvars import browse_lvar_catalog

    result = await browse_lvar_catalog(panel="Signs", catalog="pmdg_777")

    assert result.status == "ok"
    assert result.message is None


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
    guess -- the one PMDG response shape that had dropped the field.

    Also pins cdu_name on this same branch (B10): it was dropped here too,
    even though cdu_names[cdu] is in scope and the powered branch sets it."""
    from simconnect_mcp.pmdg import PMDG_777X_CDU_Screen, PmdgDataManager
    from simconnect_mcp.tools.pmdg import get_pmdg_cdu

    manager = mock_simconnect["manager"]
    pmdg = PmdgDataManager(sm=manager.sm)
    pmdg.cdu_subscribed[0] = True
    pmdg._cdu_screens[0] = PMDG_777X_CDU_Screen()  # Powered defaults to False
    pmdg._cdu_timestamps[0] = time.time()
    manager.pmdg = pmdg

    result = await get_pmdg_cdu(cdu=0)

    assert result.status == "ok"
    assert result.powered is False
    assert result.variant_source is not None
    assert result.cdu_name == "Left (Captain)"


# ---------------------------------------------------------------------------
# tools.pmdg._probe_pmdg_variant / _resolve_pmdg_catalog probing
#
# Live-verified defect: a PMDG 737-600 reports TITLE='737-600 PAX TC' and
# ATC_MODEL='ATCCOM.AC_MODEL B736.0.text' -- neither carries PMDG branding,
# so _detect_pmdg_variant's title/model check fails, and the old code fell
# back to guessing "pmdg_777": the wrong SDK for a 737. Both PMDG SDKs
# expose a dedicated SimConnect client data area that only the actually
# loaded variant answers (PMDG_777X_Data / PMDG_NG3_Data); probing which
# one responds is authoritative where title matching is not.
# ---------------------------------------------------------------------------


async def test_probe_returns_none_when_neither_data_area_responds(mock_simconnect):
    """No PMDG aircraft loaded (or EnableDataBroadcast unset in both):
    neither manager's data_age ever becomes finite. Fails against an
    implementation that guesses a variant anyway instead of honestly
    reporting "could not determine". asyncio.sleep is patched so this
    exercises the real wait loop without taking the full ~0.3s budget."""
    from simconnect_mcp.tools.pmdg import _probe_pmdg_variant

    with patch("simconnect_mcp.tools.pmdg.asyncio.sleep", new=AsyncMock()):
        result = await _probe_pmdg_variant()

    assert result is None
    manager = mock_simconnect["manager"]
    title, model = await manager.detect_aircraft_identity()
    assert manager.get_cached_pmdg_variant(title, model) is None


async def test_probe_finds_the_737_ng3_data_area(mock_simconnect):
    """Live-verified signal from a real PMDG 737-600: PMDG_NG3_Data responds
    (data_age finite), PMDG_777X_Data does not. Fails against an
    implementation that ignores data_age, always prefers the 777, or checks
    only one of the two managers."""
    from simconnect_mcp.pmdg import PmdgDataManager
    from simconnect_mcp.pmdg_ng3 import PmdgNG3DataManager
    from simconnect_mcp.tools.pmdg import _probe_pmdg_variant

    manager = mock_simconnect["manager"]
    manager.pmdg = PmdgDataManager(sm=manager.sm)  # never responds

    responded = PmdgNG3DataManager(sm=manager.sm)
    responded.data_subscribed = True
    responded._data_timestamp = time.time()
    manager.pmdg_ng3 = responded

    result = await _probe_pmdg_variant()

    assert result == "pmdg_737"


async def test_probe_finds_the_777_data_area(mock_simconnect):
    """Symmetric case: PMDG_777X_Data responds, PMDG_NG3_Data does not."""
    from simconnect_mcp.pmdg import PmdgDataManager
    from simconnect_mcp.pmdg_ng3 import PmdgNG3DataManager
    from simconnect_mcp.tools.pmdg import _probe_pmdg_variant

    manager = mock_simconnect["manager"]
    responded = PmdgDataManager(sm=manager.sm)
    responded.data_subscribed = True
    responded._data_timestamp = time.time()
    manager.pmdg = responded
    manager.pmdg_ng3 = PmdgNG3DataManager(sm=manager.sm)  # never responds

    result = await _probe_pmdg_variant()

    assert result == "pmdg_777"


async def test_successful_probe_is_cached_for_the_connection(mock_simconnect):
    """The probe is a real SimConnect round trip against two client data
    areas; once a connection establishes which variant responds, it must
    not repeat that round trip on every subsequent call. Fails against an
    implementation with no cache: the second call's forced run_sync failure
    would propagate instead of being skipped."""
    from simconnect_mcp.pmdg_ng3 import PmdgNG3DataManager
    from simconnect_mcp.tools.pmdg import _probe_pmdg_variant

    manager = mock_simconnect["manager"]
    responded = PmdgNG3DataManager(sm=manager.sm)
    responded.data_subscribed = True
    responded._data_timestamp = time.time()
    manager.pmdg_ng3 = responded

    first = await _probe_pmdg_variant()
    assert first == "pmdg_737"
    title, model = await manager.detect_aircraft_identity()
    assert manager.get_cached_pmdg_variant(title, model) == "pmdg_737"

    with patch.object(
        manager, "run_sync", new=AsyncMock(side_effect=AssertionError("must not re-probe"))
    ):
        second = await _probe_pmdg_variant()

    assert second == "pmdg_737"


async def test_cached_probe_result_is_not_reused_after_the_aircraft_changes(mock_simconnect):
    """Live-verified concern: a user can swap aircraft mid-session without a
    reconnect. A cache keyed only on "a probe succeeded this connection"
    (rather than on the identity it succeeded for) would keep answering
    with the PREVIOUS aircraft's variant -- exactly the failure this task
    exists to prevent, just relabelled "probed" instead of "fallback".
    Fails against a cache with no identity check: the second call would
    return the first aircraft's cached "pmdg_737" instead of re-probing and
    finding the second aircraft's "pmdg_777"."""
    from simconnect_mcp.pmdg import PmdgDataManager
    from simconnect_mcp.pmdg_ng3 import PmdgNG3DataManager
    from simconnect_mcp.tools.pmdg import _probe_pmdg_variant

    manager = mock_simconnect["manager"]

    # First aircraft: a 737 that responds to the probe.
    mock_simconnect["simvar_values"]["TITLE"] = b"737-600 PAX TC"
    mock_simconnect["simvar_values"]["ATC_MODEL"] = b"ATCCOM.AC_MODEL B736.0.text"
    manager._title_cache = None
    ng3 = PmdgNG3DataManager(sm=manager.sm)
    ng3.data_subscribed = True
    ng3._data_timestamp = time.time()
    manager.pmdg_ng3 = ng3
    manager.pmdg = PmdgDataManager(sm=manager.sm)  # never responds

    first = await _probe_pmdg_variant()
    assert first == "pmdg_737"

    # Aircraft swapped mid-session, no reconnect: new identity. The old
    # aircraft's data area has gone stale (it's no longer being updated --
    # that add-on unloaded), and the new one now responds instead.
    mock_simconnect["simvar_values"]["TITLE"] = b"777-300ER PAX"
    mock_simconnect["simvar_values"]["ATC_MODEL"] = b"ATCCOM.AC_MODEL B77L.0.text"
    manager._title_cache = None
    ng3._data_timestamp = 0.0
    b777 = PmdgDataManager(sm=manager.sm)
    b777.data_subscribed = True
    b777._data_timestamp = time.time()
    manager.pmdg = b777

    second = await _probe_pmdg_variant()

    assert second == "pmdg_777"


def test_pmdg_variant_cache_is_cleared_on_disconnect(mock_simconnect):
    """Cache invalidation: the loaded aircraft can change on a reconnect (or,
    per the test above, even without one), so a stale probe result must not
    survive disconnect()."""
    manager = mock_simconnect["manager"]
    manager.set_cached_pmdg_variant("Boeing 747-8i", "ATCCOM.AC_MODEL B747.0.text", "pmdg_737")
    assert manager.get_cached_pmdg_variant(
        "Boeing 747-8i", "ATCCOM.AC_MODEL B747.0.text"
    ) == "pmdg_737"

    manager.disconnect()

    assert manager.get_cached_pmdg_variant("Boeing 747-8i", "ATCCOM.AC_MODEL B747.0.text") is None


async def test_resolve_pmdg_catalog_reports_probed_for_the_737_600_defect(mock_simconnect):
    """The exact live-verified scenario: TITLE/ATC_MODEL carry no PMDG
    branding, but the NG3 data area responds. Fails against the pre-fix
    code, which returns ("pmdg_777", "fallback") here -- the wrong SDK,
    reported as an unlabelled guess."""
    from simconnect_mcp.pmdg_ng3 import PmdgNG3DataManager
    from simconnect_mcp.tools.pmdg import _resolve_pmdg_catalog

    mock_simconnect["simvar_values"]["TITLE"] = b"737-600 PAX TC"
    mock_simconnect["simvar_values"]["ATC_MODEL"] = b"ATCCOM.AC_MODEL B736.0.text"
    manager = mock_simconnect["manager"]
    manager._title_cache = None

    responded = PmdgNG3DataManager(sm=manager.sm)
    responded.data_subscribed = True
    responded._data_timestamp = time.time()
    manager.pmdg_ng3 = responded

    catalog_key, source = await _resolve_pmdg_catalog(None, None)

    assert catalog_key == "pmdg_737"
    assert source == "probed"


async def test_resolve_pmdg_catalog_skips_the_probe_once_title_matches(mock_simconnect):
    """Cheap-probe requirement: title/model detection succeeding must skip
    the client-data round trip entirely. Fails loudly (AssertionError, not a
    silent slowdown) if the probe runs anyway."""
    from simconnect_mcp.tools import pmdg as pmdg_tools

    mock_simconnect["simvar_values"]["TITLE"] = b"PMDG 777-300ER"
    manager = mock_simconnect["manager"]
    manager._title_cache = None

    async def _must_not_run():
        raise AssertionError("must not probe when title/model already matched")

    with patch.object(pmdg_tools, "_probe_pmdg_variant", new=_must_not_run):
        catalog_key, source = await pmdg_tools._resolve_pmdg_catalog(None, None)

    assert catalog_key == "pmdg_777"
    assert source == "detected"


async def test_resolve_pmdg_catalog_still_falls_back_when_probe_also_fails(mock_simconnect):
    """When title/model detection AND the probe both come up empty (no name
    given either), resolution must still fall back to pmdg_777 labelled
    "fallback" -- probing must not turn a genuine "cannot determine" into a
    hang or an unlabelled guess. asyncio.sleep is patched to skip the ~0.3s
    probe wait."""
    from simconnect_mcp.tools.pmdg import _resolve_pmdg_catalog

    mock_simconnect["simvar_values"]["TITLE"] = b"777F"
    mock_simconnect["simvar_values"]["ATC_MODEL"] = b"ATCCOM.AC_MODEL B77L.0.text"
    manager = mock_simconnect["manager"]
    manager._title_cache = None

    with patch("simconnect_mcp.tools.pmdg.asyncio.sleep", new=AsyncMock()):
        catalog_key, source = await _resolve_pmdg_catalog(None, None)

    assert catalog_key == "pmdg_777"
    assert source == "fallback"


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


async def test_state_aircraft_reports_the_standard_vocabulary_when_not_connected(mock_simconnect):
    """B10: this used to return {"status": "not_connected"}, a status value
    outside the status/error/message/suggestion vocabulary every tool
    uses."""
    import json

    from mcp.server.fastmcp import FastMCP

    from simconnect_mcp.resources.state import register_state_resources

    mock_simconnect["manager"].disconnect()

    mcp = FastMCP("test")
    register_state_resources(mcp)

    contents = await mcp.read_resource("simconnect://state/aircraft")
    result = json.loads(contents[0].content)

    assert result["status"] == "error"
    assert result["error"] == "NOT_CONNECTED"
    assert result["message"]
    assert result["suggestion"]


async def test_state_aircraft_lets_read_many_size_its_own_batch_budget(mock_simconnect):
    """read_many's budget argument is now PER ITEM and it scales the batch
    deadline by len(requests) itself, so this resource must stop
    pre-multiplying.

    It used to compute `len(names) * DEFAULT_TIMEOUT` by hand to compensate
    for a total-budget argument that defaulted to one read's worth. Leaving
    that correction in place after the change would multiply twice, handing
    six reads a budget sized for thirty-six -- so this asserts the resource
    passes no budget override at all and inherits read_many's own sizing.
    """
    from mcp.server.fastmcp import FastMCP

    from simconnect_mcp.resources.state import register_state_resources

    mcp = FastMCP("test")
    register_state_resources(mcp)

    await mcp.read_resource("simconnect://state/aircraft")

    call = mock_simconnect["accessor"].read_many.call_args
    assert len(call.args) == 1, (
        f"expected only the request list, got positional args {call.args!r} "
        "-- a hand-computed budget is a leftover from the total-budget signature"
    )
    assert "timeout" not in call.kwargs and "per_item_timeout" not in call.kwargs, (
        f"expected no budget override, got {call.kwargs!r}"
    )


async def test_state_aircraft_reads_all_six_names_within_the_scaled_budget(mock_simconnect):
    """The behavioural half: with each read costing just under the per-item
    default, all six names must come back with values.

    Under the old total-budget default (DEFAULT_TIMEOUT for the whole
    batch), the later names would fall off the end of the budget -- exactly
    the failure the resource's hand-multiplication existed to avoid, which
    read_many's own sizing must now deliver without help.
    """
    import json

    from mcp.server.fastmcp import FastMCP

    from simconnect_mcp.resources.state import register_state_resources
    from simconnect_mcp.simvar_access import DEFAULT_TIMEOUT

    mock_simconnect["accessor"].simulated_read_seconds = DEFAULT_TIMEOUT * 0.9

    mcp = FastMCP("test")
    register_state_resources(mcp)

    contents = await mcp.read_resource("simconnect://state/aircraft")
    result = json.loads(contents[0].content)

    assert result["status"] == "ok"
    assert len(result["aircraft"]) == 6
    for key, entry in result["aircraft"].items():
        assert "error" not in entry, f"{key} fell off the batch budget: {entry}"


# ---------------------------------------------------------------------------
# tools.lvars._detect_lvar_catalog / search_lvars / browse_lvar_catalog
#
# catalog-detection-brief.md's live-verified defect: search_lvars and
# browse_lvar_catalog only ever called data.catalog.detect_catalog, whose
# plain title_pattern match ("PMDG 737" / "PMDG 777") cannot see a real,
# unbranded PMDG -- confirmed live on a PMDG 737-800 at KBOS (TITLE=
# '737-800 PAX SSW TC') and previously on a PMDG 737-600 (TITLE=
# '737-600 PAX TC'). tools.pmdg._resolve_pmdg_catalog's client-data-area
# probe already solves exactly this for the PMDG-specific tools; these
# tests pin that tools/lvars.py's catalog auto-detection now consults it
# too, via pmdg_detect.detect_or_probe_pmdg_catalog, before falling back to
# title_pattern matching for third-party catalogs.
# ---------------------------------------------------------------------------

_UNBRANDED_PMDG_737_TITLES = ["737-800 PAX SSW TC", "737-600 PAX TC"]


@pytest.mark.parametrize("title", _UNBRANDED_PMDG_737_TITLES)
def test_detect_catalog_alone_still_misses_these_real_pmdg_737_titles(title):
    """Pins the premise the tests below rely on: neither observed TITLE
    carries a "PMDG" substring, so the plain title_pattern match that
    search_lvars/browse_lvar_catalog used to depend on exclusively still
    fails on its own. If this ever starts passing, the probe-based tests
    below are no longer proving what they claim to."""
    from simconnect_mcp.data.catalog import detect_catalog

    assert detect_catalog(title) is None


@pytest.mark.parametrize("title", _UNBRANDED_PMDG_737_TITLES)
async def test_search_lvars_detects_an_unbranded_pmdg_737_via_the_probe(mock_simconnect, title):
    """The live defect itself, reproduced with both real observed TITLE
    strings. Fails against title-matching alone (confirmed to return None
    for both, in the test above) -- only the client-data probe can tell
    these apart from any other airframe with a similarly generic title.
    Must resolve to pmdg_737 and say so, not fall through to "searched all
    catalogs"."""
    from simconnect_mcp.pmdg_ng3 import PmdgNG3DataManager
    from simconnect_mcp.tools.lvars import search_lvars

    mock_simconnect["simvar_values"]["TITLE"] = title.encode("ascii")
    mock_simconnect["simvar_values"]["ATC_MODEL"] = b"ATCCOM.AC_MODEL B736.0.text"
    manager = mock_simconnect["manager"]
    manager._title_cache = None

    responded = PmdgNG3DataManager(sm=manager.sm)
    responded.data_subscribed = True
    responded._data_timestamp = time.time()
    manager.pmdg_ng3 = responded

    result = await search_lvars("autopilot")

    assert result.filters["catalog"] == "pmdg_737"
    assert result.message is not None
    assert "probing" in result.message


async def test_browse_lvar_catalog_detects_an_unbranded_pmdg_737_via_the_probe(mock_simconnect):
    """Same fix, other tool: browse_lvar_catalog shares _detect_lvar_catalog
    with search_lvars, so the KBOS PMDG 737-800's exact TITLE must resolve
    here too, not just in search_lvars."""
    from simconnect_mcp.pmdg_ng3 import PmdgNG3DataManager
    from simconnect_mcp.tools.lvars import browse_lvar_catalog

    mock_simconnect["simvar_values"]["TITLE"] = b"737-800 PAX SSW TC"
    mock_simconnect["simvar_values"]["ATC_MODEL"] = b"ATCCOM.AC_MODEL B736.0.text"
    manager = mock_simconnect["manager"]
    manager._title_cache = None

    responded = PmdgNG3DataManager(sm=manager.sm)
    responded.data_subscribed = True
    responded._data_timestamp = time.time()
    manager.pmdg_ng3 = responded

    result = await browse_lvar_catalog()

    assert result.catalog == "pmdg_737"
    assert result.message is not None
    assert "probing" in result.message


async def test_detect_lvar_catalog_explicit_short_circuits_with_no_source(mock_simconnect):
    """An explicit catalog needs no disclosure -- nothing was detected, the
    caller said so directly -- and must not touch the sim at all."""
    from simconnect_mcp.tools.lvars import _detect_lvar_catalog

    catalog_key, source, ran_auto_detect = await _detect_lvar_catalog("pmdg_777")

    assert (catalog_key, source, ran_auto_detect) == ("pmdg_777", None, False)
    assert not mock_simconnect["accessor"].read.called


async def test_detect_lvar_catalog_falls_back_to_title_pattern_when_pmdg_finds_nothing(
    mock_simconnect,
):
    """The other bucket: a third-party catalog (e.g. a user-regenerated
    Fenix catalog dropped into data/, per CLAUDE.md) carries no PMDG signal
    at all, so neither the fast TITLE/ATC_MODEL check nor the client-data
    probe can find it -- only its own title_pattern can, via
    data.catalog.detect_catalog. No such catalog ships by default (the
    bundled Fenix one was removed in favour of the HubHop client), so this
    patches detect_catalog directly rather than depending on one existing."""
    from simconnect_mcp.tools.lvars import _detect_lvar_catalog

    with patch("simconnect_mcp.data.catalog.detect_catalog", return_value="fenix_a320"):
        catalog_key, source, ran_auto_detect = await _detect_lvar_catalog(None)

    assert (catalog_key, source, ran_auto_detect) == ("fenix_a320", "title_match", True)


def test_detected_catalog_message_distinguishes_probed_from_title_match():
    """Report provenance honestly (catalog-detection-brief.md): a live probe
    response is a materially stronger signal than a plain text match, so
    the two must not read identically."""
    from simconnect_mcp.tools.lvars import _detected_catalog_message

    probed = _detected_catalog_message("pmdg_737", "probed")
    title_matched = _detected_catalog_message("pmdg_777", "detected")

    assert "probing" in probed
    assert "pmdg_737" in probed
    assert "TITLE" in title_matched
    assert "pmdg_777" in title_matched
    assert probed != title_matched


# ---------------------------------------------------------------------------
# F2 (catalog-detection-brief.md): the fallback table must carry a per-row
# catalog column whenever the search was not scoped to one confirmed
# catalog -- with the Fenix catalog removed, every bundled catalog is PMDG,
# so a table of PMDG variables on an unrecognised aircraft used to render
# identically to a confirmed, aircraft-specific result. Only an
# easy-to-miss italic footer said otherwise.
# ---------------------------------------------------------------------------


async def test_search_lvars_markdown_carries_catalog_column_when_undetected(mock_simconnect):
    """'altitude' against the undetected default fixture aircraft spans
    both bundled PMDG catalogs -- exactly the shape the brief measured live
    on a Citation ("a clean table of AFS_* rows with nothing in the table
    itself indicating they are for a different aircraft")."""
    from simconnect_mcp.tools.lvars import search_lvars

    result = await search_lvars("altitude")

    assert result.filters["catalog"] == "all"
    assert "Catalog" in result.markdown
    assert "pmdg_737" in result.markdown
    assert "pmdg_777" in result.markdown


async def test_search_lvars_markdown_omits_catalog_column_when_explicit(mock_simconnect):
    """No ambiguity to disclose: every row is already known to come from
    exactly the catalog the caller asked for."""
    from simconnect_mcp.tools.lvars import search_lvars

    result = await search_lvars("altitude", catalog="pmdg_777")

    assert "| Catalog |" not in result.markdown


async def test_search_lvars_markdown_omits_catalog_column_when_probed(mock_simconnect):
    """A probed (non-explicit but confirmed) detection is just as
    unambiguous as an explicit one -- the column exists for when the
    catalog is a guess, not whenever detection ran at all."""
    from simconnect_mcp.pmdg_ng3 import PmdgNG3DataManager
    from simconnect_mcp.tools.lvars import search_lvars

    mock_simconnect["simvar_values"]["TITLE"] = b"737-800 PAX SSW TC"
    manager = mock_simconnect["manager"]
    manager._title_cache = None
    responded = PmdgNG3DataManager(sm=manager.sm)
    responded.data_subscribed = True
    responded._data_timestamp = time.time()
    manager.pmdg_ng3 = responded

    result = await search_lvars("altitude")

    assert result.filters["catalog"] == "pmdg_737"
    assert "| Catalog |" not in result.markdown


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
