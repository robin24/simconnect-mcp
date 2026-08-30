"""Tests for facility list parsing and accumulation.

See src/simconnect_mcp/facilities.py's module docstring for why this does
NOT use SimConnect.Enum's SIMCONNECT_DATA_FACILITY_* structs: those bindings
are stale against MSFS 2024's actual (packed, Ident+Region) wire format.
Parsing a real message at the binding's offsets decodes ICAO "PAKX" with a
latitude of 1.99e+170 -- see
.superpowers/sdd/2026-08-29-mcp-modernization-phase2-capability/
task-1-addendum.md for the full derivation and live evidence.
"""

from __future__ import annotations

import ctypes
import struct

import pytest

from simconnect_mcp.facilities import (
    FacilityCollector,
    FacilityKind,
    _FacilityAirport,
    _FacilityNdb,
    _FacilityVor,
    _FacilityWaypoint,
    great_circle_nm,
    parse_facility_message,
)


class FakeHeader:
    def __init__(self, entry_number, out_of, array_size):
        self.dwEntryNumber = entry_number
        self.dwOutOf = out_of
        self.dwArraySize = array_size


def test_collector_accumulates_a_single_chunk():
    collector = FacilityCollector()
    collector.handle(FacilityKind.AIRPORT, FakeHeader(0, 1, 2),
                     [{"icao": "KSEA"}, {"icao": "KBFI"}])

    assert collector.is_complete(FacilityKind.AIRPORT) is True
    assert [a["icao"] for a in collector.results(FacilityKind.AIRPORT)] == ["KSEA", "KBFI"]


def test_collector_accumulates_multiple_chunks():
    """SimConnect chops long lists into dwOutOf transmissions."""
    collector = FacilityCollector()
    collector.handle(FacilityKind.AIRPORT, FakeHeader(0, 3, 1), [{"icao": "A"}])
    assert collector.is_complete(FacilityKind.AIRPORT) is False

    collector.handle(FacilityKind.AIRPORT, FakeHeader(1, 3, 1), [{"icao": "B"}])
    collector.handle(FacilityKind.AIRPORT, FakeHeader(2, 3, 1), [{"icao": "C"}])

    assert collector.is_complete(FacilityKind.AIRPORT) is True
    assert len(collector.results(FacilityKind.AIRPORT)) == 3


def test_a_new_first_chunk_starts_a_fresh_list():
    """A re-request must not append to the previous result."""
    collector = FacilityCollector()
    collector.handle(FacilityKind.AIRPORT, FakeHeader(0, 1, 1), [{"icao": "OLD"}])
    collector.handle(FacilityKind.AIRPORT, FakeHeader(0, 1, 1), [{"icao": "NEW"}])

    assert [a["icao"] for a in collector.results(FacilityKind.AIRPORT)] == ["NEW"]


def test_kinds_are_kept_separate():
    collector = FacilityCollector()
    collector.handle(FacilityKind.AIRPORT, FakeHeader(0, 1, 1), [{"icao": "KSEA"}])
    collector.handle(FacilityKind.VOR, FakeHeader(0, 1, 1), [{"icao": "SEA"}])

    assert len(collector.results(FacilityKind.AIRPORT)) == 1
    assert len(collector.results(FacilityKind.VOR)) == 1


def test_results_returns_a_copy_not_a_live_list():
    """The dispatch thread writes via handle() while tool coroutines read via
    results(); a snapshot taken before a later handle() call must not change
    underneath the caller. Fails against a `results()` that returns the
    internal list object directly instead of a copy."""
    collector = FacilityCollector()
    collector.handle(FacilityKind.AIRPORT, FakeHeader(0, 1, 1), [{"icao": "KSEA"}])

    snapshot = collector.results(FacilityKind.AIRPORT)
    collector.handle(FacilityKind.AIRPORT, FakeHeader(0, 1, 1), [{"icao": "KPDX"}])

    assert [a["icao"] for a in snapshot] == ["KSEA"]


