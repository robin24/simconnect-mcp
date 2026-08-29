from simconnect_mcp.tools.utilities import send_sim_text


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
