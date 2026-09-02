# Weather Preset Tool Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `msfs_write_weather_preset`, an MCP tool that authors a validated MSFS `.WPR` weather preset file.

**Architecture:** A pure builder module (`weather.py`) holds the Pydantic models, XML rendering, warning generation and preset-folder discovery, with no I/O and no SimConnect. A thin tool module (`tools/weather.py`) adds path resolution, the file write and the `ToolError` envelope. This mirrors the existing `simvar_access.py`/`tools/simvars.py` and `facilities.py`/`tools/facilities.py` split.

**Tech Stack:** Python 3.10+, Pydantic v2, FastMCP, pytest (`asyncio_mode = "auto"`), ruff.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-09-01-weather-preset-tool-design.md`. Read it before starting.
- ruff: `line-length = 100`, `target-version = "py310"`, `select = ["E", "F", "I", "UP", "B"]`. Run `uv run ruff check` before every commit.
- Tests: `uv run pytest`. `asyncio_mode = "auto"`, so async tests need no decorator. `addopts = "-m 'not live'"`.
- Every tool returns `SomeResult | ToolError` — never a bare dict, never a fabricated success.
- Every tool is registered in `server.py` via `_register` with explicit `ToolAnnotations`.
- **Hard validation comes only from the SDK-documented ranges.** Values measured live become `warnings`, never bounds. This distinction is the point of the feature — do not "tighten" a bound because a measurement suggested it.
- Do not enforce any minimum number of cloud or wind layers. The SDK states none.
- All new code is Windows-and-Linux importable: no `ctypes.wintypes` at module level (CI runs `windows-latest`, but these modules must stay importable for unit tests).

---

### Task 1: Weather models and documented bounds

**Files:**
- Create: `src/simconnect_mcp/weather.py`
- Test: `tests/test_weather.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `CloudLayer`, `WindLayer`, `WeatherPreset` (Pydantic `BaseModel`s). Field names used by every later task: `CloudLayer(density, coverage, altitude_bot_m, altitude_top_m, scattering)`; `WindLayer(altitude_m, angle_deg, speed_kt)`; `WeatherPreset(name, cloud_layers, wind_layers, msl_pressure_pa, msl_temperature_k, aerosol_density, pollution, precipitation_mm_h, snow_cover_m, thunderstorm_intensity, is_altitude_amgl, compute_wind_from_departure)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_weather.py`:

```python
"""Tests for the pure .WPR builder module."""
import pytest
from pydantic import ValidationError

from simconnect_mcp.weather import CloudLayer, WeatherPreset, WindLayer


def test_cloud_layer_accepts_documented_range():
    layer = CloudLayer(density=1.0, coverage=0.0, altitude_bot_m=-200.0,
                       altitude_top_m=20000.0, scattering=0.5)
    assert layer.density == 1.0
    assert layer.altitude_bot_m == -200.0


@pytest.mark.parametrize("field", ["density", "coverage", "scattering"])
@pytest.mark.parametrize("bad", [-0.01, 1.01])
def test_cloud_layer_rejects_out_of_range_unit_fields(field, bad):
    kwargs = {"altitude_bot_m": 600.0, "altitude_top_m": 3000.0, field: bad}
    with pytest.raises(ValidationError):
        CloudLayer(**kwargs)


def test_cloud_layer_rejects_inverted_altitudes():
    """Not an SDK range, a coherence check: a layer whose base is above its
    top describes nothing, and the sim has no defined behaviour for it."""
    with pytest.raises(ValidationError):
        CloudLayer(altitude_bot_m=3000.0, altitude_top_m=600.0)


@pytest.mark.parametrize("bad", [-0.01, 360.01])
def test_wind_layer_rejects_out_of_range_angle(bad):
    with pytest.raises(ValidationError):
        WindLayer(altitude_m=4.0, angle_deg=bad, speed_kt=10.0)


def test_wind_layer_accepts_speed_above_the_measured_clamp():
    """150 kt is a measured clamp, not a documented bound. The SDK states no
    WindLayerSpeed maximum, so the model must NOT reject it -- that is what
    effective_limit_warnings is for."""
    assert WindLayer(altitude_m=4.0, angle_deg=0.0, speed_kt=185.0).speed_kt == 185.0


@pytest.mark.parametrize(
    "field,bad",
    [
        ("msl_pressure_pa", 49999.0), ("msl_pressure_pa", 130001.0),
        ("precipitation_mm_h", -0.1), ("precipitation_mm_h", 100.1),
        ("snow_cover_m", -0.1), ("snow_cover_m", 4.1),
        ("thunderstorm_intensity", -0.1), ("thunderstorm_intensity", 1.1),
    ],
)
def test_preset_rejects_values_outside_documented_ranges(field, bad):
    with pytest.raises(ValidationError):
        WeatherPreset(name="X", cloud_layers=[CloudLayer()],
                      wind_layers=[WindLayer()], **{field: bad})


def test_preset_accepts_pressure_below_the_measured_floor():
    """95000 pa is a measured floor; the SDK permits 50000. Measurements
    never become bounds."""
    p = WeatherPreset(name="X", cloud_layers=[CloudLayer()],
                      wind_layers=[WindLayer()], msl_pressure_pa=87000.0)
    assert p.msl_pressure_pa == 87000.0


def test_preset_accepts_no_layers_at_all():
    """The SDK states no minimum layer count, so the model must not invent one."""
    p = WeatherPreset(name="X", cloud_layers=[], wind_layers=[])
    assert p.cloud_layers == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_weather.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'simconnect_mcp.weather'`

