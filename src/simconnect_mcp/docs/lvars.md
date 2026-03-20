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
get_lvar("A32NX_EFIS_L_OPTION")  # Returns numeric value
```

### Writing
```python
set_lvar("A32NX_EFIS_L_OPTION", 1)  # Set to 1
```

### Discovering
```python
list_lvars()  # Returns ALL active L-var names on current aircraft
```

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

1. **Use `list_lvars()` first** — discover what variables are available before trying to read specific ones
2. **L-var names are case-sensitive** — match the exact casing
3. **Values are always numeric** (float) — booleans are 0/1, enums are integers
4. **L-vars only exist while the aircraft is loaded** — they disappear when changing aircraft
5. **Some L-vars are read-only** — the aircraft code may ignore writes to certain variables
6. **Rate-limit your reads** — reading hundreds of L-vars every frame will impact performance

## MobiFlight WASM Setup

1. Install the MobiFlight WASM module in MSFS Community folder
2. Install `MSFSPythonSimConnectMobiFlightExtension` Python package
3. The SimConnect MCP server will automatically detect and use it
