"""Flight and scenario tools -- load/save flights, flight plans, AI objects.

Aimed at scripted test-scenario setup: put the aircraft into a known state,
capture it, and replay it.
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Annotated

from pydantic import Field

from simconnect_mcp.connection import SimConnectManager
from simconnect_mcp.tools import handle_simconnect_errors, require_connection
from simconnect_mcp.tools.models import AiObjectResult, FlightResult, ToolError

logger = logging.getLogger(__name__)

# How long save_flight polls for the .FLT file to appear before giving up on
# it. See _wait_for_file's docstring for why a single immediate check is not
# enough.
_SAVE_POLL_TIMEOUT_S = 2.0
_SAVE_POLL_INTERVAL_S = 0.1


def _validate_path(path: str, suffix: str, must_exist: bool) -> ToolError | Path:
    """Check a sim file path. Returns a Path or the ToolError to send back."""
    candidate = Path(path)
    if not candidate.is_absolute():
        return ToolError(
            error="INVALID_PATH",
            message=f"'{path}' is not an absolute path.",
            suggestion="MSFS resolves these paths itself, so give an absolute "
                       r"path such as C:\Users\you\Documents\flight.FLT",
        )
    if candidate.suffix.upper() != suffix.upper():
        return ToolError(
            error="INVALID_PATH",
            message=f"'{path}' does not end in {suffix}.",
            suggestion=f"Flight files use {suffix}.",
        )
    if must_exist and not candidate.exists():
        return ToolError(
            error="FILE_NOT_FOUND",
            message=f"No file at '{path}'.",
            suggestion="Check the path. Saved flights usually live under "
                       "Documents or the MSFS package folder.",
        )
    return candidate


async def _wait_for_file(
    path: Path,
    timeout_s: float = _SAVE_POLL_TIMEOUT_S,
    interval_s: float = _SAVE_POLL_INTERVAL_S,
) -> bool:
    """Poll for a file to appear rather than checking once.

    The underlying FlightSave SimConnect call is asynchronous: it queues the
    write and returns before MSFS has necessarily finished it. The vendored
    library's own save_flight() reads the file straight back immediately
    afterwards (flight_to_dic) -- exactly this race, and the reason it can
    raise instead of returning False; see save_flight's docstring below. A
    single immediate Path.exists() can land in that same window and report a
    save that succeeds a moment later as a failure -- the same shape of
    dishonesty as claiming success for work that never happened, just
    inverted, so this polls briefly instead of trusting one snapshot in time.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        if path.exists():
            return True
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(interval_s)


@handle_simconnect_errors
@require_connection
async def load_flight(
    path: Annotated[
        str,
        Field(description=r"Absolute path to a .FLT file, e.g. "
                          r"'C:\Users\you\Documents\approach-test.FLT'",
              min_length=4),
    ],
) -> FlightResult | ToolError:
    """Load a saved flight, replacing the current one.

    Use this to restore a known starting state before a test run. The
    current flight is discarded without prompting.
    """
    validated = _validate_path(path, ".FLT", must_exist=True)
    if isinstance(validated, ToolError):
        return validated

    manager = SimConnectManager()
    ok = await manager.run_sync(lambda: manager.sm.load_flight(str(validated)))
    if not ok:
        return ToolError(
            error="LOAD_FAILED",
            message=f"MSFS refused to load '{path}'.",
            suggestion="Check the file is a valid .FLT for this MSFS version, "
                       "and that the sim is not mid-load.",
        )
    return FlightResult(
        action="msfs_load_flight", path=str(validated),
        message=f"Loaded flight '{validated.name}'",
    )


