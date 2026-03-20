# SimConnect Simulation Variables (SimVars)

SimVars are the primary way to read (and sometimes write) the state of the aircraft and simulation.

## Reading SimVars

Use `get_simvar(name, unit)` to read a variable. The unit parameter is optional — if omitted, the default unit is used.

```
get_simvar("PLANE_ALTITUDE", "feet")      → 35000.0
get_simvar("PLANE_ALTITUDE", "meters")    → 10668.0
get_simvar("AIRSPEED_INDICATED", "knots") → 250.0
```

## Writing SimVars

Only SimVars marked as **settable** can be written. Use `set_simvar(name, value, unit)`.

```
set_simvar("PLANE_LATITUDE", 47.6062, "degrees")
set_simvar("AUTOPILOT_HEADING_LOCK_DIR", 270, "degrees")
```

## Indexed SimVars

Engine and other multi-instance variables use an index (starting at 1):

```
get_simvar("ENG_N1_RPM", index=1)   → Engine 1 N1
get_simvar("ENG_N1_RPM", index=2)   → Engine 2 N1
```

## Aircraft Position

| Variable | Units | Settable | Description |
|----------|-------|----------|-------------|
| PLANE_LATITUDE | degrees | Yes | Aircraft latitude |
| PLANE_LONGITUDE | degrees | Yes | Aircraft longitude |
| PLANE_ALTITUDE | feet | Yes | Aircraft altitude |
| PLANE_HEADING_DEGREES_TRUE | degrees | No | True heading |
| PLANE_HEADING_DEGREES_MAGNETIC | degrees | No | Magnetic heading |
| GROUND_ALTITUDE | feet | No | Terrain elevation below aircraft |
| SIM_ON_GROUND | bool | No | Whether aircraft is on ground |

## Aircraft Speed

| Variable | Units | Settable | Description |
|----------|-------|----------|-------------|
| AIRSPEED_INDICATED | knots | No | Indicated airspeed (IAS) |
| AIRSPEED_TRUE | knots | No | True airspeed (TAS) |
| GROUND_VELOCITY | knots | No | Ground speed |
| VERTICAL_SPEED | feet per minute | No | Vertical speed |
| MACH | mach | No | Mach number |

## Autopilot

| Variable | Units | Settable | Description |
|----------|-------|----------|-------------|
| AUTOPILOT_MASTER | bool | No | AP master switch (use AP_MASTER event to toggle) |
| AUTOPILOT_HEADING_LOCK_DIR | degrees | Yes | Heading bug value |
| AUTOPILOT_ALTITUDE_LOCK_VAR | feet | Yes | Target altitude |
| AUTOPILOT_VERTICAL_HOLD_VAR | feet/min | Yes | Target vertical speed |
| AUTOPILOT_AIRSPEED_HOLD_VAR | knots | Yes | Target airspeed |

## Engine (Indexed)

| Variable | Units | Settable | Description |
|----------|-------|----------|-------------|
| GENERAL_ENG_THROTTLE_LEVER_POSITION:index | percent | Yes | Throttle position |
| ENG_N1_RPM:index | percent | No | N1 RPM percentage |
| ENG_N2_RPM:index | percent | No | N2 RPM percentage |
| ENG_FUEL_FLOW_GPH:index | gallons/hour | No | Fuel flow |
| ENG_OIL_TEMPERATURE:index | rankine | No | Oil temperature |
| ENG_OIL_PRESSURE:index | psf | No | Oil pressure |

## Fuel

| Variable | Units | Settable | Description |
|----------|-------|----------|-------------|
| FUEL_TOTAL_QUANTITY | gallons | No | Total fuel quantity |
| FUEL_TOTAL_QUANTITY_WEIGHT | pounds | No | Total fuel weight |
| FUEL_TOTAL_CAPACITY | gallons | No | Total fuel capacity |

## Electrical

| Variable | Units | Settable | Description |
|----------|-------|----------|-------------|
| ELECTRICAL_MASTER_BATTERY | bool | No | Master battery switch |
| ELECTRICAL_AVIONICS_BUS_VOLTAGE | volts | No | Avionics bus voltage |
| GENERAL_ENG_GENERATOR_ACTIVE:index | bool | No | Generator active |

## Controls

| Variable | Units | Settable | Description |
|----------|-------|----------|-------------|
| ELEVATOR_POSITION | position (-1 to 1) | Yes | Elevator deflection |
| AILERON_POSITION | position (-1 to 1) | Yes | Aileron deflection |
| RUDDER_POSITION | position (-1 to 1) | Yes | Rudder deflection |
| FLAPS_HANDLE_INDEX | number | Yes | Flap handle position |
| GEAR_HANDLE_POSITION | bool | Yes | Gear handle up/down |
| SPOILERS_HANDLE_POSITION | percent | Yes | Spoiler handle position |

## Environment

| Variable | Units | Settable | Description |
|----------|-------|----------|-------------|
| AMBIENT_TEMPERATURE | celsius | No | Outside air temperature |
| AMBIENT_WIND_VELOCITY | knots | No | Wind speed |
| AMBIENT_WIND_DIRECTION | degrees | No | Wind direction |
| BAROMETER_PRESSURE | millibars | No | Barometric pressure |
| SEA_LEVEL_PRESSURE | millibars | No | Sea level pressure |
| AMBIENT_VISIBILITY | meters | No | Visibility |

## Miscellaneous

| Variable | Units | Settable | Description |
|----------|-------|----------|-------------|
| TITLE | string | No | Aircraft title |
| ATC_TYPE | string | No | ATC type designator |
| ATC_ID | string | No | ATC identifier |
| SIMULATION_RATE | number | No | Current sim rate |
| ABSOLUTE_TIME | seconds | No | Absolute sim time |
| ZULU_TIME | seconds | No | Zulu time |
| LOCAL_TIME | seconds | No | Local time |
