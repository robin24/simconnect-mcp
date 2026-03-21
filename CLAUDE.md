# CLAUDE.md

## Project Overview

SimConnect MCP Server — an MCP server that gives AI agents full read/write access to Microsoft Flight Simulator via SimConnect. Built for the add-on development use case, not consumer flight assistance.

## Architecture

```
MCP Protocol (FastMCP, stdio)
  → server.py (tool/resource/prompt registration, lifespan)
    → tools/*.py (domain modules)
      → connection.py (SimConnectManager singleton)
        → SimConnect Python + vendored SimConnectMobiFlight
          → SimConnect DLL / MSFS
          → MobiFlight WASM Module (L-vars, calculator code)
```

### Key Design Decisions

- **Singleton connection** — SimConnect allows one connection per process. `SimConnectManager` is a thread-safe singleton with lazy-connect.
- **`run_in_executor` + `threading.Lock`** — all SimConnect DLL calls are blocking and not thread-safe. Every call goes through `run_sync()` which acquires a lock and runs in an executor to avoid blocking the async MCP event loop.
- **MobiFlight optional** — the vendored `SimConnectMobiFlight` (from [Koseng/MSFSPythonSimConnectMobiFlightExtension](https://github.com/Koseng/MSFSPythonSimConnectMobiFlightExtension)) is a drop-in subclass of `SimConnect` that adds client-data support for the WASM bridge. If it fails to load, core SimVar/event tools still work; only L-var tools degrade.
- **Native L-var writes** — `set_lvar` uses `AddToDataDefinition` + `SetDataOnSimObject` (the native SimConnect API), NOT the MobiFlight RPN `set()` command. This is critical because proprietary aircraft like the Fenix ignore MobiFlight RPN writes but respond to native SimConnect data writes.
- **`clear_sim_variables()` on connect** — the MobiFlight WASM module retains stale variable registrations from prior sessions. Without clearing on connect, all reads return 0.

## Project Structure

```
src/simconnect_mcp/
├── server.py              # FastMCP instance, lifespan, tool registration
├── connection.py          # SimConnectManager singleton + native set_lvar
├── tools/
│   ├── __init__.py        # @handle_simconnect_errors, @require_connection decorators
│   ├── simvars.py         # SimVar CRUD + built-in catalog (~50 common vars)
│   ├── events.py          # Event trigger/search + built-in catalog (~50 events)
│   ├── lvars.py           # L-var read/write/search/panels/calculator code
│   ├── aircraft.py        # State snapshots (get_aircraft_state, position, systems)
│   ├── facilities.py      # Airport/navaid lookup via FacilitiesRequests
│   └── utilities.py       # send_sim_text, set_aircraft_position
├── resources/
│   ├── documentation.py   # Embedded docs served as MCP resources
│   └── state.py           # Live connection/aircraft state resources
├── prompts/
│   └── templates.py       # debug_simvar, analyze_aircraft_vars, rpn_helper, etc.
├── data/
│   ├── catalog.py         # L-var catalog loader and search engine
│   ├── hubhop.py          # MobiFlight HubHop API client (CLI + library)
│   ├── fenix_a320.json    # Fenix A320/A321 catalog (1,433 vars, 26 panels)
│   └── simvars_catalog.json  # Built-in SimVar catalog (1,080+ vars, 25 categories)
├── vendor/
│   ├── simconnect_mobiflight.py          # Vendored from Koseng's repo
│   └── mobiflight_variable_requests.py   # MobiFlight WASM bridge
└── docs/                  # Embedded markdown documentation
    ├── overview.md, simvars.md, events.md, rpn.md, lvars.md, best_practices.md
```

## Extending the Aircraft L-Var Catalog

The catalog system provides searchable, human-readable L-var databases per aircraft. When `search_lvars("seatbelt")` is called, the server auto-detects the loaded aircraft from its `TITLE` SimVar and searches the matching catalog.

### Adding a New Aircraft

Create a JSON file in `src/simconnect_mcp/data/` (e.g., `fbw_a320.json`). All `*.json` files in this directory are auto-discovered on startup.

```json
{
  "aircraft": "FlyByWire A320neo",
  "title_pattern": "FlyByWire",
  "variables": [
    {
      "name": "A32NX_AUTOPILOT_1_ACTIVE",
      "display_name": "Autopilot 1 Active",
      "category": "Autopilot",
      "prefix": "A32NX",
      "writable": true,
      "values": {"0": "Off", "1": "On"}
    }
  ],
  "panels": {
    "Autopilot": [
      "A32NX_AUTOPILOT_1_ACTIVE",
      "A32NX_AUTOPILOT_2_ACTIVE"
    ]
  }
}
```

### Field Reference

| Field | Required | Description |
|-------|----------|-------------|
| `aircraft` | Yes | Human-readable aircraft name |
| `title_pattern` | Yes | Substring matched against `TITLE` SimVar for auto-detection (case-insensitive) |
| `variables` | Yes | Array of variable definitions |
| `variables[].name` | Yes | L-var name (without `L:` prefix) |
| `variables[].display_name` | Yes | Human-readable name for search and display |
| `variables[].category` | No | System/panel category for grouping |
| `variables[].prefix` | No | Variable prefix (for filtering) |
| `variables[].writable` | No | Whether the variable can be written (default: false) |
| `variables[].values` | No | Map of numeric values to descriptions (e.g., `{"0": "Off", "1": "On"}`) |
| `panels` | No | Groups variable names by physical panel/section |

### Tips for Building Catalogs

- **From existing tools:** export variable lists from FSUIPC, MobiFlight Connector, or SPAD.neXt
- **From source code:** if the aircraft is open source (e.g., FlyByWire), extract variable names from the codebase
- **By discovery:** use `list_lvars` to enumerate variables on a loaded aircraft, then categorize by prefix patterns
- **The `title_pattern`** should match a unique substring from `get_simvar("TITLE")`

## Fenix A320/A321 Notes

The Fenix uses a proprietary internal system with specific patterns you need to know when interacting with it.

### Variable Prefix Convention

| Prefix | Type | Writable | Description |
|--------|------|----------|-------------|
| `S_` | Switch | Yes | Toggle switches, selector positions |
| `A_` | Analog | Yes | Rotary knobs with numeric positions |
| `E_` | Event counter | Yes | Rotary encoders (increment/decrement by changing value) |
| `N_` | Numeric | No | Display readouts, computed values |
| `I_` | Indicator | No | Status lights, switch position indicators |
| `B_` | Boolean | No | On/off indicator lights |

### Counter-Based FCU Controls

Fenix FCU controls (altitude, heading, speed, V/S) do **NOT** accept direct value writes. They use a counter-based pattern:

1. Read the current counter value from the `E_` variable (e.g., `E_FCU_ALTITUDE`)
2. Read the current display value from the `N_` variable (e.g., `N_FCU_ALTITUDE`)
3. Calculate the number of steps needed
4. For altitude: set `S_FCU_ALTITUDE_SCALE` to `0` (force 100ft mode) first
5. Increment/decrement the `E_` counter by 1 for each step, with 15ms delay between steps
6. Restore the scale mode after

Each change of ±1 in the counter triggers one knob click in the sim. The Fenix detects the **direction of change**, not the absolute value.

### Button Transitions

Some Fenix switches are momentary buttons that need a press-release cycle: set to 1, wait 200ms, set to 0.

### Write Method

The Fenix responds to native SimConnect `SetDataOnSimObject` for L-var writes, NOT to MobiFlight RPN `set()` commands. The `set_lvar` tool already uses the native method.

## Running Tests

```bash
uv run pytest           # all tests
uv run pytest -v        # verbose
uv run pytest -k search # only search tests
```

Tests mock SimConnect so they run without MSFS. The `conftest.py` fixture provides a mock SimConnect with realistic SimVar values.