- [ ] **Step 3: Write the module**

Create `src/simconnect_mcp/weather.py`:

```python
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
```

Note on `pollution` and `coverage`: neither appears in any SDK page, but Active Sky writes both and the simulator accepts them. Their `0.0-1.0` bounds are inferred from the `Unit="(0 - 1)"` string those elements carry in working presets, not from documentation. Keep this note as a comment beside those two fields.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_weather.py -v`
Expected: PASS (all parametrized cases)

- [ ] **Step 5: Lint**

Run: `uv run ruff check src/simconnect_mcp/weather.py tests/test_weather.py`
Expected: no findings.

- [ ] **Step 6: Commit**

```bash
git add src/simconnect_mcp/weather.py tests/test_weather.py
git commit -m "feat(weather): .WPR models with SDK-documented bounds

Bounds come only from the MSFS SDK. Measured limits (the 150 kt wind
clamp, the ~95000 pa pressure floor) are deliberately NOT enforced here --
they are one machine's observations and belong in warnings. Tests assert
both directions: documented ranges reject, measured limits do not."
```

---

### Task 2: Render the XML, with a byte-level fixture

**Files:**
- Modify: `src/simconnect_mcp/weather.py`
- Create: `tests/fixtures/weather/expected_storm.WPR`
- Modify: `tests/test_weather.py`

**Interfaces:**
- Consumes: `CloudLayer`, `WindLayer`, `WeatherPreset` from Task 1.
- Produces: `render(preset: WeatherPreset) -> str` (LF-separated XML text) and `to_bytes(preset: WeatherPreset) -> bytes` (UTF-8 BOM + CRLF).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_weather.py`:

```python
import xml.etree.ElementTree as ET
from pathlib import Path

from simconnect_mcp.weather import render, to_bytes

FIXTURES = Path(__file__).parent / "fixtures" / "weather"


def _storm() -> WeatherPreset:
    return WeatherPreset(
        name="Test Storm",
        cloud_layers=[
            CloudLayer(density=1.0, coverage=1.0, altitude_bot_m=300.0,
                       altitude_top_m=11000.0, scattering=0.12),
        ],
        wind_layers=[
            WindLayer(altitude_m=4.0, angle_deg=235.0, speed_kt=18.0),
            WindLayer(altitude_m=3000.0, angle_deg=255.0, speed_kt=45.0),
        ],
        msl_pressure_pa=100300.0, msl_temperature_k=292.15,
        aerosol_density=1.0, pollution=0.0,
        precipitation_mm_h=28.0, snow_cover_m=0.0, thunderstorm_intensity=0.95,
    )


def test_render_parses_as_xml_and_round_trips_every_value():
    root = ET.fromstring(render(_storm()))
    assert root.attrib["Type"] == "WeatherPreset"
    assert root.findtext(".//Name") == "Test Storm"

    clouds = root.findall(".//CloudLayer")
    assert len(clouds) == 1
    assert float(clouds[0].find("CloudLayerDensity").get("Value")) == 1.0
    assert float(clouds[0].find("CloudLayerAltitudeTop").get("Value")) == 11000.0
    assert clouds[0].find("CloudLayerAltitudeTop").get("Unit") == "m"

    winds = root.findall(".//WindLayer")
    assert len(winds) == 2
    assert float(winds[1].find("WindLayerSpeed").get("Value")) == 45.0
    assert winds[1].find("WindLayerSpeed").get("Unit") == "knts"

    assert float(root.find(".//Precipitations").get("Value")) == 28.0
    assert root.find(".//Precipitations").get("Unit") == "mm/h"
    assert float(root.find(".//ThunderstormIntensity").get("Value")) == 0.95
    assert float(root.find(".//MSLPressure").get("Value")) == 100300.0


def test_render_emits_no_layer_elements_when_there_are_none():
    root = ET.fromstring(render(WeatherPreset(name="Empty")))
    assert root.findall(".//CloudLayer") == []
    assert root.findall(".//WindLayer") == []


def test_to_bytes_has_utf8_bom_and_crlf():
    data = to_bytes(_storm())
    assert data.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in data
    # No bare LF: every newline must be part of a CRLF pair.
    assert data.count(b"\n") == data.count(b"\r\n")


def test_to_bytes_matches_committed_fixture():
    """Byte-level guard on the structure, following tests/fixtures/facilities/.
    Regenerate deliberately if the format is intentionally changed."""
    assert to_bytes(_storm()) == (FIXTURES / "expected_storm.WPR").read_bytes()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_weather.py -k "render or to_bytes" -v`
