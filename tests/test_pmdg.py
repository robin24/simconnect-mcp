"""Tests for PMDG 777 ctypes struct definitions."""

from __future__ import annotations

import ctypes
import struct
import time

import pytest

from simconnect_mcp.pmdg import (
    CDU_COLUMNS,
    CDU_COLOR_AMBER,
    CDU_COLOR_CYAN,
    CDU_COLOR_GREEN,
    CDU_COLOR_MAGENTA,
    CDU_COLOR_NAMES,
    CDU_COLOR_RED,
    CDU_COLOR_WHITE,
    CDU_FLAG_REVERSE,
    CDU_FLAG_SMALL_FONT,
    CDU_FLAG_UNUSED,
    CDU_ROWS,
    CDU_Grid,
    CDU_Row,
    PMDG_777X_CDU_DEFINITIONS,
    PMDG_777X_CDU_IDS,
    PMDG_777X_CDU_NAMES,
    PMDG_777X_CDU_Screen,
    PMDG_777X_CDU_Cell,
    PMDG_777X_DATA_DEFINITION,
    PMDG_777X_DATA_ID,
    PMDG_777X_DATA_NAME,
    PMDG_777X_DataStruct,
    THIRD_PARTY_EVENT_ID_MIN,
    render_cdu_text,
    render_cdu_grid,
)


# ---------------------------------------------------------------------------
# CDU Cell struct
# ---------------------------------------------------------------------------

class TestCDUCell:
    def test_size_is_3_bytes(self):
        assert ctypes.sizeof(PMDG_777X_CDU_Cell) == 3

    def test_fields_accessible(self):
        cell = PMDG_777X_CDU_Cell()
        cell.Symbol = 65   # 'A'
        cell.Color = CDU_COLOR_CYAN
        cell.Flags = CDU_FLAG_SMALL_FONT
        assert cell.Symbol == 65
        assert cell.Color == CDU_COLOR_CYAN
        assert cell.Flags == CDU_FLAG_SMALL_FONT

    def test_pack_1(self):
        """No padding — each byte is immediately after the previous."""
        assert PMDG_777X_CDU_Cell._pack_ == 1

    def test_field_order_in_memory(self):
        """Symbol, Color, Flags must be packed in that order without gaps."""
        raw = bytes([0x41, 0x02, 0x01])  # Symbol=0x41, Color=2, Flags=1
        cell = PMDG_777X_CDU_Cell.from_buffer_copy(raw)
        assert cell.Symbol == 0x41
        assert cell.Color == 2
        assert cell.Flags == 1


# ---------------------------------------------------------------------------
# CDU Row / Grid type aliases
# ---------------------------------------------------------------------------

class TestCDUDimensions:
    def test_constants(self):
        assert CDU_COLUMNS == 24
        assert CDU_ROWS == 14

    def test_cdu_row_length(self):
        row = CDU_Row()
        assert len(row) == CDU_ROWS  # 14 cells

    def test_cdu_grid_length(self):
        grid = CDU_Grid()
        assert len(grid) == CDU_COLUMNS  # 24 columns

    def test_cdu_grid_element_is_row(self):
        grid = CDU_Grid()
        assert isinstance(grid[0], CDU_Row)

    def test_cdu_grid_cell_accessible(self):
        grid = CDU_Grid()
        grid[3][7].Symbol = 0x42
        assert grid[3][7].Symbol == 0x42


# ---------------------------------------------------------------------------
# CDU Screen struct
# ---------------------------------------------------------------------------

