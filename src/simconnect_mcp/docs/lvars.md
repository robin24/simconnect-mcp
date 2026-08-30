# L-Vars (Local Variables) Guide

L-vars are aircraft-specific local variables used extensively by MSFS add-on developers. Unlike standard SimVars which are defined by the simulator, L-vars are created by the aircraft or gauge code at runtime.

## Why L-Vars Matter for Add-on Development

Standard SimVars cover generic aircraft systems, but modern add-on aircraft implement complex custom systems that go far beyond what SimVars can represent. L-vars bridge this gap:

- **FlyByWire A320:** 500+ L-vars for EFIS, FCU, MCDU, fly-by-wire systems
- **Working Title CJ4:** 200+ L-vars for custom avionics
- **PMDG 737:** Hundreds of L-vars for systems simulation

## Accessing L-Vars

L-vars are NOT accessible through the standard SimConnect API. They require the **MobiFlight WASM module** installed in MSFS.

### Reading
```python
msfs_get_lvar("A32NX_EFIS_L_OPTION")  # Returns numeric value
```

### Writing
```python
msfs_set_lvar("A32NX_EFIS_L_OPTION", 1)  # Set to 1
```

### Discovering
Live enumeration of the L-vars an aircraft has registered is **not
available yet** — `msfs_list_lvars()` returns `NOT_IMPLEMENTED` on every
call. Discover names from the bundled catalogs instead:

```python
msfs_search_lvars("seatbelt")            # keyword search, auto-detects the aircraft
msfs_browse_lvar_catalog()               # the catalogs that ship with this server
msfs_browse_lvar_catalog(catalog="pmdg_737", panel="COMMUNICATION")
```

A catalog covers the variables that were catalogued for that aircraft, not
necessarily everything it registers, so a name absent from it may still
exist — `msfs_get_lvar` will read any name you can supply.

## Naming Conventions

Most aircraft developers use a prefix for their L-vars:

| Aircraft | Prefix | Example |
|----------|--------|---------|
| FlyByWire A320 | `A32NX_` | `A32NX_AUTOPILOT_1_ACTIVE` |
| Working Title CJ4 | `WT_CJ4_` | `WT_CJ4_HDG_ON` |
| Working Title G1000/G3000 | `AS1000_` / `AS3000_` | `AS1000_PFD_ScreenLuminosity` |
| Aerosoft CRJ | `ASCRJ_` | `ASCRJ_OVHD_COCKPITLIGHT` |

## Common L-Var Categories

### Autopilot/Flight Control
Variables controlling autopilot modes, flight director, and autothrust beyond what standard SimVars expose.

### EFIS/Display
Variables for PFD/ND range, mode, filters, and display options.

### Overhead Panel
Switches, knobs, and indicators on the overhead panel.

### Pedestal
Throttle detents, speed brake, radio panels.

### Lighting
Specific lighting states and brightness levels.

### Systems
Hydraulic, pneumatic, electrical subsystem states.

## Using Calculator Code with L-Vars

L-vars can also be read/written via RPN calculator code:

```rpn
(L:A32NX_AUTOPILOT_1_ACTIVE)              # Read
1 (>L:A32NX_AUTOPILOT_1_ACTIVE)           # Write
(L:MyCounter) 1 + (>L:MyCounter)          # Increment
```

## Tips

1. **Look the name up first** — `msfs_search_lvars()` or `msfs_browse_lvar_catalog()` for the catalogued variables of the loaded aircraft. Live enumeration is not available yet, so a name missing from a catalog may still exist on the aircraft
2. **L-var names are case-sensitive** — match the exact casing
3. **Values are always numeric** (float) — booleans are 0/1, enums are integers
4. **L-vars only exist while the aircraft is loaded** — they disappear when changing aircraft
5. **Some L-vars are read-only** — the aircraft code may ignore writes to certain variables
6. **Rate-limit your reads** — reading hundreds of L-vars every frame will impact performance

## MobiFlight WASM Setup

1. Install the MobiFlight WASM module in MSFS Community folder
2. Install `MSFSPythonSimConnectMobiFlightExtension` Python package
3. The SimConnect MCP server will automatically detect and use it
