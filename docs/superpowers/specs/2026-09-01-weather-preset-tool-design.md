# Weather Preset Tool — Design

**Date:** 2026-09-01
**Status:** Approved, ready for implementation planning

## Problem

The server has no way to set weather. MSFS removed the legacy SimConnect
weather API (`WeatherSetModeCustom`, `WeatherSetObservation`), so weather can
only be changed through a `.WPR` preset file. Writing one by hand is fiddly:
the format is XML with specific units, and several of its fields have limits
that are either undocumented or differ from what the simulator actually
honours.

`msfs_write_weather_preset` builds and validates a `.WPR` and writes it where
MSFS can find it.

## Scope

**In:** authoring a valid `.WPR`, validating against the documented ranges,
warning about the limits the simulator actually enforces, writing the file to
the sim's preset folder.

**Out:** applying the weather. Applying requires rewriting a `.FLT`'s
`[Weather]` section and reloading the flight, which resets the aircraft. That
belongs to the caller, who can compose this tool with the existing
`msfs_load_flight`, or simply select the preset in the sim's own weather menu —
files in the preset folder appear there.

Deliberately out of scope, and worth stating because an earlier draft assumed
otherwise: this tool does **not** enforce any minimum number of cloud or wind
layers. See "Two sources of truth" below.

## Two sources of truth

The validation split is the core of this design, so it is worth being precise
about where each number comes from.

### Documented — becomes hard validation

From the MSFS SDK, "Weather XML (WPR File) Properties"
(`docs.flightsimulator.com/msfs2024`) and the older `Weather_Definitions` page:

| Element | Range | Unit |
|---|---|---|
| `CloudLayerDensity` | 0.0 – 1.0 | (0 - 1) |
| `CloudLayerScattering` | 0.0 – 1.0 | (0 - 1) |
| `CloudLayerAltitudeBot` / `Top` | none stated | m |
| `WindLayerAltitude` | none stated | m |
| `WindLayerAngle` | 0.0 – 360.0 | degrees |
| `WindLayerSpeed` | none stated | knts |
| `MSLPressure` | 50000.0 – 130000.0 | pa |
| `MSLTemperature` | none stated | k |
| `AerosolDensity` | default 1, no max stated | m |
| `Precipitations` | 0.0 – 100.0 | mm/h |
| `SnowCover` | 0.0 – 4.0 | m |
| `ThunderstormIntensity` | 0.0 – 1.0 | (0 - 1) |

Both pages state only that a preset "may have multiple" `CloudLayer` and
`WindLayer` elements — **no minimum and no maximum**.

`CloudLayerCoverage` and `Pollution` appear in no SDK page but are written by
Active Sky's own preset, which the simulator accepts. They are supported with a
code comment recording that they are unverified against any specification.

### Measured — becomes warnings, never validation

Probed live at EDDF on 2026-09-01. These are single-machine, single-version
observations, so they inform the caller rather than constrain them:

- **Wind clamps at 150 kt.** Reported on MSFS DevSupport as a bug — the UI's
  max/clamp overrides higher values. Consistent with measurement: 185 kt gave
  92–98 kt at 4 m AGL (further reduced by the boundary layer), while 400 kt
  collapsed the entire wind field to 0.3 kt while every other field in the same
  preset still applied.
- **Pressure floors near 95000 Pa.** Asking for 87000 Pa produced roughly
  949 hPa MSL-equivalent, even though the SDK permits 50000. A request of
  100300 Pa was honoured exactly, so the field works — it just has an
  undocumented floor.
- **Temperature is not clamped.** 331.15 K gave 57.3 °C at field elevation.
- **Two fields cannot be verified at all.** There is no SimVar for
  precipitation *rate* (only `AMBIENT_PRECIP_STATE`, a mask: 2 = none,
  4 = rain) and none whatsoever for thunder or lightning. A caller must be told
  which of its settings are unconfirmable rather than left to assume they took.

Why measurements must not become hard bounds: they came from one machine and
one sim version, and encoding them as limits would forbid values that may work
fine elsewhere. The documented ranges are the contract; the measurements are
advice.

## Architecture

Follows the existing domain-module/tool-module split used by
`simvar_access.py`/`tools/simvars.py`, `facilities.py`/`tools/facilities.py`,
and `data/catalog.py`/`tools/lvars.py`.

### `src/simconnect_mcp/weather.py` — pure, no I/O

- `CloudLayer`, `WindLayer` — Pydantic models carrying the documented bounds.
- `WeatherPreset` — the scalar fields, same treatment.
- `render(preset) -> str` — emits the XML. Content is plain ASCII, so this
  avoids the cp1252 hazard that `.FLT` files carry.
- `effective_limit_warnings(preset) -> list[str]` — the measured-versus-
  documented gap. Separate from validation by design, so the distinction above
  is visible in the code structure rather than only in comments.
