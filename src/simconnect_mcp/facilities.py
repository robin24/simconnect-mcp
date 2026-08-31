"""Facility list parsing and accumulation.

SimConnect answers SubscribeToFacilities/RequestFacilitiesList with one or
more ``*_LIST`` messages. Each carries a ``SIMCONNECT_RECV_FACILITIES_LIST``
header immediately followed by ``dwArraySize`` facility structs, and long
lists are chopped into ``dwOutOf`` transmissions that must be accumulated.

The SimConnect library's own handler for these messages calls ``dump()``,
which ``print()``s to stdout -- fatal on a stdio MCP server -- so this
module replaces it entirely; see dispatch.py's module docstring.

Struct layout note -- read before touching the ``_Facility*`` classes
======================================================================
This module does **not** use ``SimConnect.Enum``'s ``SIMCONNECT_DATA_
FACILITY_AIRPORT/WAYPOINT/NDB/VOR``. Those bindings are stale against what
MSFS 2024 actually sends on the wire, in two independent ways -- both
confirmed against a live sim (MSFS 2024, PMDG 737-600 parked at KATL; see
``.superpowers/sdd/2026-08-29-mcp-modernization-phase2-capability/
task-1-addendum.md`` for the full derivation and reproduction scripts):

1. The installed binding declares a single ``Icao[9]`` field. MSFS 2024
   sends two separate fields, ``Ident[6]`` + ``Region[3]`` -- the region
   code is a real field the binding never modelled.
2. **The wire records are packed** (no alignment padding). The binding's
   structs are ordinary 8-aligned ``ctypes.Structure``s, so their Latitude/
   Longitude/Altitude doubles sit at offsets 16/24/32. The real values sit
   at record-relative 9/17/25. The two bugs partially cancel in
   ``sizeof`` (both report 40 for AIRPORT), which is what let the wrong
   layout look plausible: parsing a real AIRPORT_LIST message at the
   binding's offsets decodes ICAO ``"PAKX"`` with a latitude of
   ``1.99e+170`` -- every field past byte 9 is read from the wrong place.

The structs below mirror the SDK's field order and inheritance chain (each
kind extends the previous, exactly as the stale binding's own classes do)
but with ``_pack_ = 1`` and the Ident/Region split, which is what MSFS
actually sends. Every record of a full live message of each kind validated
against this layout: printable ident, in-range coordinates, and (for
WAYPOINT/NDB/VOR) physically sane derived fields. Verified stride: AIRPORT
33, WAYPOINT 37, NDB 41, VOR 77 -- asserted directly in
test_facilities_parsing.py so a regression back to 8-byte alignment (which
``ctypes`` will produce silently if ``_pack_ = 1`` is ever dropped) fails
the suite immediately.

Two observations that look like bugs but are not -- do NOT "fix" either:

* Waypoint/VOR ``fMagVar`` is reported over 0-360 degrees, not +/-180 (e.g.
  ``359.0`` means one degree west). That is how MSFS expresses it.
* A VOR's frequency can fall outside the 108.000-118.000 MHz nav band --
  live-verified for ``WRB`` (Robins AFB, GA) at 135.300 MHz, one record out
  of 115, at a plausible position with a clean Flags value. A scenery data
  quirk, not a parse error; do not add a filter that hides it.
"""
from __future__ import annotations

import ctypes
import enum
import logging
import math
import threading
from ctypes import c_char, c_double, c_float
from ctypes.wintypes import DWORD
from typing import Any

from SimConnect.Enum import SIMCONNECT_RECV_FACILITIES_LIST, SIMCONNECT_RECV_ID

log = logging.getLogger(__name__)

EARTH_RADIUS_NM = 3440.065


class FacilityKind(str, enum.Enum):
    AIRPORT = "airport"
    WAYPOINT = "waypoint"
    NDB = "ndb"
    VOR = "vor"


_RECV_ID_TO_KIND = {
    SIMCONNECT_RECV_ID.SIMCONNECT_RECV_ID_AIRPORT_LIST: FacilityKind.AIRPORT,
    SIMCONNECT_RECV_ID.SIMCONNECT_RECV_ID_WAYPOINT_LIST: FacilityKind.WAYPOINT,
    SIMCONNECT_RECV_ID.SIMCONNECT_RECV_ID_NDB_LIST: FacilityKind.NDB,
    SIMCONNECT_RECV_ID.SIMCONNECT_RECV_ID_VOR_LIST: FacilityKind.VOR,
}


