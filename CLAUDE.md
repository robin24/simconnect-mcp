# CLAUDE.md

## Project Overview

SimConnect MCP Server — an MCP server that gives AI agents full read/write access to Microsoft Flight Simulator via SimConnect. Built for the add-on development use case, not consumer flight assistance.

## Architecture

```
MCP Protocol (FastMCP, stdio)
  → server.py (tool/resource/prompt registration, lifespan)
    → tools/*.py (domain modules)
      → connection.py (SimConnectManager singleton)
        → simvar_access.py (SimVarAccessor — generic SimVar reads/writes, replaces AircraftRequests)
        → facilities.py (facility *_LIST message parsing/accumulation, feeds tools/facilities.py)
        → dispatch.py (SimConnectDispatcher — owns the dispatch loop; both of the above depend on it)
          → vendored SimConnectMobiFlight (client-data support for the WASM bridge)
            → SimConnect DLL / MSFS
            → MobiFlight WASM Module (L-vars, calculator code)
```

### Key Design Decisions

- **Singleton connection** — SimConnect allows one connection per process. `SimConnectManager` is a thread-safe singleton with lazy-connect.
- **`run_in_executor` + `threading.Lock`** — all SimConnect DLL calls are blocking and not thread-safe. Every call goes through `run_sync()` which acquires a lock and runs in an executor to avoid blocking the async MCP event loop.
- **MobiFlight optional** — the vendored `SimConnectMobiFlight` (from [Koseng/MSFSPythonSimConnectMobiFlightExtension](https://github.com/Koseng/MSFSPythonSimConnectMobiFlightExtension)) is a drop-in subclass of `SimConnect` that adds client-data support for the WASM bridge. If it fails to load, core SimVar/event tools still work; only L-var tools degrade.
- **`SimVarAccessor` replaced `AircraftRequests`** — SimVar access no longer goes through `SimConnect.AircraftRequests`, which holds a hardcoded table of ~828 variables each bound to one fixed unit and signals failure by returning `None`/`False`. `simvar_access.py`'s `SimVarAccessor` builds data definitions directly (`AddToDataDefinition` + `RequestDataOnSimObject`/`SetDataOnSimObject`), which is what makes unit selection, reading variables outside the table, string variables, and honest write failures possible. `SimConnectManager.set_lvar` routes through the same accessor rather than a hand-rolled copy of the pattern.
- **`SimConnectDispatcher` owns `my_dispatch_proc`.** This is the single most important invariant in the codebase. `dispatch.py`'s `SimConnectDispatcher` (a subclass of the vendored `SimConnectMobiFlight`) takes over the SimConnect dispatch loop so SimVar reads/writes and exceptions can be correlated back to the call that caused them, and so facility (`*_LIST`) messages can be parsed directly. **Anything added to the dispatch loop must never fall through to the library's `SYSTEM_STATE` or `*_LIST` branches** — both call `print()` (`handle_state_event`, and `dump()` on the facility objects) and would corrupt the JSON-RPC stream this server speaks over stdio.
- **The vendored bridge has exactly two documented local changes** — see the header comment in `vendor/mobiflight_variable_requests.py`: per-call logging demoted from INFO to DEBUG, and response-channel strings (definition ID 0) routed to registered handlers instead of being logged as "DefinitionID not found" and dropped. Both must survive a re-sync from upstream — the header comment is the only thing standing between a re-sync and silently reverting them. `vendor/simconnect_mobiflight.py` carries no local changes at all.
- **Native L-var writes** — `SimConnectManager.set_lvar()` (which the `msfs_set_lvar` tool calls) uses `AddToDataDefinition` + `SetDataOnSimObject` (the native SimConnect API, via `SimVarAccessor`), NOT the MobiFlight RPN `set()` command. This is critical because proprietary aircraft like the Fenix ignore MobiFlight RPN writes but respond to native SimConnect data writes.
- **`clear_sim_variables()` on connect** — the MobiFlight WASM module retains stale variable registrations from prior sessions. Without clearing on connect, all reads return 0.
- **Tool naming and contract** — every tool is registered in `server.py` with the `msfs_` prefix and explicit `ToolAnnotations` (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `title`); there is no annotation-free registration path. Every tool function returns `SomeResult | ToolError`, never a bare dict and never a fabricated success — a new tool must follow both conventions.

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
├── pmdg_detect.py         # PMDG variant detection/probe, shared by tools/pmdg.py and tools/lvars.py
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
├── resources/
│   ├── documentation.py   # Embedded docs served as MCP resources
│   └── state.py           # Live connection/aircraft state resources
├── prompts/
│   └── templates.py       # debug_simvar, analyze_aircraft_vars, rpn_helper, etc.
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
└── docs/                  # Embedded markdown documentation, served as MCP resources
    ├── overview.md, simvars.md, events.md, rpn.md, lvars.md, best_practices.md
    └── pmdg_777.md, pmdg_737.md
```

## Known Sim Behaviours

Measured against a live sim during this project. Each cost real investigation to establish, so they are recorded here rather than left to be rediscovered. This is a reference, not a narrative.

- **MSFS 2024 packs facility records on the wire.** The installed `SimConnect.Enum` bindings for `SIMCONNECT_DATA_FACILITY_AIRPORT/WAYPOINT/NDB/VOR` are stale in two independent ways: they declare a single `Icao[9]` field where MSFS 2024 actually sends `Ident[6]` + `Region[3]`, and they assume ordinary 8-byte-aligned structs where the wire records are packed with no padding. `facilities.py` defines its own structs with `_pack_ = 1` and the Ident/Region split; verified strides are AIRPORT 33, WAYPOINT 37, NDB 41, VOR 77. Committed fixtures in `tests/fixtures/facilities/` replay real captured bytes, so a regression back to 8-byte alignment fails the suite immediately.
- **Only the airport facility list is world-wide** (85,249 records, unrelated to the aircraft's position). Waypoints, NDBs and VORs are a "reality bubble" scoped to wherever the aircraft currently is — all measured within ~193 nm of it — so they must not be cached across a reposition. Only AIRPORT is cached (`SimConnectManager._facility_cache`); the other three are recollected on every call.
- **`MF.LVars.List` is capped at 1000 names and still sends its end sentinel**, so a truncated response is indistinguishable from a complete one at the protocol level. `msfs_list_lvars` reports `truncated: true` when the raw pre-filter wire count hits the cap. Treat any one source — a catalog or a live listing — as a starting point, not a guaranteed inventory: `msfs_get_lvar` reads any name you supply, whether or not it surfaced there.
- **The WASM module ignores a repeated identical command.** Sending `MF.LVars.List` twice in a row returns nothing the second time. A trailing space does not help — this is not a byte-level dedupe check — and the state survives reconnection, so it lives in the WASM module itself, not the client. The fix that works reliably: send a different, zero-side-effect RPN command (a bare `MF.SimVars.Set.1` literal, which stores nothing) immediately before `MF.LVars.List` to re-arm it. See the call site in `tools/lvars.py` before "simplifying" this away.
- **Only the PMDG 777's LEFT CDU accepts key events.** The aircraft has three
  CDUs and `msfs_get_pmdg_cdu` reads all three, but `EVT_CDU_C_*` and
  `EVT_CDU_R_*` are accepted by SimConnect and then ignored by the aircraft.
  Measured on a live 777-200ER through *both* dispatch paths -- the
  `(>K:ROTOR_BRAKE)` carrier and a direct `PMDG_777X_Control` client-data write
  that returned `S_OK` -- while the matching `EVT_CDU_L_*` events paged the
  captain's screen every time. So neither the event id nor the transport is at
  fault, and the catalog ids are genuine (blocks at offsets 328-400 left,
  401-473 right, 653-725 center, each contiguous with its neighbours). Because
  nothing below reports a failure, `send_pmdg_event` refuses these with
  `PMDG_EVENT_NOT_IMPLEMENTED` rather than returning a success no caller could
  falsify -- see `pmdg.inert_cdu_event`. **This is 777-only:** the NG3 has two
  CDUs and its `EVT_CDU_R_*` is the first officer's real, working unit, so
  `pmdg_ng3.py` has no equivalent and the check is gated on the catalog. A
  prefix test applied to both catalogs would silently break the 737's F/O CDU.

- **PMDG aircraft ignore default key events.** A `(>K:PARKING_BRAKES)` that does nothing on a loaded PMDG aircraft is the aircraft's own behaviour — PMDG reimplements most default events internally rather than responding to them — not a broken mechanism. Use `msfs_send_pmdg_event` for PMDG control surfaces instead of `msfs_trigger_event`.

- **Saving a flight freezes SimConnect for several seconds.** `FlightSave` writes its `.FLT` almost immediately (~0.13s for a 69 KB file) and returns `S_OK`, but MSFS then stops answering SimConnect requests entirely while it finishes — measured from 0.7s up to 14.5s on the same aircraft and session, with no identified cause for the variance. During that window every read fails with a timeout whose message blames a paused or loading sim, which is wrong. `msfs_save_flight` therefore polls until the sim answers again before returning, so its contract is "when this returns, the sim is usable"; `msfs_load_flight` and `msfs_load_flight_plan` use the same wait (a load's own stall measured only ~0.9s). Do not remove that wait, and do not "fix" it by reconnecting — the connection is fine, the sim is busy.

- **PMDG event parameters are not always the documented switch positions.** `EVT_OH_ELEC_BATTERY_SWITCH` with `parameter=1` behaves as a *toggle* (0 → 1, then 1 → 0); `parameter=0`, `2` and no parameter all do nothing, even though the catalog's `values` map for `ELEC_BatSelector` reads `{"0": "OFF  1", "2": "ON"}` (itself visibly malformed). Guarded switches also need their guard lifted first. When a PMDG event appears not to work, read the field back rather than trusting the catalog's value map — the tool reports the send honestly as unconfirmed precisely because the sim gives no acknowledgement.

## Extending the Aircraft L-Var Catalog

The catalog system provides searchable, human-readable L-var databases per aircraft. When `msfs_search_lvars("seatbelt")` is called, the server auto-detects the loaded aircraft in two steps: PMDG's own client-data-area probe first (`pmdg_detect.detect_or_probe_pmdg_catalog` — the same authoritative probe `msfs_get_pmdg_var`/`msfs_get_pmdg_cdu` use, confirming which SDK is actually loaded regardless of what `TITLE`/`ATC_MODEL` say), then every catalog's own `title_pattern` matched against `TITLE`/`ATC_MODEL` as the fallback. The fallback is the *only* mechanism available to a third-party catalog dropped into `data/` (e.g. a regenerated Fenix catalog — see "Fenix A320/A321 Notes" below), since the probe only knows about PMDG. Both `msfs_search_lvars` and `msfs_browse_lvar_catalog` report which of the two resolved the catalog in `message`, so a caller can tell a live-confirmed detection apart from a plain text match — and when neither finds anything, the search spans every catalog and says so rather than guessing.

A real, live-verified gap this two-step order fixes: a PMDG 737-800's `TITLE` can read `'737-800 PAX SSW TC'` — no "PMDG" substring at all — which fails every bundled catalog's `title_pattern` on its own. Before the probe was added to catalog auto-detection, `msfs_search_lvars` on that aircraft silently searched every bundled catalog (all PMDG) instead of scoping to the one actually loaded, with only an easy-to-miss footer note distinguishing the two.

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
- **By discovery:** use `msfs_list_lvars` to enumerate variables on a loaded aircraft (capped at 1000 names — see "Known Sim Behaviours" above), then categorize by prefix patterns
- **The `title_pattern`** should match a unique substring from `msfs_get_simvar("TITLE")`

## Fenix A320/A321 Notes

The Fenix uses a proprietary internal system with specific patterns you need to know when interacting with it.

**No catalog is bundled for the Fenix.** `data/fenix_a320.json` (1,433 plain L-vars, no SDK struct fields) was removed once HubHop's own `FenixSim` coverage overtook it (2,273 presets, community-maintained, current the moment Fenix ships an update). Search it live with `msfs_search_hubhop(vendor="FenixSim")`, or regenerate a local catalog file with `data/hubhop.py`'s CLI --
`python -m simconnect_mcp.data.hubhop --vendor FenixSim --aircraft-name "Fenix A320/A321" --title-pattern Fenix -o fenix_a320.json` -- and drop it into `src/simconnect_mcp/data/`; every `*.json` there is auto-discovered on startup, so it starts working immediately with no code change. The prefix/FCU/button knowledge below still applies regardless of whether a catalog is loaded, since it describes the sim's behaviour, not catalog data.

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

The Fenix responds to native SimConnect `SetDataOnSimObject` for L-var writes, NOT to MobiFlight RPN `set()` commands. The `msfs_set_lvar` tool already uses the native method.

## Running Tests

```bash
uv run pytest           # all tests (mocked; no MSFS required)
uv run pytest -v        # verbose
uv run pytest -k search # only search tests
uv run pytest -m live   # live suite against a real, running MSFS instance
```

Tests mock SimConnect so the default run happens without MSFS. The `conftest.py` fixture provides a mock SimConnect with realistic SimVar values.

### What the live suite is for

`tests/live/` is deliberately small, and stays that way by one rule: **a
live test earns its place only if a self-consistent mock could agree with
itself regardless of whether the code is right.** A round trip on one L-var
name proves nothing about encoding, because a mangled datum name would make
the write and the read-back agree with each other on the wrong variable —
see `test_two_distinct_lvars_do_not_collide` in `test_live_lvars.py` for the
test that actually closes that gap, and its docstring for the full
reasoning. Anything a mock *can* settle — pure Python logic, cache
bookkeeping, message wording, string matching against a catalog — belongs
in the mocked suite instead, however tempting it is to also check it live.

What's left after that filter is a short, specific list: does the real DLL's
own unit conversion match the physical constant we assume; does a real
SimConnect wire reply decode correctly through structs this project has
already caught the installed package mis-declaring once (`RecvException` in
`dispatch.py`); does a real airframe's calculated variables actually reject
a write (or silently ignore it, which is the bug this layer replaces); does
the real MobiFlight WASM module's undocumented quirks (repeated-command
drop, definition-ID-0 routing) behave the way the workaround assumes; and
whether a real PMDG's binary client-data area answers a probe the way its
struct decode expects. None of that can be established by a mock inventing
its own answer and checking it against itself.

Prefer freezing a live finding into a committed fixture over adding another
live test. `tests/fixtures/facilities/` holds real SimConnect wire bytes
captured once from a live session, replayed by the *mocked*
`tests/test_facilities_parsing.py` on every run, with no simulator and no
flakiness — strictly better than a live test for anything it can cover,
because it pins the exact discovery deterministically instead of
re-discovering it (or failing to) on whatever aircraft happens to be loaded
that day. Reach for that pattern first; reach for `tests/live/` only for the
residue no fixture can freeze because the claim is about behaviour, not
about a fixed byte layout.

`tests/live/test_live_pmdg.py`'s tests need a real PMDG 737/777 loaded and
skip -- rather than fail -- when the loaded aircraft doesn't look like one
(see that file's gate in `conftest.py`); a connection failure skips the
whole suite the same way.

The live suite under `tests/live/` is marked `@pytest.mark.live` and deselected by default (`pyproject.toml`'s `addopts = "-m 'not live'"`), so it never runs on a machine without MSFS — including CI. It requires MSFS running with an aircraft loaded; a connection failure is skipped rather than failed, but some files assume a specific aircraft is loaded (check that file's own module docstring before running it against an arbitrary airframe).

## Releasing

Pushing a `v*` tag is the whole release. `.github/workflows/release.yml` runs
the mocked suite on Windows, rewrites the version from the tag (minus its `v`)
into `pyproject.toml` and `server.json` via `scripts/set_version.py`, builds,
publishes to PyPI, publishes to the MCP Registry, and commits the bump back to
`main` in a separate job.

```bash
git tag v0.3.0
git push origin v0.3.0
```

Notes for anyone changing this:

- **Do not hand-edit the version in more than one place.** `pyproject.toml` is
  authoritative; `src/simconnect_mcp/__init__.py` resolves its own version from
  installed metadata, and `server.json` is rewritten by the release. Tests in
  `tests/test_packaging.py` fail if these drift apart.
- **`scripts/set_version.py` is unit-tested and pins its writes to LF** so it
  produces identical bytes on the Linux runner and a Windows checkout. It is
  covered by CI's lint step for the same reason: a bug there ships a wrong
  version number to PyPI.
- **Both publish steps authenticate via OIDC, not stored tokens** — hence
  `id-token: write` on the `publish` job. PyPI additionally requires a trusted
  publisher registered once through the PyPI web UI (project `simconnect-mcp`,
  owner `robin24`, repo `simconnect-mcp`, workflow `release.yml`, no
  environment). Without it, `uv publish` fails with an authentication error.
- **The registry verifies PyPI ownership by finding `mcp-name:
  io.github.robin24/simconnect-mcp` in the package description**, which is
  `README.md`. Removing that marker from the README breaks registry publishing
  even though nothing else notices.
- **CI must stay on `windows-latest`** — `dispatch.py` and `facilities.py`
  import `ctypes.wintypes` at module level, which does not import on Linux.
