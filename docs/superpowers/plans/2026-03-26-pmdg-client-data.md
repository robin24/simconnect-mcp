# PMDG 777 Client Data Area Support — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give AI agents direct read access to PMDG 777 aircraft state and CDU screens, plus a clean interface for sending PMDG control events — replacing the incomplete MobiFlight bridge workaround.

**Architecture:** A `PmdgDataManager` class lazily subscribes to PMDG SimConnect Client Data Areas on first use, caches the latest binary struct, and serves reads from cache. Three new MCP tools (`get_pmdg_var`, `get_pmdg_cdu`, `send_pmdg_event`) provide the agent-facing interface.

**Tech Stack:** Python ctypes for struct definitions, SimConnect DLL calls for client data area registration, existing `SimConnectMobiFlight` handler dispatch system.

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `src/simconnect_mcp/pmdg.py` | Create | PmdgDataManager class + ctypes struct definitions + client data subscription/caching |
| `src/simconnect_mcp/tools/pmdg.py` | Create | Three MCP tools: get_pmdg_var, get_pmdg_cdu, send_pmdg_event |
| `src/simconnect_mcp/connection.py` | Modify | Add `pmdg` field to SimConnectManager, cleanup on disconnect |
| `src/simconnect_mcp/server.py` | Modify | Register new tools |
| `tests/test_pmdg.py` | Create | Unit tests for struct parsing, CDU rendering, event resolution |

---

### Task 1: ctypes Struct Definitions

**Files:**
- Create: `src/simconnect_mcp/pmdg.py`
- Create: `tests/test_pmdg.py`

Define the PMDG 777 binary structs as ctypes Structures so we can parse raw bytes from SimConnect into Python values.

- [ ] **Step 1: Write the failing test for CDU cell/screen structs**

```python
# tests/test_pmdg.py
"""Tests for PMDG 777 client data area support."""

import ctypes
import struct

import pytest


class TestCDUStructs:
    def test_cdu_cell_size_is_3_bytes(self):
        from simconnect_mcp.pmdg import PMDG_777X_CDU_Cell

        assert ctypes.sizeof(PMDG_777X_CDU_Cell) == 3

    def test_cdu_screen_has_correct_dimensions(self):
        from simconnect_mcp.pmdg import PMDG_777X_CDU_Screen

        screen = PMDG_777X_CDU_Screen()
        # 24 columns x 14 rows
        assert len(screen.Cells) == 24
        assert len(screen.Cells[0]) == 14

    def test_cdu_cell_fields(self):
        from simconnect_mcp.pmdg import PMDG_777X_CDU_Cell

        cell = PMDG_777X_CDU_Cell()
        cell.Symbol = ord("A")
        cell.Color = 1  # CYAN
        cell.Flags = 0x01  # SMALL_FONT
        assert cell.Symbol == ord("A")
        assert cell.Color == 1
        assert cell.Flags == 0x01
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_pmdg.py::TestCDUStructs -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'simconnect_mcp.pmdg'`

- [ ] **Step 3: Implement CDU struct definitions**

