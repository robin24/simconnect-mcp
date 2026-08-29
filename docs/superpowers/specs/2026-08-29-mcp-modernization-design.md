# SimConnect MCP Server Modernization

## Problem

The server exposes 29 tools across SimVars, events, L-vars, PMDG SDK access, facilities and
utilities. An audit against MCP best practices and against the installed `SimConnect` 0.4.26
library found three classes of problem.

**Tools that cannot work.** Seven tools fail on every call or return fabricated success. Three
were confirmed by executing them, not by reading them:

| Tool | Root cause |
|------|-----------|
| `send_sim_text` | Calls `sm.send_text(...)`; the library method is `sendText(text, timeSeconds, TEXT_TYPE)`. `AttributeError` on every call. |
| `set_simvar` | `AircraftRequests.set()` returns `False` for unknown or non-settable vars. The return value is discarded and the tool reports `status: ok`. |
| `get_simvar` | The unreadable-var branch does `from SimConnect.Constants import DATATYPE_FLOAT64`, which does not exist (`SIMCONNECT_CLIENTDATATYPE_FLOAT64` does). It raises `ImportError`, so the `SIMVAR_NOT_FOUND` + suggestion path below it is unreachable dead code. |
| `get_nearby_airports`, `get_facility_info` | `FacilitiesRequests.Airports` is a helper object, not a list; iterating it raises. `FacilitiesHelper.get()` fires a request and returns `None` — results only ever reach `dump()`, which prints them. |
| `list_lvars` | Sends `MF.LVars.List` to the WASM module; the vendored bridge never reads the response channel. Returns a canned string. |
| `set_aircraft_position` | Accepts `on_ground` and silently ignores it. Writes lat/lon/alt individually instead of using `sm.set_pos()` (`SIMCONNECT_DATA_INITPOSITION`). |
| `search_events` | Imports `EventList` from `SimConnect.RequestList`; it lives in `SimConnect.EventList`. The `ImportError` is swallowed and the catalog silently degrades to 50 hardcoded events. 994 events across 24 categories are available, and `trigger_event` can already fire all of them — so agents can trigger events they cannot discover. |

**Answers that are wrong rather than absent.** `unit` is accepted by `get_simvar`/`set_simvar`,
echoed back in the response, and never applied — `AircraftRequests` binds each variable to a
fixed unit at import time. An agent asking for feet may receive metres and be told it received
feet. `index=0` is dropped by `get_simvar_bulk` and `watch_simvar` (`if idx` instead of
`is not None`). `simvars_catalog.json` is picked up by the L-var catalog's `data/*.json` glob,
so `list_lvar_catalogs` reports a phantom aircraft with zero variables.

**Two paths that can corrupt the transport.** This is a stdio server, so anything written to
stdout lands inside the JSON-RPC stream. `SimConnect.handle_state_event` prints on every
`SIMCONNECT_RECV_ID_SYSTEM_STATE` message, and the facilities dispatch branch calls two `dump()`
methods that also print. Neither fires today only because no code path reaches them; naively
"fixing" facilities by calling `.get()` would break the connection.

Alongside these, the server predates most of the modern MCP surface: no tool annotations, no
Pydantic input models, no output schemas, no pagination, and no service prefix on tool names.

## Scope

One design, three phases. Each phase leaves the server coherent and shippable.

- **Phase 0 — correctness.** Broken tools, wrong answers, thread-safety, transport safety.
- **Phase 1 — MCP surface.** Annotations, schemas, validation, pagination, naming.
- **Phase 2 — capability.** Facilities, L-var enumeration, HubHop, flight/scenario tools.

Out of scope: forking the `SimConnect` package, rewriting the PMDG SDK modules (they are sound),
and changes to the bundled catalog JSON data.

## Architecture

```
server.py                     registration only — explicit per-tool annotations
  └─ tools/*.py               Pydantic-validated tools, typed outputs
      └─ connection.py        SimConnectManager singleton
          ├─ simvar_access.py NEW — generic data-definition read/write
          ├─ dispatch.py      NEW — owns my_dispatch_proc
          ├─ vendor/          MobiFlight bridge, kept faithful to upstream
          └─ SimConnect pkg   DLL handle, connection, AircraftEvents only
```