class TestCDUScreen:
    def test_cells_field_is_grid(self):
        screen = PMDG_777X_CDU_Screen()
        # Cells should be a CDU_Grid (24 columns × 14 rows)
        assert len(screen.Cells) == CDU_COLUMNS

    def test_powered_field(self):
        screen = PMDG_777X_CDU_Screen()
        screen.Powered = True
        assert screen.Powered is True
        screen.Powered = False
        assert screen.Powered is False

    def test_size(self):
        # 24 columns × 14 rows × 3 bytes per cell + 1 byte for Powered
        expected = CDU_COLUMNS * CDU_ROWS * 3 + 1
        assert ctypes.sizeof(PMDG_777X_CDU_Screen) == expected

    def test_pack_1(self):
        assert PMDG_777X_CDU_Screen._pack_ == 1

    def test_cell_write_read_roundtrip(self):
        screen = PMDG_777X_CDU_Screen()
        screen.Cells[0][0].Symbol = ord("H")
        screen.Cells[0][0].Color = CDU_COLOR_GREEN
        screen.Cells[0][0].Flags = CDU_FLAG_REVERSE
        assert screen.Cells[0][0].Symbol == ord("H")
        assert screen.Cells[0][0].Color == CDU_COLOR_GREEN
        assert screen.Cells[0][0].Flags == CDU_FLAG_REVERSE

    def test_from_bytes(self):
        """Verify we can parse a raw byte buffer into a CDU screen."""
        # Build a raw buffer: all cells zero, Powered = 1
        size = ctypes.sizeof(PMDG_777X_CDU_Screen)
        raw = bytearray(size)
        raw[-1] = 1  # Powered byte is last
        screen = PMDG_777X_CDU_Screen.from_buffer_copy(bytes(raw))
        assert screen.Powered is True
        assert screen.Cells[0][0].Symbol == 0


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class TestConstants:
    def test_color_constants(self):
        assert CDU_COLOR_WHITE == 0
        assert CDU_COLOR_CYAN == 1
        assert CDU_COLOR_GREEN == 2
        assert CDU_COLOR_MAGENTA == 3
        assert CDU_COLOR_AMBER == 4
        assert CDU_COLOR_RED == 5

    def test_color_names_mapping(self):
        assert CDU_COLOR_NAMES[0] == "white"
        assert CDU_COLOR_NAMES[5] == "red"

    def test_flag_constants(self):
        assert CDU_FLAG_SMALL_FONT == 0x01
        assert CDU_FLAG_REVERSE == 0x02
        assert CDU_FLAG_UNUSED == 0x04

    def test_data_name_and_id(self):
        assert PMDG_777X_DATA_NAME == "PMDG_777X_Data"
        assert PMDG_777X_DATA_ID == 0x504D4447

    def test_cdu_names_and_ids(self):
        assert len(PMDG_777X_CDU_NAMES) == 3
        assert PMDG_777X_CDU_NAMES[0] == "PMDG_777X_CDU_0"
        assert len(PMDG_777X_CDU_IDS) == 3
        assert len(PMDG_777X_CDU_DEFINITIONS) == 3

    def test_third_party_event_id_min(self):
        assert THIRD_PARTY_EVENT_ID_MIN == 0x00011000


# ---------------------------------------------------------------------------
# DataStruct — structure integrity
# ---------------------------------------------------------------------------

