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


async def test_does_not_create_a_missing_parent_directory(tmp_path):
    """A typo'd explicit `path` must fail rather than silently fabricate the
    directory it points into and report success somewhere the caller never
    intended."""
    missing_dir = tmp_path / "does_not_exist"
    target = missing_dir / "Storm.WPR"
    result = await write_weather_preset(name="Storm", path=str(target))
    assert isinstance(result, ToolError)
    assert result.error == "WRITE_FAILED"
    assert not missing_dir.exists()


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