### New module: `src/simconnect_mcp/simvar_access.py`

`SimVarAccessor` replaces `AircraftRequests` for all SimVar reads and writes. It uses the same
native pattern already proven in `connection.set_lvar()`: `AddToDataDefinition` +
`RequestDataOnSimObject` / `SetDataOnSimObject`.

**Why this rather than a workaround.** `AircraftRequests` holds a hardcoded table of 828
variables, each bound to one fixed unit, and reports failure by returning `None`/`False`. Real
unit support, access to variables outside that table, string variables, and honest write failures
are all unreachable while reads go through it. A generic data-definition path fixes all four at
once.

**Unit resolution.** SimConnect requires a unit string for every data definition, so the accessor
resolves in this order: explicit `unit` argument → the `unit` field for that variable in
`simvars_catalog.json` → `"number"`. This makes the bundled catalog load-bearing, and makes
`msfs_search_simvars` and `msfs_get_simvar` agree on units by construction.

**Definition cache.** Definition IDs are a finite SimConnect resource and must not be created per
call. The accessor caches them keyed by `(name, unit, index)` behind a bounded LRU (capacity 256).
Eviction drops the mapping only; SimConnect definitions are not reclaimed, so the bound exists to
cap growth, not to recycle IDs.

**Error correlation.** After each send the accessor calls `GetLastSentPacketID` and records the
returned packet ID against the pending request. `dispatch.py` matches the incoming exception's
send ID against that record and resolves the request with a typed error.

The installed package's `SIMCONNECT_RECV_EXCEPTION` binding **cannot be used for this**. The SDK
declares `UNKNOWN_SENDID` and `UNKNOWN_INDEX` as static constants alongside three wire fields
(`dwException`, `dwSendID`, `dwIndex`); the package wrongly places both constants *inside*
`_fields_`, giving a 32-byte struct where the wire format is 24. The names are therefore shifted:
its `UNKNOWN_SENDID` (offset 16) is the real `dwSendID`, and its `dwSendID` (offset 20) is the
real `dwIndex`. That the same package models the identical pattern correctly for
`SIMCONNECT_RECV_EVENT.UNKNOWN_GROUP` — a class constant outside `_fields_` — confirms this is a
mistake rather than a deliberate layout. `dispatch.py` therefore declares its own correctly-shaped
struct and casts to that, which is immune to the packaging error either way. (The library's own
`handle_exception_event` reads `UNKNOWN_SENDID`, which is in fact the correct offset.)

Typed errors: `SimVarNotFoundError`, `SimVarNotSettableError`, `UnitMismatchError`,
`SimVarTimeoutError`. A request receiving neither data nor exception within 2000 ms raises
`SimVarTimeoutError`.

**String variables.** `TITLE`, `ATC_ID`, `ATC_TYPE` and similar read via
`SIMCONNECT_DATATYPE_STRING256` and decode to `str`, so no `bytes` reach the JSON layer.

### New module: `src/simconnect_mcp/dispatch.py`

`SimConnectDispatcher(SimConnectMobiFlight)` owns `my_dispatch_proc` and handles the following
without ever delegating to a library branch that prints:

- `SIMCONNECT_RECV_ID_SIMOBJECT_DATA` — resolves `SimVarAccessor` reads
- `SIMCONNECT_RECV_ID_EXCEPTION` — correlates by `dwSendID`, resolves the pending request
- `SIMCONNECT_RECV_ID_{AIRPORT,WAYPOINT,NDB,VOR}_LIST` — parses
  `SIMCONNECT_RECV_FACILITIES_LIST` into Python dicts
- `SIMCONNECT_RECV_ID_SYSTEM_STATE` — intercepted purely to prevent the library's print
- `SIMCONNECT_RECV_ID_CLIENT_DATA` — delegated to registered client-data handlers, preserving
  today's MobiFlight and PMDG behaviour

