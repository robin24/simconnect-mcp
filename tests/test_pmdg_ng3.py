"""Tests for PMDG 737 NG3 ctypes struct definitions, manager, and catalog."""

from __future__ import annotations

import ctypes
import time

import pytest

from simconnect_mcp.pmdg_ng3 import (
    CDU_COLOR_AMBER,
    CDU_COLOR_CYAN,
    CDU_COLUMNS,
    CDU_FLAG_REVERSE,
    CDU_FLAG_SMALL_FONT,
    CDU_ROWS,
    PMDG_NG3_CDU_DEFINITIONS,
    PMDG_NG3_CDU_IDS,
    PMDG_NG3_CDU_NAMES,
    PMDG_NG3_DATA_NAME,
    THIRD_PARTY_EVENT_ID_MIN,
    CDU_Grid,
    CDU_Row,
    PMDG_NG3_CDU_Cell,
    PMDG_NG3_CDU_Screen,
    PMDG_NG3_DataStruct,
    PmdgNG3DataManager,
    render_cdu_grid,
    render_cdu_text,
    resolve_pmdg_event,
)

# ---------------------------------------------------------------------------
# CDU Cell struct
# ---------------------------------------------------------------------------

class TestCDUCell:
    def test_size_is_3_bytes(self):
        assert ctypes.sizeof(PMDG_NG3_CDU_Cell) == 3

    def test_fields_accessible(self):
        cell = PMDG_NG3_CDU_Cell()
        cell.Symbol = 65   # 'A'
        cell.Color = CDU_COLOR_CYAN
        cell.Flags = CDU_FLAG_SMALL_FONT
        assert cell.Symbol == 65
        assert cell.Color == CDU_COLOR_CYAN
        assert cell.Flags == CDU_FLAG_SMALL_FONT

    def test_pack_1(self):
        assert PMDG_NG3_CDU_Cell._pack_ == 1

    def test_field_order_in_memory(self):
        raw = bytes([0x41, 0x02, 0x01])
        cell = PMDG_NG3_CDU_Cell.from_buffer_copy(raw)
        assert cell.Symbol == 0x41
        assert cell.Color == 2
        assert cell.Flags == 1


# ---------------------------------------------------------------------------
# CDU dimensions and screen struct
# ---------------------------------------------------------------------------

class TestCDUDimensions:
    def test_constants(self):
        assert CDU_COLUMNS == 24
        assert CDU_ROWS == 14

    def test_cdu_row_length(self):
        assert ctypes.sizeof(CDU_Row) == 14 * 3

    def test_cdu_grid_length(self):
        assert ctypes.sizeof(CDU_Grid) == 24 * 14 * 3


class TestCDUScreen:
    def test_size(self):
        # 24 * 14 cells * 3 bytes + 1 byte Powered (alignment may pad)
        assert ctypes.sizeof(PMDG_NG3_CDU_Screen) >= 24 * 14 * 3 + 1

    def test_powered_default_false(self):
        screen = PMDG_NG3_CDU_Screen()
        assert screen.Powered is False

    def test_pack_1(self):
        assert PMDG_NG3_CDU_Screen._pack_ == 1


# ---------------------------------------------------------------------------
# CDU constants and IDs
# ---------------------------------------------------------------------------

class TestSDKConstants:
    def test_two_cdus_only(self):
        # NG3 has only two CDUs (Capt + F/O), unlike 777's three
        assert len(PMDG_NG3_CDU_NAMES) == 2
        assert len(PMDG_NG3_CDU_IDS) == 2
        assert len(PMDG_NG3_CDU_DEFINITIONS) == 2

    def test_cdu_names(self):
        assert PMDG_NG3_CDU_NAMES == ["PMDG_NG3_CDU_0", "PMDG_NG3_CDU_1"]

    def test_data_area_name(self):
        assert PMDG_NG3_DATA_NAME == "PMDG_NG3_Data"

    def test_event_base(self):
        assert THIRD_PARTY_EVENT_ID_MIN == 0x00011000


# ---------------------------------------------------------------------------
# CDU rendering
# ---------------------------------------------------------------------------