```python
# src/simconnect_mcp/pmdg.py
"""PMDG 777 Client Data Area support.

Provides PmdgDataManager for lazy-subscribing to PMDG SimConnect Client
Data Areas (aircraft state + CDU screens) and caching the latest state.
"""

from __future__ import annotations

import ctypes
import logging
import time

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants from PMDG_777X_SDK.h
# ---------------------------------------------------------------------------

PMDG_777X_DATA_NAME = "PMDG_777X_Data"
PMDG_777X_DATA_ID = 0x504D4447
PMDG_777X_DATA_DEFINITION = 0x504D4448

PMDG_777X_CONTROL_NAME = "PMDG_777X_Control"
PMDG_777X_CONTROL_ID = 0x504D4449
PMDG_777X_CONTROL_DEFINITION = 0x504D444A

PMDG_777X_CDU_NAMES = [
    "PMDG_777X_CDU_0",
    "PMDG_777X_CDU_1",
    "PMDG_777X_CDU_2",
]
PMDG_777X_CDU_IDS = [0x4E477835, 0x4E477836, 0x4E477837]
PMDG_777X_CDU_DEFINITIONS = [0x4E477838, 0x4E477839, 0x4E47783A]

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

THIRD_PARTY_EVENT_ID_MIN = 0x00011000  # 69632

# ROTOR_BRAKE carrier event offset used by MobiFlight/HubHop community
ROTOR_BRAKE_OFFSET = 100


# ---------------------------------------------------------------------------
# ctypes struct definitions
# ---------------------------------------------------------------------------

class PMDG_777X_CDU_Cell(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("Symbol", ctypes.c_ubyte),
        ("Color", ctypes.c_ubyte),
        ("Flags", ctypes.c_ubyte),
    ]


CDU_Row = PMDG_777X_CDU_Cell * CDU_ROWS
CDU_Grid = CDU_Row * CDU_COLUMNS


class PMDG_777X_CDU_Screen(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("Cells", CDU_Grid),
        ("Powered", ctypes.c_bool),
    ]


class PMDG_777X_Control(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("Event", ctypes.c_uint),
        ("Parameter", ctypes.c_uint),
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_pmdg.py::TestCDUStructs -v`
Expected: 3 tests PASS

- [ ] **Step 5: Write the failing test for Data struct**

Add to `tests/test_pmdg.py`:

```python
class TestDataStruct:
    def test_data_struct_has_battery_field(self):
        from simconnect_mcp.pmdg import PMDG_777X_DataStruct

        data = PMDG_777X_DataStruct()
        data.ELEC_Battery_Sw_ON = True
        assert data.ELEC_Battery_Sw_ON is True

    def test_data_struct_has_array_fields(self):
        from simconnect_mcp.pmdg import PMDG_777X_DataStruct

        data = PMDG_777X_DataStruct()
        data.ELEC_BusTie_Sw_AUTO[0] = True
        data.ELEC_BusTie_Sw_AUTO[1] = False
        assert data.ELEC_BusTie_Sw_AUTO[0] is True
        assert data.ELEC_BusTie_Sw_AUTO[1] is False

    def test_data_struct_has_mcp_fields(self):
        from simconnect_mcp.pmdg import PMDG_777X_DataStruct

        data = PMDG_777X_DataStruct()
        data.MCP_IASMach = 250.0
        data.MCP_Heading = 270
        data.MCP_Altitude = 35000
        data.MCP_VertSpeed = -1800
        assert data.MCP_IASMach == pytest.approx(250.0)
        assert data.MCP_Heading == 270
        assert data.MCP_Altitude == 35000
        assert data.MCP_VertSpeed == -1800

    def test_data_struct_has_door_state_array(self):
        from simconnect_mcp.pmdg import PMDG_777X_DataStruct

        data = PMDG_777X_DataStruct()
        data.DOOR_state[0] = 2  # closed and armed
        assert data.DOOR_state[0] == 2

    def test_data_struct_has_fmc_flight_number(self):
        from simconnect_mcp.pmdg import PMDG_777X_DataStruct

        data = PMDG_777X_DataStruct()
        data.FMC_flightNumber = b"UAL123\x00\x00\x00"
        assert data.FMC_flightNumber == b"UAL123\x00\x00\x00"
```

- [ ] **Step 6: Run test to verify it fails**

Run: `uv run pytest tests/test_pmdg.py::TestDataStruct -v`
Expected: FAIL with `ImportError: cannot import name 'PMDG_777X_DataStruct'`

- [ ] **Step 7: Implement the Data struct**

Add to `src/simconnect_mcp/pmdg.py` — the full ctypes Structure matching the SDK header. This is a direct translation of every field in `PMDG_777X_Data` from `PMDG_777X_SDK.h`:

