# PMDG 777 SDK Reference

## Architecture Overview

The PMDG 777 uses a fundamentally different approach from standard MSFS L-var aircraft. Instead of exposing individual named L-vars, PMDG communicates through **SimConnect Client Data Areas** — binary data structures shared between the aircraft and external applications.

There are three data channels:

| Channel | Name | Direction | Purpose |
|---------|------|-----------|---------|
| **Data** | `PMDG_777X_Data` | Read | Aircraft state (switches, annunciators, displays, sensors) |
| **Control** | `PMDG_777X_Control` | Write | Send commands (button presses, switch toggles, knob rotations) |
| **CDU** | `PMDG_777X_CDU_0/1/2` | Read | CDU screen content (24×14 character grid) |

### MobiFlight Bridge

MobiFlight creates L-vars (like `switch_01_a`) as **read-only annunciator outputs**. Naming convention:
- `switch_NN_a` — primary annunciator (e.g., battery ON light)
- `switch_NN_c` — secondary annunciator (e.g., battery OFF light)

**To send commands**, PMDG events are triggered via the `ROTOR_BRAKE` carrier event with an offset parameter:

```
(event_offset + 100) (>K:ROTOR_BRAKE)
```

For example, the battery switch (event offset 1):
- **Toggle:** `101 (>K:ROTOR_BRAKE)`
- **Turn ON:** `(L:switch_01_a) ! if{ 101 (>K:ROTOR_BRAKE) }`
- **Turn OFF:** `(L:switch_01_a) if{ 101 (>K:ROTOR_BRAKE) }`

This `ROTOR_BRAKE` carrier pattern is the community-standard method (via MobiFlight HubHop) for controlling PMDG aircraft from external tools. The parameter formula is `THIRD_PARTY_EVENT_OFFSET + 100`, where the offset comes from the SDK `EVT_*` defines.

### Setup Requirements

To enable SDK data broadcasting, add these lines to `777_Options.ini`:

```ini
[SDK]
EnableDataBroadcast=1
EnableCDUBroadcast.0=1
EnableCDUBroadcast.1=1
EnableCDUBroadcast.2=1
```

**File locations:**
- Microsoft Store: `%LOCALAPPDATA%\Packages\Microsoft.FlightSimulator_8wekyb3d8bbwe\LocalState\packages\pmdg-aircraft-77w\work\777_Options.ini`
- Steam: `%APPDATA%\Microsoft Flight Simulator\Packages\pmdg-aircraft-77w\work\777_Options.ini`

---

## Variable Naming Conventions

### System Prefixes

| Prefix | System | Examples |
|--------|--------|----------|
| `ELEC_` | Electrical | Battery, generators, bus ties, ground power |
| `FUEL_` | Fuel | Pumps, crossfeed, jettison, quantities |
| `ENG_` | Engines | EEC mode, start selector, autostart |
| `HYD_` | Hydraulic | Demand pumps, air pumps, engine pumps, RAM air |
| `AIR_` | Air conditioning / Bleed / Pressurization | Packs, trim air, recirc fans, bleed, outflow valves |
| `FCTL_` | Flight controls | Spoilers, flaps, stab cutout, trim, alternate flaps |
| `LTS_` | Lights | Landing, taxi, beacon, nav, panel, flood, dome |
| `MCP_` | Mode Control Panel | Speed, heading, altitude, VS, AP, AT, modes |
| `EFIS_` | EFIS | Mins, baro, mode, range, VOR/ADF selectors |
| `CDU_` | CDU | Annunciators (EXEC, MSG, FAIL, OFST), brightness |
| `COMM_` | Communication | Mic selection, receiver switches, radio panels |
| `FIRE_` | Fire protection | Engine/APU handles, cargo fire, discharge |
| `XPDR_` / `TCAS_` | Transponder | Mode, ident, altitude source |
| `BRAKES_` | Brakes | Autobrake selector, parking brake, brake pressure |
| `ICE_` | Anti-ice / De-ice | Window heat, wing anti-ice, engine anti-ice |
| `SIGNS_` | Passenger signs | No smoking, seat belts |
| `GPWS_` | GPWS | Terrain/gear/flap inhibit, GS inhibit |
| `DSP_` | Display select | EICAS page selection, inboard display swap |
| `EVAC_` | Evacuation | Command switch, horn shutoff |
| `OXY_` | Oxygen | Passenger oxygen, crew oxygen panels |
| `DOOR_` | Doors | Door state array, cockpit door |
| `FMC_` | FMC data | V-speeds, altitudes, distances, flight number |
| `ISP_` | Instrument source | Nav source, display control, air data/attitude |
| `ISFD_` | Integrated standby | Baro, reset, plus/minus, APP, HP/IN |
| `CHR_` | Chronometers | Chrono, time/date, elapsed time, set |
| `WXR_` / `WIPERS_` | Weather | Radar controls, wiper selectors |
| `ADIRU_` | ADIRU | Switch, annunciators |