class TestCDURendering:
    def _make_screen(self, powered: bool = True) -> PMDG_NG3_CDU_Screen:
        screen = PMDG_NG3_CDU_Screen()
        screen.Powered = powered
        return screen

    def test_render_text_unpowered_returns_none(self):
        screen = self._make_screen(powered=False)
        assert render_cdu_text(screen) is None
        assert render_cdu_grid(screen) is None

    def test_render_text_powered_returns_14_rows(self):
        screen = self._make_screen()
        rows = render_cdu_text(screen)
        assert len(rows) == 14
        for row in rows:
            assert len(row) == 24

    def test_render_text_ascii_chars(self):
        screen = self._make_screen()
        # Put 'A' in cell (col=0, row=0)
        screen.Cells[0][0].Symbol = ord("A")
        rows = render_cdu_text(screen)
        assert rows[0][0] == "A"

    def test_render_text_left_right_arrows(self):
        screen = self._make_screen()
        screen.Cells[0][0].Symbol = 0xA1  # left arrow
        screen.Cells[1][0].Symbol = 0xA2  # right arrow
        rows = render_cdu_text(screen)
        assert rows[0][0] == "←"
        assert rows[0][1] == "→"

    def test_render_text_up_down_arrows(self):
        # NG3-specific: up (0xA3) and down (0xA4) arrows
        screen = self._make_screen()
        screen.Cells[0][0].Symbol = 0xA3
        screen.Cells[1][0].Symbol = 0xA4
        rows = render_cdu_text(screen)
        assert rows[0][0] == "↑"
        assert rows[0][1] == "↓"

    def test_render_grid_includes_color_and_flags(self):
        screen = self._make_screen()
        screen.Cells[0][0].Symbol = ord("X")
        screen.Cells[0][0].Color = CDU_COLOR_AMBER
        screen.Cells[0][0].Flags = CDU_FLAG_SMALL_FONT | CDU_FLAG_REVERSE
        grid = render_cdu_grid(screen)
        cell = grid[0][0]
        assert cell["char"] == "X"
        assert cell["color"] == "amber"
        assert cell["small"] is True
        assert cell["reverse"] is True


# ---------------------------------------------------------------------------
# Data struct
# ---------------------------------------------------------------------------

class TestDataStruct:
    def test_has_many_fields(self):
        # Sanity check we have several hundred fields (SDK has ~365)
        assert len(PMDG_NG3_DataStruct._fields_) > 300

    def test_well_known_fields_present(self):
        field_names = {f[0] for f in PMDG_NG3_DataStruct._fields_}
        # 737-specific fields
        assert "ELEC_BatSelector" in field_names
        assert "FCTL_YawDamper_Sw" in field_names
        assert "MCP_IASMach" in field_names
        assert "MAIN_GearLever" in field_names
        assert "FMC_V1" in field_names
        assert "ENG_StartSelector" in field_names

    def test_array_fields_have_size(self):
        # ENG_StartSelector is uint8[2] per the NG3 SDK
        for name, typ in PMDG_NG3_DataStruct._fields_:
            if name == "ENG_StartSelector":
                assert typ._length_ == 2
                break
        else:
            pytest.fail("ENG_StartSelector not found")

    def test_struct_can_be_instantiated(self):
        s = PMDG_NG3_DataStruct()
        # Set and read back
        s.ELEC_BatSelector = 1
        assert s.ELEC_BatSelector == 1
        s.MCP_Heading = 270
        assert s.MCP_Heading == 270

    def test_struct_roundtrips_via_bytes(self):
        s = PMDG_NG3_DataStruct()
        s.MCP_IASMach = 250.0
        s.MAIN_GearLever = 2
        raw = bytes(s)
        s2 = PMDG_NG3_DataStruct.from_buffer_copy(raw)
        assert s2.MCP_IASMach == pytest.approx(250.0)
        assert s2.MAIN_GearLever == 2


# ---------------------------------------------------------------------------
# Data manager
# ---------------------------------------------------------------------------

class TestPmdgNG3DataManager:
    def test_init_sets_empty_state(self):
        mgr = PmdgNG3DataManager(sm=None)
        assert mgr.data_subscribed is False
        # NG3 has 2 CDUs, not 3
        assert mgr.cdu_subscribed == [False, False]
        assert mgr._data_struct is None

    def test_read_field_bool(self):
        mgr = PmdgNG3DataManager(sm=None)
        mgr._data_struct = PMDG_NG3_DataStruct()
        mgr._data_struct.FCTL_YawDamper_Sw = True
        mgr._data_timestamp = time.time()
        mgr.data_subscribed = True
        assert mgr.read_field("FCTL_YawDamper_Sw") is True

    def test_read_field_array(self):
        mgr = PmdgNG3DataManager(sm=None)
        mgr._data_struct = PMDG_NG3_DataStruct()
        mgr._data_struct.ENG_StartSelector[0] = 1
        mgr._data_struct.ENG_StartSelector[1] = 2
        mgr._data_timestamp = time.time()
        mgr.data_subscribed = True
        assert mgr.read_field("ENG_StartSelector", index=0) == 1
        assert mgr.read_field("ENG_StartSelector", index=1) == 2

    def test_read_field_float(self):
        mgr = PmdgNG3DataManager(sm=None)
        mgr._data_struct = PMDG_NG3_DataStruct()
        mgr._data_struct.MCP_IASMach = 280.0
        mgr._data_timestamp = time.time()
        mgr.data_subscribed = True
        assert mgr.read_field("MCP_IASMach") == pytest.approx(280.0)

    def test_read_field_unknown_raises(self):
        mgr = PmdgNG3DataManager(sm=None)
        mgr._data_struct = PMDG_NG3_DataStruct()
        mgr._data_subscribed = True
        with pytest.raises(ValueError, match="Unknown field"):
            mgr.read_field("NONEXISTENT_FIELD")

    def test_read_field_not_subscribed_returns_none(self):
        mgr = PmdgNG3DataManager(sm=None)
        assert mgr.read_field("FCTL_YawDamper_Sw") is None

    def test_read_cdu_not_subscribed_returns_none(self):
        mgr = PmdgNG3DataManager(sm=None)
        assert mgr.read_cdu(0) is None
        assert mgr.read_cdu(1) is None

    def test_read_cdu_invalid_index_returns_none(self):
        mgr = PmdgNG3DataManager(sm=None)
        # NG3 only has 2 CDUs — index 2 must not raise
        assert mgr.read_cdu(2) is None

    def test_subscribe_cdu_rejects_invalid_index(self):
        mgr = PmdgNG3DataManager(sm=None)
        with pytest.raises(ValueError, match="only 2 CDUs"):
            mgr.subscribe_cdu(2)

    def test_data_age_infinity_when_no_data(self):
        mgr = PmdgNG3DataManager(sm=None)
        assert mgr.data_age == float("inf")

    def test_data_age_recent(self):
        mgr = PmdgNG3DataManager(sm=None)
        mgr._data_timestamp = time.time()
        assert mgr.data_age < 1.0

    def test_cleanup_resets_state(self):
        mgr = PmdgNG3DataManager(sm=None)
        mgr._data_struct = PMDG_NG3_DataStruct()
        mgr.data_subscribed = True
        mgr.cdu_subscribed = [True, True]
        mgr.cleanup()
        assert mgr._data_struct is None
        assert mgr.data_subscribed is False
        assert mgr.cdu_subscribed == [False, False]


