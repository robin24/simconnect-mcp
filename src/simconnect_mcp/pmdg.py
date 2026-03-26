"""PMDG 777X SimConnect Client Data Area structs and constants.

Defines ctypes Structures that mirror the binary layout of the PMDG 777X SDK
C structs so that raw bytes received from SimConnect can be parsed directly
into Python values with zero-copy semantics via ``from_buffer_copy``.

Binary compatibility with the PMDG SDK is critical — field order and types
must match the C header exactly.

References
----------
- PMDG_777X_SDK.h (shipped with the PMDG 777 add-on)
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

PMDG_777X_DATA_NAME = "PMDG_777X_Data"
PMDG_777X_DATA_ID = 0x504D4447
PMDG_777X_DATA_DEFINITION = 0x504D4448
PMDG_777X_CONTROL_NAME = "PMDG_777X_Control"
PMDG_777X_CONTROL_ID = 0x504D4449
PMDG_777X_CONTROL_DEFINITION = 0x504D444A

PMDG_777X_CDU_NAMES = ["PMDG_777X_CDU_0", "PMDG_777X_CDU_1", "PMDG_777X_CDU_2"]
PMDG_777X_CDU_IDS = [0x4E477835, 0x4E477836, 0x4E477837]
PMDG_777X_CDU_DEFINITIONS = [0x4E477838, 0x4E477839, 0x4E47783A]

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


class PMDG_777X_CDU_Cell(ctypes.Structure):
    """A single CDU character cell — 3 bytes, packed."""

    _pack_ = 1
    _fields_ = [
        ("Symbol", ctypes.c_ubyte),
        ("Color", ctypes.c_ubyte),
        ("Flags", ctypes.c_ubyte),
    ]


# CDU_Row  : 14 cells  (one column of cells, indexed by row)
# CDU_Grid : 24 such rows (indexed by column first, then row)
CDU_Row = PMDG_777X_CDU_Cell * CDU_ROWS      # 14 cells per column
CDU_Grid = CDU_Row * CDU_COLUMNS             # 24 columns


class PMDG_777X_CDU_Screen(ctypes.Structure):
    """Full CDU screen — 24 × 14 grid of cells plus a Powered flag."""

    _pack_ = 1
    _fields_ = [
        ("Cells", CDU_Grid),
        ("Powered", ctypes.c_bool),
    ]


# ---------------------------------------------------------------------------
# CDU rendering helpers
# ---------------------------------------------------------------------------


def render_cdu_text(screen: PMDG_777X_CDU_Screen) -> list[str] | None:
    """Render CDU screen as 14 plain-text row strings (24 chars each).

    Returns None if CDU is not powered.
    """
    if not screen.Powered:
        return None

    rows = []
    for row in range(CDU_ROWS):
        chars = []
        for col in range(CDU_COLUMNS):
            symbol = screen.Cells[col][row].Symbol
            if symbol == 0xA1:
                chars.append("\u2190")  # left arrow
            elif symbol == 0xA2:
                chars.append("\u2192")  # right arrow
            elif 0x20 <= symbol <= 0x7E:
                chars.append(chr(symbol))
            else:
                chars.append(" ")
        rows.append("".join(chars))
    return rows


def render_cdu_grid(screen: PMDG_777X_CDU_Screen) -> list[list[dict]] | None:
    """Render CDU screen as structured grid with per-cell color and flags.

    Returns None if CDU is not powered.
    Returns: list of 14 rows, each row a list of 24 cell dicts.
    """
    if not screen.Powered:
        return None

    grid = []
    for row in range(CDU_ROWS):
        row_cells = []
        for col in range(CDU_COLUMNS):
            cell = screen.Cells[col][row]
            symbol = cell.Symbol
            if symbol == 0xA1:
                char = "\u2190"
            elif symbol == 0xA2:
                char = "\u2192"
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
# Main data struct
# ---------------------------------------------------------------------------


class PMDG_777X_DataStruct(ctypes.Structure):
    """Binary-compatible mirror of PMDG_777X_DataStruct from PMDG_777X_SDK.h.

    Every field is in the exact order and type required by the SDK so that a
    raw byte buffer received from SimConnect can be parsed with
    ``PMDG_777X_DataStruct.from_buffer_copy(raw_bytes)``.
    """

    _pack_ = 1
    _fields_ = [
        # ------------------------------------------------------------------
        # Overhead Maintenance Panel
        # ------------------------------------------------------------------
        ("ICE_WindowHeatBackUp_Sw_OFF", ctypes.c_bool * 2),
        ("ELEC_StandbyPowerSw", ctypes.c_ubyte),
        ("FCTL_WingHydValve_Sw_SHUT_OFF", ctypes.c_bool * 3),
        ("FCTL_TailHydValve_Sw_SHUT_OFF", ctypes.c_bool * 3),
        ("FCTL_annunTailHydVALVE_CLOSED", ctypes.c_bool * 3),
        ("FCTL_annunWingHydVALVE_CLOSED", ctypes.c_bool * 3),
        ("FCTL_PrimFltComputersSw_AUTO", ctypes.c_bool),
        ("FCTL_annunPrimFltComputersDISC", ctypes.c_bool),
        ("APU_Power_Sw_TEST", ctypes.c_bool),
        ("ENG_EECPower_Sw_TEST", ctypes.c_bool * 2),
        ("ELEC_TowingPower_Sw_BATT", ctypes.c_bool),
        ("ELEC_annunTowingPowerON_BATT", ctypes.c_bool),
        ("AIR_CargoTemp_Selector", ctypes.c_ubyte * 2),
        ("AIR_CargoTemp_MainDeckFwd_Sel", ctypes.c_ubyte),
        ("AIR_CargoTemp_MainDeckAft_Sel", ctypes.c_ubyte),
        ("AIR_CargoTemp_LowerFwd_Sel", ctypes.c_ubyte),
        ("AIR_CargoTemp_LowerAft_Sel", ctypes.c_ubyte),
        # ------------------------------------------------------------------
        # Overhead Panel
        # ------------------------------------------------------------------
        ("ADIRU_Sw_On", ctypes.c_bool),
        ("ADIRU_annunOFF", ctypes.c_bool),
        ("ADIRU_annunON_BAT", ctypes.c_bool),
        ("FCTL_ThrustAsymComp_Sw_AUTO", ctypes.c_bool),
        ("FCTL_annunThrustAsymCompOFF", ctypes.c_bool),
        ("ELEC_CabUtilSw", ctypes.c_bool),
        ("ELEC_annunCabUtilOFF", ctypes.c_bool),
        ("ELEC_IFEPassSeatsSw", ctypes.c_bool),
        ("ELEC_annunIFEPassSeatsOFF", ctypes.c_bool),
        ("ELEC_Battery_Sw_ON", ctypes.c_bool),
        ("ELEC_annunBattery_OFF", ctypes.c_bool),
        ("ELEC_annunAPU_GEN_OFF", ctypes.c_bool),
        ("ELEC_APUGen_Sw_ON", ctypes.c_bool),
        ("ELEC_APU_Selector", ctypes.c_ubyte),
        ("ELEC_annunAPU_FAULT", ctypes.c_bool),
        ("ELEC_BusTie_Sw_AUTO", ctypes.c_bool * 2),
        ("ELEC_annunBusTieISLN", ctypes.c_bool * 2),
        ("ELEC_ExtPwrSw", ctypes.c_bool * 2),
        ("ELEC_annunExtPowr_ON", ctypes.c_bool * 2),
        ("ELEC_annunExtPowr_AVAIL", ctypes.c_bool * 2),
        ("ELEC_Gen_Sw_ON", ctypes.c_bool * 2),
        ("ELEC_annunGenOFF", ctypes.c_bool * 2),
        ("ELEC_BackupGen_Sw_ON", ctypes.c_bool * 2),
        ("ELEC_annunBackupGenOFF", ctypes.c_bool * 2),
        ("ELEC_IDGDiscSw", ctypes.c_bool * 2),
        ("ELEC_annunIDGDiscDRIVE", ctypes.c_bool * 2),
        ("WIPERS_Selector", ctypes.c_ubyte * 2),
        ("LTS_EmerLightsSelector", ctypes.c_ubyte),
        ("COMM_ServiceInterphoneSw", ctypes.c_bool),
        ("OXY_PassOxygen_Sw_On", ctypes.c_bool),
        ("OXY_annunPassOxygenON", ctypes.c_bool),
        ("ICE_WindowHeat_Sw_ON", ctypes.c_bool * 4),
        ("ICE_annunWindowHeatINOP", ctypes.c_bool * 4),
        ("HYD_RamAirTurbineSw", ctypes.c_bool),
        ("HYD_annunRamAirTurbinePRESS", ctypes.c_bool),
        ("HYD_annunRamAirTurbineUNLKD", ctypes.c_bool),
        ("HYD_PrimaryEngPump_Sw_ON", ctypes.c_bool * 2),
        ("HYD_PrimaryElecPump_Sw_ON", ctypes.c_bool * 2),
        ("HYD_DemandElecPump_Selector", ctypes.c_ubyte * 2),
        ("HYD_DemandAirPump_Selector", ctypes.c_ubyte * 2),
        ("HYD_annunPrimaryEngPumpFAULT", ctypes.c_bool * 2),
        ("HYD_annunPrimaryElecPumpFAULT", ctypes.c_bool * 2),
        ("HYD_annunDemandElecPumpFAULT", ctypes.c_bool * 2),
        ("HYD_annunDemandAirPumpFAULT", ctypes.c_bool * 2),
        ("SIGNS_NoSmokingSelector", ctypes.c_ubyte),
        ("SIGNS_SeatBeltsSelector", ctypes.c_ubyte),
        ("LTS_DomeLightKnob", ctypes.c_ubyte),
        ("LTS_CircuitBreakerKnob", ctypes.c_ubyte),
        ("LTS_OvereadPanelKnob", ctypes.c_ubyte),
        ("LTS_GlareshieldPNLlKnob", ctypes.c_ubyte),
        ("LTS_GlareshieldFLOODKnob", ctypes.c_ubyte),
        ("LTS_Storm_Sw_ON", ctypes.c_bool),
        ("LTS_MasterBright_Sw_ON", ctypes.c_bool),
        ("LTS_MasterBrigntKnob", ctypes.c_ubyte),
        ("LTS_IndLightsTestSw", ctypes.c_ubyte),
        ("LTS_LandingLights_Sw_ON", ctypes.c_bool * 3),
        ("LTS_Beacon_Sw_ON", ctypes.c_bool),
        ("LTS_NAV_Sw_ON", ctypes.c_bool),
        ("LTS_Logo_Sw_ON", ctypes.c_bool),
        ("LTS_Wing_Sw_ON", ctypes.c_bool),
        ("LTS_RunwayTurnoff_Sw_ON", ctypes.c_bool * 2),
        ("LTS_Taxi_Sw_ON", ctypes.c_bool),
        ("LTS_Strobe_Sw_ON", ctypes.c_bool),
        ("FIRE_CargoFire_Sw_Arm", ctypes.c_bool * 2),
        ("FIRE_annunCargoFire", ctypes.c_bool * 2),
        ("FIRE_CargoFireDisch_Sw", ctypes.c_bool),
        ("FIRE_annunCargoDISCH", ctypes.c_bool),
        ("FIRE_FireOvhtTest_Sw", ctypes.c_bool),
        ("FIRE_APUHandle", ctypes.c_ubyte),
        ("FIRE_APUHandleUnlock_Sw", ctypes.c_bool),
        ("FIRE_annunAPU_BTL_DISCH", ctypes.c_bool),
        ("FIRE_EngineHandleIlluminated", ctypes.c_bool * 2),
        ("FIRE_APUHandleIlluminated", ctypes.c_bool),
        ("FIRE_EngineHandleIsUnlocked", ctypes.c_bool * 2),
        ("FIRE_APUHandleIsUnlocked", ctypes.c_bool),
        ("FIRE_annunMainDeckCargoFire", ctypes.c_bool),
        ("FIRE_annunCargoDEPR", ctypes.c_bool),
        ("ENG_EECMode_Sw_NORM", ctypes.c_bool * 2),
        ("ENG_Start_Selector", ctypes.c_ubyte * 2),
        ("ENG_Autostart_Sw_ON", ctypes.c_bool),
        ("ENG_annunALTN", ctypes.c_bool * 2),
        ("ENG_annunAutostartOFF", ctypes.c_bool),
        ("FUEL_CrossFeedFwd_Sw", ctypes.c_bool),
        ("FUEL_CrossFeedAft_Sw", ctypes.c_bool),
        ("FUEL_PumpFwd_Sw", ctypes.c_bool * 2),
        ("FUEL_PumpAft_Sw", ctypes.c_bool * 2),
        ("FUEL_PumpCtr_Sw", ctypes.c_bool * 2),
        ("FUEL_JettisonNozle_Sw", ctypes.c_bool * 2),
        ("FUEL_JettisonArm_Sw", ctypes.c_bool),
        ("FUEL_FuelToRemain_Sw_Pulled", ctypes.c_bool),
        ("FUEL_FuelToRemain_Selector", ctypes.c_ubyte),
        ("FUEL_annunFwdXFEED_VALVE", ctypes.c_bool),
        ("FUEL_annunAftXFEED_VALVE", ctypes.c_bool),
        ("FUEL_annunLOWPRESS_Fwd", ctypes.c_bool * 2),
        ("FUEL_annunLOWPRESS_Aft", ctypes.c_bool * 2),
        ("FUEL_annunLOWPRESS_Ctr", ctypes.c_bool * 2),
        ("FUEL_annunJettisonNozleVALVE", ctypes.c_bool * 2),
        ("FUEL_annunArmFAULT", ctypes.c_bool),
        ("ICE_WingAntiIceSw", ctypes.c_ubyte),
        ("ICE_EngAntiIceSw", ctypes.c_ubyte * 2),
        ("AIR_Pack_Sw_AUTO", ctypes.c_bool * 2),
        ("AIR_TrimAir_Sw_On", ctypes.c_bool * 2),
        ("AIR_RecircFan_Sw_On", ctypes.c_bool * 2),
        ("AIR_TempSelector", ctypes.c_ubyte * 2),
        ("AIR_AirCondReset_Sw_Pushed", ctypes.c_bool),
        ("AIR_EquipCooling_Sw_AUTO", ctypes.c_bool),
        ("AIR_Gasper_Sw_On", ctypes.c_bool),
        ("AIR_annunPackOFF", ctypes.c_bool * 2),
        ("AIR_annunTrimAirFAULT", ctypes.c_bool * 2),
        ("AIR_annunEquipCoolingOVRD", ctypes.c_bool),
        ("AIR_AltnVentSw_ON", ctypes.c_bool),
        ("AIR_annunAltnVentFAULT", ctypes.c_bool),
        ("AIR_MainDeckFlowSw_NORM", ctypes.c_bool),
        ("AIR_EngBleedAir_Sw_AUTO", ctypes.c_bool * 2),
        ("AIR_APUBleedAir_Sw_AUTO", ctypes.c_bool),
        ("AIR_IsolationValve_Sw", ctypes.c_bool * 2),
        ("AIR_CtrIsolationValve_Sw", ctypes.c_bool),
        ("AIR_annunEngBleedAirOFF", ctypes.c_bool * 2),
        ("AIR_annunAPUBleedAirOFF", ctypes.c_bool),
        ("AIR_annunIsolationValveCLOSED", ctypes.c_bool * 2),
        ("AIR_annunCtrIsolationValveCLOSED", ctypes.c_bool),
        ("AIR_OutflowValve_Sw_AUTO", ctypes.c_bool * 2),
        ("AIR_OutflowValveManual_Selector", ctypes.c_ubyte * 2),
        ("AIR_LdgAlt_Sw_Pulled", ctypes.c_bool),
        ("AIR_LdgAlt_Selector", ctypes.c_ubyte),
        ("AIR_annunOutflowValve_MAN", ctypes.c_bool * 2),
        # ------------------------------------------------------------------
        # Forward panel
        # ------------------------------------------------------------------
        ("GEAR_Lever", ctypes.c_ubyte),
        ("GEAR_LockOvrd_Sw", ctypes.c_bool),
        ("GEAR_AltnGear_Sw_DOWN", ctypes.c_bool),
        ("GPWS_FlapInhibitSw_OVRD", ctypes.c_bool),
        ("GPWS_GearInhibitSw_OVRD", ctypes.c_bool),
        ("GPWS_TerrInhibitSw_OVRD", ctypes.c_bool),
        ("GPWS_RunwayOvrdSw_OVRD", ctypes.c_bool),
        ("GPWS_GSInhibit_Sw", ctypes.c_bool),
        ("GPWS_annunGND_PROX_top", ctypes.c_bool),
        ("GPWS_annunGND_PROX_bottom", ctypes.c_bool),
        ("BRAKES_AutobrakeSelector", ctypes.c_ubyte),
        ("ISFD_Baro_Sw_Pushed", ctypes.c_bool),
        ("ISFD_RST_Sw_Pushed", ctypes.c_bool),
        ("ISFD_Minus_Sw_Pushed", ctypes.c_bool),
        ("ISFD_Plus_Sw_Pushed", ctypes.c_bool),
        ("ISFD_APP_Sw_Pushed", ctypes.c_bool),
        ("ISFD_HP_IN_Sw_Pushed", ctypes.c_bool),
        ("ISP_Nav_L_Sw_CDU", ctypes.c_bool),
        ("ISP_DsplCtrl_L_Sw_Altn", ctypes.c_bool),
        ("ISP_AirDataAtt_L_Sw_Altn", ctypes.c_bool),
        ("DSP_InbdDspl_L_Selector", ctypes.c_ubyte),
        ("EFIS_HdgRef_Sw_Norm", ctypes.c_bool),
        ("EFIS_annunHdgRefTRUE", ctypes.c_bool),
        ("BRAKES_BrakePressNeedle", ctypes.c_int),
        ("BRAKES_annunBRAKE_SOURCE", ctypes.c_bool),
        ("ISP_Nav_R_Sw_CDU", ctypes.c_bool),
        ("ISP_DsplCtrl_R_Sw_Altn", ctypes.c_bool),
        ("ISP_AirDataAtt_R_Sw_Altn", ctypes.c_bool),
        ("ISP_FMC_Selector", ctypes.c_ubyte),
        ("DSP_InbdDspl_R_Selector", ctypes.c_ubyte),
        ("AIR_ShoulderHeaterKnob", ctypes.c_ubyte * 2),
        ("AIR_FootHeaterSelector", ctypes.c_ubyte * 2),
        ("LTS_LeftFwdPanelPNLKnob", ctypes.c_ubyte),
        ("LTS_LeftFwdPanelFLOODKnob", ctypes.c_ubyte),
        ("LTS_LeftOutbdDsplBRIGHTNESSKnob", ctypes.c_ubyte),
        ("LTS_LeftInbdDsplBRIGHTNESSKnob", ctypes.c_ubyte),
        ("LTS_RightFwdPanelPNLKnob", ctypes.c_ubyte),
        ("LTS_RightFwdPanelFLOODKnob", ctypes.c_ubyte),
        ("LTS_RightInbdDsplBRIGHTNESSKnob", ctypes.c_ubyte),
        ("LTS_RightOutbdDsplBRIGHTNESSKnob", ctypes.c_ubyte),
        ("CHR_Chr_Sw_Pushed", ctypes.c_bool * 2),
        ("CHR_TimeDate_Sw_Pushed", ctypes.c_bool * 2),
        ("CHR_TimeDate_Selector", ctypes.c_ubyte * 2),
        ("CHR_Set_Selector", ctypes.c_ubyte * 2),
        ("CHR_ET_Selector", ctypes.c_ubyte * 2),
        # ------------------------------------------------------------------
        # Glareshield
        # ------------------------------------------------------------------
        ("EFIS_MinsSelBARO", ctypes.c_bool * 2),
        ("EFIS_BaroSelHPA", ctypes.c_bool * 2),
        ("EFIS_VORADFSel1", ctypes.c_ubyte * 2),
        ("EFIS_VORADFSel2", ctypes.c_ubyte * 2),
        ("EFIS_ModeSel", ctypes.c_ubyte * 2),
        ("EFIS_RangeSel", ctypes.c_ubyte * 2),
        ("EFIS_MinsKnob", ctypes.c_ubyte * 2),
        ("EFIS_BaroKnob", ctypes.c_ubyte * 2),
        ("EFIS_MinsRST_Sw_Pushed", ctypes.c_bool * 2),
        ("EFIS_BaroSTD_Sw_Pushed", ctypes.c_bool * 2),
        ("EFIS_ModeCTR_Sw_Pushed", ctypes.c_bool * 2),
        ("EFIS_RangeTFC_Sw_Pushed", ctypes.c_bool * 2),
        ("EFIS_WXR_Sw_Pushed", ctypes.c_bool * 2),
        ("EFIS_STA_Sw_Pushed", ctypes.c_bool * 2),
        ("EFIS_WPT_Sw_Pushed", ctypes.c_bool * 2),
        ("EFIS_ARPT_Sw_Pushed", ctypes.c_bool * 2),
        ("EFIS_DATA_Sw_Pushed", ctypes.c_bool * 2),
        ("EFIS_POS_Sw_Pushed", ctypes.c_bool * 2),
        ("EFIS_TERR_Sw_Pushed", ctypes.c_bool * 2),
        ("MCP_IASMach", ctypes.c_float),
        ("MCP_IASBlank", ctypes.c_bool),
        ("MCP_Heading", ctypes.c_ushort),
        ("MCP_Altitude", ctypes.c_ushort),
        ("MCP_VertSpeed", ctypes.c_short),
        ("MCP_FPA", ctypes.c_float),
        ("MCP_VertSpeedBlank", ctypes.c_bool),
        ("MCP_FD_Sw_On", ctypes.c_bool * 2),
        ("MCP_ATArm_Sw_On", ctypes.c_bool * 2),
        ("MCP_BankLimitSel", ctypes.c_ubyte),
        ("MCP_AltIncrSel", ctypes.c_bool),
        ("MCP_DisengageBar", ctypes.c_bool),
        ("MCP_Speed_Dial", ctypes.c_ubyte),
        ("MCP_Heading_Dial", ctypes.c_ubyte),
        ("MCP_Altitude_Dial", ctypes.c_ubyte),
        ("MCP_VS_Wheel", ctypes.c_ubyte),
        ("MCP_HDGDial_Mode", ctypes.c_ubyte),
        ("MCP_VSDial_Mode", ctypes.c_ubyte),
        ("MCP_AP_Sw_Pushed", ctypes.c_bool * 2),
        ("MCP_CLB_CON_Sw_Pushed", ctypes.c_bool),
        ("MCP_AT_Sw_Pushed", ctypes.c_bool),
        ("MCP_LNAV_Sw_Pushed", ctypes.c_bool),
        ("MCP_VNAV_Sw_Pushed", ctypes.c_bool),
        ("MCP_FLCH_Sw_Pushed", ctypes.c_bool),
        ("MCP_HDG_HOLD_Sw_Pushed", ctypes.c_bool),
        ("MCP_VS_FPA_Sw_Pushed", ctypes.c_bool),
        ("MCP_ALT_HOLD_Sw_Pushed", ctypes.c_bool),
        ("MCP_LOC_Sw_Pushed", ctypes.c_bool),
        ("MCP_APP_Sw_Pushed", ctypes.c_bool),
        ("MCP_Speeed_Sw_Pushed", ctypes.c_bool),
        ("MCP_Heading_Sw_Pushed", ctypes.c_bool),
        ("MCP_Altitude_Sw_Pushed", ctypes.c_bool),
        ("MCP_IAS_MACH_Toggle_Sw_Pushed", ctypes.c_bool),
        ("MCP_HDG_TRK_Toggle_Sw_Pushed", ctypes.c_bool),
        ("MCP_VS_FPA_Toggle_Sw_Pushed", ctypes.c_bool),
        ("MCP_annunAP", ctypes.c_bool * 2),
        ("MCP_annunAT", ctypes.c_bool),
        ("MCP_annunLNAV", ctypes.c_bool),
        ("MCP_annunVNAV", ctypes.c_bool),
        ("MCP_annunFLCH", ctypes.c_bool),
        ("MCP_annunHDG_HOLD", ctypes.c_bool),
        ("MCP_annunVS_FPA", ctypes.c_bool),
        ("MCP_annunALT_HOLD", ctypes.c_bool),
        ("MCP_annunLOC", ctypes.c_bool),
        ("MCP_annunAPP", ctypes.c_bool),
        ("DSP_L_INBD_Sw", ctypes.c_bool),
        ("DSP_R_INBD_Sw", ctypes.c_bool),
        ("DSP_LWR_CTR_Sw", ctypes.c_bool),
        ("DSP_ENG_Sw", ctypes.c_bool),
        ("DSP_STAT_Sw", ctypes.c_bool),
        ("DSP_ELEC_Sw", ctypes.c_bool),
        ("DSP_HYD_Sw", ctypes.c_bool),
        ("DSP_FUEL_Sw", ctypes.c_bool),
        ("DSP_AIR_Sw", ctypes.c_bool),
        ("DSP_DOOR_Sw", ctypes.c_bool),
        ("DSP_GEAR_Sw", ctypes.c_bool),
        ("DSP_FCTL_Sw", ctypes.c_bool),
        ("DSP_CAM_Sw", ctypes.c_bool),
        ("DSP_CHKL_Sw", ctypes.c_bool),
        ("DSP_COMM_Sw", ctypes.c_bool),
        ("DSP_NAV_Sw", ctypes.c_bool),
        ("DSP_CANC_RCL_Sw", ctypes.c_bool),
        ("DSP_annunL_INBD", ctypes.c_bool),
        ("DSP_annunR_INBD", ctypes.c_bool),
        ("DSP_annunLWR_CTR", ctypes.c_bool),
        ("WARN_Reset_Sw_Pushed", ctypes.c_bool * 2),
        ("WARN_annunMASTER_WARNING", ctypes.c_bool * 2),
        ("WARN_annunMASTER_CAUTION", ctypes.c_bool * 2),
        # ------------------------------------------------------------------
        # Forward Aisle Stand Panel
        # ------------------------------------------------------------------
        ("ISP_DsplCtrl_C_Sw_Altn", ctypes.c_bool),
        ("LTS_UpperDsplBRIGHTNESSKnob", ctypes.c_ubyte),
        ("LTS_LowerDsplBRIGHTNESSKnob", ctypes.c_ubyte),
        ("EICAS_EventRcd_Sw_Pushed", ctypes.c_bool),
        ("CDU_annunEXEC", ctypes.c_bool * 3),
        ("CDU_annunDSPY", ctypes.c_bool * 3),
        ("CDU_annunFAIL", ctypes.c_bool * 3),
        ("CDU_annunMSG", ctypes.c_bool * 3),
        ("CDU_annunOFST", ctypes.c_bool * 3),
        ("CDU_BrtKnob", ctypes.c_ubyte * 3),
        # ------------------------------------------------------------------
        # Control Stand
        # ------------------------------------------------------------------
        ("FCTL_AltnFlaps_Sw_ARM", ctypes.c_bool),
        ("FCTL_AltnFlaps_Control_Sw", ctypes.c_ubyte),
        ("FCTL_StabCutOutSw_C_NORMAL", ctypes.c_bool),
        ("FCTL_StabCutOutSw_R_NORMAL", ctypes.c_bool),
        ("FCTL_AltnPitch_Lever", ctypes.c_ubyte),
        ("FCTL_Speedbrake_Lever", ctypes.c_ubyte),
        ("FCTL_Flaps_Lever", ctypes.c_ubyte),
        ("ENG_FuelControl_Sw_RUN", ctypes.c_bool * 2),
        ("BRAKES_ParkingBrakeLeverOn", ctypes.c_bool),
        # ------------------------------------------------------------------
        # Aft Aisle Stand Panel
        # ------------------------------------------------------------------
        ("COMM_SelectedMic", ctypes.c_ubyte * 3),
        ("COMM_ReceiverSwitches", ctypes.c_ushort * 3),
        ("COMM_OBSAudio_Selector", ctypes.c_ubyte),
        ("COMM_SelectedRadio", ctypes.c_ubyte * 3),
        ("COMM_RadioTransfer_Sw_Pushed", ctypes.c_bool * 3),
        ("COMM_RadioPanelOff", ctypes.c_bool * 3),
        ("COMM_annunAM", ctypes.c_bool * 3),
        ("XPDR_XpndrSelector_R", ctypes.c_bool),
        ("XPDR_AltSourceSel_ALTN", ctypes.c_bool),
        ("XPDR_ModeSel", ctypes.c_ubyte),
        ("XPDR_Ident_Sw_Pushed", ctypes.c_bool),
        ("FIRE_EngineHandle", ctypes.c_ubyte * 2),
        ("FIRE_EngineHandleUnlock_Sw", ctypes.c_bool * 2),
        ("FIRE_annunENG_BTL_DISCH", ctypes.c_bool * 2),
        ("FCTL_AileronTrim_Switches", ctypes.c_ubyte),
        ("FCTL_RudderTrim_Knob", ctypes.c_ubyte),
        ("FCTL_RudderTrimCancel_Sw_Pushed", ctypes.c_bool),
        ("EVAC_Command_Sw_ON", ctypes.c_bool),
        ("EVAC_PressToTest_Sw_Pressed", ctypes.c_bool),
        ("EVAC_HornSutOff_Sw_Pulled", ctypes.c_bool),
        ("EVAC_LightIlluminated", ctypes.c_bool),
        ("LTS_AisleStandPNLKnob", ctypes.c_ubyte),
        ("LTS_AisleStandFLOODKnob", ctypes.c_ubyte),
        ("LTS_FloorLightsSw", ctypes.c_ubyte),
        # ------------------------------------------------------------------
        # Door state
        # ------------------------------------------------------------------
        ("DOOR_state", ctypes.c_ubyte * 16),
        ("DOOR_CockpitDoorOpen", ctypes.c_bool),
        # ------------------------------------------------------------------
        # Additional variables
        # ------------------------------------------------------------------
        ("ENG_StartValve", ctypes.c_bool * 2),
        ("AIR_DuctPress", ctypes.c_float * 2),
        ("FUEL_QtyCenter", ctypes.c_float),
        ("FUEL_QtyLeft", ctypes.c_float),
        ("FUEL_QtyRight", ctypes.c_float),
        ("FUEL_QtyAux", ctypes.c_float),
        ("IRS_aligned", ctypes.c_bool),
        ("EFIS_BaroMinimumsSet", ctypes.c_bool * 2),
        ("EFIS_BaroMinimums", ctypes.c_int * 2),
        ("EFIS_RadioMinimumsSet", ctypes.c_bool * 2),
        ("EFIS_RadioMinimums", ctypes.c_int * 2),
        ("EFIS_Display", ctypes.c_ubyte * 6),
        ("AircraftModel", ctypes.c_ubyte),
        ("WeightInKg", ctypes.c_bool),
        ("GPWS_V1CallEnabled", ctypes.c_bool),
        ("GroundConnAvailable", ctypes.c_bool),
        ("FMC_TakeoffFlaps", ctypes.c_ubyte),
        ("FMC_V1", ctypes.c_ubyte),
        ("FMC_VR", ctypes.c_ubyte),
        ("FMC_V2", ctypes.c_ubyte),
        ("FMC_ThrustRedAlt", ctypes.c_ushort),
        ("FMC_AccelerationAlt", ctypes.c_ushort),
        ("FMC_EOAccelerationAlt", ctypes.c_ushort),
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
        ("WheelChocksSet", ctypes.c_bool),
        ("APURunning", ctypes.c_bool),
        ("FMC_ThrustLimitMode", ctypes.c_ubyte),
        ("ECL_ChecklistComplete", ctypes.c_bool * 10),
        ("reserved", ctypes.c_ubyte * 84),
    ]


# ---------------------------------------------------------------------------
# PmdgDataManager — lazy subscription and caching
# ---------------------------------------------------------------------------


class PmdgDataManager:
    """Manages lazy subscription to PMDG 777 client data areas.

    Subscribes on first use, caches latest state, serves reads from cache.
    """

    def __init__(self, sm) -> None:
        self._sm = sm  # SimConnectMobiFlight instance (or None for testing)
        self.data_subscribed = False
        self.cdu_subscribed = [False, False, False]
        self._data_struct: PMDG_777X_DataStruct | None = None
        self._data_timestamp: float = 0.0
        self._cdu_screens: list[PMDG_777X_CDU_Screen | None] = [None, None, None]
        self._cdu_timestamps: list[float] = [0.0, 0.0, 0.0]
        self._valid_fields = {f[0] for f in PMDG_777X_DataStruct._fields_}

    def subscribe_data(self) -> None:
        """Subscribe to PMDG_777X_Data client data area."""
        if self.data_subscribed or self._sm is None:
            return
        from SimConnect.Enum import SIMCONNECT_CLIENT_DATA_PERIOD, SIMCONNECT_UNUSED

        self._sm.dll.MapClientDataNameToID(
            self._sm.hSimConnect,
            PMDG_777X_DATA_NAME.encode("ascii"),
            PMDG_777X_DATA_ID,
        )
        self._sm.dll.AddToClientDataDefinition(
            self._sm.hSimConnect,
            PMDG_777X_DATA_DEFINITION,
            0,
            ctypes.sizeof(PMDG_777X_DataStruct),
            0,
            SIMCONNECT_UNUSED,
        )
        self._sm.dll.RequestClientData(
            self._sm.hSimConnect,
            PMDG_777X_DATA_ID,
            PMDG_777X_DATA_DEFINITION,
            PMDG_777X_DATA_DEFINITION,
            SIMCONNECT_CLIENT_DATA_PERIOD.SIMCONNECT_CLIENT_DATA_PERIOD_ON_SET,
            0, 0, 0, 0,
        )
        self._data_struct = PMDG_777X_DataStruct()
        self.data_subscribed = True
        log.info("Subscribed to PMDG_777X_Data")

    def subscribe_cdu(self, cdu: int) -> None:
        """Subscribe to a CDU screen client data area (0, 1, or 2)."""
        if self.cdu_subscribed[cdu] or self._sm is None:
            return
        from SimConnect.Enum import SIMCONNECT_CLIENT_DATA_PERIOD, SIMCONNECT_UNUSED

        area_name = PMDG_777X_CDU_NAMES[cdu]
        area_id = PMDG_777X_CDU_IDS[cdu]
        def_id = PMDG_777X_CDU_DEFINITIONS[cdu]

        self._sm.dll.MapClientDataNameToID(
            self._sm.hSimConnect, area_name.encode("ascii"), area_id,
        )
        self._sm.dll.AddToClientDataDefinition(
            self._sm.hSimConnect, def_id, 0,
            ctypes.sizeof(PMDG_777X_CDU_Screen), 0, SIMCONNECT_UNUSED,
        )
        self._sm.dll.RequestClientData(
            self._sm.hSimConnect, area_id, def_id, def_id,
            SIMCONNECT_CLIENT_DATA_PERIOD.SIMCONNECT_CLIENT_DATA_PERIOD_ON_SET,
            0, 0, 0, 0,
        )
        self._cdu_screens[cdu] = PMDG_777X_CDU_Screen()
        self.cdu_subscribed[cdu] = True
        log.info("Subscribed to %s", area_name)

    def client_data_handler(self, client_data) -> None:
        """Handle incoming client data from SimConnect dispatch."""
        def_id = client_data.dwDefineID

        if def_id == PMDG_777X_DATA_DEFINITION and self._data_struct is not None:
            ctypes.memmove(
                ctypes.addressof(self._data_struct),
                ctypes.addressof(client_data.dwData),
                ctypes.sizeof(PMDG_777X_DataStruct),
            )
            self._data_timestamp = time.time()
            return

        for i, cdu_def in enumerate(PMDG_777X_CDU_DEFINITIONS):
            if def_id == cdu_def and self._cdu_screens[i] is not None:
                ctypes.memmove(
                    ctypes.addressof(self._cdu_screens[i]),
                    ctypes.addressof(client_data.dwData),
                    ctypes.sizeof(PMDG_777X_CDU_Screen),
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

    def read_cdu(self, cdu: int) -> PMDG_777X_CDU_Screen | None:
        """Return the cached CDU screen struct, or None if not subscribed."""
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
        self._cdu_screens = [None, None, None]
        self.data_subscribed = False
        self.cdu_subscribed = [False, False, False]
        log.info("PMDG data manager cleaned up")
