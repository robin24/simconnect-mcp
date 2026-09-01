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

from pydantic import BaseModel, Field, model_validator

# The SDK templates show version="1,3", but the only value confirmed to
# actually apply on MSFS 2024 here is "1,4" -- it is what the simulator's own
# consumers write (Active Sky's preset) and what was verified live. The SDK
# also shows a <Descr>AceXML Document</Descr> element that working presets on
# disk omit. This module matches the known-working artifact rather than the
# template, since the sim is the authority on what it accepts.
WPR_VERSION = "1,4"


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
        600.0, description="Layer base in metres. May be negative.")
    altitude_top_m: float = Field(
        3000.0, description="Layer top in metres")
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

    altitude_m: float = Field(4.0, description="Layer altitude in metres")
    angle_deg: float = Field(
        0.0, ge=0.0, le=360.0,
        description="Direction the wind blows from, degrees, 0 is North")
    speed_kt: float = Field(0.0, ge=0.0, description="Wind speed in knots")


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
        288.15, gt=0.0, description="Mean-sea-level temperature in kelvin")
    aerosol_density: float = Field(
        1.0, ge=0.0,
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

    is_altitude_amgl: bool = Field(
        False, description="Treat layer altitudes as above ground rather than MSL")
    compute_wind_from_departure: bool = Field(
        False, description="Let the sim derive wind from the departure airport")