Expected: FAIL — `ImportError: cannot import name 'render'`

- [ ] **Step 3: Implement rendering**

Append to `src/simconnect_mcp/weather.py`:

```python
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
```

Add near the top of the module, below `WPR_VERSION`:

```python
def _escape(text: str) -> str:
    """Escape XML character data. Preset names reach here from tool callers."""
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
```

- [ ] **Step 4: Generate the fixture, then read it back**

Generate it once from the implementation, then **inspect it by eye** to confirm it looks like a real preset before committing:

```bash
uv run python -c "
from pathlib import Path
import sys; sys.path.insert(0, 'tests')
from test_weather import _storm
from simconnect_mcp.weather import to_bytes
d = Path('tests/fixtures/weather'); d.mkdir(parents=True, exist_ok=True)
(d / 'expected_storm.WPR').write_bytes(to_bytes(_storm()))
print((d / 'expected_storm.WPR').read_text(encoding='utf-8-sig'))
"
```

Confirm the printed XML has `<CloudLayer>` with five children, two `<WindLayer>` blocks, and `Precipitations` at `28.000`. A fixture generated from a broken implementation would lock the bug in, so this eyeball step is the check.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_weather.py -v`
Expected: PASS

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src/simconnect_mcp/weather.py tests/test_weather.py
git add src/simconnect_mcp/weather.py tests/test_weather.py tests/fixtures/weather/expected_storm.WPR
git commit -m "feat(weather): render .WPR XML with BOM+CRLF encoding

Emits version 1,4 and omits <Descr>, matching working presets on disk
rather than the SDK template -- the simulator is the authority on what it
accepts. A committed fixture pins the byte-level output, following the
precedent of tests/fixtures/facilities/."
```

---

### Task 3: Effective-limit warnings

**Files:**
- Modify: `src/simconnect_mcp/weather.py`
- Modify: `tests/test_weather.py`

**Interfaces:**
- Consumes: `WeatherPreset`, `WindLayer` from Task 1.
- Produces: `effective_limit_warnings(preset: WeatherPreset) -> list[str]`, and the constants `MEASURED_WIND_CLAMP_KT = 150.0` and `MEASURED_PRESSURE_FLOOR_PA = 95000.0`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_weather.py`:

```python
from simconnect_mcp.weather import effective_limit_warnings


def test_no_warnings_for_an_ordinary_preset():
    p = WeatherPreset(name="Mild", cloud_layers=[CloudLayer()],
                      wind_layers=[WindLayer(speed_kt=12.0)])
    assert effective_limit_warnings(p) == []


def test_warns_above_the_measured_wind_clamp():
    p = WeatherPreset(name="Gale", wind_layers=[WindLayer(speed_kt=185.0)])
    warnings = effective_limit_warnings(p)
    assert any("150" in w for w in warnings)


def test_wind_warning_names_every_offending_layer_once():
    p = WeatherPreset(name="Gale", wind_layers=[
        WindLayer(altitude_m=4.0, speed_kt=200.0),
        WindLayer(altitude_m=3000.0, speed_kt=10.0),
        WindLayer(altitude_m=9000.0, speed_kt=400.0),
    ])
    wind = [w for w in effective_limit_warnings(p) if "150" in w]
    assert len(wind) == 1, "one aggregated warning, not one per layer"


