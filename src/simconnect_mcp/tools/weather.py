"""Weather preset authoring.

MSFS removed the legacy SimConnect weather API, so a .WPR preset file is the
only way to set weather. This tool writes one; it deliberately does not apply
it -- see write_weather_preset's docstring.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Annotated

from pydantic import Field, ValidationError

from simconnect_mcp.tools import handle_simconnect_errors
from simconnect_mcp.tools.models import ToolError, WeatherPresetResult
from simconnect_mcp.weather import (
    CloudLayer,
    WeatherPreset,
    WindLayer,
    effective_limit_warnings,
    find_presets_dir,
    to_bytes,
)

# `name` becomes a filename when `path` is omitted, so it must not carry a
# separator, a traversal, or anything a filesystem would reject. Enforced
# rather than sanitised: silently rewriting a caller's name would leave them
# holding a path that does not match what they asked for. ASCII-only is
# deliberate too, not an oversight: it is the simplest rule that is safe as
# a filename across filesystems, so no non-ASCII preset name reaches even
# the <Name> element, regardless of whether `path` is supplied -- even
# though the file itself is written as UTF-8 (see to_bytes() in weather.py).
#
# `\Z` rather than `$`: unanchored, `$` also matches just before a trailing
# newline, so `.match()` on "Storm\n" would accept it and go on to attempt
# the filename "Storm\n.WPR" -- rejected by Windows, surfacing as a
# misdiagnosed WRITE_FAILED instead of this check's own INVALID_PRESET_NAME.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}\Z")

# Defaults used when the caller supplies no layers. Chosen to be unsurprising
# rather than dramatic: a caller wanting a storm passes the scalars.
_DEFAULT_CLOUD_BOT_M = 600.0
_DEFAULT_CLOUD_TOP_M = 3000.0
_DEFAULT_CLOUD_SCATTERING = 0.5
_DEFAULT_CLOUD_WHEN_DRY = 0.1
_DEFAULT_CLOUD_WHEN_WET = 0.9
_DEFAULT_WIND_ALTITUDE_M = 4.0


# This tool never touches SimConnect -- it is pure file I/O, same situation
# as tools/hubhop.py (see the comment on its imports for the full reasoning).
# @handle_simconnect_errors is kept anyway, as the safety net every tool in
# this package uses to guarantee it returns ToolError rather than raising,
# not because a failure here is SimConnect-shaped: an escaping OSError would
# be mapped to CONNECTION_LOST, a false diagnosis for a tool that has no
# connection to lose. Low-risk in practice, since the write below has its
# own `except OSError` and returns WRITE_FAILED before this decorator ever
# sees the exception.
@handle_simconnect_errors
async def write_weather_preset(
    name: Annotated[
        str,
        Field(description="Preset name, shown in the MSFS weather menu and used "
                          "as the filename when `path` is omitted",
              min_length=1, max_length=64),
    ],
    cloud_layers: Annotated[
        list[CloudLayer] | None,
        Field(description="Cloud layers. Omit for a single default layer."),
    ] = None,
    wind_layers: Annotated[
        list[WindLayer] | None,
        Field(description="Wind layers. Omit for a single calm surface layer."),
    ] = None,
    precipitation_mm_h: Annotated[
        float, Field(description="Precipitation rate, mm/h", ge=0.0, le=100.0)
    ] = 0.0,
    thunderstorm_intensity: Annotated[
        float, Field(description="Thunderstorm intensity", ge=0.0, le=1.0)
    ] = 0.0,
    msl_pressure_pa: Annotated[
        float, Field(description="Sea-level pressure, pascals",
                     ge=50000.0, le=130000.0)
    ] = 101325.0,
    msl_temperature_k: Annotated[
        float, Field(description="Sea-level temperature, kelvin", gt=0.0)
    ] = 288.15,
    aerosol_density: Annotated[
        float, Field(description="Aerosol density; 1.0 default, higher is hazier",
                     ge=0.0)
    ] = 1.0,
    pollution: Annotated[float, Field(description="Pollution", ge=0.0, le=1.0)] = 0.0,
    snow_cover_m: Annotated[
        float, Field(description="Snow cover depth, metres", ge=0.0, le=4.0)
    ] = 0.0,
    path: Annotated[
        str | None,
        Field(description=r"Absolute path for the .WPR file. Omit to write into "
                          r"the simulator's own preset folder."),
    ] = None,
    overwrite: Annotated[
        bool, Field(description="Replace an existing file at the target path")
    ] = False,
) -> WeatherPresetResult | ToolError:
    """Write an MSFS weather preset (.WPR) file.

    Writes the file only -- it does not change the weather in a running sim.
    MSFS removed the legacy SimConnect weather API, so weather comes from a
    preset file, applied one of two ways: select the preset in the simulator's
    weather menu, or point a .FLT's [Weather] section at it
    (UseWeatherFile=True, WeatherPresetFile=<path>) and reload with
    msfs_load_flight -- which resets the aircraft.

    Value ranges are enforced from the MSFS SDK's documented limits. Limits the
    simulator applies but does not document -- a 150 kt wind clamp, a pressure
    floor near 95000 pa -- are reported in `warnings` rather than enforced,
    because they were measured on one machine and one sim version. `warnings`
    also names the settings that cannot be verified at all: there is no SimVar
    for precipitation rate or for thunder.
    """
    if not _SAFE_NAME.match(name or ""):
        return ToolError(
            error="INVALID_PRESET_NAME",
            message=f"'{name}' is not usable as a preset name.",
            suggestion="Use letters, digits, spaces, dots, hyphens or "
                       "underscores, starting with a letter or digit. The name "
                       "becomes a filename, so it cannot contain a path "
                       "separator.",
        )

    if cloud_layers is None:
        cover = (_DEFAULT_CLOUD_WHEN_WET if precipitation_mm_h > 0
                 else _DEFAULT_CLOUD_WHEN_DRY)
        cloud_layers = [CloudLayer(
            density=cover, coverage=cover,
            altitude_bot_m=_DEFAULT_CLOUD_BOT_M, altitude_top_m=_DEFAULT_CLOUD_TOP_M,
            scattering=_DEFAULT_CLOUD_SCATTERING)]
    if wind_layers is None:
        wind_layers = [WindLayer(
            altitude_m=_DEFAULT_WIND_ALTITUDE_M, angle_deg=0.0, speed_kt=0.0)]

    # Pydantic bounds on the parameters above are enforced by FastMCP for real
    # MCP calls, but a direct Python call bypasses that entirely -- the same
    # reasoning as create_ai_object's coordinate check. Building the model here
    # is what actually enforces them on that path.
    try:
        preset = WeatherPreset(
            name=name,
            cloud_layers=[CloudLayer.model_validate(c) for c in cloud_layers],
            wind_layers=[WindLayer.model_validate(w) for w in wind_layers],
            msl_pressure_pa=msl_pressure_pa, msl_temperature_k=msl_temperature_k,
            aerosol_density=aerosol_density, pollution=pollution,
            precipitation_mm_h=precipitation_mm_h, snow_cover_m=snow_cover_m,
            thunderstorm_intensity=thunderstorm_intensity,
        )
    except ValidationError as e:
        fields = ", ".join(
            ".".join(str(p) for p in err["loc"]) for err in e.errors()
        ) or "input"
        return ToolError(
            error="INVALID_WEATHER",
            message=f"{fields} rejected by the documented value ranges.",
            suggestion="These bounds come from the MSFS SDK's weather preset "
                       "documentation. Check the units -- pressure is pascals, "
                       "temperature kelvin, precipitation mm/h (max 100). "
                       f"Validation detail: {e}",
        )

    if path is not None:
        target = Path(path)
        if not target.is_absolute():
            return ToolError(
                error="INVALID_PATH",
                message=f"'{path}' is not an absolute path.",
                suggestion=r"Give an absolute path such as "
                           r"C:\Users\you\Documents\Storm.WPR, or omit `path` "
                           r"to use the simulator's own preset folder.",
            )
        if target.suffix.upper() != ".WPR":
            return ToolError(
                error="INVALID_PATH",
                message=f"'{path}' does not end in .WPR.",
                suggestion="Weather presets use the .WPR extension.",
            )
    else:
        presets_dir = find_presets_dir()
        if presets_dir is None:
            return ToolError(
                error="PRESETS_DIR_NOT_FOUND",
                message="Could not find the simulator's weather preset folder.",
                suggestion=r"Pass an absolute `path` instead, e.g. "
                           r"...\LocalState\Weather\Presets\Storm.WPR for a "
                           r"Store install of MSFS.",
            )
        target = presets_dir / f"{name}.WPR"

    if target.exists() and not overwrite:
        return ToolError(
            error="ALREADY_EXISTS",
            message=f"A file already exists at '{target}'.",
            suggestion="Pass overwrite=True to replace it, or choose another name.",
        )

    data = to_bytes(preset)
    try:
        # Deliberately no mkdir here -- do not "helpfully" restore one. Auto-
        # creating target.parent would let a typo'd explicit `path` silently
        # fabricate a directory and report success somewhere the caller never
        # intended, instead of failing honestly. That's the same "don't
        # guess" principle find_presets_dir() (simconnect_mcp/weather.py)
        # follows by returning None rather than a guess. This is a no-op for
        # the auto-discovered branch above: find_presets_dir() only ever
        # returns a directory that already passed is_dir(). A missing parent
        # here surfaces as FileNotFoundError (an OSError), caught below as
        # WRITE_FAILED.
        target.write_bytes(data)
    except OSError as e:
        return ToolError(
            error="WRITE_FAILED",
            message=f"Could not write '{target}': {e}",
            suggestion="Check the directory exists and is writable.",
        )

    return WeatherPresetResult(
        path=str(target), name=name, bytes_written=len(data),
        warnings=effective_limit_warnings(preset),
        message=(
            f"Wrote weather preset '{name}' to {target}. This does not change "
            "the weather in a running sim: select the preset in the MSFS "
            "weather menu, or point a .FLT's [Weather] section at it "
            "(UseWeatherFile=True) and reload with msfs_load_flight."
        ),
    )