def test_great_circle_distance_seattle_to_portland():
    # KSEA to KPDX is commonly quoted as "129 miles" -- but that figure is
    # STATUTE miles (129.26 sm here), not nautical miles. 129.26 sm converts
    # to 112.3 nm (sm / 1.15078), which is what this nm-returning function
    # actually computes and what a sanity check against a real distance
    # calculator confirms. The brief's own draft of this test asserted
    # `pytest.approx(129, abs=5)` against a nautical-mile result -- a
    # units mix-up, not a bug in great_circle_nm (verified independently
    # by hand and by cross-multiplying the standard 1.15078 sm/nm ratio).
    nm = great_circle_nm(47.4502, -122.3088, 45.5898, -122.5951)
    assert nm == pytest.approx(112.3, abs=2)


def test_great_circle_zero_distance():
    assert great_circle_nm(47.0, -122.0, 47.0, -122.0) == pytest.approx(0.0, abs=0.01)


# ---------------------------------------------------------------------------
# Struct layout -- the whole reason this addendum exists.
#
# ctypes silently pads a Structure to 8-byte alignment (because these
# structs contain doubles) unless _pack_ = 1 is set on every class in the
# inheritance chain. A silent regression there would NOT raise -- it would
# just make every offset past the Ident/Region prefix wrong again, exactly
# like the original SimConnect.Enum bug this module works around. Asserting
# the sizes directly, rather than relying only on the record-level parse
# tests below, catches that regression even if nobody notices a coordinate
# looks slightly off.
# ---------------------------------------------------------------------------


def test_facility_struct_sizes_match_the_wire_format():
    assert ctypes.sizeof(_FacilityAirport) == 33
    assert ctypes.sizeof(_FacilityWaypoint) == 37
    assert ctypes.sizeof(_FacilityNdb) == 41
    assert ctypes.sizeof(_FacilityVor) == 77


def test_facility_struct_field_offsets_match_the_wire_format():
    """Sizes alone could pass by coincidence (e.g. two fields swapped with
    matching widths); pin the offsets from the addendum's table directly."""
    assert _FacilityAirport.Ident.offset == 0
    assert _FacilityAirport.Region.offset == 6
    assert _FacilityAirport.Latitude.offset == 9
    assert _FacilityAirport.Longitude.offset == 17
    assert _FacilityAirport.Altitude.offset == 25
    assert _FacilityWaypoint.fMagVar.offset == 33
    assert _FacilityNdb.fFrequency.offset == 37
    assert _FacilityVor.Flags.offset == 41
    assert _FacilityVor.fLocalizer.offset == 45
    assert _FacilityVor.GlideLat.offset == 49
    assert _FacilityVor.GlideLon.offset == 57
    assert _FacilityVor.GlideAlt.offset == 65
    assert _FacilityVor.fGlideSlopeAngle.offset == 73


# ---------------------------------------------------------------------------
# parse_facility_message -- end-to-end against a synthetic wire buffer.
#
# The tests above exercise FacilityCollector with a hand-built FakeHeader
# and never touch ctypes at all, so on their own they could not have caught
# the original bug (wrong offsets from an 8-aligned, Icao[9] struct). These
# build a real packed byte buffer shaped exactly like a live SimConnect
# message -- a 28-byte SIMCONNECT_RECV_FACILITIES_LIST header followed by
# dwArraySize packed records -- and parse it through the real function.
# ---------------------------------------------------------------------------

# SIMCONNECT_RECV_ID values for the four *_LIST message kinds (see
# SimConnect.Enum.SIMCONNECT_RECV_ID; these four values are not part of the
# addendum's dispute -- only the per-kind record structs are).
_RECV_ID_AIRPORT_LIST = 18
_RECV_ID_VOR_LIST = 19
_RECV_ID_NDB_LIST = 20
_RECV_ID_WAYPOINT_LIST = 21