Everything else falls through to `super()`. `connection.connect()` instantiates
`SimConnectDispatcher` where it currently instantiates `SimConnectMobiFlight`; the existing
fallback chain to plain `SimConnect` is preserved. `vendor/` stays faithful to upstream so it can
be re-synced.

## Phase 0 — correctness

Each item is a distinct fix with its own test.

1. `send_sim_text` → `sm.sendText(text, timeSeconds, TEXT_TYPE)`; expose text type as an enum.
2. `set_simvar` → `SimVarAccessor.write`; propagate typed failures instead of reporting `ok`.
3. `get_simvar` → `SimVarAccessor.read`; delete the dead branch and restore `SIMVAR_NOT_FOUND`,
   with suggestions from `difflib.get_close_matches` over the catalog rather than the current
   four-character prefix heuristic.
4. `search_events` → import from `SimConnect.EventList`, walking the `AircraftEvents` inner
   classes (24 categories, 994 events). Keep the builtin catalog only as a genuine last-resort
   fallback, and fall back when the parsed catalog is *empty*, not only when the import raises —
   the same latent bug exists in `_load_event_catalog` today.
5. `trigger_event` → when `ae.find()` misses, fall back to `sm.map_to_sim_event(name.encode())`
   so arbitrary and third-party events work. Convert negative parameters to two's-complement
   DWORD (`AP_VS_VAR_SET_ENGLISH` and similar take negative values; `send_event` takes an
   unsigned DWORD).
6. `index=0` → use `is not None` in `get_simvar_bulk` and `watch_simvar`.
7. L-var catalog loading → load from an explicit set of aircraft catalog files rather than
   `data/*.json`, so `simvars_catalog.json` is no longer misread as an aircraft catalog.
8. Thread-safety → route `search_lvars`, `list_lvar_panels`, `_detect_pmdg_variant` and the
   `simconnect://state/aircraft` resource through `run_sync`. Today they call `aq.get()` directly
   on the event loop without the lock, violating the invariant stated in CLAUDE.md.
   Aircraft-title detection is extracted into one shared `detect_aircraft_title()` in
   `connection.py` — it needs `run_sync` and the accessor — and used by all four call sites, with
   a short TTL cache (5 s) since it is read on most catalog calls.
9. `asyncio.get_event_loop()` → `get_running_loop()` in `connection.run_sync` and the two inline
   connection tools.
10. `set_aircraft_position` → `sm.set_pos(...)`, honouring `on_ground`, with `airspeed`, `pitch`
    and `bank` exposed. Altitude and heading stay optional as today.
11. `disconnect()` ordering → run `pmdg.cleanup()` / `pmdg_ng3.cleanup()` **before** clearing
    `self.sm`, since cleanup unregisters handlers on it.
12. Logging → default to WARNING, always on stderr; drop the INFO `basicConfig` in `main()`.
    Demote the vendored bridge's per-call `logging.info` in `add_to_client_data_definition`,
    `send_data` and `send_command` to DEBUG; at INFO each L-var read currently emits several
    lines.

## Phase 1 — MCP surface

**Input validation.** One Pydantic model per tool, with
`model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")` and every field carrying a
description and constraints. Notably: `cdu` bounded per variant, `duration_s` on text
`ge=0.1 le=60`, `interval_ms` `ge=50 le=10000`, `duration_s` on watch `ge=1 le=30`, `latitude`
`ge=-90 le=90`, `longitude` `ge=-180 le=180`, `limit` `ge=1 le=200`, `offset` `ge=0`.

**Annotations.** Every tool registers with an explicit `name` and `annotations` (`title`,
`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`). The registration loop in
`server.py` is replaced by explicit registration, since the loop cannot carry per-tool metadata.
Write tools — `msfs_set_simvar`, `msfs_set_lvar`, `msfs_execute_calculator_code`,
`msfs_trigger_event`, `msfs_trigger_custom_event`, `msfs_send_pmdg_event`,
`msfs_set_aircraft_position`, `msfs_load_flight`, `msfs_create_ai_object` — are marked
`readOnlyHint: false, destructiveHint: true`. `msfs_execute_calculator_code` runs arbitrary RPN
in the sim and is currently indistinguishable from a read-only tool to a client.