# ---------------------------------------------------------------------------
# Event resolution against the catalog
# ---------------------------------------------------------------------------

class TestEventResolution:
    def test_resolve_panel_event(self):
        # EVT_OH_ELEC_BATTERY_SWITCH is at offset 1 in both 777 and NG3.
        result = resolve_pmdg_event("EVT_OH_ELEC_BATTERY_SWITCH")
        assert result["method"] == "rotor_brake"
        assert result["code"] == "101 (>K:ROTOR_BRAKE)"

    def test_resolve_panel_event_with_parameter(self):
        result = resolve_pmdg_event("EVT_OH_ELEC_BATTERY_SWITCH", parameter=1)
        assert result["method"] == "rotor_brake"
        assert result["code"] == "101 1 (>K:ROTOR_BRAKE)"

    def test_resolve_cdu_event(self):
        # NG3 EVT_CDU_L_L1 is at offset 534 → 534*100 + 1 = 53401
        result = resolve_pmdg_event("EVT_CDU_L_L1")
        assert result["method"] == "rotor_brake"
        assert result["code"] == "53401 (>K:ROTOR_BRAKE)"

    def test_resolve_direct_set_mcp_alt(self):
        result = resolve_pmdg_event("EVT_MCP_ALT_SET", parameter=5000)
        assert result["method"] == "control_data"
        assert result["event_id"] == 69632 + 14505
        assert result["parameter"] == 5000

    def test_resolve_direct_set_pressurization(self):
        # NG3-specific: EVT_OH_PRESS_FLT_ALT_SET (offset 14507)
        result = resolve_pmdg_event("EVT_OH_PRESS_FLT_ALT_SET", parameter=12000)
        assert result["method"] == "control_data"
        assert result["event_id"] == 69632 + 14507
        assert result["parameter"] == 12000

    def test_resolve_unknown_event_raises(self):
        with pytest.raises(ValueError, match="not found in PMDG 737 catalog"):
            resolve_pmdg_event("EVT_NONEXISTENT_NG3")


# ---------------------------------------------------------------------------
# Catalog detection
# ---------------------------------------------------------------------------

class TestCatalogDetection:
    def test_catalog_loads(self):
        from simconnect_mcp.data.catalog import get_catalog
        cat = get_catalog("pmdg_737")
        assert cat is not None
        assert cat["aircraft"] == "PMDG 737"
        assert cat["title_pattern"] == "PMDG 737"

    def test_catalog_has_sdk_info(self):
        from simconnect_mcp.data.catalog import get_catalog
        cat = get_catalog("pmdg_737")
        info = cat["sdk_info"]
        assert info["data_area"] == "PMDG_NG3_Data"
        assert info["control_area"] == "PMDG_NG3_Control"
        assert info["cdu_areas"] == ["PMDG_NG3_CDU_0", "PMDG_NG3_CDU_1"]
        assert info["event_base"] == 0x00011000

    def test_title_pattern_detects_catalog(self):
        from simconnect_mcp.data.catalog import detect_catalog
        assert detect_catalog("PMDG 737-800 NG3") == "pmdg_737"
        assert detect_catalog("Boeing PMDG 737 BBJ") == "pmdg_737"

    def test_777_and_ng3_catalogs_dont_collide(self):
        from simconnect_mcp.data.catalog import detect_catalog
        assert detect_catalog("PMDG 777-300ER") == "pmdg_777"
        assert detect_catalog("PMDG 737-800") == "pmdg_737"