### Suffixes

| Suffix | Meaning | Data Type | Writable |
|--------|---------|-----------|----------|
| `_Sw_ON` / `_Sw_OFF` | Toggle switch state | bool | Yes |
| `_Sw_Pushed` | Momentary button (press state) | bool | Yes |
| `_Selector` | Multi-position rotary selector | unsigned char (0–N) | Yes |
| `_Knob` | Rotary knob position | unsigned char (0–100) | Yes |
| `_Lever` | Lever position | unsigned char (0–100 or discrete) | Yes |
| `_annun*` | Annunciator light state | bool | No (read-only) |
| `_Dial` | MCP dial position | unsigned char (0–99) | No |

---

## Data Types

| C Type | Size | Usage | Examples |
|--------|------|-------|----------|
| `bool` | 1 byte | Switches, annunciators, momentary buttons | `ELEC_Battery_Sw_ON`, `MCP_annunLNAV` |
| `unsigned char` | 1 byte | Multi-position selectors (0–N), knob positions (0–100) | `BRAKES_AutobrakeSelector`, `LTS_DomeLightKnob` |
| `unsigned short` | 2 bytes | Heading (0–359), altitude, transponder codes | `MCP_Heading`, `MCP_Altitude` |
| `short` | 2 bytes | Signed values (vertical speed) | `MCP_VertSpeed` |
| `float` | 4 bytes | IAS/Mach, FPA, duct pressure, fuel quantity | `MCP_IASMach`, `AIR_DuctPress` |
| `char[N]` | N bytes | String data | `FMC_flightNumber[9]` |

### Array Fields

Many variables are arrays indexed by position:
- `[2]` — left/right, engine 1/2, captain/F/O, fwd/aft
- `[3]` — captain/F/O/observer, left/center/right CDU
- `[4]` — L-Side/L-Fwd/R-Fwd/R-Side (window heat)
- `[16]` — Door state array (see Door State section)

---

## Control Event System

### Event Structure

Commands are sent via the `PMDG_777X_Control` data area:

```
Event:     unsigned int    // EVT_* constant (THIRD_PARTY_EVENT_ID_MIN + offset)
Parameter: unsigned int    // Position value or mouse flag
```

`THIRD_PARTY_EVENT_ID_MIN` = `0x00011000` = `69632`

### Two Control Methods

**Method 1: Client Data Write (Blocking)**
Write Event + Parameter to the control data area. Wait for Event to return to 0 before sending the next command. Best for sequential operations requiring precise timing.

**Method 2: SimConnect Event (Non-Blocking)**
Map the event ID to a SimEvent using `SimConnect_MapClientEventToSimEvent` with the string `#eventID` (e.g., `#69748` for logo light). Transmit via `SimConnect_TransmitClientEvent`. Can fire multiple events without waiting.

### Parameter Types

**Direct Position Values (0–8191):**
Used for switches and selectors. The value directly sets the control position.
- Toggle switches: `0` = OFF, `1` = ON
- Multi-position selectors: `0`, `1`, `2`, etc.
- Knobs: `0`–`100` for continuous range

**Mouse Flags:**
Simulate physical mouse interactions for complex controls:

| Flag | Value | Use |
|------|-------|-----|
| `MOUSE_FLAG_LEFTSINGLE` | `0x20000000` | Toggle switches, push buttons |
| `MOUSE_FLAG_LEFTRELEASE` | `0x00020000` | Release after press |
| `MOUSE_FLAG_WHEEL_UP` | `0x00004000` | Increment rotary controls |
| `MOUSE_FLAG_WHEEL_DOWN` | `0x00002000` | Decrement rotary controls |
| `MOUSE_FLAG_RIGHTDRAG` | `0x02000000` | Alternative rotation direction |
| `MOUSE_FLAG_LEFTDRAG` | `0x00800000` | Drag operations |

