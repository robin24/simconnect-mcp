"""PMDG 737 NG3 SimConnect Client Data Area structs and constants.

Defines ctypes Structures that mirror the binary layout of the PMDG 737 NG3 SDK
C structs so that raw bytes received from SimConnect can be parsed directly
into Python values via ``from_buffer_copy``.

Binary compatibility with the PMDG SDK is critical — field order and types
must match the C header exactly.

References
----------
- PMDG_NG3_SDK.h (shipped with the PMDG 737 add-on)
- Koseng/MSFSPythonSimConnectMobiFlightExtension (MobiFlight WASM bridge)
"""

from __future__ import annotations

import ctypes
import logging
import time

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SimConnect Client Data Area identifiers
# ---------------------------------------------------------------------------

PMDG_NG3_DATA_NAME = "PMDG_NG3_Data"
PMDG_NG3_DATA_ID = 0x4E473331
PMDG_NG3_DATA_DEFINITION = 0x4E473332
PMDG_NG3_CONTROL_NAME = "PMDG_NG3_Control"
PMDG_NG3_CONTROL_ID = 0x4E473333
PMDG_NG3_CONTROL_DEFINITION = 0x4E473334

PMDG_NG3_CDU_NAMES = ["PMDG_NG3_CDU_0", "PMDG_NG3_CDU_1"]
PMDG_NG3_CDU_IDS = [0x4E473335, 0x4E473336]
PMDG_NG3_CDU_DEFINITIONS = [0x4E473338, 0x4E473339]

# ---------------------------------------------------------------------------
# CDU dimensions and colour/flag constants
# ---------------------------------------------------------------------------

CDU_COLUMNS = 24
CDU_ROWS = 14

CDU_COLOR_WHITE = 0
CDU_COLOR_CYAN = 1
CDU_COLOR_GREEN = 2
CDU_COLOR_MAGENTA = 3
CDU_COLOR_AMBER = 4
CDU_COLOR_RED = 5

CDU_COLOR_NAMES = {
    0: "white",
    1: "cyan",
    2: "green",
    3: "magenta",
    4: "amber",
    5: "red",
}

CDU_FLAG_SMALL_FONT = 0x01
CDU_FLAG_REVERSE = 0x02
CDU_FLAG_UNUSED = 0x04

# ---------------------------------------------------------------------------
# Miscellaneous constants
# ---------------------------------------------------------------------------

THIRD_PARTY_EVENT_ID_MIN = 0x00011000
ROTOR_BRAKE_OFFSET = 100

# ---------------------------------------------------------------------------
# CDU structs
# ---------------------------------------------------------------------------


class PMDG_NG3_CDU_Cell(ctypes.Structure):
    """A single NG3 CDU character cell — 3 bytes, packed.

    The NG3 SDK adds up/down arrow symbols (0xA3, 0xA4) over the 777 alphabet.
    """

    _pack_ = 1
    _fields_ = [
        ("Symbol", ctypes.c_ubyte),
        ("Color", ctypes.c_ubyte),
        ("Flags", ctypes.c_ubyte),
    ]


# CDU_Row  : 14 cells  (one column of cells, indexed by row)
# CDU_Grid : 24 such rows (indexed by column first, then row)
CDU_Row = PMDG_NG3_CDU_Cell * CDU_ROWS      # 14 cells per column
CDU_Grid = CDU_Row * CDU_COLUMNS             # 24 columns


class PMDG_NG3_CDU_Screen(ctypes.Structure):
    """Full NG3 CDU screen — 24 × 14 grid of cells plus a Powered flag."""

    _pack_ = 1
    _fields_ = [
        ("Cells", CDU_Grid),
        ("Powered", ctypes.c_bool),
    ]


# ---------------------------------------------------------------------------
# CDU rendering helpers
# ---------------------------------------------------------------------------


def render_cdu_text(screen: PMDG_NG3_CDU_Screen) -> list[str] | None:
    """Render NG3 CDU screen as 14 plain-text row strings (24 chars each).

    Returns None if the CDU is not powered.
    """
    if not screen.Powered:
        return None

    rows = []
    for row in range(CDU_ROWS):
        chars = []
        for col in range(CDU_COLUMNS):
            symbol = screen.Cells[col][row].Symbol
            if symbol == 0xA1:
                chars.append("←")  # left arrow
            elif symbol == 0xA2:
                chars.append("→")  # right arrow
            elif symbol == 0xA3:
                chars.append("↑")  # up arrow (NG3-specific)
            elif symbol == 0xA4:
                chars.append("↓")  # down arrow (NG3-specific)
            elif 0x20 <= symbol <= 0x7E:
                chars.append(chr(symbol))
            else:
                chars.append(" ")
        rows.append("".join(chars))
    return rows


def render_cdu_grid(screen: PMDG_NG3_CDU_Screen) -> list[list[dict]] | None:
    """Render NG3 CDU screen as structured grid with per-cell color and flags."""
    if not screen.Powered:
        return None

    grid = []
    for row in range(CDU_ROWS):
        row_cells = []
        for col in range(CDU_COLUMNS):
            cell = screen.Cells[col][row]
            symbol = cell.Symbol
            if symbol == 0xA1:
                char = "←"
            elif symbol == 0xA2:
                char = "→"
            elif symbol == 0xA3:
                char = "↑"
            elif symbol == 0xA4:
                char = "↓"
            elif 0x20 <= symbol <= 0x7E:
                char = chr(symbol)
            else:
                char = " "

            row_cells.append({
                "char": char,
                "color": CDU_COLOR_NAMES.get(cell.Color, "white"),
                "small": bool(cell.Flags & CDU_FLAG_SMALL_FONT),
                "reverse": bool(cell.Flags & CDU_FLAG_REVERSE),
                "dim": bool(cell.Flags & CDU_FLAG_UNUSED),
            })
        grid.append(row_cells)
    return grid