class _FacilityAirport(ctypes.Structure):
    """MSFS 2024's real wire layout for one AIRPORT_LIST record. sizeof == 33.

    NOT ``SimConnect.Enum.SIMCONNECT_DATA_FACILITY_AIRPORT`` -- see module
    docstring. The Ident/Region split and ``_pack_ = 1`` are both
    load-bearing: dropping either silently shifts every offset after it.
    """

    _pack_ = 1
    _fields_ = [
        ("Ident", c_char * 6),
        ("Region", c_char * 3),
        ("Latitude", c_double),   # degrees
        ("Longitude", c_double),  # degrees
        ("Altitude", c_double),   # metres
    ]


class _FacilityWaypoint(_FacilityAirport):
    """sizeof == 37: AIRPORT + fMagVar."""

    _pack_ = 1
    _fields_ = [
        ("fMagVar", c_float),  # degrees, 0-360 -- see module docstring
    ]


class _FacilityNdb(_FacilityWaypoint):
    """sizeof == 41: WAYPOINT + fFrequency."""

    _pack_ = 1
    _fields_ = [
        ("fFrequency", DWORD),  # Hz
    ]


class _FacilityVor(_FacilityNdb):
    """sizeof == 77: NDB + Flags, fLocalizer, glide-path position, fGlideSlopeAngle."""

    _pack_ = 1
    _fields_ = [
        ("Flags", DWORD),               # SIMCONNECT_VOR_FLAGS bitmask
        ("fLocalizer", c_float),        # degrees
        ("GlideLat", c_double),         # glide-path antenna position
        ("GlideLon", c_double),
        ("GlideAlt", c_double),         # metres
        ("fGlideSlopeAngle", c_float),  # degrees -- see module docstring
    ]


_KIND_TO_STRUCT = {
    FacilityKind.AIRPORT: _FacilityAirport,
    FacilityKind.WAYPOINT: _FacilityWaypoint,
    FacilityKind.NDB: _FacilityNdb,
    FacilityKind.VOR: _FacilityVor,
}

# Metres to feet, for the Altitude field.
_M_TO_FT = 3.280839895


def great_circle_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in nautical miles (haversine)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_NM * math.asin(min(1.0, math.sqrt(a)))


def _decode(raw: bytes | str) -> str:
    if isinstance(raw, bytes):
        return raw.decode("ascii", errors="replace").strip()
    return str(raw).strip()


def _entry_to_dict(kind: FacilityKind, entry: ctypes.Structure) -> dict[str, Any]:
    """Convert one facility struct to a plain dict.

    ``icao`` is sourced from the struct's ``Ident`` field -- the installed
    binding's ``Icao`` field does not exist on these structs, see module
    docstring -- and ``region`` is a field the old binding never carried.
    """
    result: dict[str, Any] = {
        "icao": _decode(entry.Ident),
        "region": _decode(entry.Region),
        "kind": kind.value,
        "latitude": entry.Latitude,
        "longitude": entry.Longitude,
        "altitude_ft": round(entry.Altitude * _M_TO_FT, 1),
    }
    if kind in (FacilityKind.WAYPOINT, FacilityKind.NDB, FacilityKind.VOR):
        result["magvar"] = round(entry.fMagVar, 2)
    if kind in (FacilityKind.NDB, FacilityKind.VOR):
        result["frequency_hz"] = int(entry.fFrequency)
    if kind is FacilityKind.VOR:
        result["localizer_deg"] = round(entry.fLocalizer, 2)
        result["glide_slope_deg"] = round(entry.fGlideSlopeAngle, 2)
    return result


def parse_facility_message(pData: Any) -> tuple[FacilityKind, Any, list[dict[str, Any]]]:
    """Parse a ``*_LIST`` dispatch message into ``(kind, header, entries)``.

    The array base -- 28 bytes past the start of the message, immediately
    after the header -- is the one part of the naive computation that is
    correct even via the stale binding: ``SIMCONNECT_RECV_FACILITIES_LIST``
    has no doubles, so it needs no 8-byte alignment pad regardless of
    packing, and that header struct's fields are otherwise unchanged from
    the SDK. Only the per-kind record structs that follow it were wrong in
    that binding -- see module docstring.
    """
    recv_id = pData.contents.dwID
    kind = _RECV_ID_TO_KIND[recv_id]
    header = ctypes.cast(
        pData, ctypes.POINTER(SIMCONNECT_RECV_FACILITIES_LIST)
    ).contents

    struct_type = _KIND_TO_STRUCT[kind]
    base = ctypes.addressof(header) + ctypes.sizeof(SIMCONNECT_RECV_FACILITIES_LIST)
    array = (struct_type * header.dwArraySize).from_address(base)
    return kind, header, [_entry_to_dict(kind, entry) for entry in array]


