"""Build MSFS .WPR weather preset files.

Pure construction and validation: no file I/O, no SimConnect. tools/weather.py
adds the write and the MCP envelope.

MSFS removed the legacy SimConnect weather API (WeatherSetModeCustom,
WeatherSetObservation), so a .WPR file is the only way to set weather.

Two sources of truth, kept deliberately apart:

* The **documented** ranges below come from the MSFS SDK ("Weather XML (WPR
  File) Properties", docs.flightsimulator.com/msfs2024, and the older
  Weather_Definitions page). They are enforced as Pydantic bounds.
* Values **measured** against a live sim live in effective_limit_warnings()
  and are never enforced. They came from one machine and one sim version;
  promoting them to bounds would forbid values that may work elsewhere.

Do not move a number from the second group into the first.
"""
from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

# The SDK templates show version="1,3", but the only value confirmed to
# actually apply on MSFS 2024 here is "1,4" -- it is what the simulator's own
# consumers write (Active Sky's preset) and what was verified live. The SDK
# also shows a <Descr>AceXML Document</Descr> element that working presets on
# disk omit. This module matches the known-working artifact rather than the
# template, since the sim is the authority on what it accepts.
WPR_VERSION = "1,4"


def _escape(text: str) -> str:
    """Escape XML character data. Preset names reach here from tool callers."""
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


class CloudLayer(BaseModel):
    """One cloud layer. Ranges are SDK-documented except where noted."""

    density: float = Field(
        0.5, ge=0.0, le=1.0,
        description="Cloud density, 0.0 (almost none) to 1.0 (very dense)")
    # Neither appears in any SDK page, but Active Sky writes both and the
    # simulator accepts them. Their 0.0-1.0 bounds are inferred from the
    # Unit="(0 - 1)" string those elements carry in working presets.
    coverage: float = Field(
        0.5, ge=0.0, le=1.0,
        description="Cloud coverage, 0.0 to 1.0")
    altitude_bot_m: float = Field(
        600.0, allow_inf_nan=False, description="Layer base in metres. May be negative.")
    altitude_top_m: float = Field(
        3000.0, allow_inf_nan=False, description="Layer top in metres")
    scattering: float = Field(
        0.5, ge=0.0, le=1.0,
        description="Light scattering, 0.0 (dark/dense) to 1.0 (very scattered)")

    @model_validator(mode="after")
    def _base_below_top(self) -> CloudLayer:
        # Not an SDK range -- a coherence check. An inverted layer describes
        # no volume and the sim has no defined behaviour for it.
        if self.altitude_bot_m >= self.altitude_top_m:
            raise ValueError(
                f"altitude_bot_m ({self.altitude_bot_m}) must be below "
                f"altitude_top_m ({self.altitude_top_m})"
            )
        return self


class WindLayer(BaseModel):
    """One wind layer.

    speed_kt carries NO upper bound: the SDK states none. MSFS is known to
    clamp wind layers at 150 kt, and a request of 400 kt was measured to
    collapse the whole wind field -- both are reported by
    effective_limit_warnings(), not enforced here.
    """

    altitude_m: float = Field(
        4.0, allow_inf_nan=False, description="Layer altitude in metres")
    angle_deg: float = Field(
        0.0, ge=0.0, le=360.0,
        description="Direction the wind blows from, degrees, 0 is North")
    speed_kt: float = Field(
        0.0, ge=0.0, allow_inf_nan=False, description="Wind speed in knots")