def test_warns_below_the_measured_pressure_floor():
    p = WeatherPreset(name="Low", msl_pressure_pa=87000.0)
    assert any("95000" in w for w in effective_limit_warnings(p))


def test_no_pressure_warning_at_a_normal_setting():
    p = WeatherPreset(name="Normal", msl_pressure_pa=100300.0)
    assert not any("95000" in w for w in effective_limit_warnings(p))


def test_warns_that_precipitation_cannot_be_verified():
    p = WeatherPreset(name="Rain", precipitation_mm_h=5.0)
    assert any("AMBIENT_PRECIP_STATE" in w for w in effective_limit_warnings(p))


def test_warns_that_thunderstorm_cannot_be_verified():
    p = WeatherPreset(name="Storm", thunderstorm_intensity=0.9)
    assert any("no SimVar" in w for w in effective_limit_warnings(p))


def test_no_unverifiable_warnings_when_those_fields_are_zero():
    p = WeatherPreset(name="Clear", precipitation_mm_h=0.0, thunderstorm_intensity=0.0)
    text = " ".join(effective_limit_warnings(p))
    assert "AMBIENT_PRECIP_STATE" not in text and "no SimVar" not in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_weather.py -k warn -v`
Expected: FAIL — `ImportError: cannot import name 'effective_limit_warnings'`

- [ ] **Step 3: Implement**

Append to `src/simconnect_mcp/weather.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_weather.py -v`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/simconnect_mcp/weather.py tests/test_weather.py
git add src/simconnect_mcp/weather.py tests/test_weather.py
git commit -m "feat(weather): advisory warnings for undocumented sim limits

Reports the 150 kt wind clamp and the ~95000 pa pressure floor, plus the
two settings that cannot be verified at all -- precipitation rate has only
a boolean-ish mask, and thunder has no SimVar whatsoever. Advisory by
construction: this function never alters the preset."
```

---

### Task 4: Preset folder discovery

**Files:**
- Modify: `src/simconnect_mcp/weather.py`
- Modify: `tests/test_weather.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `find_presets_dir() -> Path | None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_weather.py`:

```python
from simconnect_mcp.weather import find_presets_dir

_MSFS2024 = "Packages/Microsoft.Limitless_8wekyb3d8bbwe/LocalState/Weather/Presets"
_MSFS2020 = "Packages/Microsoft.FlightSimulator_8wekyb3d8bbwe/LocalState/Weather/Presets"