### MCP Direct Control Events

These special events set MCP values directly without simulating knob rotation:

| Event | Offset | Parameter |
|-------|--------|-----------|
| `EVT_MCP_IAS_SET` | 14502 | IAS in knots (if IAS mode active) |
| `EVT_MCP_MACH_SET` | 14503 | Mach × 1000 (e.g., 780 = M0.780) |
| `EVT_MCP_HDGTRK_SET` | 14504 | Heading/track in degrees |
| `EVT_MCP_ALT_SET` | 14505 | Altitude in feet |
| `EVT_MCP_VS_SET` | 14506 | VS + 10000 (e.g., 8200 = −1800 fpm) |
| `EVT_MCP_FPA_SET` | 14507 | FPA × 10 + 100 (e.g., 82 = −1.8°) |

---

## Panel Reference

### Overhead Maintenance Panel
- Backup window heat switches (L/R)
- Standby power selector (OFF/AUTO/BAT)
- Flight control hydraulic valve shutoffs (Wing L/C/R, Tail L/C/R)
- Primary flight computers (AUTO/DISC)
- APU maintenance test switch
- EEC power test switches (L/R)
- Towing power switch
- Cargo temperature selectors

### Overhead Panel — Electrical
- Battery switch, APU generator, APU selector (OFF/ON/START)
- Bus tie switches (L/R), external power (primary/secondary)
- Generator switches (L/R), backup generators (L/R)
- IDG disconnect switches (L/R)
- Cabin utility, IFE/passenger seats switches

### Overhead Panel — Hydraulic
- Primary engine pump switches (L/R)
- Primary electric pump switches (L/R)
- Demand electric pump selectors (OFF/AUTO/ON)
- Demand air pump selectors (OFF/AUTO/ON)
- RAM air turbine switch
- Associated FAULT annunciators

### Overhead Panel — Fuel
- Forward/aft pump switches (L/R each)
- Center pump switches (L/R)
- Auxiliary fuel pump
- Forward/aft crossfeed switches
- Jettison nozzle switches (L/R), jettison arm
- Fuel to remain selector + pull switch
- LOW PRESS and VALVE annunciators
- Fuel quantity readouts (center, left, right, aux) — *read-only*

### Overhead Panel — Engines
- EEC mode switches (NORM/ALTN) with guards (L/R)
- Start selectors (START/NORM) (L/R)
- Autostart switch
- ALTN and autostart OFF annunciators
- Engine start valve state — *read-only*

### Overhead Panel — Bleed Air
- Engine bleed air switches (L/R)
- APU bleed air switch
- Isolation valve switches (L/C/R)
- Associated OFF and CLOSED annunciators

### Overhead Panel — Air Conditioning & Pressurization
- Pack switches (L/R)
- Trim air switches (L/R)
- Recirculation fan switches (upper/lower)
- Temperature selectors (flight deck/cabin)
- Equipment cooling switch
- Gasper switch
- Alternate ventilation switch
- Main deck flow switch
- Outflow valve switches (fwd/aft)
- Landing altitude selector

### Overhead Panel — Anti-Ice
- Window heat switches (4 zones: L-Side/L-Fwd/R-Fwd/R-Side)
- Backup window heat switches (L/R) with guards
- Wing anti-ice selector (OFF/AUTO/ON)
- Engine anti-ice selectors (OFF/AUTO/ON) (L/R)
- INOP annunciators

### Overhead Panel — Fire Protection
- Cargo fire arm switches (FWD/AFT), main deck (Freighter)
- Cargo fire discharge switch with guard
- Fire/overheat test switch
- APU fire handle (4 positions: NORMAL/PULLED/LEFT/RIGHT)
- APU handle unlock switch
- Engine fire handles (same 4-position pattern)
- Engine handle unlock switches
- BTL DISCH annunciators
- Handle illumination and unlock state — *read-only*

