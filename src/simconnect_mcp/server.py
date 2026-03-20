"""FastMCP server instance, lifespan, and tool/resource/prompt registration."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from mcp.server.fastmcp import FastMCP

from simconnect_mcp.connection import SimConnectManager

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[dict]:
    """Manage SimConnect connection lifecycle."""
    manager = SimConnectManager()
    logger.info("SimConnect MCP server starting")
    try:
        yield {"manager": manager}
    finally:
        manager.disconnect()
        logger.info("SimConnect MCP server stopped")


mcp = FastMCP(
    "SimConnect MCP",
    description="MSFS SimConnect development companion — full read/write sim state, "
    "L-var inspection, event triggering, calculator code, and embedded docs.",
    lifespan=lifespan,
)

# --- Register tools, resources, and prompts from domain modules ---

from simconnect_mcp.tools.simvars import (  # noqa: E402
    get_simvar,
    set_simvar,
    get_simvar_bulk,
    search_simvars,
    list_simvar_categories,
    watch_simvar,
)
from simconnect_mcp.tools.events import (  # noqa: E402
    trigger_event,
    search_events,
    trigger_custom_event,
)
from simconnect_mcp.tools.lvars import (  # noqa: E402
    get_lvar,
    set_lvar,
    list_lvars,
    execute_calculator_code,
    search_lvars,
    list_lvar_panels,
    list_lvar_catalogs,
)
from simconnect_mcp.tools.aircraft import (  # noqa: E402
    get_aircraft_state,
    get_aircraft_position,
    get_aircraft_systems,
)
from simconnect_mcp.tools.facilities import (  # noqa: E402
    get_nearby_airports,
    get_facility_info,
)
from simconnect_mcp.tools.utilities import (  # noqa: E402
    send_sim_text,
    set_aircraft_position,
)
from simconnect_mcp.resources.documentation import register_doc_resources  # noqa: E402
from simconnect_mcp.resources.state import register_state_resources  # noqa: E402
from simconnect_mcp.prompts.templates import register_prompts  # noqa: E402


# Connection tools (inline — small enough)
@mcp.tool()
async def connect_to_sim() -> dict:
    """Establish SimConnect connection to MSFS.

    Must be called before using any other tools. Automatically attempts
    to load MobiFlight WASM extension for L-var support.
    """
    manager = SimConnectManager()
    return await manager.run_sync(manager.connect)


@mcp.tool()
async def disconnect_from_sim() -> dict:
    """Close the SimConnect connection to MSFS."""
    manager = SimConnectManager()
    return await manager.run_sync(manager.disconnect)


@mcp.tool()
async def get_connection_status() -> dict:
    """Check SimConnect connection state, whether sim is running/paused."""
    manager = SimConnectManager()
    return manager.get_status()


# Register tool functions
for tool_fn in [
    get_simvar, set_simvar, get_simvar_bulk, search_simvars,
    list_simvar_categories, watch_simvar,
    trigger_event, search_events, trigger_custom_event,
    get_lvar, set_lvar, list_lvars, execute_calculator_code,
    search_lvars, list_lvar_panels, list_lvar_catalogs,
    get_aircraft_state, get_aircraft_position, get_aircraft_systems,
    get_nearby_airports, get_facility_info,
    send_sim_text, set_aircraft_position,
]:
    mcp.tool()(tool_fn)

# Register resources and prompts
register_doc_resources(mcp)
register_state_resources(mcp)
register_prompts(mcp)


def main() -> None:
    """Run the MCP server with stdio transport."""
    logging.basicConfig(level=logging.INFO)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
