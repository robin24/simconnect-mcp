# SimConnect MCP Server

An [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server that connects AI coding agents to Microsoft Flight Simulator via SimConnect. This server is built for **add-on development** — full read/write access to SimVars, L-vars, events, calculator code execution, and embedded documentation.

## What It Does

- **Read/write any SimVar** — altitude, heading, speed, autopilot settings, engine parameters, and 1,080+ more from a comprehensive built-in catalog
- **Read/write L-vars** — aircraft-specific local variables used by add-on developers (Fenix A320, FlyByWire A32NX, PMDG, etc.)
- **PMDG 777 and 737 NG3 native SDK support** — direct access to all aircraft data fields and control events via the PMDG SDK Client Data Areas, plus CDU screen reading with colors/formatting
- **Trigger events** — toggle switches, set autopilot modes, control lights, fire custom MobiFlight events
- **Execute RPN calculator code** — run arbitrary Reverse Polish Notation code directly in the sim
- **Search and discover variables** — searchable catalogs of SimVars, events, and aircraft-specific L-vars with human-readable names and valid values
- **Embedded documentation** — SimConnect reference docs served as MCP resources, available offline
- **Aircraft-specific catalogs** — pre-built variable databases with panel groupings, display names, and value descriptions. Ships with 1,433 Fenix A320/A321 variables, 1,607 PMDG 777 variables (777-200LR/200F/300ER/F), and 1,861 PMDG 737 NG3 variables (-600/700/800/900 incl. BBJ/BDSF/BCF).
- **HubHop integration** — built-in client for the [MobiFlight HubHop](https://hubhop.mobiflight.com) community database to generate and extend L-var catalogs for any supported aircraft

## Prerequisites

- **Microsoft Flight Simulator** (MSFS 2020 or 2024) running on the same machine
- **Python 3.10+**
- **[uv](https://docs.astral.sh/uv/)** (recommended) or pip
- **MobiFlight WASM Module** (optional, for L-var support) — install in your MSFS Community folder. Download from [MobiFlight](https://www.mobiflight.com/)

## Installation

```bash
# Clone the repository
git clone https://github.com/robin24/simconnect-mcp.git
cd simconnect-mcp

# Create virtual environment and install
uv venv
uv pip install -e ".[dev]"
```

## Configuring as an MCP Server

### Claude Code

**Via CLI:**

```bash
claude mcp add --transport stdio simconnect -- uv run --directory /path/to/simconnect-mcp simconnect-mcp
```

Or to make it available across all projects:

```bash
claude mcp add --transport stdio --scope user simconnect -- uv run --directory /path/to/simconnect-mcp simconnect-mcp
```

**Via JSON** (`~/.claude/settings.json` or project-level `.claude/settings.json`):

```json
{
  "mcpServers": {
    "simconnect": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/simconnect-mcp", "simconnect-mcp"]
    }
  }
}
```

### OpenAI Codex CLI

**Via CLI:**

```bash
codex mcp add simconnect -- uv run --directory /path/to/simconnect-mcp simconnect-mcp
```

**Via JSON:**

```json
{
  "mcpServers": {
    "simconnect": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/simconnect-mcp", "simconnect-mcp"]
    }
  }
}
```

### Gemini CLI

**Via CLI:**

```bash
gemini mcp add --transport stdio simconnect -- uv run --directory /path/to/simconnect-mcp simconnect-mcp
```

**Via JSON** (`~/.gemini/settings.json`):

```json
{
  "mcpServers": {
    "simconnect": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/simconnect-mcp", "simconnect-mcp"]
    }
  }
}
```

### MCP Inspector (Interactive Testing)

```bash
uv run mcp dev src/simconnect_mcp/server.py
```

> **Note:** Replace `/path/to/simconnect-mcp` with the actual absolute path to your clone of this repository.

## Tools (29)

### Connection (3)

| Tool | Description |
|------|-------------|
| `connect_to_sim` | Establish SimConnect connection to MSFS |
| `disconnect_from_sim` | Close the connection |
| `get_connection_status` | Check connection state, sim running/paused |

### SimVar Operations (6)

| Tool | Description |
|------|-------------|
| `get_simvar` | Read any SimVar by name with optional unit and index |
| `set_simvar` | Write a settable SimVar |
| `get_simvar_bulk` | Read multiple SimVars in one call |
| `search_simvars` | Search SimVars by keyword, filter by category |
| `list_simvar_categories` | List all SimVar categories with counts |
| `watch_simvar` | Monitor a SimVar over time (returns time-series) |

### Event Operations (3)

| Tool | Description |
|------|-------------|
| `trigger_event` | Fire a SimConnect event with optional parameter |
| `search_events` | Search events by keyword/category |
| `trigger_custom_event` | Fire MobiFlight/custom events |

### L-Var Operations (7)

| Tool | Description |
|------|-------------|
| `get_lvar` | Read an L-var value (requires MobiFlight) |
| `set_lvar` | Write an L-var value |
| `list_lvars` | Request enumeration of active L-vars |
| `execute_calculator_code` | Run RPN calculator code in the sim |
| `search_lvars` | Search aircraft L-var catalogs by keyword |
| `list_lvar_panels` | Browse L-vars by panel/system category |
| `list_lvar_catalogs` | List available aircraft L-var catalogs |

### Aircraft State (3)

| Tool | Description |
|------|-------------|
| `get_aircraft_state` | Comprehensive snapshot (~30 key SimVars) |
| `get_aircraft_position` | Position, heading, speed, on_ground |
| `get_aircraft_systems` | Engines, fuel, electrical, hydraulics |

### Facilities (2)

| Tool | Description |
|------|-------------|
| `get_nearby_airports` | Airports from SimConnect facilities subscription |
| `get_facility_info` | Detail on a specific airport/waypoint/NDB/VOR |

### Utilities (2)

| Tool | Description |
|------|-------------|
| `send_sim_text` | Display a text overlay in the simulator |
| `set_aircraft_position` | Teleport aircraft to a specific position |

### PMDG 777 / 737 NG3 (3)

| Tool | Description |
|------|-------------|
| `get_pmdg_var` | Read any PMDG data field (switches, MCP values, fuel qty, FMC data) |
| `get_pmdg_cdu` | Read CDU screen as text rows with per-cell color and formatting |
| `send_pmdg_event` | Send PMDG control events (toggle switches, press buttons, set selectors) |

These tools use the PMDG SDK Client Data Areas for direct binary access to the aircraft state — bypassing the MobiFlight L-var bridge. The right SDK (777 or 737 NG3) is auto-detected from the loaded aircraft, or can be forced with the `variant` argument. Requires `EnableDataBroadcast=1` and `EnableCDUBroadcast.N=1` in the aircraft's options.ini (`777_Options.ini` or `737NG3_Options.ini`). The 777 has three CDUs (Capt/Center/F-O); the 737 NG3 has two (Capt/F-O). See [PMDG 777 SDK Reference](src/simconnect_mcp/docs/pmdg_777.md) and [PMDG 737 NG3 SDK Reference](src/simconnect_mcp/docs/pmdg_737.md) for details.

## Variable Catalogs

The server ships with comprehensive variable catalogs for search and discovery, so that AI agents can find the right variable names without guessing.

### SimVar Catalog (1,080+ variables)

The built-in SimVar catalog covers all documented MSFS simulation variables across 25 categories:

| Category | Vars | Category | Vars |
|----------|------|----------|------|
| Aircraft Engine | 112 | Aircraft Avionics | 122 |
| Aircraft Fuel | 48 | Miscellaneous | 162 |
| Aircraft Controls | 50 | Landing Gear | 54 |
| Autopilot | 39 | Flight Instrumentation | 45 |
| Aircraft Lights | 25 | Aircraft Position and Speed | 46 |
| Camera | 34 | Radio Navigation | 66 |
| Services | 42 | Aircraft Electrics | 47 |
| Flight Model | 11 | Aircraft Systems | 24 |
| Environment | 14 | *and more...* | |

The catalog is sourced from both the SimConnect Python package and the [official MSFS SDK documentation](https://docs.flightsimulator.com/html/Programming_Tools/SimVars/Simulation_Variables.htm), ensuring complete coverage including camera controls, ground services, circuit breakers, GPS/NAV/COM radios, and flight model variables.

### Aircraft L-Var Catalogs

L-var catalogs provide searchable, human-readable databases for specific aircraft add-ons. The server auto-detects the loaded aircraft from its `TITLE` and `ATC_MODEL` SimVars and searches the matching catalog. If neither matches a known catalog, `search_lvars` and `list_lvar_panels` search across all of them and say so, with a `catalog=<key>` argument to scope the search explicitly (see `list_lvar_catalogs`).

**Included catalogs:**

| Aircraft | Variables | Panels | Source |
|----------|-----------|--------|--------|
| Fenix A319/A320/A321 | 1,433 | 26 | HubHop + manual curation |
| PMDG 777 (all variants) | 1,607 | 28 | SDK header parse + HubHop |
| PMDG 737 NG3 (-600/700/800/900/BBJ/BDSF/BCF) | 1,861 | 27 | SDK header parse + HubHop |

Each variable includes a display name, category, writability flag, and (where applicable) a map of valid values.

### Adding New Aircraft Catalogs

The fastest way to add a new aircraft is via the built-in HubHop client:

```bash
# See what's available
python -m simconnect_mcp.data.hubhop --list-vendors

# Generate a catalog (example: FlyByWire A32NX)
python -m simconnect_mcp.data.hubhop \
    --vendor "FlyByWire Simulations" \
    --aircraft "A320neo" \
    --aircraft-name "FlyByWire A32NX" \
    --title-pattern "A32NX" \
    -o fbw_a32nx.json
```

Or update an existing one:

```bash
python -m simconnect_mcp.data.hubhop --vendor FenixSim --merge fenix_a320.json
```

You can also use the Python API:

```python
from simconnect_mcp.data.hubhop import HubHopClient

client = HubHopClient()
presets = client.fetch_presets(vendor="FlyByWire Simulations", aircraft="A320neo")
catalog = client.build_catalog(presets, aircraft="FlyByWire A32NX", title_pattern="A32NX")
client.save_catalog(catalog, "fbw_a32nx.json")
```

Or create catalogs manually by placing a JSON file in `src/simconnect_mcp/data/`. All `*.json` files are auto-discovered on startup. See [docs/extending-catalogs.md](docs/extending-catalogs.md) for the full guide, JSON schema, and best practices.

## Resources

| URI | Content |
|-----|---------|
| `simconnect://docs/overview` | SimConnect architecture and key concepts |
| `simconnect://docs/simvars/{category}` | SimVar reference by category |
| `simconnect://docs/events/{category}` | Event reference by category |
| `simconnect://docs/rpn` | RPN calculator syntax guide |
| `simconnect://docs/lvars` | L-var usage for add-on development |
| `simconnect://docs/best-practices` | Common pitfalls and performance tips |
| `simconnect://state/connection` | Live connection status |
| `simconnect://state/aircraft` | Current aircraft info and position |

## Prompts

| Prompt | Purpose |
|--------|---------|
| `debug_simvar` | Step-by-step guide for debugging a misbehaving SimVar |
| `analyze_aircraft_vars` | Enumerate and categorize all L-vars on current aircraft |
| `create_addon_boilerplate` | Generate add-on starter code by type |
| `rpn_helper` | Translate natural language to RPN calculator code |
| `simconnect_code_review` | Review SimConnect code for common issues |

## Project Structure

```
src/simconnect_mcp/
├── server.py              # FastMCP instance, lifespan, tool registration
├── connection.py          # SimConnectManager singleton + native set_lvar
├── pmdg.py                # PMDG 777 SDK structs, CDU rendering, data manager
├── pmdg_ng3.py            # PMDG 737 NG3 SDK structs, CDU rendering, data manager
├── tools/
│   ├── __init__.py        # @handle_simconnect_errors, @require_connection decorators
│   ├── simvars.py         # SimVar CRUD + catalog loading (1,080+ vars)
│   ├── events.py          # Event trigger/search + built-in catalog
│   ├── lvars.py           # L-var read/write/search/panels/calculator code
│   ├── pmdg.py            # PMDG tools — auto-dispatch to 777 or 737 NG3
│   ├── aircraft.py        # State snapshots (position, systems)
│   ├── facilities.py      # Airport/navaid lookup
│   └── utilities.py       # send_sim_text, set_aircraft_position
├── data/
│   ├── catalog.py         # L-var catalog loader and search engine
│   ├── hubhop.py          # MobiFlight HubHop API client
│   ├── fenix_a320.json    # Fenix A320/A321 catalog (1,433 vars, 26 panels)
│   ├── pmdg_777.json      # PMDG 777 catalog (1,607 vars, 28 panels)
│   ├── pmdg_737.json      # PMDG 737 NG3 catalog (1,861 vars, 27 panels)
│   └── simvars_catalog.json  # Built-in SimVar catalog (1,080+ vars, 25 categories)
├── vendor/
│   ├── simconnect_mobiflight.py
│   └── mobiflight_variable_requests.py
└── docs/                  # Embedded documentation
    ├── pmdg_777.md        # PMDG 777 SDK reference
    └── pmdg_737.md        # PMDG 737 NG3 SDK reference
```

The PMDG catalogs are regenerated from the SDK headers via `scripts/parse_pmdg_sdk.py` — it auto-detects the struct name and CDU count, so the same script handles both 777 and 737 NG3.

## Development

```bash
# Run tests (159 tests, no MSFS required)
uv run pytest

# Run tests with verbose output
uv run pytest -v

# Run only specific test files
uv run pytest tests/test_hubhop.py    # HubHop client tests
uv run pytest tests/test_simvars.py   # SimVar catalog tests

# Lint
uv run ruff check src/ tests/
```

## License

This project is licensed under the MIT License - see the LICENSE file for details.
