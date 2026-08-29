"""Live state resources — connection and aircraft state."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from simconnect_mcp.connection import SimConnectManager
from simconnect_mcp.simvar_access import DEFAULT_TIMEOUT


def register_state_resources(mcp: FastMCP) -> None:
    """Register live state resources on the MCP server."""

    @mcp.resource("simconnect://state/connection")
    async def state_connection() -> dict:
        """Current SimConnect connection status, sim running/paused."""
        manager = SimConnectManager()
        return await manager.get_status()

    @mcp.resource("simconnect://state/aircraft")
    async def state_aircraft() -> dict:
        """Current aircraft title, type, and position."""
        manager = SimConnectManager()
        if not manager.is_connected or manager.accessor is None:
            return {"status": "not_connected"}

        names = [
            "TITLE", "ATC_TYPE", "ATC_ID",
            "PLANE_LATITUDE", "PLANE_LONGITUDE", "PLANE_ALTITUDE",
        ]
        # read_many's `timeout` is a TOTAL budget for the whole batch, not
        # per item (see SimVarAccessor.read_many). The default is sized for
        # one read; passing it unchanged here would give all 6 reads
        # together the budget one used to get alone, so a merely sluggish
        # (not hung) sim could spuriously time out the later names.
        try:
            data = await manager.run_sync(
                lambda: manager.accessor.read_many(
                    [(n, None, None) for n in names], timeout=len(names) * DEFAULT_TIMEOUT
                )
            )
        except Exception as e:
            return {"status": "error", "message": str(e)}
        return {"status": "ok", "aircraft": data}
