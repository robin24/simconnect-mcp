# Extending the Variable Catalogs

This guide covers how to add new aircraft L-var catalogs and how to keep existing ones up to date.

## Overview

The MCP server ships two kinds of variable catalogs:

| Catalog | Location | Purpose |
|---------|----------|---------|
| **SimVars catalog** | `src/simconnect_mcp/data/simvars_catalog.json` | Built-in MSFS simulation variables (~1,080 vars). Rarely needs updating. |
| **Aircraft L-var catalogs** | `src/simconnect_mcp/data/<aircraft>.json` | Per-aircraft local variables. One JSON file per aircraft add-on. |

Aircraft catalogs are auto-discovered on startup -- drop a `.json` file into the `data/` directory and it's live.

## Quick Start: Adding a New Aircraft

### Option A: Generate from HubHop (recommended)

The fastest way to create a catalog for any aircraft with MobiFlight community presets:

```bash
# 1. Check if the aircraft's vendor is on HubHop
python -m simconnect_mcp.data.hubhop --list-vendors

# 2. See what aircraft models exist for the vendor
python -m simconnect_mcp.data.hubhop --list-aircraft --vendor "PMDG"

# 3. Preview what the catalog would contain
python -m simconnect_mcp.data.hubhop --vendor "PMDG" --aircraft "B737-700" --dry-run

# 4. Generate the catalog
python -m simconnect_mcp.data.hubhop \
    --vendor "PMDG" \
    --aircraft "B737-700" \
    --aircraft-name "PMDG 737-700" \
    --title-pattern "PMDG 737" \
    -o pmdg_737.json
```

The file is saved to `src/simconnect_mcp/data/pmdg_737.json` automatically.

### Option B: Generate from Python

```python
from simconnect_mcp.data.hubhop import HubHopClient

client = HubHopClient()
presets = client.fetch_presets(vendor="PMDG", aircraft="B737-700")
catalog = client.build_catalog(
    presets,
    aircraft="PMDG 737-700",
    title_pattern="PMDG 737",
)
client.save_catalog(catalog, "pmdg_737.json")
```

### Option C: Write manually

