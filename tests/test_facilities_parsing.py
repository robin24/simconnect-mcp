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
from pathlib import Path

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
    underneath the caller.

    The second handle() call here MUST be a continuation chunk
    (dwEntryNumber=1, not 0): a first-chunk (dwEntryNumber=0) call replaces
    the stored buffer for this kind with a brand-new list object, which
    would protect a prior snapshot through object-identity churn alone --
    passing even against a `results()` that returns the internal list
    directly instead of a copy. A previous version of this test used
    dwEntryNumber=0 for both calls and could not fail against that
    regression (confirmed by patching in a non-copying results() and
    watching the old test still pass); a continuation chunk extends the
    existing stored list in place, which only a genuine copy survives."""
    collector = FacilityCollector()
    collector.handle(FacilityKind.AIRPORT, FakeHeader(0, 2, 1), [{"icao": "KSEA"}])

    snapshot = collector.results(FacilityKind.AIRPORT)
    collector.handle(FacilityKind.AIRPORT, FakeHeader(1, 2, 1), [{"icao": "KPDX"}])

    assert [a["icao"] for a in snapshot] == ["KSEA"]


def test_a_dropped_chunk_never_reports_complete():
    """A chunk that never arrives (chunk 1 of 3, here) must not let a LATER
    chunk's arrival flip is_complete() to True. The previous implementation
    derived completeness from only the most recently arrived chunk's own
    dwEntryNumber (>= dwOutOf - 1), which this satisfies the moment chunk 2
    lands -- silently reporting a two-thirds-complete list as the whole
    thing, with no signal to the caller. Reproduced live against that
    implementation before this fix."""
    collector = FacilityCollector()
    collector.handle(FacilityKind.AIRPORT, FakeHeader(0, 3, 1), [{"icao": "A0"}])
    collector.handle(FacilityKind.AIRPORT, FakeHeader(2, 3, 1), [{"icao": "A2"}])  # chunk 1 dropped

    assert collector.is_complete(FacilityKind.AIRPORT) is False
    assert len(collector.results(FacilityKind.AIRPORT)) == 2


def test_reordered_chunks_report_complete_only_once_and_sort_into_logical_order():
    """Chunks arriving out of wire order must not (a) report complete with a
    gap still open, and must not (b) come back in arrival order. The
    previous implementation failed both ways here: is_complete() flipped
    True the moment ANY chunk numbered dwOutOf-1 or higher arrived
    (regardless of which indices had actually shown up), and results() was
    a plain append-on-arrival list, so this exact sequence produced
    [A0, A2, A1] instead of [A0, A1, A2]."""
    collector = FacilityCollector()
    collector.handle(FacilityKind.AIRPORT, FakeHeader(0, 3, 1), [{"icao": "A0"}])
    collector.handle(FacilityKind.AIRPORT, FakeHeader(2, 3, 1), [{"icao": "A2"}])
    assert collector.is_complete(FacilityKind.AIRPORT) is False

    collector.handle(FacilityKind.AIRPORT, FakeHeader(1, 3, 1), [{"icao": "A1"}])

    assert collector.is_complete(FacilityKind.AIRPORT) is True
    assert [a["icao"] for a in collector.results(FacilityKind.AIRPORT)] == ["A0", "A1", "A2"]


def test_reset_clears_both_results_and_completeness():
    """FacilityCollector.reset() has a caller lined up in Task 2 (re-issuing
    a request for a kind that previously completed) but had no coverage."""
    collector = FacilityCollector()
    collector.handle(FacilityKind.AIRPORT, FakeHeader(0, 1, 1), [{"icao": "KSEA"}])
    assert collector.is_complete(FacilityKind.AIRPORT) is True

    collector.reset(FacilityKind.AIRPORT)

    assert collector.is_complete(FacilityKind.AIRPORT) is False
    assert collector.results(FacilityKind.AIRPORT) == []


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


def test_parse_waypoint_reports_magvar_over_0_to_360_uncorrected():
    """MSFS reports magvar over 0-360 (359.0 == one degree west), not
    +/-180. A prior implausibility check treated this as a bug -- it is not.
    Do not clamp or wrap the value; report it exactly as parsed."""
    record = _waypoint_record(b"ALLIV", b"K6", 33.674, -84.080, 0.0, magvar=359.0)
    raw = _facilities_header(_RECV_ID_WAYPOINT_LIST, 1, 0, 1) + record
    buf = ctypes.create_string_buffer(raw, len(raw))

    _, _, entries = parse_facility_message(_pointer_to(buf))

    assert entries[0]["magvar"] == pytest.approx(359.0, abs=0.01)


def test_parse_ndb_reports_magvar_over_0_to_360_uncorrected():
    """Same as the waypoint case above, but for NDB -- NDB inherits fMagVar
    from WAYPOINT in the struct chain, and the field is exposed identically
    in _entry_to_dict, but nothing previously exercised an actual NDB
    record for this."""
    record = _ndb_record(b"MG", b"K7", 32.3116, -86.5106, 0.0, magvar=359.0, freq_hz=245_000)
    raw = _facilities_header(_RECV_ID_NDB_LIST, 1, 0, 1) + record
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


# ---------------------------------------------------------------------------
# Real captured wire bytes -- the one thing the synthetic-buffer tests above
# cannot check.
#
# Every test above builds its input with struct.pack format strings written
# BY HAND from the addendum's offset table. That is a genuinely useful
# cross-check (it catches drift between the production structs and the
# test's own understanding of them), but it does not verify the table
# itself: production code and every test fixture above share one common
# source of truth (this author's transcription of the addendum), so if that
# transcription were subtly wrong in some way not caught by the plausibility
# checks the addendum used (printable ident, in-range coordinates, sane
# derived fields), every synthetic test would agree with the bug -- which is
# exactly how the ORIGINAL SimConnect.Enum defect went unnoticed for as long
# as it did.
#
# These fixtures are raw bytes captured from a live MSFS 2024 session (PMDG
# 737-600 at KATL) via SubscribeToFacilities, one real message per kind,
# truncated to a header plus 8 records. Truncation patches dwArraySize (the
# field parse_facility_message actually trusts to bound its read) down to
# the kept count; nothing else about the captured bytes is touched. This is
# the only test in this file that could catch the offset table itself being
# wrong, because it does not go anywhere near that table -- the expected
# values below were read off the sim's own scenery database (a real ALLIV,
# real NDB frequencies), not computed from the same offsets under test.
# ---------------------------------------------------------------------------

_FIXTURE_DIR = Path(__file__).parent / "fixtures" / "facilities"


def _load_fixture(name: str) -> bytes:
    hex_text = (_FIXTURE_DIR / f"{name}_list.hex").read_text().strip()
    return bytes.fromhex(hex_text)


def _parse_fixture(name: str):
    raw = _load_fixture(name)
    buf = ctypes.create_string_buffer(raw, len(raw))
    return parse_facility_message(_pointer_to(buf))


def _assert_generic_sanity(entries: list[dict]) -> None:
    """Printable ident, in-range coordinates -- the same plausibility bar
    the addendum itself used to validate the layout against a full live
    message."""
    assert entries, "fixture produced no records"
    for e in entries:
        assert e["icao"] and all(32 <= ord(c) < 127 for c in e["icao"]), e
        assert -90 <= e["latitude"] <= 90, e
        assert -180 <= e["longitude"] <= 180, e


def test_replay_real_airport_list_fixture():
    """Live-captured AIRPORT_LIST message, truncated to 8 records. The
    addendum's headline bug (SimConnect.Enum's stale offsets) decoded this
    exact first record's ident, "PAKX", correctly -- record 0's ident sits
    at record-relative offset 0 under either layout, so index 0 cannot
    distinguish them -- but paired it with a garbage latitude of 1.99e+170.
    The real layout must decode both correctly."""
    kind, header, entries = _parse_fixture("airport")

    assert kind is FacilityKind.AIRPORT
    assert header.dwArraySize == 8
    assert len(entries) == 8
    _assert_generic_sanity(entries)

    pakx = entries[0]
    assert pakx["icao"] == "PAKX"
    assert pakx["region"] == "PA"
    # In Alaska, not 1.99e+170 -- the addendum's exact headline failure mode.
    assert 55 <= pakx["latitude"] <= 65
    assert -160 <= pakx["longitude"] <= -150


def test_replay_real_waypoint_list_fixture_finds_alliv_near_katl():
    """ALLIV is the addendum's named anchor: 'parses to 33.674 / -84.080 --
    a few miles from KATL, where the aircraft is parked.' It happens to be
    the first record of this real captured message."""
    kind, _, entries = _parse_fixture("waypoint")

    assert kind is FacilityKind.WAYPOINT
    assert len(entries) == 8
    _assert_generic_sanity(entries)
    for e in entries:
        assert 0.0 <= e["magvar"] <= 360.0

    alliv = next(e for e in entries if e["icao"] == "ALLIV")
    assert alliv["latitude"] == pytest.approx(33.674, abs=0.01)
    assert alliv["longitude"] == pytest.approx(-84.080, abs=0.01)
    katl_lat, katl_lon = 33.6367, -84.4281
    assert great_circle_nm(katl_lat, katl_lon, alliv["latitude"], alliv["longitude"]) < 30


def test_replay_real_ndb_list_fixture_frequencies_in_band():
    """Addendum: 'NDB frequencies: all 30 land in 211-521 kHz (the NDB band
    is 190-1750 kHz).' This fixture holds 8 of those same 30 real records."""
    kind, _, entries = _parse_fixture("ndb")

    assert kind is FacilityKind.NDB
    assert len(entries) == 8
    _assert_generic_sanity(entries)
    for e in entries:
        assert 190_000 <= e["frequency_hz"] <= 1_750_000, e


def test_replay_real_vor_list_fixture_ityss_altitude_and_localizer_bearings():
    """Addendum anchors: ITYS altitude ~930 ft (from a raw 283.46 m field),
    and localizer bearings are whole degrees for VORs with an ILS
    component. A record with no localizer (VOR/DME only, e.g. AHN/DCU in
    this same capture) legitimately reports 0.0 and must not be treated as
    a failure."""
    kind, _, entries = _parse_fixture("vor")

    assert kind is FacilityKind.VOR
    assert len(entries) == 8
    _assert_generic_sanity(entries)

    itys = next(e for e in entries if e["icao"] == "ITYS")
    assert itys["altitude_ft"] == pytest.approx(930.0, abs=1.0)

    with_localizer = [e for e in entries if e["localizer_deg"] != 0.0]
    assert with_localizer, "expected at least one ILS-equipped VOR in this fixture"
    for e in with_localizer:
        assert e["localizer_deg"] == pytest.approx(round(e["localizer_deg"]), abs=0.01)