# ---------------------------------------------------------------------------
# Event resolution
# ---------------------------------------------------------------------------


def _is_direct_set_event(offset: int) -> bool:
    """Check if an event is a direct-set event that needs the Control data area.

    Direct-set events (e.g., EVT_MCP_ALT_SET, EVT_OH_PRESS_FLT_ALT_SET) take a
    value parameter and must be sent via the PMDG_NG3_Control data area, not
    via ROTOR_BRAKE. These are the MCP/pressurization direct-control events
    at offsets 14500+.
    """
    return offset >= 14500


def resolve_pmdg_event(event_name: str, parameter: int | None = None) -> dict:
    """Resolve a PMDG 737 NG3 event name to dispatch information.

    Returns a dict with dispatch method and parameters:
    - ``{"method": "rotor_brake", "code": "101 (>K:ROTOR_BRAKE)"}``
    - ``{"method": "control_data", "event_id": 84137, "parameter": 5000}``

    The ROTOR_BRAKE parameter formula is ``offset * 100 + 1`` (for left click).
    This works for all standard cockpit events. Direct-set events (MCP and
    pressurization value setters) must use the PMDG_NG3_Control data area.
    """
    from simconnect_mcp.data.catalog import get_catalog

    catalog = get_catalog("pmdg_737")
    if catalog is None:
        raise ValueError("PMDG 737 catalog not loaded")

    for var in catalog["variables"]:
        events = var.get("events", [])
        for evt in events:
            if evt["name"] == event_name:
                event_id = evt["id"]
                offset = event_id - THIRD_PARTY_EVENT_ID_MIN

                if _is_direct_set_event(offset):
                    return {
                        "method": "control_data",
                        "event_id": event_id,
                        "parameter": parameter or 0,
                    }

                rotor_param = offset * 100 + 1
                if parameter is not None:
                    return {
                        "method": "rotor_brake",
                        "code": f"{rotor_param} {parameter} (>K:ROTOR_BRAKE)",
                    }
                return {
                    "method": "rotor_brake",
                    "code": f"{rotor_param} (>K:ROTOR_BRAKE)",
                }

    raise ValueError(
        f"Event '{event_name}' not found in PMDG 737 catalog. "
        "Use search_lvars to find available events."
    )


# ---------------------------------------------------------------------------
# Main data struct
# ---------------------------------------------------------------------------