def test_returns_none_when_no_sim_folder_exists(tmp_path, monkeypatch):
    """Must not return a plausible-looking path no simulator reads."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    assert find_presets_dir() is None


def test_finds_the_msfs2024_store_folder(tmp_path, monkeypatch):
    local = tmp_path / "local"
    (local / _MSFS2024).mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    assert find_presets_dir() == local / _MSFS2024


def test_prefers_2024_over_2020_when_both_exist(tmp_path, monkeypatch):
    local = tmp_path / "local"
    (local / _MSFS2024).mkdir(parents=True)
    (local / _MSFS2020).mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    assert find_presets_dir() == local / _MSFS2024


def test_falls_back_to_steam_location(tmp_path, monkeypatch):
    roaming = tmp_path / "roaming"
    steam = roaming / "Microsoft Flight Simulator" / "Weather" / "Presets"
    steam.mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("APPDATA", str(roaming))
    assert find_presets_dir() == steam


def test_ignores_a_candidate_that_is_a_file_not_a_directory(tmp_path, monkeypatch):
    local = tmp_path / "local"
    (local / _MSFS2024).parent.mkdir(parents=True)
    (local / _MSFS2024).write_text("not a directory")
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    assert find_presets_dir() is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_weather.py -k presets_dir -v`
Expected: FAIL — `ImportError: cannot import name 'find_presets_dir'`

- [ ] **Step 3: Implement**

Add `import os` and `from pathlib import Path` to the module imports, then append:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_weather.py -v`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/simconnect_mcp/weather.py tests/test_weather.py
git add src/simconnect_mcp/weather.py tests/test_weather.py
git commit -m "feat(weather): locate the sim's preset folder, honestly

Checks MSFS 2024 Store, then 2020 Store, then Steam, and requires the
directory to actually exist. Returns None rather than a plausible guess --
a path no simulator reads would swallow the caller's file while reporting
success."
```

---

### Task 5: The `write_weather_preset` tool

**Files:**
- Create: `src/simconnect_mcp/tools/weather.py`
- Modify: `src/simconnect_mcp/tools/models.py`
- Create: `tests/test_weather_tool.py`

**Interfaces:**
- Consumes: everything from Tasks 1–4; `ToolError`, `OkModel` from `tools/models.py`; `handle_simconnect_errors` from `tools/__init__.py`.
- Produces: `write_weather_preset(...) -> WeatherPresetResult | ToolError` and the `WeatherPresetResult` model with fields `path`, `name`, `bytes_written`, `warnings`, `message`.

**Note:** this tool does **not** take `@require_connection`. It writes a file and never touches SimConnect, so demanding a live sim would be a false requirement.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_weather_tool.py`:

```python
"""Tests for the msfs_write_weather_preset tool."""
from pathlib import Path

import pytest

from simconnect_mcp.tools.models import ToolError, WeatherPresetResult
from simconnect_mcp.tools.weather import write_weather_preset


async def test_writes_a_file_to_an_explicit_path(tmp_path):
    target = tmp_path / "Storm.WPR"
    result = await write_weather_preset(
        name="Storm", precipitation_mm_h=28.0, thunderstorm_intensity=0.95,
        path=str(target),
    )
    assert isinstance(result, WeatherPresetResult)
    assert Path(result.path) == target
    assert target.exists()
    assert result.bytes_written == target.stat().st_size
    assert target.read_bytes().startswith(b"\xef\xbb\xbf")


async def test_defaults_produce_cloud_when_precipitating(tmp_path):
    """Precipitation with no cloud to fall from is the one default combination
    worth avoiding."""
    target = tmp_path / "Rain.WPR"
    await write_weather_preset(name="Rain", precipitation_mm_h=10.0, path=str(target))
    text = target.read_text(encoding="utf-8-sig")
    assert '<CloudLayerCoverage Value="0.900"' in text


async def test_defaults_are_sparse_when_dry(tmp_path):
    target = tmp_path / "Dry.WPR"
    await write_weather_preset(name="Dry", path=str(target))
    text = target.read_text(encoding="utf-8-sig")
    assert '<CloudLayerCoverage Value="0.100"' in text
    assert '<WindLayerSpeed Value="0.000"' in text


async def test_explicit_layers_override_the_defaults(tmp_path):
    target = tmp_path / "Custom.WPR"
    await write_weather_preset(
        name="Custom",
        cloud_layers=[{"density": 1.0, "coverage": 1.0,
                       "altitude_bot_m": 0.0, "altitude_top_m": 9000.0,
                       "scattering": 0.1}],
        wind_layers=[{"altitude_m": 4.0, "angle_deg": 90.0, "speed_kt": 30.0},
                     {"altitude_m": 5000.0, "angle_deg": 100.0, "speed_kt": 80.0}],
        path=str(target),
    )
    text = target.read_text(encoding="utf-8-sig")
    assert text.count("<WindLayer>") == 2
    assert '<WindLayerSpeed Value="80.000"' in text


async def test_refuses_to_overwrite_by_default(tmp_path):
    target = tmp_path / "Existing.WPR"
    target.write_bytes(b"original")
    result = await write_weather_preset(name="Existing", path=str(target))
    assert isinstance(result, ToolError)
    assert result.error == "ALREADY_EXISTS"
    assert target.read_bytes() == b"original"


async def test_overwrite_true_replaces_the_file(tmp_path):
    target = tmp_path / "Existing.WPR"
    target.write_bytes(b"original")
    result = await write_weather_preset(name="Existing", path=str(target), overwrite=True)
    assert isinstance(result, WeatherPresetResult)
    assert target.read_bytes() != b"original"


async def test_rejects_a_relative_path(tmp_path):
    result = await write_weather_preset(name="X", path="relative/Storm.WPR")
    assert isinstance(result, ToolError)
    assert result.error == "INVALID_PATH"


async def test_rejects_a_wrong_suffix(tmp_path):
    result = await write_weather_preset(name="X", path=str(tmp_path / "Storm.txt"))
    assert isinstance(result, ToolError)
    assert result.error == "INVALID_PATH"


@pytest.mark.parametrize("bad_name", ["../escape", "sub/dir", "back\\slash", ""])
async def test_rejects_names_that_could_escape_the_target_directory(bad_name, tmp_path):
    """`name` becomes the filename when `path` is omitted, so it must never
    carry a path separator or traversal."""
    result = await write_weather_preset(name=bad_name)
    assert isinstance(result, ToolError)
    assert result.error == "INVALID_PRESET_NAME"


async def test_out_of_range_value_is_a_tool_error_not_an_exception(tmp_path):
    """FastMCP validates the schema for real MCP calls, but a direct Python
    call bypasses it -- same reasoning as create_ai_object's coordinate check."""
    result = await write_weather_preset(
        name="X", precipitation_mm_h=300.0, path=str(tmp_path / "X.WPR"))
    assert isinstance(result, ToolError)
    assert result.error == "INVALID_WEATHER"
    assert "precipitation_mm_h" in result.message


