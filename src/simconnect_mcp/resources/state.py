"""Live state resources — connection and aircraft state."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from simconnect_mcp.connection import SimConnectManager


def register_state_resources(mcp: FastMCP) -> None:
    """Register live state resources on the MCP server."""

    @mcp.resource("simconnect://state/connection")
    def state_connection() -> dict:
        """Current SimConnect connection status, sim running/paused."""
        manager = SimConnectManager()
        return manager.get_status()

    @mcp.resource("simconnect://state/aircraft")
    def state_aircraft() -> dict:
        """Current aircraft title, type, and position."""
        manager = SimConnectManager()
        if not manager.is_connected:
            return {"status": "not_connected"}

        try:
            data = {}
            for var in ["TITLE", "ATC_TYPE", "ATC_ID",
                        "PLANE_LATITUDE", "PLANE_LONGITUDE", "PLANE_ALTITUDE"]:
                try:
                    data[var] = manager.aq.get(var)
                except Exception:
                    data[var] = None
            return {"status": "ok", "aircraft": data}
        except Exception as e:
            return {"status": "error", "message": str(e)}
