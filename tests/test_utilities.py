from simconnect_mcp.tools.models import PositionResult, TextResult, ToolError
from simconnect_mcp.tools.utilities import send_sim_text, set_aircraft_position


async def test_send_sim_text_returns_a_model(mock_simconnect):
    """Fails against a dict-returning implementation: isinstance(dict, TextResult)
    is False, so this only passes once send_sim_text returns the Pydantic model."""
    result = await send_sim_text("hello")
    assert isinstance(result, TextResult)


async def test_send_sim_text_calls_the_real_library_method(mock_simconnect):
    """Regression: the tool called send_text; the method is sendText."""
    result = await send_sim_text("hello", duration_s=3.0)

    assert result.status == "ok"
    mock_simconnect["sm"].sendText.assert_called_once()
    args = mock_simconnect["sm"].sendText.call_args.args
    assert args[0] == "hello"
    assert args[1] == 3.0


async def test_send_sim_text_accepts_a_colour(mock_simconnect):
    result = await send_sim_text("caution", color="yellow")
    assert result.status == "ok"
    assert result.color == "yellow"


async def test_send_sim_text_rejects_an_unknown_colour(mock_simconnect):
    """A direct Python call bypasses the Literal's schema-level enforcement
    (FastMCP never runs), so send_sim_text's own runtime check is what a
    test like this actually exercises -- see the comment in utilities.py."""
    result = await send_sim_text("hi", color="chartreuse")
    assert isinstance(result, ToolError)
    assert result.error == "INVALID_COLOR"
    assert "white" in result.suggestion


async def test_send_sim_text_does_not_call_the_nonexistent_send_text(mock_simconnect):
    await send_sim_text("hello")
    assert not mock_simconnect["sm"].send_text.called


async def test_set_position_returns_a_model(mock_simconnect):
    """Fails against a dict-returning implementation for the same reason as
    test_send_sim_text_returns_a_model above.

    The read-back is mocked to confirm on_ground -- the fixture's static
    default for SIM_ON_GROUND is 0.0 regardless of what was requested, so
    without this the read-back would (correctly) report False and the
    assertion below would be testing the wrong thing.
    """
    mock_simconnect["accessor"].read.side_effect = lambda name, unit=None, **k: (
        1.0 if name == "SIM_ON_GROUND" else 47.6
    )
    result = await set_aircraft_position(latitude=47.6, longitude=-122.3, on_ground=True)
    assert isinstance(result, PositionResult)
    assert result.on_ground is True


async def test_position_uses_set_pos_not_individual_writes(mock_simconnect):
    """Regression: set_aircraft_position should use set_pos, not individual SimVar writes."""
    await set_aircraft_position(latitude=47.6, longitude=-122.3, altitude=5000)
    mock_simconnect["sm"].set_pos.assert_called_once()


async def test_on_ground_is_actually_honoured(mock_simconnect):
    """Regression: on_ground was accepted and silently ignored."""
    await set_aircraft_position(latitude=47.6, longitude=-122.3, on_ground=True)
    kwargs = mock_simconnect["sm"].set_pos.call_args.kwargs
    assert kwargs["_OnGround"] == 1


async def test_arguments_map_to_the_right_set_pos_parameters(mock_simconnect):
    """Verify argument mapping to set_pos parameters."""
    await set_aircraft_position(
        latitude=47.6,
        longitude=-122.3,
        altitude=5000,
        heading=270,
        airspeed=250,
        pitch=5.0,
        bank=10.0,
    )
    kwargs = mock_simconnect["sm"].set_pos.call_args.kwargs
    assert kwargs["_Latitude"] == 47.6
    assert kwargs["_Longitude"] == -122.3
    assert kwargs["_Altitude"] == 5000
    assert kwargs["_Heading"] == 270
    assert kwargs["_Airspeed"] == 250
    assert kwargs["_Pitch"] == 5.0
    assert kwargs["_Bank"] == 10.0


async def test_current_heading_is_read_in_degrees_not_radians(mock_simconnect):
    """The catalog default for PLANE_HEADING_DEGREES_TRUE is Radians, but
    set_pos expects degrees. Feeding radians into a degrees field would put
    the aircraft on a wildly wrong heading."""
    await set_aircraft_position(latitude=47.6, longitude=-122.3)
    # heading omitted, so current heading must be read
    heading_reads = [
        c
        for c in mock_simconnect["accessor"].read.call_args_list
        if c.args and c.args[0] == "PLANE_HEADING_DEGREES_TRUE"
    ]
    assert heading_reads, "expected the current heading to be read"
    assert heading_reads[0].kwargs.get("unit") == "degrees"


async def test_response_reports_actual_position_not_requested(mock_simconnect):
    """The sim snaps altitude to terrain when on_ground is set, so echoing the
    request would assert something that did not happen."""
    # Mock accessor to return 125 ft actual altitude
    mock_simconnect["accessor"].read.side_effect = lambda name, unit=None, **k: (
        125.0 if name == "PLANE_ALTITUDE" else 47.6
    )
    result = await set_aircraft_position(
        latitude=47.6, longitude=-122.3, altitude=433.0, on_ground=True
    )
    assert result.altitude == 125.0
    assert result.requested["altitude"] == 433.0
    assert result.warning is not None
    assert "125" in result.warning


async def test_read_back_failure_reports_none_not_the_requested_value(mock_simconnect):
    """Finding 5: _current() used to swallow read-back exceptions and return
    the *requested* value as its fallback, so a failed confirming read made
    the response echo the request back as if it were a measurement --
    status stayed "ok" reporting a position that was never actually
    confirmed. Fails against the current code because the old fallback
    makes result.altitude come back as 433.0 (the request), not None."""
    def flaky_read(name, unit=None, **k):
        if name == "PLANE_ALTITUDE":
            raise RuntimeError("sim disconnected")
        return 47.6

    mock_simconnect["accessor"].read.side_effect = flaky_read

    result = await set_aircraft_position(latitude=47.6, longitude=-122.3, altitude=433.0)

    assert result.status == "ok"
    assert result.altitude is None, "must not echo the request as a measurement"
    assert result.requested["altitude"] == 433.0
    assert result.warning is not None
    assert "altitude" in result.warning
    assert result.unverified is not None and "altitude" in result.unverified


async def test_total_read_back_failure_reports_all_nulls_without_crashing(mock_simconnect):
    """When every read-back fails, every field must come back null rather
    than echoing the request, and the altitude-divergence comparison below
    must not blow up trying to subtract None from a number. Old code
    returned the full requested position as if measured, with status "ok"
    and no warning at all -- the divergence check could never fire because
    the "actual" values equalled the request by construction."""
    mock_simconnect["accessor"].read.side_effect = RuntimeError("sim disconnected")

    result = await set_aircraft_position(
        latitude=47.6, longitude=-122.3, altitude=433.0, on_ground=True
    )

    assert result.status == "ok"
    assert result.latitude is None
    assert result.longitude is None
    assert result.altitude is None
    assert result.heading is None
    assert result.on_ground is None
    assert result.warning is not None