Create a JSON file in `src/simconnect_mcp/data/`. See the [JSON schema](#json-schema) section below for the format.

## Updating an Existing Catalog

### Merge new vars from HubHop

```bash
# Preview what would be added
python -m simconnect_mcp.data.hubhop \
    --vendor FenixSim \
    --merge fenix_a320.json \
    --dry-run

# Apply the merge
python -m simconnect_mcp.data.hubhop \
    --vendor FenixSim \
    --merge fenix_a320.json
```

Merging skips variables already present (matched by name) and only adds new ones. Existing variable metadata is not overwritten.

### Merge from Python

```python
from simconnect_mcp.data.hubhop import HubHopClient

client = HubHopClient()
presets = client.fetch_presets(vendor="FenixSim")
existing = client.load_catalog("fenix_a320.json")
updated = client.merge_catalog(presets, existing)
client.save_catalog(updated, "fenix_a320.json")
```

### Manual additions

Edit the JSON directly. Add entries to the `variables` array and update the `panels` dict to include the new variable names under the appropriate category key.

## Discovering Variables

Not every aircraft has HubHop coverage. Here are other ways to find L-var names:

### 1. Live enumeration via the MCP server

With MSFS running and the aircraft loaded:

```
msfs_list_lvars()                 -- enumerate registered L-vars (capped at 1000 names; see below)
msfs_search_lvars("ap")           -- search by keyword
msfs_browse_lvar_catalog()        -- list panels in the loaded aircraft's catalog
```

This uses the MobiFlight WASM module to read the sim's internal variable table at runtime. The module caps `msfs_list_lvars()` at 1000 names and still reports the list as complete when it truncates — a busy add-on setup (GSX and similar) can crowd the aircraft's own variables out of the response entirely. Watch for `truncated: true` in the result, and don't treat a live listing as a guaranteed inventory: `msfs_get_lvar` can still read a name that never appeared in it.

### 2. MobiFlight Connector

Open MobiFlight Connector while the aircraft is loaded. Its variable browser shows all registered L-vars with live values, which helps you determine types and ranges.

### 3. FSUIPC / SPAD.neXt

Both tools can export variable lists. FSUIPC's logging mode is particularly useful for capturing which variables change when you interact with a control.

### 4. Open-source aircraft codebases

For open-source aircraft like FlyByWire, grep the source code for `register_named_variable` or similar SimConnect registration calls.

### 5. Community resources

- [HubHop](https://hubhop.mobiflight.com) -- community presets with RPN code containing L-var names
- Aircraft-specific Discord servers often have variable documentation
- FSUIPC forums and wiki pages

## JSON Schema

Each catalog file follows this structure:

```json
{
  "aircraft": "Human-Readable Aircraft Name",
  "title_pattern": "UniqueSubstring",
  "variables": [
    {
      "name": "VAR_NAME",
      "display_name": "Human Readable Name",
      "category": "CATEGORY",
      "prefix": "S",
      "writable": true,
      "values": {
        "0": "Off",
        "1": "On"
      }
    }
  ],
  "panels": {
    "CATEGORY": ["VAR_NAME", "OTHER_VAR"]
  }
}
```

### Field reference

| Field | Required | Description |
|-------|----------|-------------|
| `aircraft` | Yes | Human-readable name shown to users |
| `title_pattern` | Yes | Case-insensitive substring matched against the `TITLE` SimVar for auto-detection |
| `variables` | Yes | Array of variable definitions |
| `variables[].name` | Yes | L-var name exactly as registered in the sim (without `L:` prefix) |
| `variables[].display_name` | Yes | Human-readable name for search results |
| `variables[].category` | Yes | System/panel category (used for grouping and panel lookup) |
| `variables[].prefix` | No | Variable prefix for filtering (e.g. `S`, `I`, `CB`) |
| `variables[].writable` | No | Whether the var accepts writes (default: `false`) |
| `variables[].values` | No | Map of numeric values to human descriptions |
| `panels` | No | Groups variable names by category. Keys should match `category` values. |

### Choosing `title_pattern`

The pattern must be a substring that uniquely identifies the aircraft when matched against the `TITLE` SimVar. To find the right value:

1. Load the aircraft in MSFS
2. Run `msfs_get_simvar("TITLE")`
3. Pick a substring unique to that aircraft (e.g. `"Fenix"`, `"PMDG 737"`, `"FlyByWire"`)

If two catalogs match the same title, only the first match is used.

## Best Practices

### Naming and categories

- **Use UPPER_CASE for category names** (e.g. `AUTOPILOT`, `ELECTRICAL`). This keeps them consistent across catalogs.
- **Keep categories broad.** Prefer `ELECTRICAL` over `ELECTRICAL PANEL ROW 3`. The goal is to help users find variables, not to mirror the physical cockpit layout perfectly. Aim for 15-30 categories.
- **Use the aircraft's own naming conventions for variable names.** Don't rename `S_OH_FUEL_LEFT_1` to something "cleaner" -- users need the exact name to read/write it.
- **Make display names readable** but keep them short. `"Left Tank Pump 1"` is better than `"Overhead Panel Fuel System Left Tank Pump Number 1 Switch"`.

### Writability

- Mark a variable `writable: true` only if writing to it actually does something in the sim.
- For aircraft with prefix conventions (like Fenix), the prefix is a reliable guide: `S_`, `A_`, `E_` are writable; `N_`, `I_`, `B_` are read-only.
- When in doubt, leave `writable` as `false`. The user can always try writing via `msfs_set_lvar` regardless.

### The `values` field

Use `values` for variables with a small set of discrete states:

```json
"values": {"0": "Off", "1": "On"}
"values": {"0": "Off", "1": "NAV", "2": "ATT"}
```

Don't add `values` for continuous ranges (volumes, positions, counters) -- the numeric value is self-explanatory.

### Quality over quantity

- A catalog with 200 well-categorized, accurately-described variables is more useful than one with 2,000 unlabeled names.
- After generating from HubHop, review the output. Fix display names that are just abbreviations. Add `values` maps for important switches.
- The Fenix A320 catalog (`fenix_a320.json`) is a good reference for what a polished catalog looks like.

### Testing after changes

```bash
# Run the test suite
uv run pytest

# Verify the catalog loads without errors
python -c "
from simconnect_mcp.data.catalog import _load_all_catalogs, _catalogs
_load_all_catalogs()
for key, data in _catalogs.items():
    print(f'{key}: {len(data.get(\"variables\", []))} vars')
"
```

## HubHop API Reference

The HubHop client talks to a single bulk endpoint:

```
GET https://hubhop-api-mgtm.azure-api.net/api/v1/msfs2020/presets
```

- Returns ~31,000 presets as a flat JSON array (~17 MB)
- No server-side filtering -- all filtering is done client-side
- No authentication required
- No documented rate limits (but be respectful -- cache results)
- The client caches the response in memory for the lifetime of the `HubHopClient` instance

### Preset fields used for catalog generation

| Field | Used for |
|-------|----------|
| `vendor` | Filtering presets by aircraft developer |
| `aircraft` | Filtering by aircraft model |
| `system` | Mapped to catalog categories |
| `code` | Parsed for L-var names (`(L:VAR)` and `(>L:VAR)` patterns) |
| `presetType` | Not used directly, but `Input` presets indicate writable vars |

### Available vendors (top 10)

Run `python -m simconnect_mcp.data.hubhop --list-vendors` for the full list. Major vendors include: Microsoft, IniBuilds, PMDG, Fly By Wire, FenixSim, Asobo, TFDi, Black Square, Just Flight, Aerosoft.