class TestDataStruct:
    def test_instantiates(self):
        ds = PMDG_777X_DataStruct()
        assert ds is not None

    def test_pack_1(self):
        assert PMDG_777X_DataStruct._pack_ == 1

    def test_bool_fields_accessible(self):
        ds = PMDG_777X_DataStruct()
        ds.ADIRU_Sw_On = True
        assert ds.ADIRU_Sw_On is True
        ds.ADIRU_Sw_On = False
        assert ds.ADIRU_Sw_On is False

    def test_array_bool_fields(self):
        """Bool array fields should be indexable."""
        ds = PMDG_777X_DataStruct()
        ds.MCP_FD_Sw_On[0] = True
        ds.MCP_FD_Sw_On[1] = False
        assert ds.MCP_FD_Sw_On[0] is True
        assert ds.MCP_FD_Sw_On[1] is False

    def test_mcp_ias_mach_float(self):
        ds = PMDG_777X_DataStruct()
        ds.MCP_IASMach = 0.82
        assert abs(ds.MCP_IASMach - 0.82) < 1e-5

    def test_mcp_heading_ushort(self):
        ds = PMDG_777X_DataStruct()
        ds.MCP_Heading = 270
        assert ds.MCP_Heading == 270

    def test_mcp_altitude_ushort(self):
        ds = PMDG_777X_DataStruct()
        ds.MCP_Altitude = 35000
        assert ds.MCP_Altitude == 35000

    def test_mcp_vert_speed_short(self):
        ds = PMDG_777X_DataStruct()
        ds.MCP_VertSpeed = -2000
        assert ds.MCP_VertSpeed == -2000

    def test_door_state_array(self):
        """DOOR_state is a 16-element ubyte array."""
        ds = PMDG_777X_DataStruct()
        assert len(ds.DOOR_state) == 16
        ds.DOOR_state[0] = 3
        assert ds.DOOR_state[0] == 3

    def test_cockpit_door_bool(self):
        ds = PMDG_777X_DataStruct()
        ds.DOOR_CockpitDoorOpen = True
        assert ds.DOOR_CockpitDoorOpen is True

    def test_fmc_flight_number_char_array(self):
        """FMC_flightNumber is a 9-byte char array.

        ctypes c_char arrays return bytes values (null-terminated strings).
        Writing b"AA001" stores 5 bytes; reading back gives b"AA001".
        """
        ds = PMDG_777X_DataStruct()
        ds.FMC_flightNumber = b"AA001"
        assert ds.FMC_flightNumber == b"AA001"

    def test_fmc_flight_number_length(self):
        """The field descriptor reports a size of 9 bytes."""
        # ctypes sizeof on an instance field value (bytes) is not supported;
        # use the struct type's field descriptor instead.
        assert PMDG_777X_DataStruct.FMC_flightNumber.size == 9

    def test_elec_apuselector_ubyte(self):
        ds = PMDG_777X_DataStruct()
        ds.ELEC_APU_Selector = 2
        assert ds.ELEC_APU_Selector == 2

    def test_gear_lever_ubyte(self):
        ds = PMDG_777X_DataStruct()
        ds.GEAR_Lever = 1
        assert ds.GEAR_Lever == 1

    def test_brakes_pressure_needle_int(self):
        ds = PMDG_777X_DataStruct()
        ds.BRAKES_BrakePressNeedle = -50
        assert ds.BRAKES_BrakePressNeedle == -50

    def test_reserved_field(self):
        """Reserved field should be 84 bytes."""
        ds = PMDG_777X_DataStruct()
        assert len(ds.reserved) == 84

    def test_ecl_checklist_complete_array(self):
        """ECL_ChecklistComplete is a 10-element bool array."""
        ds = PMDG_777X_DataStruct()
        assert len(ds.ECL_ChecklistComplete) == 10
        ds.ECL_ChecklistComplete[0] = True
        assert ds.ECL_ChecklistComplete[0] is True

    def test_fuel_qty_fields_float(self):
        ds = PMDG_777X_DataStruct()
        ds.FUEL_QtyCenter = 12345.6
        assert abs(ds.FUEL_QtyCenter - 12345.6) < 0.1

    def test_from_bytes_roundtrip(self):
        """We should be able to zero-fill a buffer and parse it."""
        size = ctypes.sizeof(PMDG_777X_DataStruct)
        raw = bytes(size)
        ds = PMDG_777X_DataStruct.from_buffer_copy(raw)
        assert ds.MCP_Heading == 0
        assert ds.ADIRU_Sw_On is False


# ---------------------------------------------------------------------------
# CDU rendering
# ---------------------------------------------------------------------------