**Output schemas.** Tools return Pydantic models rather than bare dicts, so FastMCP emits
`outputSchema` and `structuredContent`. All input and output models live in
`src/simconnect_mcp/tools/models.py` so shapes stay consistent across domains. A shared
`ToolError` model replaces the ad-hoc
`{"status": "error", ...}` dicts while keeping the same field names (`status`, `error`,
`message`, `suggestion`) so existing consumers keep parsing.

**Pagination.** All search and browse tools take `limit` (default 25) and `offset`, and return
`total`, `count`, `offset`, `has_more`, `next_offset`. This replaces the hard `[:50]` slices in
`_search_catalog`, `_search_events` and `data/catalog.search_catalog`, which silently truncate
across 4,900+ catalog entries with no signal to the caller.

**Response format.** Search, browse and catalog tools take
`response_format: "markdown" | "json"`, defaulting to markdown — listing hundreds of variables as
a markdown table is materially cheaper in context than JSON. Telemetry reads
(`msfs_get_simvar`, snapshots, PMDG data, CDU) return structured data only; markdown-formatting
numeric values an agent computes on makes them harder to use, not easier. Formatting lives in one
shared `tools/formatting.py` helper, not per tool.

**Naming and consolidation.** All tools take an `msfs_` prefix. Merges:

- `get_aircraft_state` + `get_aircraft_position` + `get_aircraft_systems` →
  `msfs_get_aircraft_snapshot(sections=[...])` over
  `identity | position | engines | systems | autopilot | environment`, defaulting to all. The
  three existing tools differ only by variable list, and `get_aircraft_state` is close to a
  superset of the other two.
- `list_lvar_panels` + `list_lvar_catalogs` → `msfs_browse_lvar_catalog(catalog=None, panel=None)`.

Inventory after Phase 1 — 26 tools, down from 29:

| Group | Tools |
|-------|-------|
| Connection | `msfs_connect`, `msfs_disconnect`, `msfs_get_connection_status` |
| SimVars | `msfs_get_simvar`, `msfs_set_simvar`, `msfs_get_simvars_bulk`, `msfs_search_simvars`, `msfs_list_simvar_categories`, `msfs_watch_simvar` |
| Events | `msfs_trigger_event`, `msfs_search_events`, `msfs_trigger_custom_event` |
| L-vars | `msfs_get_lvar`, `msfs_set_lvar`, `msfs_list_lvars`, `msfs_execute_calculator_code`, `msfs_search_lvars`, `msfs_browse_lvar_catalog` |
| Aircraft | `msfs_get_aircraft_snapshot` |
| Facilities | `msfs_get_nearby_airports`, `msfs_get_facility_info` |
| Utilities | `msfs_send_sim_text`, `msfs_set_aircraft_position` |
| PMDG | `msfs_get_pmdg_var`, `msfs_get_pmdg_cdu`, `msfs_send_pmdg_event` |

Phase 2 adds six more, for 32 total.

**Resources.** Register the two orphaned PMDG docs (`pmdg_777.md`, `pmdg_737.md`) at
`simconnect://docs/pmdg/{variant}`. Extract the duplicated section-filter logic in
`resources/documentation.py` into one helper, and fix its loop: a second `## ` heading that also
matches the category re-enters the section instead of terminating it.

## Phase 2 — capability

**Facilities.** `dispatch.py` parses `SIMCONNECT_RECV_FACILITIES_LIST` into dicts.
`msfs_get_nearby_airports(latitude=None, longitude=None, radius_nm=50, limit=25, offset=0)`
subscribes via `SubscribeToFacilities`, filters by great-circle distance client-side, and defaults
the centre to the current aircraft position. `msfs_get_facility_info(icao, facility_type)` looks
up within the cached list. Both compare ICAO codes after decoding to `str`.