```python
class PMDG_777X_DataStruct(ctypes.Structure):
    """Mirrors the PMDG_777X_Data struct from the SDK header.

    Field order and types must exactly match the C struct for binary
    compatibility with SimConnect client data.
    """

    _pack_ = 1
    _fields_ = [
        # Overhead Maintenance Panel
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

        # Overhead Panel
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

        # Forward panel
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

        # Glareshield
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

        # Forward Aisle Stand Panel
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

        # Control Stand
        ("FCTL_AltnFlaps_Sw_ARM", ctypes.c_bool),
        ("FCTL_AltnFlaps_Control_Sw", ctypes.c_ubyte),
        ("FCTL_StabCutOutSw_C_NORMAL", ctypes.c_bool),
        ("FCTL_StabCutOutSw_R_NORMAL", ctypes.c_bool),
        ("FCTL_AltnPitch_Lever", ctypes.c_ubyte),
        ("FCTL_Speedbrake_Lever", ctypes.c_ubyte),
        ("FCTL_Flaps_Lever", ctypes.c_ubyte),
        ("ENG_FuelControl_Sw_RUN", ctypes.c_bool * 2),
        ("BRAKES_ParkingBrakeLeverOn", ctypes.c_bool),

        # Aft Aisle Stand Panel
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

        # Door state
        ("DOOR_state", ctypes.c_ubyte * 16),
        ("DOOR_CockpitDoorOpen", ctypes.c_bool),

        # Additional variables
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
```

- [ ] **Step 8: Run tests to verify all pass**

Run: `uv run pytest tests/test_pmdg.py -v`
Expected: all 8 tests PASS

- [ ] **Step 9: Commit**

```bash
git add src/simconnect_mcp/pmdg.py tests/test_pmdg.py
git commit -m "feat(pmdg): add ctypes struct definitions for PMDG 777 client data areas"
```

---

### Task 2: CDU Screen Rendering

**Files:**
- Modify: `src/simconnect_mcp/pmdg.py`
- Modify: `tests/test_pmdg.py`

Add functions to render a `PMDG_777X_CDU_Screen` struct into text rows and a structured grid.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_pmdg.py`:

```python
class TestCDURender:
    def _make_screen(self, text_rows: list[str], powered: bool = True):
        """Helper: build a CDU screen from text strings."""
        from simconnect_mcp.pmdg import PMDG_777X_CDU_Screen, CDU_COLUMNS, CDU_ROWS

        screen = PMDG_777X_CDU_Screen()
        screen.Powered = powered
        for col in range(CDU_COLUMNS):
            for row in range(CDU_ROWS):
                if row < len(text_rows) and col < len(text_rows[row]):
                    screen.Cells[col][row].Symbol = ord(text_rows[row][col])
                    screen.Cells[col][row].Color = 0  # white
                else:
                    screen.Cells[col][row].Symbol = ord(" ")
        return screen

    def test_render_text_rows(self):
        from simconnect_mcp.pmdg import render_cdu_text

        screen = self._make_screen([
            "        IDENT           ",
            "                        ",
            "MODEL        ENGINES    ",
        ])
        rows = render_cdu_text(screen)
        assert len(rows) == 14
        assert rows[0] == "        IDENT           "
        assert rows[2] == "MODEL        ENGINES    "

    def test_render_unpowered_screen(self):
        from simconnect_mcp.pmdg import render_cdu_text

        screen = self._make_screen([], powered=False)
        rows = render_cdu_text(screen)
        assert rows is None

    def test_render_structured_grid(self):
        from simconnect_mcp.pmdg import render_cdu_grid, CDU_COLOR_CYAN

        screen = self._make_screen(["A"])
        screen.Cells[0][0].Color = CDU_COLOR_CYAN
        screen.Cells[0][0].Flags = 0x01  # SMALL_FONT

        grid = render_cdu_grid(screen)
        assert grid[0][0]["char"] == "A"
        assert grid[0][0]["color"] == "cyan"
        assert grid[0][0]["small"] is True
        assert grid[0][0]["reverse"] is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_pmdg.py::TestCDURender -v`
Expected: FAIL with `ImportError: cannot import name 'render_cdu_text'`

- [ ] **Step 3: Implement CDU rendering functions**

Add to `src/simconnect_mcp/pmdg.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_pmdg.py::TestCDURender -v`
Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/simconnect_mcp/pmdg.py tests/test_pmdg.py
git commit -m "feat(pmdg): add CDU screen rendering to text rows and structured grid"
```

