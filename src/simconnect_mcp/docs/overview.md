# SimConnect Overview

SimConnect is the SDK interface for communicating with Microsoft Flight Simulator (MSFS). It allows external applications to read and write simulator state, trigger events, and subscribe to data changes.

## Architecture

```
Your Application
  → SimConnect Client (DLL)
    → SimConnect Server (inside MSFS process)
      → Simulation Engine
```

## Key Concepts

### SimVars (Simulation Variables)
SimVars are named variables that represent the state of the aircraft and simulation. Examples:
- `PLANE_LATITUDE` — aircraft latitude in degrees
- `AIRSPEED_INDICATED` — indicated airspeed in knots
- `AUTOPILOT_MASTER` — whether the autopilot is engaged (bool)

SimVars have **units** — requesting the same variable with different units returns converted values. Some SimVars are **settable** (writable), others are read-only.

**Indexed SimVars:** Some variables are indexed (e.g., engine-specific vars). Use `VAR_NAME:index` syntax where index starts at 1.

### Events (Key Events)
Events trigger actions in the simulator, like pressing a button or toggling a switch:
- `PARKING_BRAKES` — toggle parking brakes
- `AP_MASTER` — toggle autopilot
- `THROTTLE_SET` — set throttle (requires a parameter value)

Events can optionally take an integer parameter.

### L-Vars (Local Variables)
L-vars are aircraft-specific variables created by add-on developers. They are NOT part of the core SimConnect SDK — accessing them requires the MobiFlight WASM module.

L-vars allow reading and controlling custom aircraft systems that go beyond what standard SimVars expose. For example, the FlyByWire A320 exposes hundreds of L-vars for its custom systems.

### Calculator Code (RPN)
MSFS includes an RPN (Reverse Polish Notation) calculator that can read/write any variable type and perform complex operations. This is the most flexible way to interact with the sim but requires learning RPN syntax.

### Data Definitions
SimConnect uses a request/response model. You define what data you want (a "data definition"), then request it. The sim sends back the data asynchronously. The Python SimConnect library abstracts much of this.

## Connection Lifecycle

1. **Open** — Create a SimConnect connection (the sim must be running)
2. **Define** — Register data definitions for the SimVars you need
3. **Request** — Request data (one-shot or recurring subscription)
4. **Receive** — Process data callbacks
5. **Close** — Disconnect cleanly with `SimConnect.exit()`

## Python SimConnect Library

This server uses the `SimConnect` Python package which wraps the native SimConnect DLL:

- `SimConnect()` — opens connection
- `AircraftRequests(sm)` — simplified SimVar read/write interface
- `AircraftEvents(sm)` — event triggering interface
- `FacilitiesRequests(sm)` — airport/navaid data

The `AircraftRequests` object caches values for a configurable time (`_time` parameter in ms) to avoid hammering the sim with requests.

## Thread Safety

The SimConnect DLL is **NOT thread-safe**. All calls must be serialized. This server uses a `threading.Lock` to ensure only one call happens at a time, and `asyncio.run_in_executor` to avoid blocking the async MCP event loop.