def _facilities_header(recv_id: int, array_size: int, entry_number: int, out_of: int) -> bytes:
    """28 bytes: the 12-byte SIMCONNECT_RECV prefix (dwSize, dwVersion, dwID)
    plus the four SIMCONNECT_RECV_FACILITIES_LIST DWORDs."""
    return struct.pack(
        "<IIIIIII",
        0,  # dwSize -- unused by parse_facility_message
        0,  # dwVersion -- unused
        recv_id,
        0,  # dwRequestID -- unused
        array_size,
        entry_number,
        out_of,
    )


def _pack_ident_region(ident: bytes, region: bytes) -> bytes:
    assert len(ident) <= 6 and len(region) <= 3
    return ident.ljust(6, b"\0") + region.ljust(3, b"\0")


def _airport_record(ident: bytes, region: bytes, lat: float, lon: float, alt: float) -> bytes:
    return _pack_ident_region(ident, region) + struct.pack("<ddd", lat, lon, alt)


def _waypoint_record(ident, region, lat, lon, alt, magvar) -> bytes:
    return _airport_record(ident, region, lat, lon, alt) + struct.pack("<f", magvar)


def _ndb_record(ident, region, lat, lon, alt, magvar, freq_hz) -> bytes:
    return _waypoint_record(ident, region, lat, lon, alt, magvar) + struct.pack("<I", freq_hz)


def _vor_record(ident, region, lat, lon, alt, magvar, freq_hz, flags, loc,
                 glide_lat, glide_lon, glide_alt, gs_angle) -> bytes:
    return _ndb_record(ident, region, lat, lon, alt, magvar, freq_hz) + struct.pack(
        "<Ifdddf", flags, loc, glide_lat, glide_lon, glide_alt, gs_angle
    )


def _pointer_to(buf: ctypes.Array) -> ctypes.pointer:
    """A pointer typed so `.contents.dwID` and the cast inside
    parse_facility_message both resolve exactly as they do for the real
    callback argument."""
    from SimConnect.Enum import SIMCONNECT_RECV_FACILITIES_LIST
    return ctypes.cast(ctypes.addressof(buf), ctypes.POINTER(SIMCONNECT_RECV_FACILITIES_LIST))


def test_parse_airport_list_message_from_a_synthetic_wire_buffer():
    """Regression guard for the exact bug the addendum documents: parsing at
    the wrong (8-aligned, Icao[9]) offsets would decode garbage here too."""
    record = _airport_record(b"KATL", b"K6", 33.6367, -84.4281, 313.0)
    raw = _facilities_header(_RECV_ID_AIRPORT_LIST, 1, 0, 1) + record
    buf = ctypes.create_string_buffer(raw, len(raw))

    kind, header, entries = parse_facility_message(_pointer_to(buf))

    assert kind is FacilityKind.AIRPORT
    assert header.dwArraySize == 1
    assert len(entries) == 1
    entry = entries[0]
    assert entry["icao"] == "KATL"
    assert entry["region"] == "K6"
    assert entry["latitude"] == pytest.approx(33.6367)
    assert entry["longitude"] == pytest.approx(-84.4281)
    assert entry["altitude_ft"] == pytest.approx(313.0 * 3.280839895, abs=0.1)
    assert "magvar" not in entry
    assert "frequency_hz" not in entry


def test_parse_multiple_airport_records_stay_aligned_to_the_true_stride():
    """Two records back to back: if the stride were wrong (e.g. 40 instead
    of 33, the stale-binding size), the second record would be read from
    the middle of the first and decode garbage."""
    records = [
        _airport_record(b"KATL", b"K6", 33.6367, -84.4281, 313.0),
        _airport_record(b"KPDK", b"K6", 33.8756, -84.3020, 316.0),
    ]
    raw = _facilities_header(_RECV_ID_AIRPORT_LIST, 2, 0, 1) + b"".join(records)
    buf = ctypes.create_string_buffer(raw, len(raw))

    _, _, entries = parse_facility_message(_pointer_to(buf))

    assert [e["icao"] for e in entries] == ["KATL", "KPDK"]
    assert entries[1]["latitude"] == pytest.approx(33.8756)
    assert entries[1]["longitude"] == pytest.approx(-84.3020)


