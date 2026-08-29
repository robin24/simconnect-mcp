from simconnect_mcp.tools.utilities import send_sim_text, set_aircraft_position


async def test_send_sim_text_calls_the_real_library_method(mock_simconnect):
    """Regression: the tool called send_text; the method is sendText."""
    result = await send_sim_text("hello", duration_s=3.0)

    assert result["status"] == "ok"
    mock_simconnect["sm"].sendText.assert_called_once()
    args = mock_simconnect["sm"].sendText.call_args.args
    assert args[0] == "hello"
    assert args[1] == 3.0


async def test_send_sim_text_accepts_a_colour(mock_simconnect):
    result = await send_sim_text("caution", color="yellow")
    assert result["status"] == "ok"
    assert result["color"] == "yellow"


async def test_send_sim_text_rejects_an_unknown_colour(mock_simconnect):
    result = await send_sim_text("hi", color="chartreuse")
    assert result["error"] == "INVALID_COLOR"
    assert "white" in result["suggestion"]


async def test_send_sim_text_does_not_call_the_nonexistent_send_text(mock_simconnect):
    await send_sim_text("hello")
    assert not mock_simconnect["sm"].send_text.called


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
        latitude=47.6, longitude=-122.3, altitude=5000, heading=270, airspeed=250
    )
    kwargs = mock_simconnect["sm"].set_pos.call_args.kwargs
    assert kwargs["_Latitude"] == 47.6
    assert kwargs["_Longitude"] == -122.3
    assert kwargs["_Altitude"] == 5000
    assert kwargs["_Heading"] == 270
    assert kwargs["_Airspeed"] == 250


async def test_latitude_out_of_range_is_rejected(mock_simconnect):
    """Verify latitude validation."""
    result = await set_aircraft_position(latitude=91.0, longitude=0.0)
    assert result["error"] == "INVALID_POSITION"
    assert not mock_simconnect["sm"].set_pos.called
