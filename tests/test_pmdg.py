"""Tests for PMDG 777 ctypes struct definitions."""

from __future__ import annotations

import ctypes
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from simconnect_mcp.pmdg import (
    CDU_COLOR_AMBER,
    CDU_COLOR_CYAN,
    CDU_COLOR_GREEN,
    CDU_COLOR_MAGENTA,
    CDU_COLOR_NAMES,
    CDU_COLOR_RED,
    CDU_COLOR_WHITE,
    CDU_COLUMNS,
    CDU_FLAG_REVERSE,
    CDU_FLAG_SMALL_FONT,
    CDU_FLAG_UNUSED,
    CDU_ROWS,
    PMDG_777X_CDU_DEFINITIONS,
    PMDG_777X_CDU_IDS,
    PMDG_777X_CDU_NAMES,
    PMDG_777X_DATA_ID,
    PMDG_777X_DATA_NAME,
    THIRD_PARTY_EVENT_ID_MIN,
    CDU_Grid,
    CDU_Row,
    PMDG_777X_CDU_Cell,
    PMDG_777X_CDU_Screen,
    PMDG_777X_DataStruct,
    render_cdu_grid,
    render_cdu_text,
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

    def test_native_alignment(self):
        """Uses native alignment (684 bytes) to match MSVC default packing."""
        assert not hasattr(PMDG_777X_DataStruct, "_pack_")
        assert ctypes.sizeof(PMDG_777X_DataStruct) == 684

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
        from simconnect_mcp.pmdg import PMDG_777X_DataStruct, PmdgDataManager
        mgr = PmdgDataManager(sm=None)
        mgr._data_struct = PMDG_777X_DataStruct()
        mgr._data_struct.ELEC_Battery_Sw_ON = True
        mgr._data_timestamp = time.time()
        mgr.data_subscribed = True
        assert mgr.read_field("ELEC_Battery_Sw_ON") is True

    def test_read_field_array(self):
        from simconnect_mcp.pmdg import PMDG_777X_DataStruct, PmdgDataManager
        mgr = PmdgDataManager(sm=None)
        mgr._data_struct = PMDG_777X_DataStruct()
        mgr._data_struct.ELEC_BusTie_Sw_AUTO[0] = True
        mgr._data_struct.ELEC_BusTie_Sw_AUTO[1] = False
        mgr._data_timestamp = time.time()
        mgr.data_subscribed = True
        assert mgr.read_field("ELEC_BusTie_Sw_AUTO", index=0) is True
        assert mgr.read_field("ELEC_BusTie_Sw_AUTO", index=1) is False

    def test_read_field_float(self):
        from simconnect_mcp.pmdg import PMDG_777X_DataStruct, PmdgDataManager
        mgr = PmdgDataManager(sm=None)
        mgr._data_struct = PMDG_777X_DataStruct()
        mgr._data_struct.MCP_IASMach = 250.0
        mgr._data_timestamp = time.time()
        mgr.data_subscribed = True
        assert mgr.read_field("MCP_IASMach") == pytest.approx(250.0)

    def test_read_field_unknown_raises(self):
        from simconnect_mcp.pmdg import PMDG_777X_DataStruct, PmdgDataManager
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
        from simconnect_mcp.pmdg import PMDG_777X_DataStruct, PmdgDataManager
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
    def test_resolve_panel_event(self):
        from simconnect_mcp.pmdg import resolve_pmdg_event
        result = resolve_pmdg_event("EVT_OH_ELEC_BATTERY_SWITCH")
        # offset 1, formula: 1*100+1 = 101
        assert result["method"] == "rotor_brake"
        assert result["code"] == "101 (>K:ROTOR_BRAKE)"

    def test_resolve_panel_event_with_parameter(self):
        from simconnect_mcp.pmdg import resolve_pmdg_event
        result = resolve_pmdg_event("EVT_OH_ELEC_BATTERY_SWITCH", parameter=1)
        assert result["method"] == "rotor_brake"
        assert result["code"] == "101 1 (>K:ROTOR_BRAKE)"

    def test_resolve_cdu_event(self):
        from simconnect_mcp.pmdg import resolve_pmdg_event
        result = resolve_pmdg_event("EVT_CDU_L_L1")
        # CDU L1 offset=328, formula: 328*100+1 = 32801
        assert result["method"] == "rotor_brake"
        assert result["code"] == "32801 (>K:ROTOR_BRAKE)"

    def test_resolve_direct_set_event(self):
        from simconnect_mcp.pmdg import resolve_pmdg_event
        result = resolve_pmdg_event("EVT_MCP_ALT_SET", parameter=5000)
        # Direct-set events use Control data area
        assert result["method"] == "control_data"
        assert result["event_id"] == 69632 + 14505
        assert result["parameter"] == 5000

    def test_resolve_unknown_event_raises(self):
        from simconnect_mcp.pmdg import resolve_pmdg_event
        with pytest.raises(ValueError, match="not found in PMDG 777 catalog"):
            resolve_pmdg_event("EVT_NONEXISTENT")


# ---------------------------------------------------------------------------
# tools.pmdg tool-layer functions (get_pmdg_var / get_pmdg_cdu / send_pmdg_event)
#
# Phase 1 Task 7 converts these from dict returns to Pydantic models. Before
# this task, none of the tool functions themselves had direct test coverage
# (only the underlying structs/manager/event-resolution above did) -- these
# fill that gap and lock in the model-return contract.
# ---------------------------------------------------------------------------

class TestGetPmdgVarTool:
    async def test_returns_a_model_with_explicit_variant(self, mock_simconnect):
        """Fails against a dict-returning implementation: isinstance(dict,
        PmdgVarResult) is False."""
        from simconnect_mcp.pmdg import PMDG_777X_DataStruct, PmdgDataManager
        from simconnect_mcp.tools.models import PmdgVarResult
        from simconnect_mcp.tools.pmdg import get_pmdg_var

        manager = mock_simconnect["manager"]
        pmdg = PmdgDataManager(sm=manager.sm)
        pmdg.data_subscribed = True
        pmdg._data_struct = PMDG_777X_DataStruct()
        pmdg._data_struct.ELEC_Battery_Sw_ON = True
        pmdg._data_timestamp = time.time()
        manager.pmdg = pmdg

        result = await get_pmdg_var("ELEC_Battery_Sw_ON", variant="pmdg_777")

        assert isinstance(result, PmdgVarResult)
        assert result.name == "ELEC_Battery_Sw_ON"
        assert result.value is True
        assert result.display_name == "ELEC Battery Sw ON"
        assert result.catalog == "pmdg_777"
        assert result.variant_source == "explicit"

    async def test_boolean_field_survives_as_a_bool_not_a_float(self, mock_simconnect):
        """Regression: PmdgVarResult.value used to be typed float|int|str|None,
        with no bool in the union. Many PMDG SDK fields are ctypes.c_bool
        (ELEC_Battery_Sw_ON here), and read_field() returns a native Python
        bool for them -- but constructing the old model coerced that bool to
        a float (True -> 1.0), silently turning a switch position into what
        looks like a measurement. Fails against the pre-fix union: `result.value
        is True` would be False (it would be 1.0, a float) even though
        `result.value == True` happens to still hold (1.0 == True in Python),
        which is why this asserts identity/type, not just equality."""
        from simconnect_mcp.pmdg import PMDG_777X_DataStruct, PmdgDataManager
        from simconnect_mcp.tools.pmdg import get_pmdg_var

        manager = mock_simconnect["manager"]
        pmdg = PmdgDataManager(sm=manager.sm)
        pmdg.data_subscribed = True
        pmdg._data_struct = PMDG_777X_DataStruct()
        pmdg._data_struct.ELEC_Battery_Sw_ON = False
        pmdg._data_timestamp = time.time()
        manager.pmdg = pmdg

        off = await get_pmdg_var("ELEC_Battery_Sw_ON", variant="pmdg_777")
        assert off.value is False
        assert type(off.value) is bool

        pmdg._data_struct.ELEC_Battery_Sw_ON = True
        on = await get_pmdg_var("ELEC_Battery_Sw_ON", variant="pmdg_777")
        assert on.value is True
        assert type(on.value) is bool

    async def test_unknown_field_returns_field_not_found(self, mock_simconnect):
        from simconnect_mcp.tools.models import ToolError
        from simconnect_mcp.tools.pmdg import get_pmdg_var

        result = await get_pmdg_var("NOT_A_REAL_VARIABLE", variant="pmdg_777")

        assert isinstance(result, ToolError)
        assert result.error == "FIELD_NOT_FOUND"

    async def test_reports_no_data_when_area_never_responds(self, mock_simconnect):
        """Fails against an implementation that returns a value (e.g. None)
        instead of the explicit NO_DATA error when the client data area never
        answers. asyncio.sleep is patched to a no-op so this exercises the
        real 20-iteration wait loop without taking two real seconds."""
        from simconnect_mcp.tools.models import ToolError
        from simconnect_mcp.tools.pmdg import get_pmdg_var

        with patch("simconnect_mcp.tools.pmdg.asyncio.sleep", new=AsyncMock()):
            result = await get_pmdg_var("ELEC_Battery_Sw_ON", variant="pmdg_777")

        assert isinstance(result, ToolError)
        assert result.error == "NO_DATA"


class TestGetPmdgCduTool:
    async def test_returns_a_model_when_powered(self, mock_simconnect):
        from simconnect_mcp.pmdg import PMDG_777X_CDU_Screen, PmdgDataManager
        from simconnect_mcp.tools.models import PmdgCduResult
        from simconnect_mcp.tools.pmdg import get_pmdg_cdu

        manager = mock_simconnect["manager"]
        pmdg = PmdgDataManager(sm=manager.sm)
        screen = PMDG_777X_CDU_Screen()
        screen.Powered = True
        screen.Cells[0][0].Symbol = ord("A")
        pmdg.cdu_subscribed[0] = True
        pmdg._cdu_screens[0] = screen
        pmdg._cdu_timestamps[0] = time.time()
        manager.pmdg = pmdg

        result = await get_pmdg_cdu(cdu=0, variant="pmdg_777")

        assert isinstance(result, PmdgCduResult)
        assert result.powered is True
        assert result.cdu_name == "Left (Captain)"
        assert result.rows is not None and result.rows[0][0] == "A"
        assert result.grid is not None
        assert result.catalog == "pmdg_777"
        assert result.variant_source == "explicit"

    async def test_rejects_invalid_cdu_index_for_777(self, mock_simconnect):
        """A direct call bypasses the schema-level 0-2 Field bound (see the
        comment in pmdg.py); the runtime check must still reject an index
        the 777's three CDUs don't have."""
        from simconnect_mcp.tools.models import ToolError
        from simconnect_mcp.tools.pmdg import get_pmdg_cdu

        result = await get_pmdg_cdu(cdu=5, variant="pmdg_777")

        assert isinstance(result, ToolError)
        assert result.error == "INVALID_CDU"


class TestSendPmdgEventTool:
    async def test_wraps_unknown_event_as_pmdg_event_not_found(self, mock_simconnect):
        """Preserved Phase 0 behaviour: resolve_pmdg_event's ValueError must
        surface as PMDG_EVENT_NOT_FOUND, not the generic UNEXPECTED a bare
        exception would get from handle_simconnect_errors. Fails against an
        implementation that lets the ValueError propagate unwrapped."""
        from simconnect_mcp.tools.models import ToolError
        from simconnect_mcp.tools.pmdg import send_pmdg_event

        result = await send_pmdg_event("EVT_TOTALLY_MADE_UP", variant="pmdg_777")

        assert isinstance(result, ToolError)
        assert result.error == "PMDG_EVENT_NOT_FOUND"

    async def test_returns_a_model_on_success(self, mock_simconnect):
        from simconnect_mcp.tools.models import PmdgEventResult
        from simconnect_mcp.tools.pmdg import send_pmdg_event

        result = await send_pmdg_event("EVT_MCP_ALT_SET", parameter=5000, variant="pmdg_777")

        assert isinstance(result, PmdgEventResult)
        assert result.event == "EVT_MCP_ALT_SET"
        assert result.parameter == 5000
        assert result.catalog == "pmdg_777"
        assert result.variant_source == "explicit"
        # B8: an explicit variant is a real signal (the caller's own say-so)
        # and needs no warning.
        assert result.warning is None

    async def test_warns_when_the_catalog_was_only_name_matched(self, mock_simconnect):
        """B8: get_pmdg_var/get_pmdg_cdu self-correct on a wrong catalog
        guess -- the client data area for the wrong SDK just never
        responds, so a bad guess surfaces as NO_DATA. send_pmdg_event has
        no such feedback loop: it actually writes to whichever SDK's
        control area the guessed catalog names, and that write can reach a
        real, wrong aircraft system with no error at all. Reproduces the
        live-realistic path: TITLE/ATC_MODEL carry no PMDG branding (the
        fixture's default "Boeing 747-8i") and the client-data probe also
        finds nothing responding, so resolution falls through to matching
        the event name against the catalogs -- asyncio.sleep is patched so
        this doesn't pay the probe's ~0.3s wait."""
        from simconnect_mcp.tools.models import PmdgEventResult
        from simconnect_mcp.tools.pmdg import send_pmdg_event

        with patch("simconnect_mcp.tools.pmdg.asyncio.sleep", new=AsyncMock()):
            result = await send_pmdg_event("EVT_MCP_ALT_SET", parameter=5000)

        assert isinstance(result, PmdgEventResult)
        assert result.catalog == "pmdg_777"
        assert result.variant_source == "name_match"
        assert result.warning is not None
        assert "pmdg_777" in result.warning

    async def test_rotor_brake_event_message_does_not_overclaim_confirmation(
        self, mock_simconnect
    ):
        """Phase 2 Task 3 residual: EVT_OH_ELEC_BATTERY_SWITCH resolves to
        the rotor_brake method, which dispatches through
        manager.mobiflight.set() -- MF.SimVars.Set.*, sent over the same
        client-data command channel as the two sites Phase 1 already fixed
        in tools/lvars.py and tools/events.py. Measured live against the
        real WASM module after Task 3 made the response channel readable
        (mobiflight_set_ack_probe.py): MF.LVars.List produces 1002
        definition-0 responses, but an MF.SimVars.Set.* command -- even
        though the write itself lands and reads back correctly -- produces
        zero. So this path remains genuinely unconfirmed and must not claim
        "successfully"."""
        from simconnect_mcp.tools.models import PmdgEventResult
        from simconnect_mcp.tools.pmdg import send_pmdg_event

        mock_simconnect["manager"]._mobiflight_available = True
        mock_simconnect["manager"].mobiflight = MagicMock()

        result = await send_pmdg_event("EVT_OH_ELEC_BATTERY_SWITCH", variant="pmdg_777")

        assert isinstance(result, PmdgEventResult)
        mock_simconnect["manager"].mobiflight.set.assert_called_once_with(
            "101 (>K:ROTOR_BRAKE)"
        )
        assert "successfully" not in result.message.lower()
        assert "sent" in result.message.lower()

    async def test_control_data_event_message_says_accepted_not_successfully(
        self, mock_simconnect
    ):
        """Review follow-up on the fix above: EVT_MCP_ALT_SET resolves to the
        control_data method, which writes straight to the PMDG SDK's own
        Control data area via send_control(). That write is a bare
        SetClientData with no return-code check of its own -- the same
        unearned "successfully" the rotor_brake fix above corrected, just on
        a different channel (pmdg.py's PmdgDataManager.send_control /
        pmdg_ng3.py's PmdgNG3DataManager.send_control, both live-verified
        against a never-created client data area: SetClientData-family
        calls DO raise a correlatable SIMCONNECT_EXCEPTION_OUT_OF_BOUNDS via
        the same GetLastSentPacketID mechanism tools/events.py already uses
        -- see pmdg_control_correlation_probe.py).

        send_control() now correlates its send IDs through the dispatcher's
        request registry exactly like tools/events.py's trigger_event. This
        pins the case where correlation is available and SimConnect raises
        no exception for it: the message may say the packet was accepted,
        never "successfully" -- accepting the packet is not the same as the
        aircraft having acted on it, which nothing here observes."""
        import threading

        from simconnect_mcp.tools.models import PmdgEventResult
        from simconnect_mcp.tools.pmdg import send_pmdg_event

        class MockRegistry:
            def __init__(self):
                self.pending_lock = threading.Lock()

            def register(self, req):
                pass

            def bind_send_id(self, req, send_id, _locked=False):
                req.done.set()  # no exception -- SimConnect accepted it

            def discard(self, req):
                pass

        mock_simconnect["sm"].registry = MockRegistry()

        result = await send_pmdg_event("EVT_MCP_ALT_SET", parameter=5000, variant="pmdg_777")

        assert isinstance(result, PmdgEventResult)
        assert "successfully" not in result.message.lower()
        assert "accepted" in result.message.lower()

    async def test_control_data_event_rejected_by_simconnect_is_an_error(
        self, mock_simconnect
    ):
        """The other half of correlation landing: SimConnect itself can
        reject the control-area write (live-confirmed possible -- see the
        docstring above), and that is a real, observed failure, not a softer
        success message. Mirrors tools/events.py's
        test_unknown_mapped_event_is_reported_not_faked, which treats a
        correlated exception as an actual error rather than degraded-but-ok
        text."""
        import threading

        from simconnect_mcp.tools.models import ToolError
        from simconnect_mcp.tools.pmdg import send_pmdg_event

        class MockRegistry:
            def __init__(self):
                self.pending_lock = threading.Lock()

            def register(self, req):
                pass

            def bind_send_id(self, req, send_id, _locked=False):
                req.exception = "SIMCONNECT_EXCEPTION_OUT_OF_BOUNDS"
                req.done.set()

            def discard(self, req):
                pass

        mock_simconnect["sm"].registry = MockRegistry()

        result = await send_pmdg_event("EVT_MCP_ALT_SET", parameter=5000, variant="pmdg_777")

        assert isinstance(result, ToolError)
        assert result.error == "PMDG_CONTROL_REJECTED"
        assert "SIMCONNECT_EXCEPTION_OUT_OF_BOUNDS" in result.message
        assert result.suggestion

    async def test_control_data_event_without_a_registry_is_not_confirmed(
        self, mock_simconnect
    ):
        """Plain SimConnect fallback (no dispatcher, no request registry):
        nothing here can tell accepted from rejected, so the message must
        say delivery is not confirmed -- not "successfully", and not
        "accepted" either, since that would claim a correlation that never
        ran. Mirrors tools/events.py's
        test_missing_registry_falls_back_without_crashing."""
        from simconnect_mcp.tools.models import PmdgEventResult
        from simconnect_mcp.tools.pmdg import send_pmdg_event

        if hasattr(mock_simconnect["sm"], "registry"):
            delattr(mock_simconnect["sm"], "registry")

        result = await send_pmdg_event("EVT_MCP_ALT_SET", parameter=5000, variant="pmdg_777")

        assert isinstance(result, PmdgEventResult)
        assert "successfully" not in result.message.lower()
        assert "not confirmed" in result.message.lower()


class TestUnassuredVariantWarning:
    """Direct coverage of _unassured_variant_warning's own branches --
    send_pmdg_event's real code paths can only exercise "name_match" (a
    "fallback" catalog, by construction, means the event name matched
    nothing in either catalog, which resolve_pmdg_event would ALSO fail to
    find first) -- but the helper is written to cover "fallback" too,
    defensively, so it is checked here independent of any specific caller.
    """

    def test_fallback_gets_a_warning_naming_the_assumed_catalog(self):
        from simconnect_mcp.tools.pmdg import _unassured_variant_warning

        warning = _unassured_variant_warning("pmdg_777", "fallback")

        assert warning is not None
        assert "pmdg_777" in warning

    def test_name_match_gets_a_warning_naming_the_assumed_catalog(self):
        from simconnect_mcp.tools.pmdg import _unassured_variant_warning

        warning = _unassured_variant_warning("pmdg_737", "name_match")

        assert warning is not None
        assert "pmdg_737" in warning

    @pytest.mark.parametrize("source", ["explicit", "detected", "probed", None])
    def test_confirmed_sources_get_no_warning(self, source):
        from simconnect_mcp.tools.pmdg import _unassured_variant_warning

        assert _unassured_variant_warning("pmdg_777", source) is None