def test_parse_vor_list_message_decodes_every_extra_field():
    """VOR is the deepest inheritance chain (77 bytes, four extra field
    types after NDB) -- most exposed to an offset regression anywhere
    upstream of it."""
    record = _vor_record(
        b"ATL", b"K6", 33.7792, -84.4194, 316.4,
        magvar=-3.5, freq_hz=113_400_000, flags=15, loc=51.0,
        glide_lat=33.6, glide_lon=-84.4, glide_alt=300.0, gs_angle=3.0,
    )
    raw = _facilities_header(_RECV_ID_VOR_LIST, 1, 0, 1) + record
    buf = ctypes.create_string_buffer(raw, len(raw))

    kind, _, entries = parse_facility_message(_pointer_to(buf))

    assert kind is FacilityKind.VOR
    entry = entries[0]
    assert entry["icao"] == "ATL"
    assert entry["magvar"] == pytest.approx(-3.5, abs=0.01)
    assert entry["frequency_hz"] == 113_400_000
    assert entry["localizer_deg"] == pytest.approx(51.0, abs=0.01)
    assert entry["glide_slope_deg"] == pytest.approx(3.0, abs=0.01)


def test_parse_respects_dwarraysize_not_buffer_length():
    """dwArraySize, not the buffer's remaining length, bounds how many
    records are read -- a trailing partial/garbage byte must be ignored."""
    record = _airport_record(b"KATL", b"K6", 33.6367, -84.4281, 313.0)
    raw = _facilities_header(_RECV_ID_AIRPORT_LIST, 1, 0, 1) + record + b"\xff"
    buf = ctypes.create_string_buffer(raw, len(raw))

    _, _, entries = parse_facility_message(_pointer_to(buf))

    assert len(entries) == 1
    assert entries[0]["icao"] == "KATL"


def test_parse_waypoint_and_ndb_report_magvar_over_0_to_360_uncorrected():
    """MSFS reports magvar over 0-360 (359.0 == one degree west), not
    +/-180. A prior implausibility check treated this as a bug -- it is not.
    Do not clamp or wrap the value; report it exactly as parsed."""
    record = _waypoint_record(b"ALLIV", b"K6", 33.674, -84.080, 0.0, magvar=359.0)
    raw = _facilities_header(_RECV_ID_WAYPOINT_LIST, 1, 0, 1) + record
    buf = ctypes.create_string_buffer(raw, len(raw))

    _, _, entries = parse_facility_message(_pointer_to(buf))

    assert entries[0]["magvar"] == pytest.approx(359.0, abs=0.01)


def test_parse_ndb_frequency_is_reported_in_hz_unfiltered():
    record = _ndb_record(b"IIU", b"K6", 33.5, -84.5, 250.0, magvar=-4.0, freq_hz=396_000)
    raw = _facilities_header(_RECV_ID_NDB_LIST, 1, 0, 1) + record
    buf = ctypes.create_string_buffer(raw, len(raw))

    _, _, entries = parse_facility_message(_pointer_to(buf))

    assert entries[0]["frequency_hz"] == 396_000


def test_parse_vor_out_of_nav_band_frequency_is_not_filtered():
    """Live-verified: "WRB" (Robins AFB, GA) sits at 135.300 MHz, outside
    the 108-118 MHz nav band -- a real scenery data quirk, not a parse
    error. The parser must report it as-is rather than dropping or
    clamping it."""
    record = _vor_record(
        b"WRB", b"K6", 32.6400, -83.5917, 91.0,
        magvar=-4.0, freq_hz=135_300_000, flags=8, loc=0.0,
        glide_lat=0.0, glide_lon=0.0, glide_alt=0.0, gs_angle=0.0,
    )
    raw = _facilities_header(_RECV_ID_VOR_LIST, 1, 0, 1) + record
    buf = ctypes.create_string_buffer(raw, len(raw))

    _, _, entries = parse_facility_message(_pointer_to(buf))

    assert entries[0]["icao"] == "WRB"
    assert entries[0]["frequency_hz"] == 135_300_000
