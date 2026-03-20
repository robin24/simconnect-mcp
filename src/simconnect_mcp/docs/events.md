# SimConnect Events (Key Events)

Events trigger actions in the simulator. Use `trigger_event(name, parameter)` to fire them.

## How Events Work

Events are like pressing buttons or toggling switches. Some events are simple toggles (no parameter needed), while others require an integer parameter specifying the target value.

```
trigger_event("PARKING_BRAKES")           → Toggle parking brakes
trigger_event("THROTTLE_SET", 8192)       → Set throttle to ~50%
trigger_event("HEADING_BUG_SET", 270)     → Set heading bug to 270°
```

## Parameter Ranges

- **Toggle events:** No parameter needed (or 0)
- **Set events:** Usually 0–16383 (SimConnect's standard range)
- **Degree events:** 0–360 (heading, course)
- **BCD16 events:** Radio frequencies encoded in BCD16 format

## Aircraft Controls

| Event | Parameter | Description |
|-------|-----------|-------------|
| PARKING_BRAKES | — | Toggle parking brakes |
| FLAPS_UP | — | Retract flaps fully |
| FLAPS_DOWN | — | Extend flaps one notch |
| FLAPS_1 | — | Set flaps position 1 |
| FLAPS_2 | — | Set flaps position 2 |
| FLAPS_3 | — | Set flaps position 3 |
| GEAR_TOGGLE | — | Toggle landing gear |
| SPOILERS_TOGGLE | — | Toggle spoilers |
| SPOILERS_ARM_TOGGLE | — | Arm/disarm spoilers |

## Autopilot

| Event | Parameter | Description |
|-------|-----------|-------------|
| AP_MASTER | — | Toggle autopilot master |
| AP_HDG_HOLD | — | Toggle heading hold |
| AP_ALT_HOLD | — | Toggle altitude hold |
| AP_NAV1_HOLD | — | Toggle NAV1 hold |
| AP_APR_HOLD | — | Toggle approach mode |
| AP_VS_HOLD | — | Toggle vertical speed hold |
| AP_SPD_VAR_SET | speed (knots) | Set AP target speed |
| AP_ALT_VAR_SET_ENGLISH | altitude (feet) | Set AP target altitude |
| HEADING_BUG_SET | heading (degrees) | Set heading bug |
| AP_VS_VAR_SET_ENGLISH | fpm | Set vertical speed |

## Engine

| Event | Parameter | Description |
|-------|-----------|-------------|
| THROTTLE_SET | 0–16383 | Set all throttles |
| THROTTLE1_SET | 0–16383 | Set engine 1 throttle |
| THROTTLE2_SET | 0–16383 | Set engine 2 throttle |
| MIXTURE_SET | 0–16383 | Set mixture all engines |
| PROPELLER_SET | 0–16383 | Set propeller all engines |
| TOGGLE_STARTER1 | — | Toggle engine 1 starter |
| TOGGLE_STARTER2 | — | Toggle engine 2 starter |
| ENGINE_AUTO_START | — | Auto-start all engines |
| ENGINE_AUTO_SHUTDOWN | — | Auto-shutdown all engines |

## Electrical

| Event | Parameter | Description |
|-------|-----------|-------------|
| TOGGLE_MASTER_BATTERY | — | Toggle master battery |
| TOGGLE_MASTER_ALTERNATOR | — | Toggle master alternator |
| TOGGLE_AVIONICS_MASTER | — | Toggle avionics master |

## Lights

| Event | Parameter | Description |
|-------|-----------|-------------|
| LANDING_LIGHTS_TOGGLE | — | Toggle landing lights |
| STROBES_TOGGLE | — | Toggle strobe lights |
| TOGGLE_BEACON_LIGHTS | — | Toggle beacon |
| TOGGLE_NAV_LIGHTS | — | Toggle nav lights |
| TOGGLE_TAXI_LIGHTS | — | Toggle taxi lights |
| TOGGLE_CABIN_LIGHTS | — | Toggle cabin lights |

## Radio

| Event | Parameter | Description |
|-------|-----------|-------------|
| COM_RADIO_SET | BCD16 freq | Set COM1 frequency |
| COM2_RADIO_SET | BCD16 freq | Set COM2 frequency |
| NAV1_RADIO_SET | BCD16 freq | Set NAV1 frequency |
| NAV2_RADIO_SET | BCD16 freq | Set NAV2 frequency |
| XPNDR_SET | BCD16 code | Set transponder code |

### BCD16 Encoding

Radio frequencies must be BCD16 encoded. For example, frequency 124.850:
1. Remove the leading "1": 24850
2. Convert each digit to 4-bit BCD: 0x24850

## Simulation

| Event | Parameter | Description |
|-------|-----------|-------------|
| PAUSE_TOGGLE | — | Toggle pause |
| PAUSE_ON | — | Pause sim |
| PAUSE_OFF | — | Unpause sim |
| SIM_RATE_INCR | — | Increase sim rate |
| SIM_RATE_DECR | — | Decrease sim rate |
| FREEZE_LATITUDE_LONGITUDE_TOGGLE | — | Freeze position |
| FREEZE_ALTITUDE_TOGGLE | — | Freeze altitude |

## Custom Events (MobiFlight)

Custom aircraft events can be triggered via `trigger_custom_event()` when MobiFlight WASM is installed. These follow the naming convention of the aircraft developer:

```
trigger_custom_event("MobiFlight.AS1000_PFD_SOFTKEYS_1")
trigger_custom_event("MobiFlight.A32NX_FCU_HDG_INC")
```