async def test_reports_measured_limit_warnings(tmp_path):
    result = await write_weather_preset(
        name="Gale", msl_pressure_pa=87000.0,
        wind_layers=[{"altitude_m": 4.0, "angle_deg": 0.0, "speed_kt": 400.0}],
        path=str(tmp_path / "Gale.WPR"))
    assert isinstance(result, WeatherPresetResult)
    joined = " ".join(result.warnings)
    assert "150" in joined and "95000" in joined


async def test_errors_when_no_preset_folder_and_no_path(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "nothing"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "nothing2"))
    result = await write_weather_preset(name="Storm")
    assert isinstance(result, ToolError)
    assert result.error == "PRESETS_DIR_NOT_FOUND"
    assert "path" in (result.suggestion or "")


async def test_uses_the_discovered_folder_when_path_is_omitted(monkeypatch, tmp_path):
    presets = (tmp_path / "local" / "Packages"
               / "Microsoft.Limitless_8wekyb3d8bbwe" / "LocalState"
               / "Weather" / "Presets")
    presets.mkdir(parents=True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    result = await write_weather_preset(name="Storm")
    assert isinstance(result, WeatherPresetResult)
    assert Path(result.path) == presets / "Storm.WPR"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_weather_tool.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'simconnect_mcp.tools.weather'`

- [ ] **Step 3: Add the result model**

In `src/simconnect_mcp/tools/models.py`, after `AiObjectResult`, add:

```python
class WeatherPresetResult(OkModel):
    """Confirmation that a .WPR weather preset file was written.

    Writing the file is the whole of this tool's contract. It does NOT apply
    the weather: that needs a .FLT's [Weather] section repointed and the
    flight reloaded (which resets the aircraft), or the preset selected in
    the simulator's own weather menu. `message` says so.

    `warnings` carries limits the simulator enforces but the SDK does not
    document, and the settings that cannot be verified at all. An empty list
    means nothing in the preset is known to be altered or unverifiable -- not
    that the simulator has confirmed anything, which it never does for a file
    on disk.
    """

    path: str = Field(..., description="Absolute path of the file written")
    name: str = Field(..., description="Preset name, as it appears in MSFS")
    bytes_written: int = Field(..., description="Size of the file written")
    warnings: list[str] = Field(
        default_factory=list,
        description="Effective-limit and unverifiability notes; may be empty")
    message: str = Field(..., description="What was written and how to apply it")
```

- [ ] **Step 4: Implement the tool**

Create `src/simconnect_mcp/tools/weather.py`:

```python
"""Weather preset authoring.

MSFS removed the legacy SimConnect weather API, so a .WPR preset file is the
only way to set weather. This tool writes one; it deliberately does not apply
it -- see write_weather_preset's docstring.
"""
from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)

# `name` becomes a filename when `path` is omitted, so it must not carry a
# separator, a traversal, or anything a filesystem would reject. Enforced
# rather than sanitised: silently rewriting a caller's name would leave them
# holding a path that does not match what they asked for.
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,63}$")

