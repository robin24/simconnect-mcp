"""Shared PMDG-variant detection: TITLE/ATC_MODEL matching plus the
authoritative client-data-area probe.

Extracted from ``tools/pmdg.py`` so ``tools/lvars.py``'s catalog
auto-detection can consult the same authoritative probe that
``tools.pmdg._resolve_pmdg_catalog`` already uses, without either tools
module importing the other. Both ``tools/pmdg.py`` and ``tools/lvars.py``
depend downward on this module -- the same direction both already depend on
``connection.py`` -- rather than depending sideways on each other, which
would be a layering cycle waiting to happen the moment one of them needed
something back from the other.

``tools/pmdg.py`` re-exports ``detect_pmdg_variant``/``probe_pmdg_variant``
under their historical ``_detect_pmdg_variant``/``_probe_pmdg_variant``
names (existing tests patch and call them there), and keeps its own
``_resolve_pmdg_catalog`` sequencing (explicit -> detected -> probed ->
name_match -> fallback) unchanged. ``tools/lvars.py`` uses
``detect_or_probe_pmdg_catalog`` -- the "detected -> probed" half only, with
no ``name_match``/``fallback`` guessing, which would be wrong for a
generic, aircraft-agnostic catalog search (see that function's docstring).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from simconnect_mcp.connection import SimConnectManager

if TYPE_CHECKING:
    from simconnect_mcp.pmdg import PmdgDataManager
    from simconnect_mcp.pmdg_ng3 import PmdgNG3DataManager


def get_or_create_pmdg_manager(
    variant: str = "pmdg_777",
) -> PmdgDataManager | PmdgNG3DataManager | None:
    """Get or lazily create the right PMDG SDK data manager for `variant`.

    Returns ``None`` (not a ``ToolError``) when the current SimConnect
    connection has no client-data support (plain SimConnect fallback, or no
    connection at all) -- this is consulted from contexts (catalog
    auto-detection) that must degrade quietly rather than surface a
    tool-facing refusal. ``tools.pmdg._ensure_pmdg_manager`` wraps this with
    its own ``ToolError`` for the PMDG-specific tools that need one.
    """
    sm_mgr = SimConnectManager()

    if not hasattr(sm_mgr.sm, "register_client_data_handler"):
        return None

    if variant == "pmdg_737":
        from simconnect_mcp.pmdg_ng3 import PmdgNG3DataManager
        if sm_mgr.pmdg_ng3 is None:
            sm_mgr.pmdg_ng3 = PmdgNG3DataManager(sm_mgr.sm)
            sm_mgr.sm.register_client_data_handler(
                sm_mgr.pmdg_ng3.client_data_handler
            )
        return sm_mgr.pmdg_ng3

    # Default: PMDG 777
    from simconnect_mcp.pmdg import PmdgDataManager
    if sm_mgr.pmdg is None:
        sm_mgr.pmdg = PmdgDataManager(sm_mgr.sm)
        sm_mgr.sm.register_client_data_handler(sm_mgr.pmdg.client_data_handler)
    return sm_mgr.pmdg


async def detect_pmdg_variant() -> str | None:
    """Return ``"pmdg_777"`` or ``"pmdg_737"`` based on the loaded aircraft.

    Checks both TITLE and ATC_MODEL against PMDG's own branding -- some
    liveries carry the vendor name only in ATC_MODEL while TITLE stays terse
    (e.g. a PMDG 777F's TITLE is just "777F"). Returns None if neither
    candidate matches, without guessing -- see ``probe_pmdg_variant`` for
    the next resolution step.
    """
    title, model = await SimConnectManager().detect_aircraft_identity()
    for candidate in (title, model):
        if not candidate:
            continue
        candidate_lower = candidate.lower()
        if "pmdg 737" in candidate_lower or "pmdg b737" in candidate_lower:
            return "pmdg_737"
        if "pmdg 777" in candidate_lower or "pmdg b777" in candidate_lower:
            return "pmdg_777"
    return None


# Tuning for probe_pmdg_variant's wait loop. A real SimConnect client-data
# round trip on the same machine completes in well under 100ms (live-verified
# on a PMDG 737-600: PMDG_NG3_Data responded in 0.09s); this budget is
# generous relative to that, while still bounding how much latency a
# genuinely non-PMDG aircraft (neither area ever responds) adds to a call
# that reaches the probe.
_PROBE_POLL_INTERVAL_S = 0.05
_PROBE_MAX_WAIT_S = 0.3


async def probe_pmdg_variant() -> str | None:
    """Probe which PMDG client data area actually responds.

    Reached only when TITLE/ATC_MODEL carry no PMDG branding at all --
    live-verified on a PMDG 737-600 reporting TITLE='737-600 PAX TC',
    ATC_MODEL='ATCCOM.AC_MODEL B736.0.text' (neither mentions PMDG or a
    model number ``detect_pmdg_variant`` matches), and again on a PMDG
    737-800 reporting TITLE='737-800 PAX SSW TC'. Both PMDG SDKs expose a
    dedicated SimConnect client data area (PMDG_777X_Data / PMDG_NG3_Data)
    that only the actually-loaded variant ever answers -- unlike a
    title/model substring, a data area cannot respond on behalf of an
    aircraft that is not loaded, which makes this authoritative where title
    matching is not.

    Requires EnableDataBroadcast=1 in the loaded aircraft's options.ini; if
    neither area responds (that setting is off, or no PMDG aircraft is
    loaded at all), this honestly returns None rather than guessing.

    The result is cached on the connection (SimConnectManager.
    get/set_cached_pmdg_variant), keyed to the aircraft identity
    (TITLE/ATC_MODEL) it was found under -- the loaded aircraft CAN change
    mid-connection with no reconnect, so this only skips a fresh round trip
    when the identity still matches, not unconditionally for the
    connection's whole lifetime. The identity lookup itself is cheap: it
    goes through the same TTL-cached detect_aircraft_identity() that
    detect_pmdg_variant already called moments earlier in whichever
    resolution flow reached here, so this is normally a cache hit, not a
    second SimVar read. A FAILED probe (neither area ever responds) is
    deliberately not cached -- see this module's callers for what that
    means for a non-PMDG aircraft.
    """
    manager = SimConnectManager()
    title, model = await manager.detect_aircraft_identity()
    cached = manager.get_cached_pmdg_variant(title, model)
    if cached is not None:
        return cached

    managers: dict[str, PmdgDataManager | PmdgNG3DataManager] = {}
    for key in ("pmdg_777", "pmdg_737"):
        mgr = get_or_create_pmdg_manager(key)
        if mgr is None:
            # No client-data support at all (e.g. MobiFlight/WASM missing) --
            # there is nothing to probe, and that is not itself a detection.
            return None
        managers[key] = mgr

    def _subscribe_and_request() -> None:
        for mgr in managers.values():
            mgr.subscribe_data()
            mgr.request_data()

    await manager.run_sync(_subscribe_and_request)

    elapsed = 0.0
    while elapsed < _PROBE_MAX_WAIT_S:
        for key, mgr in managers.items():
            if mgr.data_age != float("inf"):
                manager.set_cached_pmdg_variant(title, model, key)
                return key
        await asyncio.sleep(_PROBE_POLL_INTERVAL_S)
        elapsed += _PROBE_POLL_INTERVAL_S

    return None


async def detect_or_probe_pmdg_catalog() -> tuple[str | None, str | None]:
    """PMDG-aware half of catalog auto-detection: the cheap TITLE/ATC_MODEL
    check first, then the authoritative client-data-area probe.

    Returns ``(catalog_key, source)`` with ``source`` one of ``"detected"``
    (TITLE/ATC_MODEL matched PMDG's own branding) or ``"probed"`` (the
    client data area answered); ``(None, None)`` if neither found a PMDG
    aircraft at all.

    Deliberately stops there -- unlike ``tools.pmdg._resolve_pmdg_catalog``,
    this has no ``name_match``/``fallback`` step. Those exist for the
    PMDG-specific tools (``msfs_get_pmdg_var`` and friends), where a caller
    is already trying to use a PMDG SDK feature and an unresolved guess
    defaulting to "pmdg_777" is a reasonable last resort (flagged with a
    warning). Generic catalog search has no such context -- silently
    handing a PMDG catalog to a caller on an unrelated aircraft is exactly
    the "silently wrong" failure mode this project rejects in favour of an
    honest "nothing detected". Callers here fall back to every catalog's own
    ``title_pattern`` (``data.catalog.detect_catalog``) instead, which also
    covers third-party catalogs a user drops into ``data/``.
    """
    detected = await detect_pmdg_variant()
    if detected:
        return detected, "detected"

    probed = await probe_pmdg_variant()
    if probed:
        return probed, "probed"

    return None, None
