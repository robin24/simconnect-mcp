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