# Defaults used when the caller supplies no layers. Chosen to be unsurprising
# rather than dramatic: a caller wanting a storm passes the scalars.
_DEFAULT_CLOUD_BOT_M = 600.0
_DEFAULT_CLOUD_TOP_M = 3000.0
_DEFAULT_CLOUD_SCATTERING = 0.5
_DEFAULT_CLOUD_WHEN_DRY = 0.1
_DEFAULT_CLOUD_WHEN_WET = 0.9
_DEFAULT_WIND_ALTITUDE_M = 4.0


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
            message=f"Rejected by the documented value ranges ({fields}): {e}",
            suggestion="These bounds come from the MSFS SDK's weather preset "
                       "documentation. Check the units -- pressure is pascals, "
                       "temperature kelvin, precipitation mm/h (max 100).",
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
        target.parent.mkdir(parents=True, exist_ok=True)
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_weather_tool.py -v`
Expected: PASS

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src/simconnect_mcp/tools/weather.py src/simconnect_mcp/tools/models.py tests/test_weather_tool.py
git add src/simconnect_mcp/tools/weather.py src/simconnect_mcp/tools/models.py tests/test_weather_tool.py
git commit -m "feat(weather): add write_weather_preset tool

Writes the file and says so -- it does not claim to have changed the
weather, which needs a .FLT repoint and a flight reload the caller owns.

Rejects preset names carrying a path separator or traversal, since the
name becomes a filename when path is omitted. Re-validates in the body
because a direct Python call bypasses FastMCP's schema check."
```

---

### Task 6: Register the tool

**Files:**
- Modify: `src/simconnect_mcp/server.py`
- Modify: `tests/test_registration.py:81` (the `test_expected_tool_count` assertion)

**Interfaces:**
- Consumes: `write_weather_preset` from Task 5.
- Produces: the registered MCP tool `msfs_write_weather_preset`.

- [ ] **Step 1: Update the count test to the new expected value**

In `tests/test_registration.py`, change:

```python
async def test_expected_tool_count():
    assert len(await _tools()) == 32
```

to:

```python
async def test_expected_tool_count():
    assert len(await _tools()) == 33
```

Then add, next to `test_phase_two_tools_are_registered`:

```python
async def test_weather_tool_is_registered_and_annotated():
    tools = await _tools()
    assert "msfs_write_weather_preset" in tools
    ann = tools["msfs_write_weather_preset"].annotations
    assert ann.readOnlyHint is False, "it writes a file"
    # destructive=False holds only because overwrite defaults to False.
    assert ann.destructiveHint is False
    assert ann.idempotentHint is True, "same inputs produce the same bytes"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_registration.py -v`
Expected: FAIL — `test_expected_tool_count` asserts 33 but finds 32, and `test_weather_tool_is_registered_and_annotated` raises `KeyError`.

- [ ] **Step 3: Register the tool**

In `src/simconnect_mcp/server.py`, add the import in alphabetical position among the `tools.*` imports (after the `tools.utilities` block):

```python
from simconnect_mcp.tools.weather import write_weather_preset  # noqa: E402
```

Then after the `msfs_create_ai_object` registration, add:

```python
# --- Weather ---
# Writes a .WPR preset file; it does not apply weather, so nothing in the sim
# changes here.
#
# destructive=False for exactly the reason save_flight carries it: overwrite
# defaults to False, so an existing preset is never replaced unless the caller
# opts in. Without that guard this would be a false claim.
#
# idempotent=True, and unlike save_flight it genuinely is: the bytes are a
# pure function of the arguments, with no live sim state folded in, so a
# second identical call leaves the same file.
_register(write_weather_preset, "msfs_write_weather_preset", "Write Weather Preset",
          read_only=False, destructive=False, idempotent=True)
```

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest`
Expected: PASS, all tests. `test_registration.py`'s `_MINIMUM_EXPECTED_FILES`/`_MINIMUM_EXPECTED_MATCHES` are floors, so a new module only ever helps them.

- [ ] **Step 5: Lint**

Run: `uv run ruff check`
Expected: no findings across the repo.

- [ ] **Step 6: Commit**

```bash
git add src/simconnect_mcp/server.py tests/test_registration.py
git commit -m "feat(weather): register msfs_write_weather_preset

