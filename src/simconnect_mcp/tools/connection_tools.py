"""Connection lifecycle tools: connect, disconnect, and status.

These three are the only tools that must work while nothing is connected
yet, and the only ones that talk to SimConnectManager.connect()/disconnect()
directly instead of going through a domain module's
@handle_simconnect_errors/@require_connection pair -- connect() and
disconnect() already catch their own exceptions and hand back a status
dict (never raise), so there is nothing for a decorator to catch here.
That dict is translated into ConnectionStatus | ToolError below so these
tools carry an outputSchema like everything else in the surface.
"""

from __future__ import annotations

import asyncio

from simconnect_mcp.connection import SimConnectManager
from simconnect_mcp.tools.models import ConnectionStatus, ToolError


async def connect_to_sim() -> ConnectionStatus | ToolError:
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
    result = await asyncio.get_running_loop().run_in_executor(None, manager.connect)
    if result["status"] == "error":
        return ToolError(
            error=result["error"],
            message=result["message"],
            suggestion=result.get("suggestion"),
        )
    status = await manager.get_status()
    return ConnectionStatus(**status, message=result.get("message"))


async def disconnect_from_sim() -> ConnectionStatus | ToolError:
    """Close the SimConnect connection to MSFS."""
    manager = SimConnectManager()
    result = await asyncio.get_running_loop().run_in_executor(None, manager.disconnect)
    status = await manager.get_status()
    return ConnectionStatus(**status, message=result.get("message"))


async def get_connection_status() -> ConnectionStatus | ToolError:
    """Check SimConnect connection state, whether sim is running/paused."""
    manager = SimConnectManager()
    status = await manager.get_status()
    return ConnectionStatus(**status)