class WeatherPreset(BaseModel):
    """A complete .WPR preset.

    cloud_layers/wind_layers have no minimum count: both SDK pages say a
    preset "may have multiple" with no stated minimum, and a working
    third-party preset uses 3 cloud and 14 wind layers.
    """

    name: str = Field(..., min_length=1, max_length=64,
                      description="Preset name, shown in the MSFS weather menu")
    cloud_layers: list[CloudLayer] = Field(default_factory=list)
    wind_layers: list[WindLayer] = Field(default_factory=list)

    msl_pressure_pa: float = Field(
        101325.0, ge=50000.0, le=130000.0,
        description="Mean-sea-level pressure in pascals")
    msl_temperature_k: float = Field(
        288.15, gt=0.0, allow_inf_nan=False,
        description="Mean-sea-level temperature in kelvin")
    aerosol_density: float = Field(
        1.0, ge=0.0, allow_inf_nan=False,
        description="Aerosol density; 1.0 is the default, higher reduces transparency")
    # Neither appears in any SDK page, but Active Sky writes both and the
    # simulator accepts them. Their 0.0-1.0 bounds are inferred from the
    # Unit="(0 - 1)" string those elements carry in working presets.
    pollution: float = Field(0.0, ge=0.0, le=1.0, description="Pollution, 0.0 to 1.0")
    precipitation_mm_h: float = Field(
        0.0, ge=0.0, le=100.0, description="Precipitation rate in mm/h")
    snow_cover_m: float = Field(
        0.0, ge=0.0, le=4.0, description="Snow cover depth in metres")
    thunderstorm_intensity: float = Field(
        0.0, ge=0.0, le=1.0, description="Thunderstorm intensity, 0.0 to 1.0")

    # Module-API-only: deliberately not parameters of write_weather_preset
    # (tools/weather.py) and not documented there. Available to any caller
    # constructing a WeatherPreset directly.
    is_altitude_amgl: bool = Field(
        False, description="Treat layer altitudes as above ground rather than MSL")
    compute_wind_from_departure: bool = Field(
        False, description="Let the sim derive wind from the departure airport")


def _element(indent: int, tag: str, value: float, unit: str) -> list[str]:
    """One `<Tag Value="..." Unit="...">` pair, in the open/close form working
    presets on disk use (the simulator's own writers do not self-close these)."""
    pad = " " * indent
    return [f'{pad}<{tag} Value="{value:.3f}" Unit="{unit}">', f"{pad}</{tag}>"]


def render(preset: WeatherPreset) -> str:
    """Serialise a preset to .WPR XML, LF-separated.

    Use to_bytes() to write it: the file must be UTF-8 with a BOM and CRLF
    line endings, matching what the simulator's own writers produce.
    """
    out: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "",
        f'<SimBase.Document Type="WeatherPreset" version="{WPR_VERSION}">',
        "    <WeatherPreset.Preset>",
        f"        <Name>{_escape(preset.name)}</Name>",
        f"        <IsAltitudeAMGL>{preset.is_altitude_amgl}</IsAltitudeAMGL>",
        "        <ComputeWindFromDeparture>"
        f"{preset.compute_wind_from_departure}</ComputeWindFromDeparture>",
    ]
    for c in preset.cloud_layers:
        out.append("        <CloudLayer>")
        out += _element(12, "CloudLayerDensity", c.density, "(0 - 1)")
        out += _element(12, "CloudLayerCoverage", c.coverage, "(0 - 1)")
        out += _element(12, "CloudLayerAltitudeBot", c.altitude_bot_m, "m")
        out += _element(12, "CloudLayerAltitudeTop", c.altitude_top_m, "m")
        out += _element(12, "CloudLayerScattering", c.scattering, "(0 - 1)")
        out.append("        </CloudLayer>")
    for w in preset.wind_layers:
        out.append("        <WindLayer>")
        out += _element(12, "WindLayerAltitude", w.altitude_m, "m")
        out += _element(12, "WindLayerAngle", w.angle_deg, "degrees")
        out += _element(12, "WindLayerSpeed", w.speed_kt, "knts")
        out.append("        </WindLayer>")
    out += _element(8, "MSLPressure", preset.msl_pressure_pa, "pa")
    out += _element(8, "MSLTemperature", preset.msl_temperature_k, "k")
    out += _element(8, "AerosolDensity", preset.aerosol_density, "m")
    out += _element(8, "Pollution", preset.pollution, "(0 - 1)")
    out += _element(8, "Precipitations", preset.precipitation_mm_h, "mm/h")
    out += _element(8, "SnowCover", preset.snow_cover_m, "m")
    out += _element(8, "ThunderstormIntensity", preset.thunderstorm_intensity, "(0 - 1)")
    out += ["    </WeatherPreset.Preset>", "</SimBase.Document>"]
    return "\n".join(out)


def to_bytes(preset: WeatherPreset) -> bytes:
    """Render to the exact on-disk encoding: UTF-8 with BOM, CRLF endings."""
    return b"\xef\xbb\xbf" + render(preset).replace("\n", "\r\n").encode("utf-8")


# --- Measured, not documented. These generate warnings and never bounds. ---
#
# Probed live at EDDF on 2026-09-01 against MSFS 2024. Single machine, single
# sim version -- which is exactly why they are advisory. See the module
# docstring and the design spec.