---

### Task 3: PmdgDataManager — Subscription and Caching

**Files:**
- Modify: `src/simconnect_mcp/pmdg.py`
- Modify: `tests/test_pmdg.py`

Implement the lazy-subscription manager that registers for PMDG client data areas and caches incoming data.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_pmdg.py`:

```python
class TestPmdgDataManager:
    def test_init_sets_empty_state(self):
        from simconnect_mcp.pmdg import PmdgDataManager

        # Pass None for sm — we won't call subscribe in this test
        mgr = PmdgDataManager(sm=None)
        assert mgr.data_subscribed is False
        assert mgr.cdu_subscribed == [False, False, False]
        assert mgr.data is None

    def test_read_field_bool(self):
        from simconnect_mcp.pmdg import PmdgDataManager, PMDG_777X_DataStruct

        mgr = PmdgDataManager(sm=None)
        mgr._data_struct = PMDG_777X_DataStruct()
        mgr._data_struct.ELEC_Battery_Sw_ON = True
        mgr._data_timestamp = time.time()
        mgr.data_subscribed = True

        value = mgr.read_field("ELEC_Battery_Sw_ON")
        assert value is True

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

    def test_read_cdu_when_not_subscribed_returns_none(self):
        from simconnect_mcp.pmdg import PmdgDataManager

        mgr = PmdgDataManager(sm=None)
        assert mgr.read_cdu(0) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_pmdg.py::TestPmdgDataManager -v`
Expected: FAIL with `ImportError: cannot import name 'PmdgDataManager'`

- [ ] **Step 3: Implement PmdgDataManager**

Add to `src/simconnect_mcp/pmdg.py`:

```python
class PmdgDataManager:
    """Manages lazy subscription to PMDG 777 client data areas.

    Subscribes on first use, caches latest state, serves reads from cache.
    """

    def __init__(self, sm) -> None:
        self._sm = sm  # SimConnectMobiFlight instance (or None for testing)
        self.data_subscribed = False
        self.cdu_subscribed = [False, False, False]

        # Cached state
        self._data_struct: PMDG_777X_DataStruct | None = None
        self._data_timestamp: float = 0.0
        self._cdu_screens: list[PMDG_777X_CDU_Screen | None] = [None, None, None]
        self._cdu_timestamps: list[float] = [0.0, 0.0, 0.0]

        # Build field name set for validation
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
            0,  # offset
            ctypes.sizeof(PMDG_777X_DataStruct),
            0,  # fEpsilon
            SIMCONNECT_UNUSED,
        )
        self._sm.dll.RequestClientData(
            self._sm.hSimConnect,
            PMDG_777X_DATA_ID,
            PMDG_777X_DATA_DEFINITION,
            PMDG_777X_DATA_DEFINITION,
            SIMCONNECT_CLIENT_DATA_PERIOD.SIMCONNECT_CLIENT_DATA_PERIOD_ON_SET,
            0,  # flags
            0, 0, 0,  # origin, interval, limit
        )

        self._data_struct = PMDG_777X_DataStruct()
        self.data_subscribed = True
        logger.info("Subscribed to PMDG_777X_Data")

    def subscribe_cdu(self, cdu: int) -> None:
        """Subscribe to a CDU screen client data area (0, 1, or 2)."""
        if self.cdu_subscribed[cdu] or self._sm is None:
            return

        from SimConnect.Enum import SIMCONNECT_CLIENT_DATA_PERIOD, SIMCONNECT_UNUSED

        area_name = PMDG_777X_CDU_NAMES[cdu]
        area_id = PMDG_777X_CDU_IDS[cdu]
        def_id = PMDG_777X_CDU_DEFINITIONS[cdu]

        self._sm.dll.MapClientDataNameToID(
            self._sm.hSimConnect,
            area_name.encode("ascii"),
            area_id,
        )
        self._sm.dll.AddToClientDataDefinition(
            self._sm.hSimConnect,
            def_id,
            0,
            ctypes.sizeof(PMDG_777X_CDU_Screen),
            0,
            SIMCONNECT_UNUSED,
        )
        self._sm.dll.RequestClientData(
            self._sm.hSimConnect,
            area_id,
            def_id,
            def_id,
            SIMCONNECT_CLIENT_DATA_PERIOD.SIMCONNECT_CLIENT_DATA_PERIOD_ON_SET,
            0,
            0, 0, 0,
        )

        self._cdu_screens[cdu] = PMDG_777X_CDU_Screen()
        self.cdu_subscribed[cdu] = True
        logger.info("Subscribed to %s", area_name)

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
        if field_name not in self._valid_fields:
            raise ValueError(f"Unknown field: {field_name}")
        if self._data_struct is None:
            return None

        value = getattr(self._data_struct, field_name)
        if index is not None:
            value = value[index]

        # Convert ctypes bool to Python bool
        if isinstance(value, bool):
            return value
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
        logger.info("PMDG data manager cleaned up")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_pmdg.py -v`
Expected: all 14 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/simconnect_mcp/pmdg.py tests/test_pmdg.py
git commit -m "feat(pmdg): add PmdgDataManager with lazy subscription and field reading"
```

