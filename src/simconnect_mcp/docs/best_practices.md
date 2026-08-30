# SimConnect Best Practices

## Connection Management

### Do: Use a singleton connection
SimConnect allows one connection per process. Create it once and reuse it.

### Do: Handle disconnections gracefully
The sim can close at any time. Always handle `ConnectionError` and `OSError`.

### Do: Clean up on exit
Always call `SimConnect.exit()` when done. Leaked connections can cause issues.

### Don't: Reconnect in a tight loop
If the connection drops, wait a few seconds before retrying. The sim may be shutting down.

## Data Requests

### Do: Use AircraftRequests with caching
```python
aq = AircraftRequests(sm, _time=2000)  # Cache for 2 seconds
value = aq.get("PLANE_ALTITUDE")  # Returns cached value if recent
```

### Do: Batch related reads
Use `msfs_get_simvars_bulk()` to read multiple variables at once rather than making many individual calls.

### Don't: Poll at high frequency
Reading SimVars too frequently wastes CPU. For most development purposes, 1-2 Hz is sufficient.

### Don't: Request variables you don't need
Each registered data definition consumes resources. Only request what you'll use.

## Thread Safety

### Do: Serialize all SimConnect calls
The SimConnect DLL is not thread-safe. Use a lock:
```python
with sim_lock:
    value = aq.get("PLANE_ALTITUDE")
```

### Do: Run blocking calls in an executor
When using async code:
```python
value = await asyncio.run_in_executor(None, lambda: aq.get("PLANE_ALTITUDE"))
```

## SimVar Gotchas

### Units matter
`msfs_get_simvar("PLANE_ALTITUDE", "feet")` and `msfs_get_simvar("PLANE_ALTITUDE", "meters")` return different numbers. Always specify the unit you want.

### Indexed variables start at 1
Engine 1 is index 1, not 0: `msfs_get_simvar("ENG_N1_RPM", index=1)`

### Some SimVars need power
Avionics variables may return 0 or None if the electrical system isn't powered.

### String SimVars are special
Variables like `TITLE` return strings. Most return numeric (float) values.

### Settable ≠ always writable
Some SimVars marked as settable may be overridden by the sim's flight model or autopilot.

## Event Handling

### Use events for actions, SimVars for state
Read the current state with SimVars, change it with events:
```
# Wrong: msfs_set_simvar("AUTOPILOT_MASTER", 1)  — may not work
# Right: msfs_trigger_event("AP_MASTER")           — toggles the AP
```

### Parameter ranges vary
- Toggle events: no parameter
- Set events: 0–16383
- Heading: 0–360
- Radio: BCD16 encoded

## L-Var Best Practices

### Discover before assuming
Use `msfs_list_lvars()` to see what's available. Don't guess L-var names.

### Rate-limit L-var reads
Each MobiFlight L-var read goes through WASM — slower than native SimVars.

### Check if MobiFlight is loaded
Always verify `mobiflight_available` before attempting L-var operations.

## Testing Add-ons

### Use the SimConnect MCP server for rapid iteration
1. Start MSFS with your aircraft
2. Connect the MCP server
3. Use `msfs_get_simvar` / `msfs_get_lvar` to inspect live state
4. Use `msfs_trigger_event` / `msfs_set_lvar` to test interactions
5. Use `msfs_watch_simvar` to monitor behavior over time

### Teleport for scenario testing
Use `msfs_set_aircraft_position()` to quickly set up test scenarios (approach, cruise, etc.).

### Use `msfs_send_sim_text()` for visual feedback
Display debug messages in the sim to confirm your add-on is responding.

## Performance

- Limit SimVar polling to what you need
- Use `AircraftRequests` caching (`_time` parameter)
- Batch reads with `msfs_get_simvars_bulk()`
- Don't enumerate all L-vars repeatedly — cache the list
- Calculator code runs in the sim's gauge loop — keep it short
