"""Tests for the pure .WPR builder module."""
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from pydantic import ValidationError

from simconnect_mcp.weather import (
    CloudLayer,
    WeatherPreset,
    WindLayer,
    effective_limit_warnings,
    find_presets_dir,
    render,
    to_bytes,
)

FIXTURES = Path(__file__).parent / "fixtures" / "weather"

_MSFS2024 = "Packages/Microsoft.Limitless_8wekyb3d8bbwe/LocalState/Weather/Presets"
_MSFS2020 = "Packages/Microsoft.FlightSimulator_8wekyb3d8bbwe/LocalState/Weather/Presets"


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
