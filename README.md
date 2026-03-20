# SimConnect MCP Server

An [MCP (Model Context Protocol)](https://modelcontextprotocol.io/) server that connects AI coding agents to Microsoft Flight Simulator via SimConnect. Unlike consumer flight assistants that offer read-only instrument data, this server is built for **add-on development** — full read/write access to SimVars, L-vars, events, calculator code execution, and embedded documentation.

## What It Does

- **Read/write any SimVar** — altitude, heading, speed, autopilot settings, engine parameters, and hundreds more
- **Read/write L-vars** — aircraft-specific local variables used by add-on developers (Fenix A320, FlyByWire A32NX, Working Title, etc.)
- **Trigger events** — toggle switches, set autopilot modes, control lights, fire custom MobiFlight events
- **Execute RPN calculator code** — run arbitrary Reverse Polish Notation code directly in the sim
- **Search and discover variables** — searchable catalogs of SimVars, events, and aircraft-specific L-vars with human-readable names and valid values
- **Embedded documentation** — SimConnect reference docs served as MCP resources, available offline
- **Aircraft-specific catalogs** — pre-built L-var databases (862 Fenix A320/A321 variables included) with panel groupings, display names, and value descriptions

## Prerequisites

- **Microsoft Flight Simulator** (MSFS 2020 or 2024) running on the same machine
- **Python 3.10+**
- **[uv](https://docs.astral.sh/uv/)** (recommended) or pip
- **MobiFlight WASM Module** (optional, for L-var support) — install in your MSFS Community folder. Download from [MobiFlight](https://www.mobiflight.com/)

## Installation

```bash
# Clone the repository
git clone https://github.com/your-username/simconnect-mcp.git
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

## Tools (26)

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

## Development

```bash
# Run tests
uv run pytest

# Run tests with verbose output
uv run pytest -v

# Lint
uv run ruff check src/ tests/
```

## License

MIT