### Overhead Panel — Lights
- Landing lights (Left/Right/Nose)
- Runway turnoff lights (L/R)
- Taxi, strobe, beacon, nav, logo, wing lights
- Storm light switch
- Master brightness knob + switch
- Indicator lights test (TEST/BRT/DIM)
- Dome light knob
- Circuit breaker light knob
- Overhead panel/flood knobs
- Glareshield panel/flood knobs
- Emergency lights selector (OFF/ARMED/ON)

### Overhead Panel — Signs & Miscellaneous
- No smoking selector (OFF/AUTO/ON)
- Seat belts selector (OFF/AUTO/ON)
- Wiper selectors (L/R): OFF/INT/LOW/HIGH
- Service interphone switch
- Passenger oxygen switch with guard

### Forward Panel
- Gear lever (UP/DOWN) with lock override
- Alternate gear down switch with guard
- GPWS inhibit switches (terrain/gear/flap/GS) with guards
- Runway override switch
- Autobrake selector (RTO/OFF/DISARM/1/2/3/MAX AUTO)
- ISFD controls (Baro/RST/±/APP/HP-IN)
- Standby ASI and altimeter knobs
- Instrument source select switches (Nav/Display/Air Data for L/R)
- FMC selector (LEFT/AUTO/RIGHT)
- Inboard display selectors (L/R)
- Heading reference switch with guard
- Brake pressure indicator — *read-only*
- Chronometers (L/R): CHR/Time-Date/ET/Set controls

### Glareshield — EFIS (Captain & F/O)
Each side has identical controls:
- Minimums selector (RADIO/BARO) + knob + RST button
- Baro selector (IN/HPA) + knob + STD button
- VOR/ADF selectors 1 & 2 (VOR/OFF/ADF)
- Mode selector (APP/VOR/MAP/PLAN) + CTR button
- Range selector (10/20/40/80/160/320/640) + TFC button
- Map overlay buttons: WXR, STA, WPT, ARPT, DATA, POS, TERR
- FPV and MTRS buttons

### Glareshield — Mode Control Panel (MCP)
**Displays (read-only):**
- IAS/Mach value, heading, altitude, vertical speed/FPA
- IAS blank and VS blank flags

**Switches:**
- Flight director (L/R)
- Autothrottle arm (L/R)
- Bank limit selector (AUTO/5/10/15/20/25)
- Altitude increment selector (AUTO/1000)
- Disengage bar
- Speed/Heading/Altitude dials (0–99)
- VS wheel (0–99)
- HDG/TRK and VS/FPA mode indicators

**Momentary buttons:**
- AP engage (L/R), CLB CON, A/T
- LNAV, VNAV, FLCH, HDG HOLD, VS/FPA, ALT HOLD, LOC, APP
- Speed/Heading/Altitude push buttons
- IAS/MACH, HDG/TRK, VS/FPA toggle buttons

**Annunciators:**
- AP (L/R), AT, LNAV, VNAV, FLCH, HDG HOLD, VS/FPA, ALT HOLD, LOC, APP

### Glareshield — Display Select Panel
All momentary switches:
- L INBD, R INBD, LWR CTR
- ENG, STAT, ELEC, HYD, FUEL, AIR, DOOR, GEAR, FCTL, CAM
- CHKL, COMM, NAV, CANC/RCL
- L INBD, R INBD, LWR CTR annunciators

### Glareshield — Warning
- Master warning reset (L/R) — momentary
- Master warning annunciators (L/R)
- Master caution annunciators (L/R)
- Data link switches: ACPT/CANC/RJCT (L/R)

### CDU (Left / Right / Center)
Each CDU unit has:
- **Line select keys:** L1–L6, R1–R6 (6 left, 6 right)
- **Function keys:** INIT REF, RTE, DEP ARR, ALTN, VNAV, FIX, LEGS, HOLD, FMC COMM, PROG, EXEC, MENU, NAV RAD, PREV PAGE, NEXT PAGE
- **Numeric keys:** 0–9, DOT, PLUS/MINUS
- **Alpha keys:** A–Z
- **Special keys:** SPACE, DEL, SLASH, CLR
- **Brightness knob**
- **Annunciators:** EXEC, DSPY, FAIL, MSG, OFST

