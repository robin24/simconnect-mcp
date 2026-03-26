# PMDG 777 Client Data Area Support

## Problem

The PMDG 777 exposes aircraft state and CDU screens via SimConnect Client Data Areas — binary structs shared between the aircraft and external applications. The simconnect-mcp server currently has no support for reading these, so agents must rely on the incomplete MobiFlight bridge L-vars (503 annunciator outputs) instead of the full SDK data (600+ fields including values, quantities, and FMC data). CDU screen reading is not possible at all.

For AI-driven add-on development, agents need complete read access to aircraft state and FMC screens to verify procedures, debug behavior, and iterate on add-on logic.

## Solution

Add PMDG Client Data Area support with three new MCP tools backed by a lazy-subscription architecture.

## Architecture

### New module: `src/simconnect_mcp/pmdg.py`

`PmdgDataManager` class handling all PMDG client data area operations.

**Lifecycle:** Lazy-initialized on first PMDG tool call. Owned by `SimConnectManager` as `self.pmdg: PmdgDataManager | None = None`. Cleaned up on disconnect.

**Subscription model:** On first tool call that needs a data area, subscribe using `RequestClientData` with `SIMCONNECT_CLIENT_DATA_PERIOD_ON_SET` (push updates only when data changes). Cache the latest parsed state. This avoids polling overhead and keeps reads instant after the first subscription.

**Handler registration:** Uses `SimConnectMobiFlight.register_client_data_handler()` to receive `SIMCONNECT_RECV_ID_CLIENT_DATA` messages from the dispatch loop. The handler identifies the source area by definition ID and updates the corresponding cache.

**Struct definitions** as `ctypes.Structure` subclasses mirroring the SDK header:
- `PMDG_777X_DataStruct` — all fields from `PMDG_777X_Data` with correct C types and array sizes
- `PMDG_777X_CDU_Cell` — 3 bytes: Symbol (c_ubyte), Color (c_ubyte), Flags (c_ubyte)
- `PMDG_777X_CDU_Screen` — `CDU_Cell[24][14]` grid + `Powered` (c_bool)

**Field access** uses the pmdg_777 catalog's `sdk_field` and `sdk_index` metadata. For example:
- `ELEC_Battery_Sw_ON` → `data_struct.ELEC_Battery_Sw_ON`
- `ELEC_BusTie_Sw_AUTO_1` → `data_struct.ELEC_BusTie_Sw_AUTO[0]`

### New tools: `src/simconnect_mcp/tools/pmdg.py`

**`get_pmdg_var(name)`** — Read a PMDG 777 data field by name.
- Looks up variable in pmdg_777 catalog for `sdk_field` and `sdk_index`
- Triggers lazy subscription to `PMDG_777X_Data` if not already subscribed
- Reads from cached struct
- Returns value with display name, category, and value description (if discrete values defined)
- Errors if PMDG aircraft not loaded or field not found

**`get_pmdg_cdu(cdu=0)`** — Read a CDU screen.
- `cdu` parameter: 0 (left/captain), 1 (center), 2 (right/F/O)
- Triggers lazy subscription to the requested CDU area
- Returns screen as list of 14 row strings (24 chars each) for easy reading
- Also returns structured grid with per-cell color and flags for agents needing formatting
- Returns `powered: false` if CDU is off
- Errors if CDU broadcast not enabled

**`send_pmdg_event(event_name, parameter=None)`** — Send a PMDG control event.
- Replaces manual `execute_calculator_code("NNN (>K:ROTOR_BRAKE)")` workaround
- Looks up event ID from catalog's `events` field
- Computes ROTOR_BRAKE parameter (offset + 100) automatically
- Accepts `EVT_*` names or catalog variable names with linked events
- Optional parameter for position values

### Integration

**`connection.py`:**
- Add `pmdg: PmdgDataManager | None` to `SimConnectManager`, initialized to `None`
- On disconnect: call `pmdg.cleanup()` if initialized, reset to `None`
- `PmdgDataManager.__init__` takes `SimConnectMobiFlight` instance

**`server.py`:**
- Import and register 3 new tools from `tools/pmdg.py`
- No lifespan changes (lazy init handles lifecycle)

**Dependency:** Requires `SimConnectMobiFlight` (not plain `SimConnect`) for client data dispatch. Tools return clear error if unavailable.

**No changes to existing tools.** Agents use `search_lvars` for discovery, `get_pmdg_var` for reading.

## PMDG Data Areas

| Area | ID Constant | Direction | Content |
|------|------------|-----------|---------|
| `PMDG_777X_Data` | `0x504D4447` | Read | Full aircraft state (~600 fields) |
| `PMDG_777X_Control` | `0x504D4449` | Write | Event + Parameter (8 bytes) |
| `PMDG_777X_CDU_0` | `0x4E477835` | Read | Left CDU screen (24×14 grid) |
| `PMDG_777X_CDU_1` | `0x4E477836` | Read | Center CDU screen |
| `PMDG_777X_CDU_2` | `0x4E477837` | Read | Right CDU screen |

CDU cell colors: WHITE(0), CYAN(1), GREEN(2), MAGENTA(3), AMBER(4), RED(5)
CDU cell flags: SMALL_FONT(0x01), REVERSE(0x02), UNUSED/DIM(0x04)
CDU screen: 24 columns × 14 rows. Special symbols: `\xA1` (←), `\xA2` (→)

## Error Handling

- **CDU broadcast not enabled:** Subscription succeeds but no data within 2s → error with `777_Options.ini` instructions
- **Wrong aircraft:** Data area registration fails → "PMDG 777 not loaded or SDK broadcast not enabled"
- **MobiFlight unavailable:** → "PMDG SDK tools require SimConnectMobiFlight"
- **Stale data:** Cache entries timestamped; data older than 5s flagged as potentially stale

## Testing

Unit tests with mock structs (no sim required):
- **Struct parsing:** Pack known values into bytes, verify correct field extraction for each type (bool, uint8, uint16, short, float, char[])
- **CDU rendering:** Mock screen data with colors/flags, verify text row output and structured grid
- **Event resolution:** Catalog lookup → ROTOR_BRAKE parameter computation (offset + 100)
- **Lazy lifecycle:** Verify subscription only happens on first call, cleanup on disconnect

## Files

| File | Action |
|------|--------|
| `src/simconnect_mcp/pmdg.py` | Create — PmdgDataManager + ctypes struct definitions |
| `src/simconnect_mcp/tools/pmdg.py` | Create — get_pmdg_var, get_pmdg_cdu, send_pmdg_event |
| `src/simconnect_mcp/connection.py` | Modify — add pmdg field, cleanup on disconnect |
| `src/simconnect_mcp/server.py` | Modify — register new tools |
| `tests/test_pmdg.py` | Create — struct parsing, CDU rendering, event resolution tests |

## Prerequisites

User must configure `777_Options.ini`:
```ini
[SDK]
EnableDataBroadcast=1
EnableCDUBroadcast.0=1
EnableCDUBroadcast.1=1
EnableCDUBroadcast.2=1
```