destructive=False mirrors save_flight's reasoning: the overwrite guard is
what makes the claim true. idempotent=True is stronger here than for
save_flight -- the bytes are a pure function of the arguments, with no
live sim state folded in."
```

---

### Task 7: Document the tool in the embedded docs

**Files:**
- Create: `src/simconnect_mcp/docs/weather.md`
- Modify: `src/simconnect_mcp/resources/documentation.py`
- Modify: `tests/test_documentation.py`

**Interfaces:**
- Consumes: the registered tool from Task 6.
- Produces: an MCP resource serving the weather documentation.

**Why this is a task and not a footnote:** `docs/*.md` are served as MCP resources and are the main way an agent learns *which* limits are documented versus measured — precisely the distinction this feature exists to encode. `test_registration.py` also scans these files and asserts every `msfs_`-prefixed name in them is a real registered tool.

- [ ] **Step 1: Read the existing pattern**

Run: `uv run python -c "import pathlib; print(pathlib.Path('src/simconnect_mcp/resources/documentation.py').read_text()[:3000])"`

Note how each doc is registered (`@mcp.resource` with `mime_type` and `title` — both are asserted by `test_every_resource_and_template_declares_mime_type_and_title`), and read `src/simconnect_mcp/docs/rpn.md` for house style and length.

- [ ] **Step 2: Write the failing test**

In `tests/test_documentation.py`, add a test mirroring the existing per-document tests (match their exact style — read the file first):

```python
async def test_weather_doc_is_served_and_separates_documented_from_measured():
    from simconnect_mcp.server import mcp
    uris = {str(r.uri) for r in await mcp.list_resources()}
    assert "simconnect://docs/weather" in uris

    from pathlib import Path

    import simconnect_mcp
    text = (Path(simconnect_mcp.__file__).parent / "docs" / "weather.md").read_text(
        encoding="utf-8")
    # The whole point of the doc: an agent must be able to tell an enforced
    # bound from an advisory one.
    assert "msfs_write_weather_preset" in text
    assert "150" in text and "95000" in text
    assert "AMBIENT_PRECIP_STATE" in text
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_documentation.py -k weather -v`
Expected: FAIL — the resource is not registered.

- [ ] **Step 4: Write the doc and register it**

Create `src/simconnect_mcp/docs/weather.md` covering, in this order:
1. Why a file is the only route (MSFS removed the legacy SimConnect weather API).
2. That `msfs_write_weather_preset` writes the file and does **not** apply it, plus the two ways to apply it (weather menu; `.FLT` `[Weather]` repoint plus `msfs_load_flight`).
3. A table of the SDK-documented ranges (copy from the spec) marked **enforced**.
4. A table of measured limits — the 150 kt wind clamp, the ~95000 pa pressure floor — marked **advisory, reported in `warnings`**.
5. What cannot be verified: precipitation rate (only `AMBIENT_PRECIP_STATE`, a mask where 2 = none and 4 = rain) and thunder (no SimVar at all).
6. One worked example call producing a thunderstorm.

Register it in `resources/documentation.py` following the existing entries exactly, including `mime_type="text/markdown"` and a `title`.

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest`
Expected: PASS

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check
git add src/simconnect_mcp/docs/weather.md src/simconnect_mcp/resources/documentation.py tests/test_documentation.py
git commit -m "docs(weather): serve weather documentation as an MCP resource

Separates SDK-documented ranges (enforced) from measured limits
(advisory) and names the two settings that cannot be verified at all, so
an agent reading the docs can tell which of its settings the simulator
may quietly alter."
```

---

## Self-Review

**Spec coverage.** Every spec section maps to a task: models and documented bounds → Task 1; `render`/`to_bytes` and the fixture → Task 2; `effective_limit_warnings` → Task 3; `find_presets_dir` with the stated resolution order → Task 4; `WeatherPresetResult`, the tool, the derivation defaults, and all five error codes → Task 5; registration and annotations → Task 6. Task 7 (embedded docs) is an addition the spec did not name; it is included because `docs/*.md` are served as MCP resources and are where the documented-versus-measured distinction reaches an agent.

**Placeholders.** None. Every code step carries real code; the one prose-specified deliverable (Task 7's markdown) is given as a numbered content outline with the exact required strings asserted by its test.

**Type consistency.** Field names are used identically throughout: `CloudLayer(density, coverage, altitude_bot_m, altitude_top_m, scattering)`, `WindLayer(altitude_m, angle_deg, speed_kt)`, and the `WeatherPreset` scalars named the same in the models (Task 1), the renderer (Task 2), the warnings (Task 3) and the tool signature (Task 5). `to_bytes` is defined in Task 2 and imported in Task 5. `find_presets_dir` returns `Path | None` in Task 4 and the `None` branch is handled in Task 5. `WeatherPresetResult`'s five fields are defined in Task 5 Step 3 and constructed in Step 4.

**One deliberate deviation to flag at review:** `WPR_VERSION = "1,4"` contradicts the SDK template's `"1,3"`. It is the value verified to apply on MSFS 2024, and matches working presets on disk. If a preset ever fails to load, this is the first constant to try changing.