class PMDG_NG3_DataStruct(ctypes.Structure):
    """Binary-compatible mirror of PMDG_NG3_Data from PMDG_NG3_SDK.h.

    Every field is in the exact order and type required by the SDK so that a
    raw byte buffer received from SimConnect can be parsed with
    ``PMDG_NG3_DataStruct.from_buffer_copy(raw_bytes)``.

    Uses native alignment (no ``_pack_``) to match the default MSVC packing
    used by the PMDG SDK.
    """
    _fields_ = [
        # Aft overhead — ADIRU
        ("IRS_DisplaySelector", ctypes.c_ubyte),
        ("IRS_SysDisplay_R", ctypes.c_bool),
        ("IRS_annunGPS", ctypes.c_bool),
        ("IRS_annunALIGN", ctypes.c_bool * 2),
        ("IRS_annunON_DC", ctypes.c_bool * 2),
        ("IRS_annunFAULT", ctypes.c_bool * 2),
        ("IRS_annunDC_FAIL", ctypes.c_bool * 2),
        ("IRS_ModeSelector", ctypes.c_ubyte * 2),
        ("IRS_aligned", ctypes.c_bool),
        ("IRS_DisplayLeft", ctypes.c_char * 7),
        ("IRS_DisplayRight", ctypes.c_char * 8),
        ("IRS_DisplayShowsDots", ctypes.c_bool),
        # AFS
        ("AFS_AutothrottleServosConnected", ctypes.c_bool),
        ("AFS_ControlsPitch", ctypes.c_bool),
        ("AFS_ControlsRoll", ctypes.c_bool),
        # PSEU
        ("WARN_annunPSEU", ctypes.c_bool),
        # Service Interphone
        ("COMM_ServiceInterphoneSw", ctypes.c_bool),
        # Lights
        ("LTS_DomeWhiteSw", ctypes.c_ubyte),
        # Engine
        ("ENG_EECSwitch", ctypes.c_bool * 2),
        ("ENG_annunREVERSER", ctypes.c_bool * 2),
        ("ENG_annunENGINE_CONTROL", ctypes.c_bool * 2),
        ("ENG_annunALTN", ctypes.c_bool * 2),
        ("ENG_StartValve", ctypes.c_bool * 2),
        # Oxygen
        ("OXY_Needle", ctypes.c_ubyte),
        ("OXY_SwNormal", ctypes.c_bool),
        ("OXY_annunPASS_OXY_ON", ctypes.c_bool),
        # Gear
        ("GEAR_annunOvhdLEFT", ctypes.c_bool),
        ("GEAR_annunOvhdNOSE", ctypes.c_bool),
        ("GEAR_annunOvhdRIGHT", ctypes.c_bool),
        # Flight recorder / CVR
        ("FLTREC_SwNormal", ctypes.c_bool),
        ("FLTREC_annunOFF", ctypes.c_bool),
        ("CVR_annunTEST", ctypes.c_bool),
        # Forward overhead — Flight Controls
        ("FCTL_FltControl_Sw", ctypes.c_ubyte * 2),
        ("FCTL_Spoiler_Sw", ctypes.c_bool * 2),
        ("FCTL_YawDamper_Sw", ctypes.c_bool),
        ("FCTL_AltnFlaps_Sw_ARM", ctypes.c_bool),
        ("FCTL_AltnFlaps_Control_Sw", ctypes.c_ubyte),
        ("FCTL_annunFC_LOW_PRESSURE", ctypes.c_bool * 2),
        ("FCTL_annunYAW_DAMPER", ctypes.c_bool),
        ("FCTL_annunLOW_QUANTITY", ctypes.c_bool),
        ("FCTL_annunLOW_PRESSURE", ctypes.c_bool),
        ("FCTL_annunLOW_STBY_RUD_ON", ctypes.c_bool),
        ("FCTL_annunFEEL_DIFF_PRESS", ctypes.c_bool),
        ("FCTL_annunSPEED_TRIM_FAIL", ctypes.c_bool),
        ("FCTL_annunMACH_TRIM_FAIL", ctypes.c_bool),
        ("FCTL_annunAUTO_SLAT_FAIL", ctypes.c_bool),
        # Navigation/Displays
        ("NAVDIS_VHFNavSelector", ctypes.c_ubyte),
        ("NAVDIS_IRSSelector", ctypes.c_ubyte),
        ("NAVDIS_FMCSelector", ctypes.c_ubyte),
        ("NAVDIS_SourceSelector", ctypes.c_ubyte),
        ("NAVDIS_ControlPaneSelector", ctypes.c_ubyte),
        ("ADF_StandbyFrequency", ctypes.c_uint),
        # Fuel
        ("FUEL_FuelTempNeedle", ctypes.c_float),
        ("FUEL_CrossFeedSw", ctypes.c_bool),
        ("FUEL_PumpFwdSw", ctypes.c_bool * 2),
        ("FUEL_PumpAftSw", ctypes.c_bool * 2),
        ("FUEL_PumpCtrSw", ctypes.c_bool * 2),
        ("FUEL_AuxFwd", ctypes.c_bool * 2),
        ("FUEL_AuxAft", ctypes.c_bool * 2),
        ("FUEL_FWDBleed", ctypes.c_bool),
        ("FUEL_AFTBleed", ctypes.c_bool),
        ("FUEL_GNDXfr", ctypes.c_bool),
        ("FUEL_annunENG_VALVE_CLOSED", ctypes.c_ubyte * 2),
        ("FUEL_annunSPAR_VALVE_CLOSED", ctypes.c_ubyte * 2),
        ("FUEL_annunFILTER_BYPASS", ctypes.c_bool * 2),
        ("FUEL_annunXFEED_VALVE_OPEN", ctypes.c_ubyte),
        ("FUEL_annunLOWPRESS_Fwd", ctypes.c_bool * 2),
        ("FUEL_annunLOWPRESS_Aft", ctypes.c_bool * 2),
        ("FUEL_annunLOWPRESS_Ctr", ctypes.c_bool * 2),
        ("FUEL_QtyCenter", ctypes.c_float),
        ("FUEL_QtyLeft", ctypes.c_float),
        ("FUEL_QtyRight", ctypes.c_float),
        # Electrical
        ("ELEC_annunBAT_DISCHARGE", ctypes.c_bool),
        ("ELEC_annunTR_UNIT", ctypes.c_bool),
        ("ELEC_annunELEC", ctypes.c_bool),
        ("ELEC_DCMeterSelector", ctypes.c_ubyte),
        ("ELEC_ACMeterSelector", ctypes.c_ubyte),
        ("ELEC_BatSelector", ctypes.c_ubyte),
        ("ELEC_CabUtilSw", ctypes.c_bool),
        ("ELEC_IFEPassSeatSw", ctypes.c_bool),
        ("ELEC_annunDRIVE", ctypes.c_bool * 2),
        ("ELEC_annunSTANDBY_POWER_OFF", ctypes.c_bool),
        ("ELEC_IDGDisconnectSw", ctypes.c_bool * 2),
        ("ELEC_StandbyPowerSelector", ctypes.c_ubyte),
        ("ELEC_annunGRD_POWER_AVAILABLE", ctypes.c_bool),
        ("ELEC_GrdPwrSw", ctypes.c_bool),
        ("ELEC_BusTransSw_AUTO", ctypes.c_bool),
        ("ELEC_GenSw", ctypes.c_bool * 2),
        ("ELEC_APUGenSw", ctypes.c_bool * 2),
        ("ELEC_annunTRANSFER_BUS_OFF", ctypes.c_bool * 2),
        ("ELEC_annunSOURCE_OFF", ctypes.c_bool * 2),
        ("ELEC_annunGEN_BUS_OFF", ctypes.c_bool * 2),
        ("ELEC_annunAPU_GEN_OFF_BUS", ctypes.c_bool),
        ("ELEC_MeterDisplayTop", ctypes.c_char * 13),
        ("ELEC_MeterDisplayBottom", ctypes.c_char * 13),
        ("ELEC_BusPowered", ctypes.c_bool * 16),
        # APU
        ("APU_EGTNeedle", ctypes.c_float),
        ("APU_annunMAINT", ctypes.c_bool),
        ("APU_annunLOW_OIL_PRESSURE", ctypes.c_bool),
        ("APU_annunFAULT", ctypes.c_bool),
        ("APU_annunOVERSPEED", ctypes.c_bool),
        # Wipers
        ("OH_WiperLSelector", ctypes.c_ubyte),
        ("OH_WiperRSelector", ctypes.c_ubyte),
        # Center overhead
        ("LTS_CircuitBreakerKnob", ctypes.c_ubyte),
        ("LTS_OvereadPanelKnob", ctypes.c_ubyte),
        ("AIR_EquipCoolingSupplyNORM", ctypes.c_bool),
        ("AIR_EquipCoolingExhaustNORM", ctypes.c_bool),
        ("AIR_annunEquipCoolingSupplyOFF", ctypes.c_bool),
        ("AIR_annunEquipCoolingExhaustOFF", ctypes.c_bool),
        ("LTS_annunEmerNOT_ARMED", ctypes.c_bool),
        ("LTS_EmerExitSelector", ctypes.c_ubyte),
        ("COMM_NoSmokingSelector", ctypes.c_ubyte),
        ("COMM_FastenBeltsSelector", ctypes.c_ubyte),
        ("COMM_annunCALL", ctypes.c_bool),
        ("COMM_annunPA_IN_USE", ctypes.c_bool),
        # Anti-ice
        ("ICE_annunOVERHEAT", ctypes.c_bool * 4),
        ("ICE_annunON", ctypes.c_bool * 4),
        ("ICE_WindowHeatSw", ctypes.c_bool * 4),
        ("ICE_annunCAPT_PITOT", ctypes.c_bool),
        ("ICE_annunL_ELEV_PITOT", ctypes.c_bool),
        ("ICE_annunL_ALPHA_VANE", ctypes.c_bool),
        ("ICE_annunL_TEMP_PROBE", ctypes.c_bool),
        ("ICE_annunFO_PITOT", ctypes.c_bool),
        ("ICE_annunR_ELEV_PITOT", ctypes.c_bool),
        ("ICE_annunR_ALPHA_VANE", ctypes.c_bool),
        ("ICE_annunAUX_PITOT", ctypes.c_bool),
        ("ICE_ProbeHeatSw", ctypes.c_bool * 2),
        ("ICE_annunVALVE_OPEN", ctypes.c_bool * 2),
        ("ICE_annunCOWL_ANTI_ICE", ctypes.c_bool * 2),
        ("ICE_annunCOWL_VALVE_OPEN", ctypes.c_bool * 2),
        ("ICE_WingAntiIceSw", ctypes.c_bool),
        ("ICE_EngAntiIceSw", ctypes.c_bool * 2),
        ("ICE_WindowHeatTestSw", ctypes.c_int),
        # Hydraulics
        ("HYD_annunLOW_PRESS_eng", ctypes.c_bool * 2),
        ("HYD_annunLOW_PRESS_elec", ctypes.c_bool * 2),
        ("HYD_annunOVERHEAT_elec", ctypes.c_bool * 2),
        ("HYD_PumpSw_eng", ctypes.c_bool * 2),
        ("HYD_PumpSw_elec", ctypes.c_bool * 2),
        # Air systems
        ("AIR_TempSourceSelector", ctypes.c_ubyte),
        ("AIR_TrimAirSwitch", ctypes.c_bool),
        ("AIR_annunZoneTemp", ctypes.c_bool * 3),
        ("AIR_annunDualBleed", ctypes.c_bool),
        ("AIR_annunRamDoorL", ctypes.c_bool),
        ("AIR_annunRamDoorR", ctypes.c_bool),
        ("AIR_RecircFanSwitch", ctypes.c_bool * 2),
        ("AIR_PackSwitch", ctypes.c_ubyte * 2),
        ("AIR_BleedAirSwitch", ctypes.c_bool * 2),
        ("AIR_APUBleedAirSwitch", ctypes.c_bool),
        ("AIR_IsolationValveSwitch", ctypes.c_ubyte),
        ("AIR_annunPackTripOff", ctypes.c_bool * 2),
        ("AIR_annunWingBodyOverheat", ctypes.c_bool * 2),
        ("AIR_annunBleedTripOff", ctypes.c_bool * 2),
        ("AIR_annunAUTO_FAIL", ctypes.c_bool),
        ("AIR_annunOFFSCHED_DESCENT", ctypes.c_bool),
        ("AIR_annunALTN", ctypes.c_bool),
        ("AIR_annunMANUAL", ctypes.c_bool),
        ("AIR_DuctPress", ctypes.c_float * 2),
        ("AIR_DuctPressNeedle", ctypes.c_float * 2),
        ("AIR_CabinAltNeedle", ctypes.c_float),
        ("AIR_CabinDPNeedle", ctypes.c_float),
        ("AIR_CabinVSNeedle", ctypes.c_float),
        ("AIR_CabinValveNeedle", ctypes.c_float),
        ("AIR_TemperatureNeedle", ctypes.c_float),
        ("AIR_DisplayFltAlt", ctypes.c_char * 6),
        ("AIR_DisplayLandAlt", ctypes.c_char * 6),
        # Doors
        ("DOOR_annunFWD_ENTRY", ctypes.c_bool),
        ("DOOR_annunFWD_SERVICE", ctypes.c_bool),
        ("DOOR_annunAIRSTAIR", ctypes.c_bool),
        ("DOOR_annunLEFT_FWD_OVERWING", ctypes.c_bool),
        ("DOOR_annunRIGHT_FWD_OVERWING", ctypes.c_bool),
        ("DOOR_annunFWD_CARGO", ctypes.c_bool),
        ("DOOR_annunEQUIP", ctypes.c_bool),
        ("DOOR_annunLEFT_AFT_OVERWING", ctypes.c_bool),
        ("DOOR_annunRIGHT_AFT_OVERWING", ctypes.c_bool),
        ("DOOR_annunAFT_CARGO", ctypes.c_bool),
        ("DOOR_annunAFT_ENTRY", ctypes.c_bool),
        ("DOOR_annunAFT_SERVICE", ctypes.c_bool),
        # Pressurization (obsolete-FltAlt/LandAlt are still in struct order)
        ("AIR_FltAltWindow", ctypes.c_uint),
        ("AIR_LandAltWindow", ctypes.c_uint),
        ("AIR_OutflowValveSwitch", ctypes.c_uint),
        ("AIR_PressurizationModeSelector", ctypes.c_uint),
        # Bottom overhead
        ("LTS_LandingLtRetractableSw", ctypes.c_ubyte * 2),
        ("LTS_LandingLtFixedSw", ctypes.c_bool * 2),
        ("LTS_RunwayTurnoffSw", ctypes.c_bool * 2),
        ("LTS_TaxiSw", ctypes.c_bool),
        ("APU_Selector", ctypes.c_ubyte),
        ("ENG_StartSelector", ctypes.c_ubyte * 2),
        ("ENG_IgnitionSelector", ctypes.c_ubyte),
        ("LTS_LogoSw", ctypes.c_bool),
        ("LTS_PositionSw", ctypes.c_ubyte),
        ("LTS_AntiCollisionSw", ctypes.c_bool),
        ("LTS_WingSw", ctypes.c_bool),
        ("LTS_WheelWellSw", ctypes.c_bool),
        # Glareshield — Warnings
        ("WARN_annunFIRE_WARN", ctypes.c_bool * 2),
        ("WARN_annunMASTER_CAUTION", ctypes.c_bool * 2),
        ("WARN_annunFLT_CONT", ctypes.c_bool),
        ("WARN_annunIRS", ctypes.c_bool),
        ("WARN_annunFUEL", ctypes.c_bool),
        ("WARN_annunELEC", ctypes.c_bool),
        ("WARN_annunAPU", ctypes.c_bool),
        ("WARN_annunOVHT_DET", ctypes.c_bool),
        ("WARN_annunANTI_ICE", ctypes.c_bool),
        ("WARN_annunHYD", ctypes.c_bool),
        ("WARN_annunDOORS", ctypes.c_bool),
        ("WARN_annunENG", ctypes.c_bool),
        ("WARN_annunOVERHEAD", ctypes.c_bool),
        ("WARN_annunAIR_COND", ctypes.c_bool),
        # EFIS control panels
        ("EFIS_MinsSelBARO", ctypes.c_bool * 2),
        ("EFIS_BaroSelHPA", ctypes.c_bool * 2),
        ("EFIS_VORADFSel1", ctypes.c_ubyte * 2),
        ("EFIS_VORADFSel2", ctypes.c_ubyte * 2),
        ("EFIS_ModeSel", ctypes.c_ubyte * 2),
        ("EFIS_RangeSel", ctypes.c_ubyte * 2),
        # Mode control panel
        ("MCP_Course", ctypes.c_ushort * 2),
        ("MCP_IASMach", ctypes.c_float),
        ("MCP_IASBlank", ctypes.c_bool),
        ("MCP_IASOverspeedFlash", ctypes.c_bool),
        ("MCP_IASUnderspeedFlash", ctypes.c_bool),
        ("MCP_Heading", ctypes.c_ushort),
        ("MCP_Altitude", ctypes.c_ushort),
        ("MCP_VertSpeed", ctypes.c_short),
        ("MCP_VertSpeedBlank", ctypes.c_bool),
        ("MCP_FDSw", ctypes.c_bool * 2),
        ("MCP_ATArmSw", ctypes.c_bool),
        ("MCP_BankLimitSel", ctypes.c_ubyte),
        ("MCP_DisengageBar", ctypes.c_bool),
        ("MCP_annunFD", ctypes.c_bool * 2),
        ("MCP_annunATArm", ctypes.c_bool),
        ("MCP_annunN1", ctypes.c_bool),
        ("MCP_annunSPEED", ctypes.c_bool),
        ("MCP_annunVNAV", ctypes.c_bool),
        ("MCP_annunLVL_CHG", ctypes.c_bool),
        ("MCP_annunHDG_SEL", ctypes.c_bool),
        ("MCP_annunLNAV", ctypes.c_bool),
        ("MCP_annunVOR_LOC", ctypes.c_bool),
        ("MCP_annunAPP", ctypes.c_bool),
        ("MCP_annunALT_HOLD", ctypes.c_bool),
        ("MCP_annunVS", ctypes.c_bool),
        ("MCP_annunCMD_A", ctypes.c_bool),
        ("MCP_annunCWS_A", ctypes.c_bool),
        ("MCP_annunCMD_B", ctypes.c_bool),
        ("MCP_annunCWS_B", ctypes.c_bool),
        ("MCP_indication_powered", ctypes.c_bool),
        # Forward panel
        ("MAIN_NoseWheelSteeringSwNORM", ctypes.c_bool),
        ("MAIN_annunBELOW_GS", ctypes.c_bool * 2),
        ("MAIN_MainPanelDUSel", ctypes.c_ubyte * 2),
        ("MAIN_LowerDUSel", ctypes.c_ubyte * 2),
        ("MAIN_annunAP", ctypes.c_bool * 2),
        ("MAIN_annunAP_Amber", ctypes.c_bool * 2),
        ("MAIN_annunAT", ctypes.c_bool * 2),
        ("MAIN_annunAT_Amber", ctypes.c_bool * 2),
        ("MAIN_annunFMC", ctypes.c_bool * 2),
        ("MAIN_DisengageTestSelector", ctypes.c_ubyte * 2),
        ("MAIN_annunSPEEDBRAKE_ARMED", ctypes.c_bool),
        ("MAIN_annunSPEEDBRAKE_DO_NOT_ARM", ctypes.c_bool),
        ("MAIN_annunSPEEDBRAKE_EXTENDED", ctypes.c_bool),
        ("MAIN_annunSTAB_OUT_OF_TRIM", ctypes.c_bool),
        ("MAIN_LightsSelector", ctypes.c_ubyte),
        ("MAIN_RMISelector1_VOR", ctypes.c_bool),
        ("MAIN_RMISelector2_VOR", ctypes.c_bool),
        ("MAIN_N1SetSelector", ctypes.c_ubyte),
        ("MAIN_SpdRefSelector", ctypes.c_ubyte),
        ("MAIN_FuelFlowSelector", ctypes.c_ubyte),
        ("MAIN_AutobrakeSelector", ctypes.c_ubyte),
        ("MAIN_annunANTI_SKID_INOP", ctypes.c_bool),
        ("MAIN_annunAUTO_BRAKE_DISARM", ctypes.c_bool),
        ("MAIN_annunLE_FLAPS_TRANSIT", ctypes.c_bool),
        ("MAIN_annunLE_FLAPS_EXT", ctypes.c_bool),
        ("MAIN_TEFlapsNeedle", ctypes.c_float * 2),
        ("MAIN_annunGEAR_transit", ctypes.c_bool * 3),
        ("MAIN_annunGEAR_locked", ctypes.c_bool * 3),
        ("MAIN_GearLever", ctypes.c_ubyte),
        ("MAIN_BrakePressNeedle", ctypes.c_float),
        ("MAIN_annunCABIN_ALTITUDE", ctypes.c_bool),
        ("MAIN_annunTAKEOFF_CONFIG", ctypes.c_bool),
        ("HGS_annun_AIII", ctypes.c_bool),
        ("HGS_annun_NO_AIII", ctypes.c_bool),
        ("HGS_annun_FLARE", ctypes.c_bool),
        ("HGS_annun_RO", ctypes.c_bool),
        ("HGS_annun_RO_CTN", ctypes.c_bool),
        ("HGS_annun_RO_ARM", ctypes.c_bool),
        ("HGS_annun_TO", ctypes.c_bool),
        ("HGS_annun_TO_CTN", ctypes.c_bool),
        ("HGS_annun_APCH", ctypes.c_bool),
        ("HGS_annun_TO_WARN", ctypes.c_bool),
        ("HGS_annun_Bar", ctypes.c_bool),
        ("HGS_annun_FAIL", ctypes.c_bool),
        # Lower forward panel
        ("LTS_MainPanelKnob", ctypes.c_ubyte * 2),
        ("LTS_BackgroundKnob", ctypes.c_ubyte),
        ("LTS_AFDSFloodKnob", ctypes.c_ubyte),
        ("LTS_OutbdDUBrtKnob", ctypes.c_ubyte * 2),
        ("LTS_InbdDUBrtKnob", ctypes.c_ubyte * 2),
        ("LTS_InbdDUMapBrtKnob", ctypes.c_ubyte * 2),
        ("LTS_UpperDUBrtKnob", ctypes.c_ubyte),
        ("LTS_LowerDUBrtKnob", ctypes.c_ubyte),
        ("LTS_LowerDUMapBrtKnob", ctypes.c_ubyte),
        ("GPWS_annunINOP", ctypes.c_bool),
        ("GPWS_FlapInhibitSw_NORM", ctypes.c_bool),
        ("GPWS_GearInhibitSw_NORM", ctypes.c_bool),
        ("GPWS_TerrInhibitSw_NORM", ctypes.c_bool),
        # Control Stand
        ("CDU_annunEXEC", ctypes.c_bool * 2),
        ("CDU_annunCALL", ctypes.c_bool * 2),
        ("CDU_annunFAIL", ctypes.c_bool * 2),
        ("CDU_annunMSG", ctypes.c_bool * 2),
        ("CDU_annunOFST", ctypes.c_bool * 2),
        ("CDU_BrtKnob", ctypes.c_ubyte * 2),
        ("COMM_Attend_PressCount", ctypes.c_ubyte),
        ("COMM_GrdCall_PressCount", ctypes.c_ubyte),
        ("COMM_SelectedMic", ctypes.c_ubyte * 3),
        ("COMM_ReceiverSwitches", ctypes.c_uint * 3),
        ("TRIM_StabTrimMainElecSw_NORMAL", ctypes.c_bool),
        ("TRIM_StabTrimAutoPilotSw_NORMAL", ctypes.c_bool),
        ("PED_annunParkingBrake", ctypes.c_bool),
        ("FIRE_OvhtDetSw", ctypes.c_ubyte * 2),
        ("FIRE_annunENG_OVERHEAT", ctypes.c_bool * 2),
        ("FIRE_DetTestSw", ctypes.c_ubyte),
        ("FIRE_HandlePos", ctypes.c_ubyte * 3),
        ("FIRE_HandleIlluminated", ctypes.c_bool * 3),
        ("FIRE_annunWHEEL_WELL", ctypes.c_bool),
        ("FIRE_annunFAULT", ctypes.c_bool),
        ("FIRE_annunAPU_DET_INOP", ctypes.c_bool),
        ("FIRE_annunAPU_BOTTLE_DISCHARGE", ctypes.c_bool),
        ("FIRE_annunBOTTLE_DISCHARGE", ctypes.c_bool * 2),
        ("FIRE_ExtinguisherTestSw", ctypes.c_ubyte),
        ("FIRE_annunExtinguisherTest", ctypes.c_bool * 3),
        ("CARGO_annunExtTest", ctypes.c_bool * 2),
        ("CARGO_DetSelect", ctypes.c_ubyte * 2),
        ("CARGO_ArmedSw", ctypes.c_bool * 2),
        ("CARGO_annunFWD", ctypes.c_bool),
        ("CARGO_annunAFT", ctypes.c_bool),
        ("CARGO_annunDETECTOR_FAULT", ctypes.c_bool),
        ("CARGO_annunDISCH", ctypes.c_bool),
        ("HGS_annunRWY", ctypes.c_bool),
        ("HGS_annunGS", ctypes.c_bool),
        ("HGS_annunFAULT", ctypes.c_bool),
        ("HGS_annunCLR", ctypes.c_bool),
        ("XPDR_XpndrSelector_2", ctypes.c_bool),
        ("XPDR_AltSourceSel_2", ctypes.c_bool),
        ("XPDR_ModeSel", ctypes.c_ubyte),
        ("XPDR_annunFAIL", ctypes.c_bool),
        ("LTS_PedFloodKnob", ctypes.c_ubyte),
        ("LTS_PedPanelKnob", ctypes.c_ubyte),
        ("TRIM_StabTrimSw_NORMAL", ctypes.c_bool),
        ("PED_annunLOCK_FAIL", ctypes.c_bool),
        ("PED_annunAUTO_UNLK", ctypes.c_bool),
        ("PED_FltDkDoorSel", ctypes.c_ubyte),
        # FMS
        ("FMC_TakeoffFlaps", ctypes.c_ubyte),
        ("FMC_V1", ctypes.c_ubyte),
        ("FMC_VR", ctypes.c_ubyte),
        ("FMC_V2", ctypes.c_ubyte),
        ("FMC_LandingFlaps", ctypes.c_ubyte),
        ("FMC_LandingVREF", ctypes.c_ubyte),
        ("FMC_CruiseAlt", ctypes.c_ushort),
        ("FMC_LandingAltitude", ctypes.c_short),
        ("FMC_TransitionAlt", ctypes.c_ushort),
        ("FMC_TransitionLevel", ctypes.c_ushort),
        ("FMC_PerfInputComplete", ctypes.c_bool),
        ("FMC_DistanceToTOD", ctypes.c_float),
        ("FMC_DistanceToDest", ctypes.c_float),
        ("FMC_flightNumber", ctypes.c_char * 9),
        # General and misc
        ("AircraftModel", ctypes.c_ushort),
        ("WeightInKg", ctypes.c_bool),
        ("GPWS_V1CallEnabled", ctypes.c_bool),
        ("GroundConnAvailable", ctypes.c_bool),
        ("reserved", ctypes.c_ubyte * 255),
    ]


