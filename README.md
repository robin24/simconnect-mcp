# SimConnect MCP Server

An [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server that connects AI coding agents to Microsoft Flight Simulator via SimConnect. This server is built for **add-on development** — full read/write access to SimVars, L-vars, events, calculator code execution, and embedded documentation.

## What It Does

- **Read/write any SimVar** — altitude, heading, speed, autopilot settings, engine parameters, and 1,080+ more from a comprehensive built-in catalog, with unit selection and honest failures on a rejected write
- **Read/write L-vars** — aircraft-specific local variables used by add-on developers (Fenix A320, FlyByWire A32NX, PMDG, etc.), including live enumeration of what the loaded aircraft has actually registered
- **PMDG 777 and 737 NG3 native SDK support** — direct access to all aircraft data fields and control events via the PMDG SDK Client Data Areas, plus CDU screen reading with colors/formatting
- **Trigger events** — toggle switches, set autopilot modes, control lights, fire custom MobiFlight events
- **Execute RPN calculator code** — run arbitrary Reverse Polish Notation code directly in the sim
- **Search and discover variables** — searchable catalogs of SimVars, events, and aircraft-specific L-vars with human-readable names and valid values
- **Facilities lookup** — nearby airports sorted by distance and detail lookup for a specific airport, waypoint, NDB or VOR by ICAO identifier, read directly from SimConnect's facility subscription
- **Flight and scenario tools** — load and save `.FLT` flights, load a `.PLN` flight plan, and spawn an AI aircraft or object, for scripting test scenarios instead of setting them up by hand
- **Embedded documentation** — SimConnect reference docs served as MCP resources, available offline
- **Aircraft-specific catalogs** — pre-built variable databases with panel groupings, display names, and value descriptions. Ships with 1,607 PMDG 777 variables (777-200LR/200F/300ER/F) and 1,861 PMDG 737 NG3 variables (-600/700/800/900 incl. BBJ/BDSF/BCF).
- **HubHop integration** — search the [MobiFlight HubHop](https://hubhop.mobiflight.com) community preset database directly as an MCP tool, or use the built-in client to generate and extend L-var catalogs for any supported aircraft

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

# Install (creates the virtual environment and installs the dev group too)
uv sync
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

## Tools (32)

Every tool is prefixed `msfs_`, carries explicit `readOnlyHint`/`destructiveHint` annotations, and returns a typed result or a structured error — never a fabricated success. The tables below are generated from the live server (name, kind, and title come straight from each tool's `ToolAnnotations`); descriptions are the one-line summary from the tool's own docstring:

```bash
uv run python -c "
import asyncio
from simconnect_mcp.server import mcp
async def m():
    for t in sorted(await mcp.list_tools(), key=lambda x: x.name):
        kind = 'read' if t.annotations.readOnlyHint else 'write'
        print(f'| \`{t.name}\` | {kind} | {t.annotations.title} |')
asyncio.run(m())"
```

### Connection (3)

| Tool | Kind | Description |
|------|------|-------------|
| `msfs_connect` | write | Establish a SimConnect connection to MSFS |
| `msfs_disconnect` | write | Close the SimConnect connection to MSFS |
| `msfs_get_connection_status` | read | Check connection state, whether the sim is running/paused |

### SimVar Operations (6)

| Tool | Kind | Description |
|------|------|-------------|
| `msfs_get_simvar` | read | Read a SimVar value by name, in the requested unit |
| `msfs_set_simvar` | write | Write a value to a settable SimVar — fails honestly rather than reporting success on a rejected write |
| `msfs_get_simvars_bulk` | read | Read several SimVars in one call; one failing entry doesn't abort the rest |
| `msfs_search_simvars` | read | Search the SimVar catalog by keyword |
| `msfs_list_simvar_categories` | read | List every SimVar category with its variable count |
| `msfs_watch_simvar` | read | Sample a SimVar over time, returning a time series for debugging |

**Units:** `msfs_get_simvar` (and `msfs_set_simvar`) accept an optional `unit` argument and always report the unit actually used in the result. Omit `unit` and the bundled SimVar catalog supplies a sensible default for that variable; for a variable the catalog doesn't know, the default is `"number"`.

### Event Operations (3)

| Tool | Kind | Description |
|------|------|-------------|
| `msfs_trigger_event` | write | Fire a SimConnect event, with an optional parameter |
| `msfs_search_events` | read | Search SimConnect events by keyword, optionally filtered by category |
| `msfs_trigger_custom_event` | write | Fire a custom event as a key event through the MobiFlight WASM module |

### L-Var Operations (6)

| Tool | Kind | Description |
|------|------|-------------|
| `msfs_get_lvar` | read | Read an L-var (local variable) value from the current aircraft |
| `msfs_set_lvar` | write | Write a value to an L-var on the current aircraft |
| `msfs_list_lvars` | read | Enumerate the L-vars the currently loaded aircraft has registered |
| `msfs_execute_calculator_code` | write | Execute RPN calculator code in the simulator |
| `msfs_search_lvars` | read | Search known aircraft L-vars by keyword across the bundled catalogs |
| `msfs_browse_lvar_catalog` | read | Browse the aircraft L-var catalogs — list catalogs, the panels in one, or the variables on one panel |

`msfs_list_lvars` asks the MobiFlight WASM module to enumerate what the aircraft has *actually* registered, live — but the module caps its reply at 1000 names and still reports the list as complete when it truncates. A busy add-on setup (GSX and similar) can crowd the aircraft's own variables out of the response entirely; watch for `truncated: true`. Treat the catalogs and the live listing as a starting point, not a guaranteed inventory — `msfs_get_lvar` reads any name you supply, whether or not it surfaced in either one.

### Aircraft State (1)

| Tool | Kind | Description |
|------|------|-------------|
| `msfs_get_aircraft_snapshot` | read | Read a snapshot of the current aircraft state (position, speed, attitude, and key systems in one call) |

### Facilities (2)

| Tool | Kind | Description |
|------|------|-------------|
| `msfs_get_nearby_airports` | read | List airports near a point, nearest first |
| `msfs_get_facility_info` | read | Look up one airport, waypoint, NDB or VOR by ICAO identifier |

Airports are world-wide (SimConnect returns the full 85,249-airport list, cached after the first collection). Waypoints, NDBs and VORs are scoped to wherever the aircraft currently is, so they are re-collected on every call rather than cached — a cached navaid list would keep answering for the aircraft's *previous* position after a reposition or a flight.

### Utilities (2)

| Tool | Kind | Description |
|------|------|-------------|
| `msfs_send_sim_text` | write | Display a text overlay message in the simulator (debug feedback) |
| `msfs_set_aircraft_position` | write | Reposition the aircraft (test scenario setup) |

### PMDG 777 / 737 NG3 (3)

| Tool | Kind | Description |
|------|------|-------------|
| `msfs_get_pmdg_var` | read | Read a PMDG aircraft data field by name — switches, MCP values, fuel qty, FMC data (777 or 737 NG3) |
| `msfs_get_pmdg_cdu` | read | Read a PMDG CDU screen as text rows with per-cell color and formatting |
| `msfs_send_pmdg_event` | write | Send a PMDG control event — toggle a switch, press a button, set a selector (777 or 737 NG3) |

These tools use the PMDG SDK Client Data Areas for direct binary access to the aircraft state — bypassing the MobiFlight L-var bridge. The right SDK (777 or 737 NG3) is auto-detected from the loaded aircraft, or can be forced with the `variant` argument. Requires `EnableDataBroadcast=1` and `EnableCDUBroadcast.N=1` in the aircraft's options.ini (`777_Options.ini` or `737NG3_Options.ini`). The 777 has three CDUs (Capt/Center/F-O); the 737 NG3 has two (Capt/F-O). See [PMDG 777 SDK Reference](src/simconnect_mcp/docs/pmdg_777.md) and [PMDG 737 NG3 SDK Reference](src/simconnect_mcp/docs/pmdg_737.md) for details.

### HubHop (2)

| Tool | Kind | Description |
|------|------|-------------|
| `msfs_search_hubhop` | read | Search the MobiFlight HubHop community preset database |
| `msfs_list_hubhop_aircraft` | read | List the aircraft that HubHop has presets for |

Unlike every other tool, these two reach HubHop's HTTP API rather than the simulator, so they work with MSFS closed. The first call downloads and caches the full preset database (~17 MB); later calls in the same server process are served from that in-memory cache, which both tools share and which re-fetches on its own every 6 hours. Either tool accepts `refresh=true` to force an immediate re-fetch.

### Flight and Scenario (4)

| Tool | Kind | Description |
|------|------|-------------|
| `msfs_load_flight` | write | Load a saved flight, replacing the current one |
| `msfs_save_flight` | write | Save the current flight to a `.FLT` file — refuses to overwrite an existing file unless `overwrite=true` is passed explicitly |
| `msfs_load_flight_plan` | write | Load a `.PLN` flight plan into the aircraft's GPS/FMS, replacing whatever plan is currently active |
| `msfs_create_ai_object` | write | Spawn an AI aircraft or object at a position — reports whether SimConnect accepted the request, which is not the same as the object existing (MSFS ignores an unmatched title silently) |

These wrap the underlying SimConnect flight/scenario file operations for scripting test setups (e.g. "load this approach", "save the current state", "spawn traffic nearby") rather than driving them by hand in the sim's own UI. `msfs_create_ai_object` is confirmed live end-to-end — spawned, verified to answer a targeted SimVar request, then removed again — by `tests/live/test_live_flight.py`. `msfs_save_flight` was also confirmed live, including the multi-second post-save SimConnect stall documented in CLAUDE.md's Known Sim Behaviours; its own live tests were retired in the 2026-08-29 live-suite trim once that finding was captured there, since what remained (the overwrite-guard logic) is pure Python already covered by `tests/test_flight.py`'s mocks. `msfs_load_flight` and `msfs_load_flight_plan` are mock-only by design — see `tests/live/test_live_flight.py`'s module docstring for why. See [Running the live tests](#running-the-live-tests) below.

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

L-var catalogs provide searchable, human-readable databases for specific aircraft add-ons. The server auto-detects the loaded aircraft from its `TITLE` and `ATC_MODEL` SimVars and searches the matching catalog. If neither matches a known catalog, `msfs_search_lvars` and `msfs_browse_lvar_catalog` search across all of them and say so, with a `catalog=<key>` argument to scope the search explicitly (call `msfs_browse_lvar_catalog` with no arguments to list the available catalog keys).

**Included catalogs:**

| Aircraft | Variables | Panels | Source |
|----------|-----------|--------|--------|
| PMDG 777 (all variants) | 1,607 | 28 | SDK header parse + HubHop |
| PMDG 737 NG3 (-600/700/800/900/BBJ/BDSF/BCF) | 1,861 | 27 | SDK header parse + HubHop |

Each variable includes a display name, category, writability flag, and (where applicable) a map of valid values.

No Fenix catalog ships — a prior `fenix_a320.json` (1,433 plain L-vars) was removed in favor of HubHop's own broader, community-maintained `FenixSim` coverage (2,273 presets, current the moment Fenix ships an update, where a bundled snapshot would only go stale). Search it live with `msfs_search_hubhop(vendor="FenixSim")`, or regenerate a local catalog file with the HubHop client below and drop it into `src/simconnect_mcp/data/` — every `*.json` there is auto-discovered on startup, so it works immediately with no code change.

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

| URI | Type | Content |
|-----|------|---------|
| `simconnect://docs/overview` | `text/markdown` | SimConnect architecture and key concepts |
| `simconnect://docs/simvars/{category}` | `text/markdown` | SimVar reference by category |
| `simconnect://docs/events/{category}` | `text/markdown` | Event reference by category |
| `simconnect://docs/rpn` | `text/markdown` | RPN calculator syntax guide |
| `simconnect://docs/lvars` | `text/markdown` | L-var usage for add-on development |
| `simconnect://docs/best-practices` | `text/markdown` | Common pitfalls and performance tips |
| `simconnect://docs/pmdg/{variant}` | `text/markdown` | PMDG SDK reference; `variant` is `777` or `737` (a leading `B` is accepted, case-insensitive) |
| `simconnect://state/connection` | `application/json` | Live connection status |
| `simconnect://state/aircraft` | `application/json` | Current aircraft title, type, and position |

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
├── dispatch.py            # SimConnectDispatcher — owns the SimConnect dispatch loop
├── simvar_access.py       # SimVarAccessor — generic SimVar reads/writes via data definitions
├── facilities.py          # Facility (*_LIST) message parsing and accumulation
├── pmdg.py                # PMDG 777 SDK structs, CDU rendering, data manager
├── pmdg_ng3.py            # PMDG 737 NG3 SDK structs, CDU rendering, data manager
├── tools/
│   ├── __init__.py        # @handle_simconnect_errors, @require_connection decorators
│   ├── connection_tools.py # connect_to_sim, disconnect_from_sim, get_connection_status
│   ├── simvars.py         # SimVar CRUD via SimVarAccessor (1,080+ vars in the catalog)
│   ├── events.py          # Event trigger/search + built-in catalog
│   ├── lvars.py           # L-var read/write/search/enumerate/browse catalogs/calculator code
│   ├── pmdg.py            # PMDG tools — auto-dispatch to 777 or 737 NG3
│   ├── aircraft.py        # get_aircraft_snapshot — combined state snapshot
│   ├── facilities.py      # Airport/navaid lookup on top of facilities.py
│   ├── flight.py          # load/save flight, load flight plan, spawn AI object
│   ├── hubhop.py          # HubHop preset search, exposed as MCP tools
│   ├── utilities.py       # send_sim_text, set_aircraft_position
│   ├── models.py          # Shared Pydantic result/error models
│   └── formatting.py      # Pagination and markdown-table helpers
├── data/
│   ├── catalog.py         # L-var catalog loader and search engine
│   ├── simvar_catalog.py  # SimVar catalog loader, unit resolution, string-var detection
│   ├── hubhop.py          # MobiFlight HubHop API client (CLI + library), in-memory cache
│   ├── pmdg_777.json      # PMDG 777 catalog (1,607 vars, 28 panels)
│   ├── pmdg_737.json      # PMDG 737 NG3 catalog (1,861 vars, 27 panels)
│   └── simvars_catalog.json  # Built-in SimVar catalog (1,080+ vars, 25 categories)
├── vendor/                # Byte-faithful to upstream except two documented local changes
│   ├── simconnect_mobiflight.py          # unmodified
│   └── mobiflight_variable_requests.py   # log-level demotion + response-channel routing
└── docs/                  # Embedded documentation, served as MCP resources
    ├── overview.md, simvars.md, events.md, rpn.md, lvars.md, best_practices.md
    ├── pmdg_777.md        # PMDG 777 SDK reference
    └── pmdg_737.md        # PMDG 737 NG3 SDK reference
```

The PMDG catalogs are regenerated from the SDK headers via `scripts/parse_pmdg_sdk.py` — it auto-detects the struct name and CDU count, so the same script handles both 777 and 737 NG3.

## Development

```bash
# Run the mocked test suite (no MSFS required; live tests are deselected by default)
uv run pytest

# Run tests with verbose output
uv run pytest -v

# Run only specific test files
uv run pytest tests/test_hubhop.py    # HubHop client tests
uv run pytest tests/test_simvars.py   # SimVar catalog tests

# Lint
uv run ruff check src/ tests/
```

Tests mock SimConnect so the suite above runs without MSFS. `tests/conftest.py` provides a mock SimConnect with realistic SimVar values.

### Running the Live Tests

A second suite under `tests/live/` exercises the real SimConnect DLL and is marked `@pytest.mark.live`. `pyproject.toml` deselects it by default (`addopts = "-m 'not live'"`), so it never runs on a machine without MSFS — including CI.

This suite is deliberately small and stays that way on purpose: a test belongs here only if a self-consistent mock could agree with itself regardless of whether the code is right — for example, a round trip that writes and reads back a single L-var name proves nothing about encoding, because a mangled datum name would make the write and the read-back agree with each other on the wrong variable (see `test_two_distinct_lvars_do_not_collide` in `tests/live/test_live_lvars.py`). What's left after that filter is real DLL/DLL-adjacent behaviour a mock can only assume rather than verify: unit conversion against the physical constant, wire-decode of structs whose third-party bindings this project has already caught wrong once, which real SimVars/events actually accept a write versus reject or silently ignore it, undocumented MobiFlight/WASM protocol quirks, and whether a real PMDG's binary client-data area answers a probe the way its struct decode expects.

Where a live finding can instead be frozen into a committed fixture and replayed offline, that's preferred over a live test: `tests/fixtures/facilities/` holds real SimConnect wire bytes captured once, replayed by the *mocked* `tests/test_facilities_parsing.py` on every run, with no simulator and no flakiness — it pins the same discovery deterministically instead of depending on whatever aircraft happens to be loaded that day.

```bash
uv run pytest -m live
```

Requires MSFS running with an aircraft loaded; a test whose connection attempt fails is skipped rather than failed (see `tests/live/conftest.py`'s `live_manager` fixture). `tests/live/test_live_pmdg.py`'s tests need a real PMDG 737/777 loaded and skip — rather than fail — when the loaded aircraft doesn't look like one (see that file's gate in `tests/live/conftest.py`).

## License

This project is licensed under the MIT License - see the LICENSE file for details.