class FacilityCollector:
    """Accumulates chunked facility lists, one buffer per kind.

    Touched from two threads: the SimConnect dispatch thread calls
    ``handle()`` as ``*_LIST`` messages arrive; tool coroutines call
    ``results()``/``is_complete()`` (via an executor, off the event loop).
    All access goes through ``_lock``, and ``results()`` returns a fresh
    list on every call so a caller holding a previous snapshot is never
    affected by a concurrent ``handle()`` mutating storage underneath it.

    Completeness is derived from which ``dwEntryNumber`` chunks have
    actually been *seen*, not from the most recently arrived chunk's own
    number. An earlier version tracked only
    ``dwEntryNumber >= dwOutOf - 1``, which is a claim about the last chunk
    to arrive, not about the whole set: if chunk 1 of 3 never arrives and
    chunk 2 does, that check is satisfied forever with a third of the list
    silently missing and no signal to the caller. Chunks are stored keyed
    by their own ``dwEntryNumber`` (a dict, not an appended list) so that a
    result is only ever reported complete once every index in
    ``range(dwOutOf)`` has actually been recorded, and so that a result
    reconstructed from out-of-order chunk arrival is still sorted back into
    logical order rather than wire-arrival order.

    ``handle()`` also discards a chunk whose ``dwRequestID`` does not match
    the request ``reset()`` was most recently told to expect for that kind.
    ``UnsubscribeToFacilities`` does not retroactively cancel messages
    SimConnect has already queued, and a caller can also stop watching a
    kind without ever unsubscribing at all -- its coroutine's ``asyncio``
    task can be cancelled (e.g. an MCP client sending
    ``notifications/cancelled`` mid-poll) with no chance to run cleanup
    that was not wrapped in a ``finally``. Either way, an orphaned
    subscription can go on delivering chunks for a kind nobody is waiting
    on, and without this check a stray late chunk would land in whatever
    the *next* collection for that same kind happens to be -- silently
    mixing an old subscription's data into a new one, potentially flipping
    ``is_complete()`` true on a torn mix of both, with no signal to either
    caller. A ``reset()`` that omits ``request_id`` (every call site that
    predates this parameter) leaves correlation disabled for that kind, so
    existing callers are unaffected.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._chunks: dict[FacilityKind, dict[int, list[dict[str, Any]]]] = {}
        self._out_of: dict[FacilityKind, int] = {}
        self._request_id: dict[FacilityKind, int | None] = {}

    def handle(self, kind: FacilityKind, header: Any, entries: list[dict[str, Any]]) -> None:
        with self._lock:
            expected = self._request_id.get(kind)
            incoming = getattr(header, "dwRequestID", None)
            if expected is not None and incoming is not None and incoming != expected:
                # A chunk from a subscription this collector is no longer
                # waiting on -- see class docstring. Not logged: this runs
                # on SimConnect's own dispatch callback thread, and a
                # discard here is the expected outcome of an ordinary
                # timeout or cancellation, not a failure worth a line every
                # time it happens.
                return
            # dwEntryNumber 0 begins a new transmission; do not merge with
            # the chunks of a previous request.
            if header.dwEntryNumber == 0:
                self._chunks[kind] = {}
            # Keyed (not appended) so a duplicate or reordered delivery of
            # the same index overwrites rather than double-counts, and so
            # results() can always reassemble in logical order regardless
            # of the order chunks actually arrived in.
            self._chunks.setdefault(kind, {})[header.dwEntryNumber] = list(entries)
            self._out_of[kind] = header.dwOutOf

    def results(self, kind: FacilityKind) -> list[dict[str, Any]]:
        with self._lock:
            chunks = self._chunks.get(kind, {})
            return [entry for index in sorted(chunks) for entry in chunks[index]]

    def is_complete(self, kind: FacilityKind) -> bool:
        with self._lock:
            out_of = self._out_of.get(kind)
            if out_of is None:
                return False
            return len(self._chunks.get(kind, {})) == out_of

    def reset(self, kind: FacilityKind, request_id: int | None = None) -> None:
        """Clear kind's buffer and start tracking a new request id.

        `request_id` should be the SimConnect request ID the caller is
        about to (re)subscribe with, so a later `handle()` can tell a chunk
        that actually belongs to this request apart from one left over from
        a previous, abandoned subscription for the same kind -- see class
        docstring. Omitting it (the default) disables that check for this
        reset cycle; every call site that predates this parameter does.
        """
        with self._lock:
            self._chunks[kind] = {}
            self._out_of.pop(kind, None)
            self._request_id[kind] = request_id