# ---------------------------------------------------------------------------
# PmdgNG3DataManager — lazy subscription and caching
# ---------------------------------------------------------------------------


class PmdgNG3DataManager:
    """Manages lazy subscription to PMDG 737 NG3 client data areas.

    Subscribes on first use, caches latest state, serves reads from cache.
    Mirrors :class:`simconnect_mcp.pmdg.PmdgDataManager` for the 777, but with
    NG3-specific data-area identifiers and only two CDUs.
    """

    def __init__(self, sm) -> None:
        self._sm = sm  # SimConnectMobiFlight instance (or None for testing)
        self.data_subscribed = False
        self.cdu_subscribed = [False, False]
        self._data_struct: PMDG_NG3_DataStruct | None = None
        self._data_timestamp: float = 0.0
        self._cdu_screens: list[PMDG_NG3_CDU_Screen | None] = [None, None]
        self._cdu_timestamps: list[float] = [0.0, 0.0]
        self._valid_fields = {f[0] for f in PMDG_NG3_DataStruct._fields_}
        self._control_registered = False

    def subscribe_data(self) -> None:
        """Subscribe to PMDG_NG3_Data client data area."""
        if self.data_subscribed or self._sm is None:
            return
        from SimConnect.Enum import SIMCONNECT_CLIENT_DATA_PERIOD, SIMCONNECT_UNUSED

        self._sm.dll.MapClientDataNameToID(
            self._sm.hSimConnect,
            PMDG_NG3_DATA_NAME.encode("ascii"),
            PMDG_NG3_DATA_ID,
        )
        self._sm.dll.AddToClientDataDefinition(
            self._sm.hSimConnect,
            PMDG_NG3_DATA_DEFINITION,
            0,
            ctypes.sizeof(PMDG_NG3_DataStruct),
            0,
            SIMCONNECT_UNUSED,
        )
        self._sm.dll.RequestClientData(
            self._sm.hSimConnect,
            PMDG_NG3_DATA_ID,
            PMDG_NG3_DATA_DEFINITION,
            PMDG_NG3_DATA_DEFINITION,
            SIMCONNECT_CLIENT_DATA_PERIOD.SIMCONNECT_CLIENT_DATA_PERIOD_ONCE,
            0, 0, 0, 0,
        )
        self._data_struct = PMDG_NG3_DataStruct()
        self.data_subscribed = True
        log.info("Subscribed to PMDG_NG3_Data")

    def subscribe_cdu(self, cdu: int) -> None:
        """Subscribe to a CDU screen client data area (0 or 1)."""
        if cdu not in (0, 1):
            raise ValueError(f"NG3 has only 2 CDUs (0=Capt, 1=F/O); got {cdu}")
        if self.cdu_subscribed[cdu] or self._sm is None:
            return
        from SimConnect.Enum import SIMCONNECT_CLIENT_DATA_PERIOD, SIMCONNECT_UNUSED

        area_name = PMDG_NG3_CDU_NAMES[cdu]
        area_id = PMDG_NG3_CDU_IDS[cdu]
        def_id = PMDG_NG3_CDU_DEFINITIONS[cdu]

        self._sm.dll.MapClientDataNameToID(
            self._sm.hSimConnect, area_name.encode("ascii"), area_id,
        )
        self._sm.dll.AddToClientDataDefinition(
            self._sm.hSimConnect, def_id, 0,
            ctypes.sizeof(PMDG_NG3_CDU_Screen), 0, SIMCONNECT_UNUSED,
        )
        self._sm.dll.RequestClientData(
            self._sm.hSimConnect, area_id, def_id, def_id,
            SIMCONNECT_CLIENT_DATA_PERIOD.SIMCONNECT_CLIENT_DATA_PERIOD_ONCE,
            0, 0, 0, 0,
        )
        self._cdu_screens[cdu] = PMDG_NG3_CDU_Screen()
        self.cdu_subscribed[cdu] = True
        log.info("Subscribed to %s", area_name)

    def request_data(self) -> None:
        """Request a fresh one-time read of the Data area."""
        if not self.data_subscribed or self._sm is None:
            return
        from SimConnect.Enum import SIMCONNECT_CLIENT_DATA_PERIOD

        self._sm.dll.RequestClientData(
            self._sm.hSimConnect,
            PMDG_NG3_DATA_ID,
            PMDG_NG3_DATA_DEFINITION,
            PMDG_NG3_DATA_DEFINITION,
            SIMCONNECT_CLIENT_DATA_PERIOD.SIMCONNECT_CLIENT_DATA_PERIOD_ONCE,
            0, 0, 0, 0,
        )

    def request_cdu(self, cdu: int) -> None:
        """Request a fresh one-time read of a CDU screen."""
        if cdu not in (0, 1):
            return
        if not self.cdu_subscribed[cdu] or self._sm is None:
            return
        from SimConnect.Enum import SIMCONNECT_CLIENT_DATA_PERIOD

        def_id = PMDG_NG3_CDU_DEFINITIONS[cdu]
        area_id = PMDG_NG3_CDU_IDS[cdu]
        self._sm.dll.RequestClientData(
            self._sm.hSimConnect, area_id, def_id, def_id,
            SIMCONNECT_CLIENT_DATA_PERIOD.SIMCONNECT_CLIENT_DATA_PERIOD_ONCE,
            0, 0, 0, 0,
        )

    def send_control(self, event_id: int, parameter: int) -> None:
        """Write an event to the PMDG_NG3_Control data area."""
        if self._sm is None:
            return
        import struct as pystruct

        from SimConnect.Enum import SIMCONNECT_UNUSED

        if not self._control_registered:
            self._sm.dll.MapClientDataNameToID(
                self._sm.hSimConnect,
                PMDG_NG3_CONTROL_NAME.encode("ascii"),
                PMDG_NG3_CONTROL_ID,
            )
            self._sm.dll.AddToClientDataDefinition(
                self._sm.hSimConnect,
                PMDG_NG3_CONTROL_DEFINITION,
                0,
                8,
                0,
                SIMCONNECT_UNUSED,
            )
            self._control_registered = True

        data = pystruct.pack("<II", event_id, parameter)
        self._sm.dll.SetClientData(
            self._sm.hSimConnect,
            PMDG_NG3_CONTROL_ID,
            PMDG_NG3_CONTROL_DEFINITION,
            0, 0, 8, data,
        )

    def client_data_handler(self, client_data) -> None:
        """Handle incoming client data from SimConnect dispatch."""
        def_id = client_data.dwDefineID

        if def_id == PMDG_NG3_DATA_DEFINITION and self._data_struct is not None:
            ctypes.memmove(
                ctypes.addressof(self._data_struct),
                ctypes.addressof(client_data.dwData),
                ctypes.sizeof(PMDG_NG3_DataStruct),
            )
            self._data_timestamp = time.time()
            return

        for i, cdu_def in enumerate(PMDG_NG3_CDU_DEFINITIONS):
            if def_id == cdu_def and self._cdu_screens[i] is not None:
                ctypes.memmove(
                    ctypes.addressof(self._cdu_screens[i]),
                    ctypes.addressof(client_data.dwData),
                    ctypes.sizeof(PMDG_NG3_CDU_Screen),
                )
                self._cdu_timestamps[i] = time.time()
                return

    def read_field(self, field_name: str, index: int | None = None):
        """Read a field from the cached Data struct."""
        if self._data_struct is None:
            return None
        if field_name not in self._valid_fields:
            raise ValueError(f"Unknown field: {field_name}")

        value = getattr(self._data_struct, field_name)
        if index is not None:
            value = value[index]
        return value

    def read_cdu(self, cdu: int) -> PMDG_NG3_CDU_Screen | None:
        """Return the cached CDU screen struct, or None if not subscribed."""
        if cdu not in (0, 1):
            return None
        if not self.cdu_subscribed[cdu]:
            return None
        return self._cdu_screens[cdu]

    @property
    def data_age(self) -> float:
        """Seconds since last data update."""
        if self._data_timestamp == 0:
            return float("inf")
        return time.time() - self._data_timestamp

    def cdu_age(self, cdu: int) -> float:
        """Seconds since last CDU update."""
        if cdu not in (0, 1):
            return float("inf")
        if self._cdu_timestamps[cdu] == 0:
            return float("inf")
        return time.time() - self._cdu_timestamps[cdu]

    def cleanup(self) -> None:
        """Unregister handler and clear state."""
        if self._sm is not None:
            try:
                self._sm.unregister_client_data_handler(self.client_data_handler)
            except Exception:
                pass
        self._data_struct = None
        self._cdu_screens = [None, None]
        self.data_subscribed = False
        self.cdu_subscribed = [False, False]
        log.info("PMDG NG3 data manager cleaned up")
