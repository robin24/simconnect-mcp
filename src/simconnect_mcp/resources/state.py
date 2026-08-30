"""Live state resources — connection and aircraft state."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from simconnect_mcp.connection import SimConnectManager


def register_state_resources(mcp: FastMCP) -> None:
    """Register live state resources on the MCP server."""

    @mcp.resource(
        "simconnect://state/connection",
        mime_type="application/json",
        title="Connection Status",
    )
    async def state_connection() -> dict:
        """Current SimConnect connection status, sim running/paused."""
        manager = SimConnectManager()
        return await manager.get_status()

    @mcp.resource(
        "simconnect://state/aircraft",
        mime_type="application/json",
        title="Aircraft State",
    )
    async def state_aircraft() -> dict:
        """Current aircraft title, type, and position."""
        manager = SimConnectManager()
        if not manager.is_connected or manager.accessor is None:
            return {"status": "not_connected"}

        names = [
            "TITLE", "ATC_TYPE", "ATC_ID",
            "PLANE_LATITUDE", "PLANE_LONGITUDE", "PLANE_ALTITUDE",
        ]
        # read_many's budget argument is PER ITEM and it scales the batch
        # deadline by len(requests) itself (see SimVarAccessor.read_many),
        # so this no longer pre-multiplies. It used to be a total budget
        # defaulting to one read's worth, which this resource had to
        # compensate for by hand -- a correction the two tool call sites
        # never made, so they ran 44 and 100 variables on a single read's
        # budget. Sizing it inside read_many is what makes that
        # impossible to get wrong at a call site.
        try:
            data = await manager.run_sync(
                lambda: manager.accessor.read_many([(n, None, None) for n in names])
            )
        except Exception as e:
            return {"status": "error", "message": str(e)}
        return {"status": "ok", "aircraft": data}
