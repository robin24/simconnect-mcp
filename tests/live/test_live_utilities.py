import pytest

pytestmark = pytest.mark.live


async def test_send_text_reaches_the_sim(live_manager):
    """Watch the sim window: a white message should appear for 3 seconds."""
    from simconnect_mcp.tools.utilities import send_sim_text

    result = await send_sim_text("simconnect-mcp live test", duration_s=3.0)
    assert result["status"] == "ok"