**CDU Screen Data** (via `PMDG_777X_CDU_Screen`):
- 24 columns × 14 rows grid
- Each cell: Symbol (ASCII), Color (0–5), Flags
- Colors: WHITE(0), CYAN(1), GREEN(2), MAGENTA(3), AMBER(4), RED(5)
- Flags: SMALL_FONT(0x01), REVERSE(0x02), UNUSED/DIM(0x04)
- Special symbols: `\xA1` (left arrow), `\xA2` (right arrow)

### Control Stand
- Speed brake lever (DOWN/ARMED/26–100 deployed)
- Flaps lever (UP/1/5/15/20/25/30)
- Alternate flaps arm switch with guard
- Alternate flaps control (RET/OFF/EXT)
- Stab cutout switches (C/R) with guards
- Alternate pitch trim lever (NOSE DOWN/NEUTRAL/NOSE UP)
- Fuel control switches (L/R)
- Parking brake lever
- Thrust levers (forward/reverse per engine)
- TOGA switches (per engine)
- A/T disengage switches (per engine)

### Aft Aisle Stand — Communication
**Audio Control Panels (Captain / F/O / Observer):**
Each panel has microphone selection and receiver switches for:
VHFL, VHFC, VHFR, FLT, CAB, PA, HFL, HFR, SAT1, SAT2, SPKR, VOR/ADF, APP

Mic selection stored as index (0–9), receivers as bitmask.

**Radio Control Panels (3 panels):**
- Radio selector (VHFL/VHFC/VHFR/HFL/HFR)
- Transfer switch, inner/outer frequency selectors
- Panel off switch, AM annunciator

**Observer audio selector** (CAPT/NORMAL/F/O)

### Weather Radar Panel
Left and right radar controls:
- TFR, WX, WX+T, MAP, GC mode buttons
- Tilt control knob
- Gain control knob
- AUTO switch, L/R selector, TEST button

### Transponder / TCAS Panel
- Transponder selector (L/R)
- Altitude source (NORM/ALTN)
- Mode selector (STBY/ALT RPTG OFF/XPNDR/TA ONLY/TA/RA)
- IDENT button
- Transponder code knobs (L outer/inner, R outer/inner)
- ABV/BLW and ABS/REL selectors (L/R)

### Doors
16-element door state array:
- Entry doors: 1L, 1R, 2L, 2R, 3L, 3R, 4L, 4R, 5L, 5R
- Cargo: Forward, Aft, Main (Freighter), Bulk
- Access: Avionics, E/E

States: 0=Open, 1=Closed, 2=Closed+Armed, 3=Closing, 4=Opening

Door operation events available for all doors.
Cockpit door open/close state.
Captain/F/O window handles and clipboards.

### FMC Data (Read-Only)
- Takeoff flaps setting
- V1, VR, V2 speeds
- Thrust reduction altitude, acceleration altitudes
- Landing flaps, VREF
- Cruise altitude, landing altitude
- Transition altitude/level
- Performance input complete flag
- Distance to TOD, distance to destination
- Flight number string
- Thrust limit mode (TO/CLB/CRZ/CON/G/A/D-TO/A-TO variants)
- Normal checklist completion array (10 phases)

### Additional Read-Only State
- Aircraft model (1=-200, 2=-200ER, 3=-300, 4=-200LR, 5=777F, 6=-300ER)
- Weight unit (LBS/KG)
- GPWS V1 call-out enabled
- Ground connections available
- Wheel chocks set
- APU running
- IRS aligned
- EFIS display format per display unit (6 units)
- EFIS barometric/radio minimums set and values

### Miscellaneous
- Yoke AP disconnect switch
- Captain/F/O armrest switches (L/R each)
- Floor lights switch (BRT/OFF/DIM)
- Aisle stand panel/flood knobs
- EFB screen actions and buttons (L/R)
- CVR test/erase
- Standby instrument clickspots
- GMCS zoom

---

## Aircraft Model Detection

The PMDG 777 TITLE SimVar contains the variant and livery name. Examples:
- `PMDG 777-200ER RR PMDG House`
- `PMDG 777-300ER GE PMDG House`

The catalog uses title pattern `"PMDG 777"` for auto-detection, matching all 777 variants.

The `AircraftModel` data field indicates the specific variant:
| Value | Model |
|-------|-------|
| 1 | 777-200 |
| 2 | 777-200ER |
| 3 | 777-300 |
| 4 | 777-200LR |
| 5 | 777F |
| 6 | 777-300ER |