# MSFS clamps wind layers at 150 kt (reported on MSFS DevSupport: the UI's
# max/clamp overrides higher values). Consistent with measurement: 185 kt gave
# 92-98 kt at 4 m AGL, while 400 kt collapsed the entire wind field to 0.3 kt
# while every other field in the same preset still applied.
MEASURED_WIND_CLAMP_KT = 150.0

# Asking for 87000 pa produced ~949 hPa MSL-equivalent, though the SDK permits
# 50000. A request of 100300 pa was honoured exactly, so the field works -- it
# just has an undocumented floor.
MEASURED_PRESSURE_FLOOR_PA = 95000.0


def effective_limit_warnings(preset: WeatherPreset) -> list[str]:
    """Advisory notes about limits the simulator enforces but the SDK omits.

    Never raises and never modifies the preset. A caller is free to ignore
    every one of these -- they exist so an agent is told which of its settings
    the simulator is likely to alter, and which it cannot verify at all,
    rather than assuming all of them took effect.
    """
    warnings: list[str] = []

    fast = [w for w in preset.wind_layers if w.speed_kt > MEASURED_WIND_CLAMP_KT]
    if fast:
        altitudes = ", ".join(f"{w.altitude_m:g} m" for w in fast)
        warnings.append(
            f"{len(fast)} wind layer(s) exceed {MEASURED_WIND_CLAMP_KT:g} kt "
            f"(at {altitudes}). MSFS clamps wind layers at "
            f"{MEASURED_WIND_CLAMP_KT:g} kt, and a request of 400 kt was "
            "measured to collapse the wind field to near zero rather than "
            "clamp. The SDK documents no maximum, so this is not enforced."
        )

    if preset.msl_pressure_pa < MEASURED_PRESSURE_FLOOR_PA:
        warnings.append(
            f"msl_pressure_pa {preset.msl_pressure_pa:g} is below "
            f"{MEASURED_PRESSURE_FLOOR_PA:g}, which was measured to be the "
            "simulator's effective floor (87000 pa produced roughly 949 hPa). "
            "The SDK permits 50000, so this is not enforced."
        )

    if preset.precipitation_mm_h > 0:
        warnings.append(
            "Precipitation rate cannot be verified: the only related SimVar is "
            "AMBIENT_PRECIP_STATE, a mask (2 = none, 4 = rain) that reports "
            "whether precipitation is falling, never how hard."
        )

    if preset.thunderstorm_intensity > 0:
        warnings.append(
            "Thunderstorm intensity cannot be verified: there is no SimVar for "
            "thunder or lightning at all."
        )

    return warnings


# Where each MSFS edition keeps user weather presets, in resolution order.
# 2024 first: a machine with both installed is far likelier to be flying the
# newer one, and this only decides a default the caller can always override.
_PRESET_SUBPATH = ("LocalState", "Weather", "Presets")
_STORE_PACKAGES = (
    "Microsoft.Limitless_8wekyb3d8bbwe",          # MSFS 2024
    "Microsoft.FlightSimulator_8wekyb3d8bbwe",    # MSFS 2020
)


def _preset_dir_candidates() -> list[Path]:
    """Every place a preset folder might live, most-likely first.

    Reads the environment rather than hardcoding a user profile so this is
    testable off-Windows and honest on a machine with a relocated profile.
    """
    candidates: list[Path] = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        for package in _STORE_PACKAGES:
            candidates.append(Path(local) / "Packages" / package / Path(*_PRESET_SUBPATH))
    roaming = os.environ.get("APPDATA")
    if roaming:
        # 2024 first, same reasoning as the Store packages above: Steam MSFS
        # 2024 and 2020 keep presets under differently-named roaming folders
        # ("Microsoft Flight Simulator 2024" vs. "Microsoft Flight
        # Simulator"), and a machine with both is likelier flying the newer
        # one.
        candidates.append(
            Path(roaming) / "Microsoft Flight Simulator 2024" / "Weather" / "Presets"
        )
        candidates.append(
            Path(roaming) / "Microsoft Flight Simulator" / "Weather" / "Presets"
        )
    return candidates


def find_presets_dir() -> Path | None:
    """Locate the simulator's weather-preset folder, or None.

    Returns None rather than a best guess when nothing is found: a path no
    simulator reads would send the caller's file into the void while
    reporting success. The tool turns None into an error asking for an
    explicit path.
    """
    for candidate in _preset_dir_candidates():
        if candidate.is_dir():
            return candidate
    return None
