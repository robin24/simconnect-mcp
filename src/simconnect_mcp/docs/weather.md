# Weather Presets (.WPR Files)

MSFS removed the legacy SimConnect weather API (`WeatherSetModeCustom`,
`WeatherSetObservation`) — there is no SimConnect call left that sets weather
directly. The only route left is a `.WPR` preset file, in the XML format the
simulator's own weather menu reads. `msfs_write_weather_preset` builds and
validates one of these files and writes it to disk.

## Writing vs. Applying

`msfs_write_weather_preset` **writes the file only** — it never touches a
running sim, and calling it does not change the weather. Unlike most write
tools in this server it does not need an active connection either: it is
pure file I/O, so `msfs_connect` is not a prerequisite.

To make a written preset take effect, do one of:

- **Open the sim's weather menu** and select it — any file in the preset
  folder appears there.
- **Point a `.FLT`'s `[Weather]` section at it** (`UseWeatherFile=True`,
  `WeatherPresetFile=<path>`) and reload with `msfs_load_flight`. This resets
  the aircraft, so it's the right choice for a fresh flight, not a preset
  swap mid-session.

`bytes_written`/`path` in the result confirm the write; nothing in this
tool's result confirms the weather actually changed, because neither of the
above happens as part of the call.

## Documented Ranges (Enforced)

These come from the MSFS SDK's "Weather XML (WPR File) Properties" page
(docs.flightsimulator.com/msfs2024) and the older `Weather_Definitions` page.
They are enforced as Pydantic bounds: a value outside one of these ranges is
rejected with `INVALID_WEATHER` and nothing is written.

| SDK element | Tool field | Enforced range | Unit |
|---|---|---|---|
| `CloudLayerDensity` | `density` (per cloud layer) | 0.0 – 1.0 | (0-1) |
| `CloudLayerScattering` | `scattering` | 0.0 – 1.0 | (0-1) |
| `CloudLayerAltitudeBot` / `CloudLayerAltitudeTop` | `altitude_bot_m` / `altitude_top_m` | none stated (`altitude_bot_m` may be negative) | m |
| `WindLayerAltitude` | `altitude_m` (per wind layer) | none stated | m |
| `WindLayerAngle` | `angle_deg` | 0.0 – 360.0 | degrees |
| `WindLayerSpeed` | `speed_kt` | none stated (code requires ≥ 0) | knts |
| `MSLPressure` | `msl_pressure_pa` | 50000.0 – 130000.0 | pa |
| `MSLTemperature` | `msl_temperature_k` | none stated (code requires > 0) | k |
| `AerosolDensity` | `aerosol_density` | default 1.0, no max stated (code requires ≥ 0) | m |
| `Precipitations` | `precipitation_mm_h` | 0.0 – 100.0 | mm/h |
| `SnowCover` | `snow_cover_m` | 0.0 – 4.0 | m |
| `ThunderstormIntensity` | `thunderstorm_intensity` | 0.0 – 1.0 | (0-1) |

Two more fields are enforced to the same 0.0–1.0 bound despite appearing on
**neither** SDK page at all: `coverage` (`CloudLayerCoverage`) and
`pollution` (`Pollution`). Both are written by Active Sky's own presets,
which the simulator accepts, and the bound is inferred from the
`Unit="(0 - 1)"` string those elements carry in working files on disk — not
from SDK prose. Separately, a cloud layer's `altitude_bot_m` must be less
than its `altitude_top_m`; that's a coherence check (an inverted layer
describes no volume), not an SDK limit either.

`cloud_layers` and `wind_layers` carry no enforced minimum or maximum count.
Both SDK pages say a preset "may have multiple" of each, with no stated
minimum — omit either argument for one sensible default layer (see the
example below), or supply as many as you want.

## Measured Limits (Advisory, Reported in `warnings`)

These were probed once, live, against one MSFS 2024 install (2026-09-01,
EDDF) — not verified across versions or installs. That's exactly why they
are **never enforced**: a value this tool rejected outright might work fine
on a different machine. Instead they're reported back in the result's
`warnings` list so an agent knows which of its settings the simulator is
likely to alter.

| Field | Measured limit | What happens beyond it |
|---|---|---|
| `speed_kt` (wind layer) | ~150 kt | The sim clamps it. Measured: 185 kt gave 92–98 kt at 4 m AGL, and 400 kt collapsed the *entire* wind field to 0.3 kt while every other field in the same preset still applied. |
| `msl_pressure_pa` | ~95000 pa floor | A request of 87000 pa produced roughly 949 hPa at MSL — well short of a linear reading of 87000 pa. 100300 pa was honoured exactly, so the field works; it just has an undocumented floor. |

`msl_temperature_k` was also probed and found **not** to be limited — 331.15
K produced 57.3°C at field elevation — so a value with "no SDK maximum"
doesn't automatically hide an undocumented one; these two just happen to.

## What Can't Be Verified

Two fields are accepted, validated, and written to the file, but this server
has no way to confirm they took effect at all:

- **`precipitation_mm_h`** — the only related SimVar is
  `AMBIENT_PRECIP_STATE`, a mask (`2` = none, `4` = rain) that reports
  whether precipitation is currently falling, never its rate.
- **`thunderstorm_intensity`** — there is no SimVar for thunder or lightning
  whatsoever.

Any non-zero value for either field adds a warning saying so, regardless of
how small — there's no threshold below which verification becomes possible.
A caller that wants at least partial confirmation can read
`AMBIENT_PRECIP_STATE` with `msfs_get_simvar` after applying the preset: it
won't confirm the rate, but `4` confirms precipitation is falling at all.

## Example: A Thunderstorm

```python
msfs_write_weather_preset(
    name="Storm",
    precipitation_mm_h=25.0,
    thunderstorm_intensity=0.8,
)
```

Both `cloud_layers` and `wind_layers` are omitted, so the defaults apply:

- One cloud layer, 600–3000 m, scattering 0.5. Because `precipitation_mm_h`
  is non-zero, `density`/`coverage` default to 0.9 rather than the
  dry-weather default of 0.1 — rain needs cloud to fall from.
- One calm wind layer at 4 m, 0 kt.

The file is written as `Storm.WPR` into the sim's auto-discovered preset
folder. The result's `warnings` list contains both unverifiable-field notes
from above, since both `precipitation_mm_h` and `thunderstorm_intensity` are
non-zero. The preset is now on disk but not applied — select "Storm" in the
MSFS weather menu, or repoint a `.FLT`'s `[Weather]` section at the written
path and reload with `msfs_load_flight`.

## Errors

| Code | When |
|---|---|
| `INVALID_PRESET_NAME` | `name` isn't safe as a filename — letters, digits, spaces, dots, hyphens or underscores only, starting with a letter or digit |
| `INVALID_WEATHER` | a value falls outside the enforced ranges above |
| `INVALID_PATH` | `path` was given but isn't absolute, or doesn't end in `.WPR` |
| `PRESETS_DIR_NOT_FOUND` | `path` was omitted and the sim's preset folder couldn't be auto-discovered |
| `ALREADY_EXISTS` | a file already exists at the target and `overwrite` is `False` (the default) |
| `WRITE_FAILED` | the file couldn't be written, e.g. a permissions error |
