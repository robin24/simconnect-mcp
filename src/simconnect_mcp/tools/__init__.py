"""SimConnect MCP tools."""

import functools
import logging
from collections.abc import Callable
from typing import Any

from simconnect_mcp.connection import SimConnectManager

logger = logging.getLogger(__name__)


def handle_simconnect_errors(fn: Callable) -> Callable:
    """Decorator that wraps tool functions with structured error handling."""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await fn(*args, **kwargs)
        except ConnectionError as e:
            return {
                "status": "error",
                "error": "CONNECTION_LOST",
                "message": str(e),
                "suggestion": "Try reconnecting with connect_to_sim.",
            }
        except OSError as e:
            return {
                "status": "error",
                "error": "CONNECTION_LOST",
                "message": f"SimConnect communication error: {e}",
                "suggestion": "The connection may have dropped. Try reconnecting.",
            }
        except Exception as e:
            logger.exception("Unexpected error in %s", fn.__name__)
            return {
                "status": "error",
                "error": "UNEXPECTED",
                "message": str(e),
                "suggestion": "Check that MSFS is running and try again.",
            }

    return wrapper


def require_connection(fn: Callable) -> Callable:
    """Decorator that ensures SimConnect is connected before calling the tool."""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        manager = SimConnectManager()
        err = manager.ensure_connected()
        if err is not None:
            return err
        return await fn(*args, **kwargs)

    return wrapper
