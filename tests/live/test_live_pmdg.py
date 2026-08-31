"""Live end-to-end verification of the PMDG variant probe (Task 7).

Live-verified defect: a PMDG 737-600 reports TITLE='737-600 PAX TC' and
ATC_MODEL='ATCCOM.AC_MODEL B736.0.text' -- neither carries any PMDG
branding, so title/model detection alone cannot tell this apart from any
other airframe, and the pre-fix code silently fell back to guessing
"pmdg_777" -- the wrong SDK for a 737. This file verifies the client-data
probe added in tools.pmdg._probe_pmdg_variant correctly identifies the
loaded 737 NG3 instead, against a real running MSFS + PMDG 737-600 (loaded
cold and dark). Every operation here is read-only.

Every test below needs a real PMDG loaded to mean anything -- run against
any other aircraft (a Cessna Citation Longitude, say), they depend on
`require_pmdg` (conftest.py) to skip instead of failing for a reason that
has nothing to do with the code under test. See conftest.py's "PMDG gate"
section for why that check is a plain TITLE substring match rather than the
very detect/probe logic these tests exist to verify.

(The 2026-08-29 live-suite trim removed this file's fifth test,
test_title_and_model_carry_no_pmdg_branding: its title/model detection
assertion ran unconditionally, against whatever aircraft happened to be
loaded, but that exact "no false positive" property -- given a controlled
title/model string -- is already covered precisely by
test_detect_pmdg_variant_no_false_positive_on_pmdg_777f_telemetry in
tests/test_title_detection.py, with no sim required. See
.superpowers/sdd/2026-08-29-mcp-modernization-phase2-capability/
live-trim-report.md for the full reasoning.)

Run only this file, not the full live suite:
    uv run pytest tests/live/test_live_pmdg.py -m live -v -s
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.live


async def test_probe_identifies_the_737_ng3_data_area(live_manager, require_pmdg):
    """The authoritative signal: only the loaded variant's client data area
    responds. Requires EnableDataBroadcast=1 in 737NG3_Options.ini."""
    from simconnect_mcp.tools.pmdg import _probe_pmdg_variant

    variant = await _probe_pmdg_variant()
    print(f"\n_probe_pmdg_variant() -> {variant!r}")

    assert variant == "pmdg_737"


async def test_resolve_pmdg_catalog_reports_probed_not_fallback(live_manager, require_pmdg):
    """End-to-end: with no explicit variant and no name, resolution must
    reach the probe and label the result "probed" -- not silently guess
    "pmdg_777" labelled "fallback", which is the live defect this task
    fixes."""
    from simconnect_mcp.tools.pmdg import _resolve_pmdg_catalog

    catalog_key, source = await _resolve_pmdg_catalog(None, None)
    print(f"\n_resolve_pmdg_catalog(None, None) -> ({catalog_key!r}, {source!r})")

    assert catalog_key == "pmdg_737"
    assert source == "probed"


async def test_get_pmdg_var_end_to_end_with_auto_detection(live_manager, require_pmdg):
    """Full tool-layer path, no explicit variant: auto-detection must reach
    the 737 NG3 catalog via the probe and return a real read, not silently
    answer from the wrong (777) catalog."""
    from simconnect_mcp.tools.models import PmdgVarResult
    from simconnect_mcp.tools.pmdg import get_pmdg_var

    result = await get_pmdg_var("FCTL_YawDamper_Sw")
    print(f"\nget_pmdg_var('FCTL_YawDamper_Sw') -> {result!r}")

    assert isinstance(result, PmdgVarResult)
    assert result.catalog == "pmdg_737"
    assert result.variant_source == "probed"


async def test_control_data_event_is_accepted_by_the_real_dispatcher(live_manager, require_pmdg):
    """Review follow-up (Phase 2 Task 3): send_pmdg_event's control_data
    branch (PmdgNG3DataManager.send_control) now correlates its send IDs
    through the real dispatcher's request registry instead of assuming
    success from a DLL call that didn't raise. This is the live counterpart
    of the mocked MockRegistry tests in test_pmdg.py/test_pmdg_ng3.py --
    those prove the branching logic; this proves the real
    GetLastSentPacketID/SIMCONNECT_RECV_ID_EXCEPTION correlation actually
    resolves 'accepted' against the real dispatch thread, not a scripted
    mock resolving it.

    Reads MCP_Altitude first and writes the same value back (true no-op --
    the aircraft's displayed value cannot change), mirroring this project's
    established live-test restore discipline. EVT_MCP_ALT_SET is a
    control_data (direct-set) event, not rotor_brake, per
    resolve_pmdg_event's offset>=14500 rule.
    """
    from simconnect_mcp.tools.models import PmdgEventResult, PmdgVarResult
    from simconnect_mcp.tools.pmdg import get_pmdg_var, send_pmdg_event

    current = await get_pmdg_var("MCP_Altitude", variant="pmdg_737")
    assert isinstance(current, PmdgVarResult)
    print(f"\ncurrent MCP_Altitude: {current.value!r}")

    result = await send_pmdg_event(
        "EVT_MCP_ALT_SET", parameter=int(current.value or 0), variant="pmdg_737"
    )
    print(f"send_pmdg_event('EVT_MCP_ALT_SET', parameter={int(current.value or 0)}) -> {result!r}")

    assert isinstance(result, PmdgEventResult)
    assert "successfully" not in result.message.lower()
    # A real dispatcher connection always has a request registry, so this
    # must land on "accepted", not the no-registry "not confirmed" wording.
    assert "accepted" in result.message.lower()
