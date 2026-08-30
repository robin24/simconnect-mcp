"""FastMCP server instance, lifespan, and tool/resource/prompt registration."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

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
    lifespan=lifespan,
)

# --- Register tools, resources, and prompts from domain modules ---

from simconnect_mcp.prompts.templates import register_prompts  # noqa: E402
from simconnect_mcp.resources.documentation import register_doc_resources  # noqa: E402
from simconnect_mcp.resources.state import register_state_resources  # noqa: E402
from simconnect_mcp.tools.aircraft import (  # noqa: E402
    get_aircraft_position,
    get_aircraft_state,
    get_aircraft_systems,
)
from simconnect_mcp.tools.events import (  # noqa: E402
    search_events,
    trigger_custom_event,
    trigger_event,
)
from simconnect_mcp.tools.facilities import (  # noqa: E402
    get_facility_info,
    get_nearby_airports,
)
from simconnect_mcp.tools.lvars import (  # noqa: E402
    browse_lvar_catalog,
    execute_calculator_code,
    get_lvar,
    list_lvars,
    search_lvars,
    set_lvar,
)
from simconnect_mcp.tools.pmdg import (  # noqa: E402
    get_pmdg_cdu,
    get_pmdg_var,
    send_pmdg_event,
)
from simconnect_mcp.tools.simvars import (  # noqa: E402
    get_simvar,
    get_simvar_bulk,
    list_simvar_categories,
    search_simvars,
    set_simvar,
    watch_simvar,
)
from simconnect_mcp.tools.utilities import (  # noqa: E402
    send_sim_text,
    set_aircraft_position,
)


# Connection tools (inline — small enough)
@mcp.tool()
async def connect_to_sim() -> dict:
    """Establish SimConnect connection to MSFS.

    Must be called before using any other tools. Automatically attempts
    to load MobiFlight WASM extension for L-var support.
    """
    manager = SimConnectManager()
    # Run in executor but NOT through run_sync: connect() takes no lock of
    # its own (see the comment in SimConnectManager.connect). The reason to
    # keep it off _sim_lock is that its SimConnect() constructor starts the
    # dispatch thread, and doing that while holding _sim_lock is best
    # avoided -- not, as this comment used to claim, that connect() manages
    # the lock itself and run_sync would double-lock. Whether connect/
    # disconnect should take _sim_lock at all is a separate design question,
    # left open here.
    return await asyncio.get_running_loop().run_in_executor(None, manager.connect)


@mcp.tool()
async def disconnect_from_sim() -> dict:
    """Close the SimConnect connection to MSFS."""
    manager = SimConnectManager()
    return await asyncio.get_running_loop().run_in_executor(None, manager.disconnect)


@mcp.tool()
async def get_connection_status() -> dict:
    """Check SimConnect connection state, whether sim is running/paused."""
    manager = SimConnectManager()
    return await manager.get_status()


# Register tool functions
for tool_fn in [
    get_simvar, set_simvar, get_simvar_bulk, search_simvars,
    list_simvar_categories, watch_simvar,
    trigger_event, search_events, trigger_custom_event,
    get_lvar, set_lvar, list_lvars, execute_calculator_code,
    search_lvars, browse_lvar_catalog,
    get_aircraft_state, get_aircraft_position, get_aircraft_systems,
    get_nearby_airports, get_facility_info,
    send_sim_text, set_aircraft_position,
    get_pmdg_var, get_pmdg_cdu, send_pmdg_event,
]:
    mcp.tool()(tool_fn)

# Register resources and prompts
register_doc_resources(mcp)
register_state_resources(mcp)
register_prompts(mcp)


_LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG", "NOTSET"}


def configure_logging() -> None:
    """Send logs to stderr only.

    This server speaks JSON-RPC over stdio; anything written to stdout
    corrupts the protocol stream. Defaults to WARNING because the vendored
    MobiFlight bridge is chatty at INFO. Override with SIMCONNECT_MCP_LOG_LEVEL.

    The level name is matched against a fixed set rather than looked up on the
    logging module: a bare getattr would resolve any all-caps attribute, and
    logging.BASIC_FORMAT is a format string that makes setLevel raise and takes
    the whole server down at startup.
    """
    level_name = os.environ.get("SIMCONNECT_MCP_LOG_LEVEL", "WARNING").strip().upper()
    level = getattr(logging, level_name) if level_name in _LOG_LEVELS else logging.WARNING

    root = logging.getLogger()
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(level)


def main() -> None:
    """Run the MCP server with stdio transport."""
    configure_logging()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