---

### Task 4: Event Resolution

**Files:**
- Modify: `src/simconnect_mcp/pmdg.py`
- Modify: `tests/test_pmdg.py`

Add a function that resolves PMDG event names to ROTOR_BRAKE calculator code.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_pmdg.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_pmdg.py::TestEventResolution -v`
Expected: FAIL with `ImportError: cannot import name 'resolve_pmdg_event'`

- [ ] **Step 3: Implement event resolution**

Add to `src/simconnect_mcp/pmdg.py`:

```python
def resolve_pmdg_event(
    event_name: str, parameter: int | None = None
) -> str:
    """Resolve a PMDG event name to ROTOR_BRAKE calculator code.

    Looks up the event in the pmdg_777 catalog to get the event ID,
    then computes the ROTOR_BRAKE parameter (offset + 100).

    Args:
        event_name: EVT_* event name from the PMDG SDK
        parameter: Optional position value for the event

    Returns:
        RPN calculator code string like "101 (>K:ROTOR_BRAKE)"
    """
    from simconnect_mcp.data.catalog import get_catalog

    catalog = get_catalog("pmdg_777")
    if catalog is None:
        raise ValueError("PMDG 777 catalog not loaded")

    # Search for the event in catalog variables
    for var in catalog["variables"]:
        events = var.get("events", [])
        for evt in events:
            if evt["name"] == event_name:
                event_id = evt["id"]
                offset = event_id - THIRD_PARTY_EVENT_ID_MIN
                rotor_param = offset + ROTOR_BRAKE_OFFSET
                if parameter is not None:
                    return f"{rotor_param} {parameter} (>K:ROTOR_BRAKE)"
                return f"{rotor_param} (>K:ROTOR_BRAKE)"

    raise ValueError(
        f"Event '{event_name}' not found in PMDG 777 catalog. "
        "Use search_lvars to find available events."
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_pmdg.py::TestEventResolution -v`
Expected: 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/simconnect_mcp/pmdg.py tests/test_pmdg.py
git commit -m "feat(pmdg): add event name resolution to ROTOR_BRAKE calculator code"
```

---

### Task 5: Integration — connection.py and MCP Tools

**Files:**
- Modify: `src/simconnect_mcp/connection.py`
- Create: `src/simconnect_mcp/tools/pmdg.py`
- Modify: `src/simconnect_mcp/server.py`

Wire PmdgDataManager into the connection singleton and create the three MCP tools.

- [ ] **Step 1: Modify connection.py**

Add `pmdg` field initialization in `__init__`, cleanup in `disconnect`:

In `__init__`, after `self._mobiflight_available = False`:
```python
        self.pmdg = None  # PmdgDataManager, lazy-initialized
```

In `disconnect`, in the `finally` block before `self._state = ConnectionState.DISCONNECTED`:
```python
            if self.pmdg is not None:
                self.pmdg.cleanup()
                self.pmdg = None
```

- [ ] **Step 2: Create tools/pmdg.py**

```python
# src/simconnect_mcp/tools/pmdg.py
"""PMDG 777 tools — read aircraft state, CDU screens, send events."""

from __future__ import annotations

from typing import Any

from simconnect_mcp.connection import SimConnectManager
from simconnect_mcp.tools import handle_simconnect_errors, require_connection


def _ensure_pmdg_manager() -> tuple[Any, dict | None]:
    """Get or create PmdgDataManager. Returns (manager, error_dict)."""
    from simconnect_mcp.pmdg import PmdgDataManager

    sm_mgr = SimConnectManager()

    if not hasattr(sm_mgr.sm, "register_client_data_handler"):
        return None, {
            "status": "error",
            "error": "MOBIFLIGHT_REQUIRED",
            "message": "PMDG SDK tools require SimConnectMobiFlight.",
            "suggestion": "Ensure the MobiFlight WASM module is installed.",
        }

    if sm_mgr.pmdg is None:
        sm_mgr.pmdg = PmdgDataManager(sm_mgr.sm)
        sm_mgr.sm.register_client_data_handler(sm_mgr.pmdg.client_data_handler)

    return sm_mgr.pmdg, None


@handle_simconnect_errors
@require_connection
async def get_pmdg_var(name: str) -> dict:
    """Read a PMDG 777 aircraft data field by name.

    Uses the PMDG SDK data broadcast to read switch positions, annunciators,
    knob positions, MCP values, fuel quantities, FMC data, and more.

    Requires EnableDataBroadcast=1 in 777_Options.ini.

    Args:
        name: Variable name from the PMDG 777 catalog. Use search_lvars()
              to discover available variables. Examples:
              'ELEC_Battery_Sw_ON', 'MCP_IASMach', 'FUEL_QtyCenter'

    Returns:
        Dict with variable name, value, display name, and category.
    """
    from simconnect_mcp.data.catalog import get_catalog

    pmdg, err = _ensure_pmdg_manager()
    if err:
        return err

    # Look up the variable in the catalog
    catalog = get_catalog("pmdg_777")
    if catalog is None:
        return {
            "status": "error",
            "error": "CATALOG_NOT_FOUND",
            "message": "PMDG 777 catalog not loaded.",
        }

    # Find the variable entry
    var_entry = None
    for var in catalog["variables"]:
        if var["name"] == name:
            var_entry = var
            break

    if var_entry is None:
        return {
            "status": "error",
            "error": "FIELD_NOT_FOUND",
            "message": f"Variable '{name}' not found in PMDG 777 catalog.",
            "suggestion": "Use search_lvars() to find available variables.",
        }

    sdk_field = var_entry.get("sdk_field")
    sdk_index = var_entry.get("sdk_index")
    sdk_type = var_entry.get("sdk_type")

    if sdk_field is None or sdk_type == "event" or sdk_type == "lvar":
        return {
            "status": "error",
            "error": "NOT_A_DATA_FIELD",
            "message": f"'{name}' is a {sdk_type}, not a readable data field. "
                       "Use send_pmdg_event for events, or get_lvar for L-vars.",
        }

    # Subscribe if needed
    manager = SimConnectManager()

    def _subscribe():
        pmdg.subscribe_data()

    await manager.run_sync(_subscribe)

    # Wait briefly for first data if just subscribed
    import asyncio
    if pmdg.data_age == float("inf"):
        await asyncio.sleep(0.5)

    if pmdg.data_age == float("inf"):
        return {
            "status": "error",
            "error": "NO_DATA",
            "message": "No data received from PMDG 777.",
            "suggestion": "Ensure EnableDataBroadcast=1 is set in 777_Options.ini and restart the sim.",
        }

    # Read the value
    def _read():
        return pmdg.read_field(sdk_field, index=sdk_index)

    value = await manager.run_sync(_read)

    # Convert value for JSON
    if isinstance(value, bytes):
        value = value.decode("ascii", errors="replace").rstrip("\x00")

    result = {
        "status": "ok",
        "name": name,
        "value": value,
        "display_name": var_entry.get("display_name", name),
        "category": var_entry.get("category", ""),
    }

    # Add value description if available
    values_map = var_entry.get("values")
    if values_map and str(value) in values_map:
        result["value_description"] = values_map[str(value)]

    if pmdg.data_age > 5.0:
        result["warning"] = f"Data may be stale ({pmdg.data_age:.1f}s since last update)"

    return result


@handle_simconnect_errors
@require_connection
async def get_pmdg_cdu(cdu: int = 0) -> dict:
    """Read a PMDG 777 CDU screen.

    Returns the CDU display as text rows and an optional structured grid
    with per-cell color and formatting information.

    Requires EnableCDUBroadcast.N=1 in 777_Options.ini.

    Args:
        cdu: CDU unit number. 0=left (captain), 1=center, 2=right (F/O).

    Returns:
        Dict with 'rows' (list of 14 strings, 24 chars each) and 'grid'
        (structured per-cell data with color and flags).
    """
    from simconnect_mcp.pmdg import render_cdu_text, render_cdu_grid

    if cdu not in (0, 1, 2):
        return {
            "status": "error",
            "error": "INVALID_CDU",
            "message": f"CDU must be 0 (left), 1 (center), or 2 (right). Got {cdu}.",
        }

    pmdg, err = _ensure_pmdg_manager()
    if err:
        return err

    manager = SimConnectManager()

    def _subscribe():
        pmdg.subscribe_cdu(cdu)

    await manager.run_sync(_subscribe)

    # Wait briefly for first data
    import asyncio
    if pmdg.cdu_age(cdu) == float("inf"):
        await asyncio.sleep(0.5)

    if pmdg.cdu_age(cdu) == float("inf"):
        return {
            "status": "error",
            "error": "NO_CDU_DATA",
            "message": f"No CDU {cdu} data received.",
            "suggestion": f"Ensure EnableCDUBroadcast.{cdu}=1 is set in 777_Options.ini and restart the sim.",
        }

    screen = pmdg.read_cdu(cdu)
    if screen is None:
        return {"status": "error", "error": "NO_CDU_DATA", "message": "CDU screen not available."}

    rows = render_cdu_text(screen)
    if rows is None:
        return {
            "status": "ok",
            "cdu": cdu,
            "powered": False,
            "rows": None,
            "grid": None,
        }

    grid = render_cdu_grid(screen)

    cdu_names = {0: "Left (Captain)", 1: "Center", 2: "Right (F/O)"}
    result = {
        "status": "ok",
        "cdu": cdu,
        "cdu_name": cdu_names[cdu],
        "powered": True,
        "rows": rows,
        "grid": grid,
    }

    if pmdg.cdu_age(cdu) > 5.0:
        result["warning"] = f"Data may be stale ({pmdg.cdu_age(cdu):.1f}s since last update)"

    return result


@handle_simconnect_errors
@require_connection
async def send_pmdg_event(event_name: str, parameter: int | None = None) -> dict:
    """Send a PMDG 777 control event.

    Triggers cockpit controls (switches, buttons, knobs) using the PMDG SDK
    event system. Use search_lvars() to find events — look for entries with
    an 'events' field.

    Args:
        event_name: PMDG event name (e.g., 'EVT_OH_ELEC_BATTERY_SWITCH').
        parameter: Optional position value. For toggle switches, omit this.
                   For selectors, pass the desired position (0, 1, 2, etc).

    Returns:
        Confirmation dict.
    """
    from simconnect_mcp.pmdg import resolve_pmdg_event

    code = resolve_pmdg_event(event_name, parameter)

    manager = SimConnectManager()

    if not manager.mobiflight_available:
        return {
            "status": "error",
            "error": "MOBIFLIGHT_NOT_AVAILABLE",
            "message": "MobiFlight WASM extension required for PMDG events.",
        }

    def _execute():
        manager.mobiflight.set(code)

    await manager.run_sync(_execute)

    return {
        "status": "ok",
        "event": event_name,
        "parameter": parameter,
        "message": f"Event '{event_name}' sent successfully",
    }
```

- [ ] **Step 3: Register tools in server.py**

Add after the existing tool imports (around line 69):

```python
from simconnect_mcp.tools.pmdg import (  # noqa: E402
    get_pmdg_var,
    get_pmdg_cdu,
    send_pmdg_event,
)
```

Add to the tool registration list (around line 114):

```python
    get_pmdg_var, get_pmdg_cdu, send_pmdg_event,
```

- [ ] **Step 4: Run all tests**

Run: `uv run pytest -v`
Expected: all tests PASS (existing + new)

- [ ] **Step 5: Commit**

```bash
git add src/simconnect_mcp/connection.py src/simconnect_mcp/tools/pmdg.py src/simconnect_mcp/server.py
git commit -m "feat(pmdg): add get_pmdg_var, get_pmdg_cdu, send_pmdg_event MCP tools"
```

---

### Task 6: Live Validation

**Files:** None modified — verification only

Validate the new tools against the running PMDG 777-200ER in the simulator.

- [ ] **Step 1: Restart MCP server**

The user should restart the MCP server so new tools are loaded.

- [ ] **Step 2: Test get_pmdg_var with battery switch**

Call: `get_pmdg_var("ELEC_Battery_Sw_ON")`
Expected: `{"status": "ok", "value": true, ...}` (battery was turned on earlier)

- [ ] **Step 3: Test get_pmdg_var with MCP values**

Call: `get_pmdg_var("MCP_IASMach")`
Expected: Returns current MCP speed value

Call: `get_pmdg_var("MCP_Heading")`
Expected: Returns current MCP heading value

- [ ] **Step 4: Test get_pmdg_var with fuel quantity**

Call: `get_pmdg_var("FUEL_QtyCenter")`
Expected: Returns fuel quantity in LBS (float)

- [ ] **Step 5: Test send_pmdg_event**

Call: `send_pmdg_event("EVT_OH_ELEC_BATTERY_SWITCH")`
Expected: Battery toggles off

Call: `get_pmdg_var("ELEC_Battery_Sw_ON")`
Expected: `false`

Call: `send_pmdg_event("EVT_OH_ELEC_BATTERY_SWITCH")`
Expected: Battery toggles back on

- [ ] **Step 6: Test get_pmdg_cdu**

Call: `get_pmdg_cdu(0)`
Expected: Returns 14 text rows showing CDU content (IDENT page or similar), with `powered: true`

- [ ] **Step 7: Test CDU structured grid**

Verify the grid response includes per-cell color information (cyan headers, white values, etc.)

- [ ] **Step 8: Run full test suite**

Run: `uv run pytest -v`
Expected: all tests PASS

- [ ] **Step 9: Commit any fixes**

If any issues were found and fixed during validation:
```bash
git add -A
git commit -m "fix(pmdg): fixes from live validation"
```