class TestCDURender:
    """Tests for render_cdu_text() and render_cdu_grid()."""

    def _make_screen(self, text_rows: dict[int, str], powered: bool = True) -> PMDG_777X_CDU_Screen:
        """Build a PMDG_777X_CDU_Screen from a dict mapping row index → text string.

        The CDU grid is column-major: screen.Cells[col][row].
        Characters in each string are written left-to-right starting at col 0.
        Rows not present in text_rows are left as zero (Symbol=0x00).
        """
        screen = PMDG_777X_CDU_Screen()
        screen.Powered = powered
        for row, text in text_rows.items():
            for col, ch in enumerate(text[:CDU_COLUMNS]):
                screen.Cells[col][row].Symbol = ord(ch)
        return screen

    # ------------------------------------------------------------------
    # render_cdu_text
    # ------------------------------------------------------------------

    def test_render_text_rows(self):
        """render_cdu_text returns 14 strings of 24 chars, with correct content."""
        row0_text = "HELLO WORLD             "[:CDU_COLUMNS]
        row2_text = "FL350  M.84  GW 555T    "[:CDU_COLUMNS]
        screen = self._make_screen({0: row0_text, 2: row2_text})

        result = render_cdu_text(screen)

        assert result is not None
        assert len(result) == CDU_ROWS  # 14 rows
        for row_str in result:
            assert len(row_str) == CDU_COLUMNS  # 24 chars each

        # Row 0 content
        assert result[0] == row0_text
        # Row 2 content
        assert result[2] == row2_text
        # Row 1 should be all spaces (Symbol=0 → space fallback)
        assert result[1] == " " * CDU_COLUMNS

    def test_render_unpowered_screen(self):
        """render_cdu_text returns None when CDU is not powered."""
        screen = self._make_screen({0: "SOME TEXT"}, powered=False)
        assert render_cdu_text(screen) is None

    def test_render_text_left_arrow(self):
        """Symbol 0xA1 is rendered as the Unicode left-arrow character."""
        screen = PMDG_777X_CDU_Screen()
        screen.Powered = True
        screen.Cells[0][0].Symbol = 0xA1
        result = render_cdu_text(screen)
        assert result is not None
        assert result[0][0] == "\u2190"

    def test_render_text_right_arrow(self):
        """Symbol 0xA2 is rendered as the Unicode right-arrow character."""
        screen = PMDG_777X_CDU_Screen()
        screen.Powered = True
        screen.Cells[0][0].Symbol = 0xA2
        result = render_cdu_text(screen)
        assert result is not None
        assert result[0][0] == "\u2192"

    def test_render_text_non_printable_becomes_space(self):
        """Symbols outside printable ASCII range (other than arrows) become spaces."""
        screen = PMDG_777X_CDU_Screen()
        screen.Powered = True
        screen.Cells[0][0].Symbol = 0x01  # non-printable, not an arrow
        result = render_cdu_text(screen)
        assert result is not None
        assert result[0][0] == " "

    # ------------------------------------------------------------------
    # render_cdu_grid
    # ------------------------------------------------------------------

    def test_render_structured_grid(self):
        """render_cdu_grid returns per-cell dicts with char, color, small, reverse, dim."""
        screen = PMDG_777X_CDU_Screen()
        screen.Powered = True
        # Place a specific cell at col=3, row=5 with known attributes
        screen.Cells[3][5].Symbol = ord("X")
        screen.Cells[3][5].Color = CDU_COLOR_CYAN
        screen.Cells[3][5].Flags = CDU_FLAG_SMALL_FONT

        result = render_cdu_grid(screen)

        assert result is not None
        assert len(result) == CDU_ROWS          # 14 rows
        assert len(result[0]) == CDU_COLUMNS    # 24 cols per row

        cell = result[5][3]  # row 5, col 3
        assert cell["char"] == "X"
        assert cell["color"] == "cyan"
        assert cell["small"] is True
        assert cell["reverse"] is False
        assert cell["dim"] is False

    def test_render_grid_flag_reverse(self):
        """CDU_FLAG_REVERSE sets the 'reverse' field to True."""
        screen = PMDG_777X_CDU_Screen()
        screen.Powered = True
        screen.Cells[0][0].Symbol = ord("A")
        screen.Cells[0][0].Color = CDU_COLOR_WHITE
        screen.Cells[0][0].Flags = CDU_FLAG_REVERSE

        result = render_cdu_grid(screen)
        assert result is not None
        cell = result[0][0]
        assert cell["reverse"] is True
        assert cell["small"] is False
        assert cell["dim"] is False

    def test_render_grid_flag_dim(self):
        """CDU_FLAG_UNUSED sets the 'dim' field to True."""
        screen = PMDG_777X_CDU_Screen()
        screen.Powered = True
        screen.Cells[0][0].Symbol = ord("B")
        screen.Cells[0][0].Color = CDU_COLOR_GREEN
        screen.Cells[0][0].Flags = CDU_FLAG_UNUSED

        result = render_cdu_grid(screen)
        assert result is not None
        cell = result[0][0]
        assert cell["dim"] is True
        assert cell["color"] == "green"

    def test_render_grid_unpowered(self):
        """render_cdu_grid returns None when CDU is not powered."""
        screen = self._make_screen({}, powered=False)
        assert render_cdu_grid(screen) is None

    def test_render_grid_unknown_color_defaults_white(self):
        """An unrecognised color byte maps to 'white' via CDU_COLOR_NAMES.get fallback."""
        screen = PMDG_777X_CDU_Screen()
        screen.Powered = True
        screen.Cells[0][0].Symbol = ord("Z")
        screen.Cells[0][0].Color = 99  # not in CDU_COLOR_NAMES

        result = render_cdu_grid(screen)
        assert result is not None
        assert result[0][0]["color"] == "white"


# ---------------------------------------------------------------------------
# PmdgDataManager
# ---------------------------------------------------------------------------