**L-var enumeration.** Extend the MobiFlight bridge with a response-channel reader: subscribe to
the `MobiFlight.Response` client-data area as a string, send `MF.LVars.List`, and accumulate names
until the terminator. `msfs_list_lvars` returns real names with pagination, and degrades to a
clear `MOBIFLIGHT_NOT_AVAILABLE` error rather than today's canned success string.

**HubHop.** `msfs_search_hubhop(query, aircraft=None, system=None, limit=25, offset=0)` wraps the
existing `data/hubhop.py` client, which is fully tested but unreachable from the server. Network
calls go through `run_in_executor` — not the sim lock, since HubHop is HTTP rather than
SimConnect — with a 10 s timeout and an offline-friendly error.

**Flight and scenario.** `msfs_load_flight(path)`, `msfs_save_flight(path, title, description)`,
`msfs_load_flight_plan(path)` and `msfs_create_ai_object(...)` wrap `load_flight`, `save_flight`,
`load_flight_plan` and `createSimulatedObject`. Paths are validated as absolute, and as existing
for loads, before dispatch. These tools are annotated destructive.

## Error handling

All tools keep the existing envelope (`status`, `error`, `message`, `suggestion`) so current
consumers continue to parse. `handle_simconnect_errors` gains cases for the new typed accessor
errors, mapping each to a specific `error` code and an actionable `suggestion`, rather than
today's catch-all `UNEXPECTED` that leaks Python exception text — which is how the
`DATATYPE_FLOAT64` `ImportError` reached callers.

## Testing

TDD throughout: a failing test per fix, before the fix. The existing 159 tests stay green.

`tests/conftest.py` is corrected to return `TITLE` as `bytes`, matching the real sim. The current
`str` mock is why the bytes-handling bugs never surfaced in tests.

A new `tests/live/` suite marked `@pytest.mark.live`, deselected by default via `addopts`, runs
against MSFS 2024 with an aircraft loaded. It covers what mocks cannot: unit conversion
correctness on a known variable, exception correlation for an unknown variable and for a
non-settable write, string variable decoding, `set_pos` including `on_ground`, `sendText`,
facilities parsing, and L-var enumeration. The `SimVarAccessor` and `dispatch` layers are not
considered done until this suite passes against the live sim.

## Files

**New:** `src/simconnect_mcp/simvar_access.py`, `src/simconnect_mcp/dispatch.py`,
`src/simconnect_mcp/tools/formatting.py`, `src/simconnect_mcp/tools/models.py`,
`src/simconnect_mcp/tools/hubhop.py`, `src/simconnect_mcp/tools/flight.py`, `tests/live/` (with
its own `conftest.py`), and unit tests mirroring each new module.

**Modified:** `server.py`, `connection.py`, all of `tools/`, `resources/documentation.py`,
`resources/state.py`, `data/catalog.py`, `vendor/mobiflight_variable_requests.py` (response
channel and log levels), `tests/conftest.py`, `pyproject.toml`, `README.md`, `CLAUDE.md`.

## Project hygiene

- Move dev dependencies to `[dependency-groups] dev` so `uv run pytest` — the command documented
  in CLAUDE.md — works. It currently fails; the suite only runs via `uv run --extra dev pytest`.
- Pin `mcp>=1.26,<2`. `>=1.0.0` spans a large API evolution; 1.26.0 is what is installed and what
  the output-schema and annotation work depends on.
- Add `[tool.ruff]` configuration and a pytest `markers` entry for `live`.
- Update `README.md` and `CLAUDE.md` for the renamed tools, the new modules, and the fact that
  SimVar access no longer goes through `AircraftRequests`.

## Risks

- **Live-sim dependency.** The accessor and dispatch layers cannot be fully verified by mocks.
  Mitigated by the `tests/live/` suite, and by leaving `AircraftEvents` — which works — unchanged.
- **Breaking renames.** Every tool name changes. Acceptable at v0.1.0 for a self-hosted server;
  README and CLAUDE.md are updated in the same phase.
- **Dispatch override.** Taking ownership of `my_dispatch_proc` risks regressing MobiFlight and
  PMDG client-data delivery. Mitigated by delegating `CLIENT_DATA` unchanged, and by covering both
  paths in the live suite.