@handle_simconnect_errors
@require_connection
async def save_flight(
    path: Annotated[str, Field(description="Absolute path for the .FLT file to write",
                               min_length=4)],
    title: Annotated[str, Field(description="Flight title shown in MSFS",
                                min_length=1, max_length=128)],
    description: Annotated[
        str, Field(description="Flight description", max_length=512)
    ] = "",
    overwrite: Annotated[
        bool,
        Field(description="Replace the file at `path` if one already exists there"),
    ] = False,
) -> FlightResult | ToolError:
    """Save the current flight to a .FLT file.

    Capture a known state so a later msfs_load_flight call can restore it.
    Refuses to replace an existing file unless overwrite=True.

    The library's `sm.save_flight()` ends with an unconditional `return False`,
    so its return value says nothing about success -- this checks whether
    the file was actually written instead. Its body also reads the file
    straight back (flight_to_dic) immediately after issuing an asynchronous
    FlightSave; if MSFS has not finished writing yet, that read-back can
    raise rather than return False. Both a clean return and a raised
    exception are followed by the same polling existence check below, since
    the file on disk is the only signal either path can be trusted to leave
    behind.
    """
    validated = _validate_path(path, ".FLT", must_exist=False)
    if isinstance(validated, ToolError):
        return validated

    existed_before = validated.exists()
    if existed_before and not overwrite:
        return ToolError(
            error="ALREADY_EXISTS",
            message=f"A flight already exists at '{path}'.",
            suggestion="Pass overwrite=True to replace it, or choose a different path.",
        )

    manager = SimConnectManager()

    def _save() -> None:
        manager.sm.save_flight(str(validated), title, description)

    save_error: Exception | None = None
    try:
        await manager.run_sync(_save)
    except Exception as e:
        # Known library quirk (see docstring above): save_flight's own
        # post-save read-back can raise when it runs before MSFS finishes
        # writing. Caught here, specifically, so the file-existence poll
        # below -- not this exception -- decides success or failure; letting
        # it fall through to handle_simconnect_errors' generic catch-all
        # would report a save that actually landed as an opaque UNEXPECTED.
        save_error = e
        logger.debug(
            "sm.save_flight() call raised; verifying via a file poll "
            "instead of trusting this exception alone",
            exc_info=True,
        )

    if not await _wait_for_file(validated):
        detail = f" The save call also raised: {save_error!r}" if save_error else ""
        return ToolError(
            error="SAVE_FAILED",
            message=f"MSFS did not write '{path}'.{detail}",
            suggestion="Check the directory exists and is writable, and that "
                       "a flight is currently loaded.",
        )
    return FlightResult(
        action="msfs_save_flight",
        path=str(validated),
        message=(
            f"Saved flight to '{validated.name}'"
            + (" (replaced an existing file)" if existed_before else "")
        ),
    )


@handle_simconnect_errors
@require_connection
async def load_flight_plan(
    path: Annotated[str, Field(description="Absolute path to a .PLN flight plan",
                               min_length=4)],
) -> FlightResult | ToolError:
    """Load a .PLN flight plan into the aircraft's GPS or FMS.

    The aircraft is not repositioned; only the plan is loaded.
    """
    validated = _validate_path(path, ".PLN", must_exist=True)
    if isinstance(validated, ToolError):
        return validated

    manager = SimConnectManager()
    ok = await manager.run_sync(lambda: manager.sm.load_flight_plan(str(validated)))
    if not ok:
        return ToolError(
            error="LOAD_FAILED",
            message=f"MSFS refused to load flight plan '{path}'.",
            suggestion="Check the file is a valid .PLN for this MSFS version.",
        )
    return FlightResult(
        action="msfs_load_flight_plan", path=str(validated),
        message=f"Loaded flight plan '{validated.name}'",
    )


@handle_simconnect_errors
@require_connection
async def create_ai_object(
    title: Annotated[
        str,
        Field(description="Exact aircraft or object title as MSFS knows it, "
                          "e.g. 'Boeing 747-8i Asobo'", min_length=1, max_length=128),
    ],
    latitude: Annotated[float, Field(description="Latitude", ge=-90, le=90)],
    longitude: Annotated[float, Field(description="Longitude", ge=-180, le=180)],
    altitude_ft: Annotated[
        float, Field(description="Altitude in feet", ge=-2000, le=275000)
    ] = 0.0,
    heading: Annotated[float, Field(description="Heading in degrees true", ge=0, lt=360)] = 0.0,
    on_ground: Annotated[bool, Field(description="Place the object on the ground")] = True,
    airspeed: Annotated[int, Field(description="Airspeed in knots", ge=0, le=2000)] = 0,
) -> AiObjectResult | ToolError:
    """Spawn an AI aircraft or object at a position.

    Useful for building traffic or collision-avoidance test scenarios. The
    title must match an installed aircraft exactly -- MSFS ignores the
    request silently for an unmatched title, with no error at all, so a
    successful call here confirms the request was sent, never that anything
    actually appeared in the sim.
    """
    # ge=/le= above is enforced by FastMCP's schema validation for real MCP
    # calls, but a direct Python call (as tests do) bypasses that entirely --
    # same reasoning as the colour check in tools/utilities.py's
    # send_sim_text, so this is the actual enforcement for that path.
    if not (-90.0 <= latitude <= 90.0):
        return ToolError(
            error="INVALID_COORDINATES",
            message=f"Latitude {latitude} is out of range.",
            suggestion="Latitude must be between -90 and 90 degrees.",
        )
    if not (-180.0 <= longitude <= 180.0):
        return ToolError(
            error="INVALID_COORDINATES",
            message=f"Longitude {longitude} is out of range.",
            suggestion="Longitude must be between -180 and 180 degrees.",
        )

    manager = SimConnectManager()

    def _create() -> None:
        manager.sm.createSimulatedObject(
            title,
            latitude,
            longitude,
            manager.sm.new_request_id(),
            hdg=heading,
            gnd=1 if on_ground else 0,
            alt=altitude_ft,
            speed=airspeed,
        )

    await manager.run_sync(_create)
    return AiObjectResult(
        title=title, latitude=latitude, longitude=longitude,
        message=f"Requested AI object '{title}'. MSFS ignores the request "
                "silently if the title does not match an installed aircraft.",
    )