class TestPmdgDataManager:
    def test_init_sets_empty_state(self):
        from simconnect_mcp.pmdg import PmdgDataManager
        mgr = PmdgDataManager(sm=None)
        assert mgr.data_subscribed is False
        assert mgr.cdu_subscribed == [False, False, False]
        assert mgr._data_struct is None

    def test_read_field_bool(self):
        from simconnect_mcp.pmdg import PmdgDataManager, PMDG_777X_DataStruct
        mgr = PmdgDataManager(sm=None)
        mgr._data_struct = PMDG_777X_DataStruct()
        mgr._data_struct.ELEC_Battery_Sw_ON = True
        mgr._data_timestamp = time.time()
        mgr.data_subscribed = True
        assert mgr.read_field("ELEC_Battery_Sw_ON") is True

    def test_read_field_array(self):
        from simconnect_mcp.pmdg import PmdgDataManager, PMDG_777X_DataStruct
        mgr = PmdgDataManager(sm=None)
        mgr._data_struct = PMDG_777X_DataStruct()
        mgr._data_struct.ELEC_BusTie_Sw_AUTO[0] = True
        mgr._data_struct.ELEC_BusTie_Sw_AUTO[1] = False
        mgr._data_timestamp = time.time()
        mgr.data_subscribed = True
        assert mgr.read_field("ELEC_BusTie_Sw_AUTO", index=0) is True
        assert mgr.read_field("ELEC_BusTie_Sw_AUTO", index=1) is False

    def test_read_field_float(self):
        from simconnect_mcp.pmdg import PmdgDataManager, PMDG_777X_DataStruct
        mgr = PmdgDataManager(sm=None)
        mgr._data_struct = PMDG_777X_DataStruct()
        mgr._data_struct.MCP_IASMach = 250.0
        mgr._data_timestamp = time.time()
        mgr.data_subscribed = True
        assert mgr.read_field("MCP_IASMach") == pytest.approx(250.0)

    def test_read_field_unknown_raises(self):
        from simconnect_mcp.pmdg import PmdgDataManager, PMDG_777X_DataStruct
        mgr = PmdgDataManager(sm=None)
        mgr._data_struct = PMDG_777X_DataStruct()
        mgr._data_timestamp = time.time()
        mgr.data_subscribed = True
        with pytest.raises(ValueError, match="Unknown field"):
            mgr.read_field("NONEXISTENT_FIELD")

    def test_read_field_not_subscribed_returns_none(self):
        from simconnect_mcp.pmdg import PmdgDataManager
        mgr = PmdgDataManager(sm=None)
        assert mgr.read_field("ELEC_Battery_Sw_ON") is None

    def test_read_cdu_not_subscribed_returns_none(self):
        from simconnect_mcp.pmdg import PmdgDataManager
        mgr = PmdgDataManager(sm=None)
        assert mgr.read_cdu(0) is None

    def test_data_age_infinity_when_no_data(self):
        from simconnect_mcp.pmdg import PmdgDataManager
        mgr = PmdgDataManager(sm=None)
        assert mgr.data_age == float("inf")

    def test_data_age_recent(self):
        from simconnect_mcp.pmdg import PmdgDataManager
        mgr = PmdgDataManager(sm=None)
        mgr._data_timestamp = time.time()
        assert mgr.data_age < 1.0

    def test_cleanup_resets_state(self):
        from simconnect_mcp.pmdg import PmdgDataManager, PMDG_777X_DataStruct
        mgr = PmdgDataManager(sm=None)
        mgr._data_struct = PMDG_777X_DataStruct()
        mgr.data_subscribed = True
        mgr.cdu_subscribed = [True, False, True]
        mgr.cleanup()
        assert mgr._data_struct is None
        assert mgr.data_subscribed is False
        assert mgr.cdu_subscribed == [False, False, False]


# ---------------------------------------------------------------------------
# Event resolution
# ---------------------------------------------------------------------------

class TestEventResolution:
    def test_resolve_event_by_name(self):
        from simconnect_mcp.pmdg import resolve_pmdg_event
        code = resolve_pmdg_event("EVT_OH_ELEC_BATTERY_SWITCH")
        # Event offset 1 + ROTOR_BRAKE_OFFSET 100 = 101
        assert code == "101 (>K:ROTOR_BRAKE)"

    def test_resolve_event_by_name_with_parameter(self):
        from simconnect_mcp.pmdg import resolve_pmdg_event
        code = resolve_pmdg_event("EVT_OH_ELEC_BATTERY_SWITCH", parameter=1)
        assert code == "101 1 (>K:ROTOR_BRAKE)"

    def test_resolve_unknown_event_raises(self):
        from simconnect_mcp.pmdg import resolve_pmdg_event
        with pytest.raises(ValueError, match="not found in PMDG 777 catalog"):
            resolve_pmdg_event("EVT_NONEXISTENT")
