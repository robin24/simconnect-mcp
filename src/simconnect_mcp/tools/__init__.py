"""SimConnect MCP tools."""

import functools
import logging
from collections.abc import Callable
from typing import Any

from simconnect_mcp.connection import SimConnectManager
from simconnect_mcp.simvar_access import SimVarError
from simconnect_mcp.tools.models import ToolError, error_from

logger = logging.getLogger(__name__)


def handle_simconnect_errors(fn: Callable) -> Callable:
    """Wrap a tool so failures return a ToolError instead of raising."""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await fn(*args, **kwargs)
        except SimVarError as e:
            return error_from(e)
        except ConnectionError as e:
            return ToolError(
                error="CONNECTION_LOST",
                message=str(e),
                suggestion="Try reconnecting with msfs_connect.",
            )
        except OSError as e:
            return ToolError(
                error="CONNECTION_LOST",
                message=f"SimConnect communication error: {e}",
                suggestion="The connection may have dropped. Try reconnecting.",
            )
        except Exception as e:
            logger.exception("Unexpected error in %s", fn.__name__)
            return ToolError(
                error="UNEXPECTED",
                message=str(e),
                suggestion="Check that MSFS is running and try again.",
            )

    return wrapper


def require_connection(fn: Callable) -> Callable:
    """Ensure SimConnect is connected before calling the tool."""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        manager = SimConnectManager()
        err = manager.ensure_connected()
        if err is not None:
            return ToolError(
                error=err.get("error", "NOT_CONNECTED"),
                message=err.get("message", "Not connected to MSFS"),
                suggestion=err.get("suggestion"),
            )
        return await fn(*args, **kwargs)

    return wrapper