- `find_presets_dir() -> Path | None` — locates
  `…\LocalState\Weather\Presets`. Returns `None` when not found; never guesses.

  Resolution order, first hit wins: the MSFS 2024 Store package
  (`%LOCALAPPDATA%\Packages\Microsoft.Limitless_8wekyb3d8bbwe\LocalState\
  Weather\Presets`), then the MSFS 2020 Store package
  (`Microsoft.FlightSimulator_8wekyb3d8bbwe`), then the Steam location
  (`%APPDATA%\Microsoft Flight Simulator\…`). A candidate counts as a hit only
  if the directory actually exists, so a machine with neither returns `None`
  rather than a plausible-looking path that no simulator reads.

### `src/simconnect_mcp/tools/weather.py`

`write_weather_preset(...) -> WeatherPresetResult | ToolError`, decorated with
`@handle_simconnect_errors`. It does **not** take `@require_connection`: the
tool writes a file and never touches SimConnect, so demanding a live sim would
be a false requirement.

Parameters: `name`, optional `cloud_layers` / `wind_layers`, the scalars
(`precipitation_mm_h`, `thunderstorm_intensity`, `msl_pressure_pa`,
`msl_temperature_k`, `aerosol_density`, `pollution`, `snow_cover_m`), plus
`path` (override) and `overwrite` (default `False`).

Omitting the layer lists derives defaults, so "make it rain" is one call;
supplying them gives full fidelity to the file format. The derivation is fixed
and stated here so it is not reinvented at implementation time:

- `cloud_layers` omitted → one layer, `AltitudeBot` 600 m, `AltitudeTop`
  3000 m, `Scattering` 0.5. `Density` and `Coverage` are 0.1 when
  `precipitation_mm_h` is 0, else 0.9 — precipitation without cloud to fall
  from is the one combination worth avoiding by default.
- `wind_layers` omitted → one layer at `Altitude` 4 m, `Angle` 0.0,
  `Speed` 0.0 — calm, rather than an invented wind the caller did not ask for.

Both defaults are chosen to be unsurprising rather than dramatic: a caller who
wants a storm passes the scalars, and one who wants specific geometry passes
the layers.

### `tools/models.py`

`WeatherPresetResult(OkModel)` — `path`, `name`, `bytes_written`, `warnings:
list[str]`, `message`. `message` states how to apply the preset, since this
tool deliberately does not.

### `server.py`

```
_register(write_weather_preset, "msfs_write_weather_preset",
          "Write Weather Preset",
          read_only=False, destructive=False, idempotent=True)
```

`destructive=False` holds only because `overwrite` defaults to `False`, exactly
the reasoning `save_flight` already carries. `idempotent=True` because the same
inputs produce the same bytes — unlike `save_flight`, which captures live state
and so cannot claim it.

## Validation and errors

Bounds are declared as Pydantic `Field(ge=..., le=...)` **and** re-checked in
the function body. FastMCP enforces the schema for real MCP calls, but a direct
Python call — as the tests make — bypasses it entirely; `create_ai_object` and
`send_sim_text` already establish this pattern.

| Code | Cause |
|---|---|
| `INVALID_PATH` | `path` not absolute, or does not end in `.WPR` |
| `ALREADY_EXISTS` | file present and `overwrite=False` |
| `PRESETS_DIR_NOT_FOUND` | auto-discovery failed; asks for an explicit `path` |
| `WRITE_FAILED` | `OSError` while writing |
| `INVALID_WEATHER` | a value outside a documented range |

## Testing

All mocked; no MSFS required, consistent with the default suite.

- `render()` output parses under `ElementTree`, and every value round-trips.
- Byte-level check: UTF-8 BOM present, CRLF line endings.
- One rejection test per documented bound, at both ends.
- Warnings fire for wind > 150 kt and pressure < 95000 Pa, and do **not** fire
  for in-range values.
- The unverifiable-field note appears whenever precipitation or thunderstorm
  intensity is non-zero.
- `overwrite=False` refuses an existing file; `True` replaces it.
- `find_presets_dir()` returns `None` — not a guess — when the folder is absent.
- A committed fixture of a known-good preset guards the structure, following
  the precedent of `tests/fixtures/facilities/`.

No live test is warranted. Per `CLAUDE.md`'s rule, a live test earns its place
only when a self-consistent mock could agree with itself regardless of whether
the code is right. Everything here — XML shape, encoding, bounds, warning text
— is settled by a mock. Whether MSFS honours a given value is exactly what this
design refuses to assert, so there is nothing to verify live.

## Open questions

None blocking. One known unknown, recorded rather than resolved: a preset with
1 cloud and 3 wind layers was once silently ignored, and the same filename with
3 and 9 applied. That A/B changed content *and* reloaded the same filename a
second time, so it is confounded, and the SDK documents no minimum. The design
therefore encodes no layer-count rule. If preset pickup proves flaky in
practice, that is the thread to pull.
