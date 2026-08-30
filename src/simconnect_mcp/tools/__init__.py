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


def _accessor_unavailable() -> ToolError:
    """Fresh ToolError for a needs_accessor tool on the plain-SimConnect
    fallback -- a new instance per call, matching every other error site in
    this package rather than sharing one mutable model instance."""
    return ToolError(
        error="ACCESSOR_UNAVAILABLE",
        message=(
            "This connection fell back to plain SimConnect, which has no "
            "data-definition layer for unit-aware SimVar/L-var reads and writes."
        ),
        suggestion=(
            "Reconnect with msfs_connect, then check msfs_get_connection_status -- "
            "the dispatcher this layer needs can fail to build if something else "
            "already holds the SimConnect connection. Event tools "
            "(msfs_trigger_event, msfs_trigger_custom_event) and MobiFlight-based "
            "L-var tools (msfs_get_lvar, msfs_execute_calculator_code) "
            "use a different path and are unaffected. msfs_set_lvar writes through "
            "this same data-definition layer and needs it too."
        ),
    )


def require_connection(
    fn: Callable | None = None, *, needs_accessor: bool = False
) -> Callable:
    """Ensure SimConnect is connected before calling the tool.

    Usable bare (`@require_connection`) or parameterized
    (`@require_connection(needs_accessor=True)`); `fn` is only ever supplied
    by Python itself in the bare form.

    Pass `needs_accessor=True` for any tool that reaches
    `manager.accessor` -- SimVarAccessor, the data-definition layer built
    only on the SimConnectDispatcher path (see connection.py's `connect()`).
    On the plain-SimConnect fallback `manager.accessor` stays None, and a
    tool that dereferences it anyway raises a bare AttributeError that
    `handle_simconnect_errors`' catch-all turns into
    `UNEXPECTED: 'NoneType' object has no attribute 'read'` -- a Python
    traceback leaking through the envelope, exactly what the typed errors
    exist to prevent. Five call sites did this before this check existed:
    simvars.py's four tools, and get_aircraft_snapshot. For each of those,
    the accessor is not just used but IS the tool's only means of doing its
    primary job -- there is no fallback path to fall back to.

    Do not reach for this just because a tool happens to touch
    `manager.accessor` somewhere. set_aircraft_position also touches it
    (for an optional post-write read-back, wrapped in its own try/except)
    and does not take this flag -- its primary action, `manager.sm.set_pos`,
    needs no accessor at all, so a missing one should degrade to honest
    nulls, not a refusal. It briefly carried needs_accessor=True anyway
    (wave B3, on a wave B brief that mis-listed it alongside the five
    genuine sites); the effect was a tool that used to serve the
    plain-SimConnect fallback -- reposition the aircraft, report the
    read-back as unverified -- refusing outright instead. Fixed in final-fix
    wave D. The lesson: check whether the *primary action* needs the
    accessor, not merely whether the function body mentions it.

    Defaults to False, not True, deliberately: most tools (events, L-vars
    via MobiFlight, PMDG SDK reads) reach the sim through a different path
    entirely and work fine on the fallback. Defaulting to required would
    turn a missing accessor into a blanket refusal for tools that were never
    broken by it -- the opposite of what this guards against.
    """

    def decorator(inner_fn: Callable) -> Callable:
        @functools.wraps(inner_fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            manager = SimConnectManager()
            err = manager.ensure_connected()
            if err is not None:
                return ToolError(
                    error=err.get("error", "NOT_CONNECTED"),
                    message=err.get("message", "Not connected to MSFS"),
                    suggestion=err.get("suggestion"),
                )
            if needs_accessor and manager.accessor is None:
                return _accessor_unavailable()
            return await inner_fn(*args, **kwargs)

        return wrapper

    if fn is not None:
        return decorator(fn)
    return decorator
