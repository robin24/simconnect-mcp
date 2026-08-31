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
from simconnect_mcp.dispatch import PendingRequest
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


# How long msfs_save_flight/msfs_load_flight/msfs_load_flight_plan poll for
# MSFS to resume answering SimConnect after the underlying FlightSave/
# FlightLoad call, before giving up and returning anyway.
#
# Measured live against a real PMDG 737-800
# (.superpowers/sdd/2026-08-29-mcp-modernization-phase2-capability/
# flightsave_stall_probe.py): FlightSave writes its ~69 KB .FLT file in
# 0.11-0.14s, but MSFS then stops servicing SimConnect ENTIRELY -- not
# merely slow, no response of any kind -- for 14.0-14.5s while it finishes
# the save (three separate runs; a no-save control with the identical
# connect/read/wait shape never failed once). Returning as soon as the file
# exists -- the previous behaviour -- handed the caller `status: "ok"`
# roughly 14s before the sim could answer anything again, so the agent's
# very NEXT tool call failed, and failed blaming a "paused, loading, or not
# running" sim (simvar_access.py's SimVarTimeoutError message) that was in
# fact neither: it was busy finishing the save this server had just asked
# for.
#
# FlightLoad's measured stall was much shorter (~0.9s, one round-trip --
# see flightload_stall_probe.py in the same directory) but nothing
# guarantees a heavier scenery load stays that cheap, so all three flight
# tools share this one wait rather than only the tool it was measured on.
#
# 30s is comfortably over the worst number measured above (14.49s) without
# hanging forever if the sim is genuinely stuck rather than merely busy.
_SIM_RECOVERY_TIMEOUT_S = 30.0
# Short per-probe timeout, deliberately much smaller than the bound above:
# this is what keeps each probe's hold on SimConnectManager._sim_lock brief
# (see _wait_for_sim_responsive's docstring) and, as a side effect, bounds
# how long a cancellation of the calling tool call can be deferred behind
# one in-flight probe.
_SIM_RECOVERY_PROBE_TIMEOUT_S = 0.5
_SIM_RECOVERY_POLL_INTERVAL_S = 0.1
# Any cheap, always-present numeric SimVar works as the recovery probe;
# this is the one flightsave_stall_probe.py measured the stall with.
_SIM_RECOVERY_PROBE_VAR = "PLANE_ALTITUDE"


async def _wait_for_sim_responsive(manager: SimConnectManager) -> tuple[bool, float]:
    """Poll SimConnect until it answers again, bounded by
    _SIM_RECOVERY_TIMEOUT_S.

    The three module constants above (_SIM_RECOVERY_TIMEOUT_S,
    _SIM_RECOVERY_PROBE_TIMEOUT_S, _SIM_RECOVERY_POLL_INTERVAL_S) are read
    as plain globals in the body below rather than bound as default
    argument values, deliberately: it lets a test shrink them with
    `monkeypatch.setattr(flight_module, "_SIM_RECOVERY_TIMEOUT_S", ...)` --
    the same pattern tools/facilities.py's _COLLECT_TIMEOUT/_POLL_INTERVAL
    use -- so the "never recovers" path can be exercised in milliseconds
    instead of actually waiting out a 30s production bound.

    Makes good on "when this call returns, the sim is usable again" for the
    three tools below. FlightSave/FlightLoad both queue their work inside
    MSFS and hand control back here well before MSFS is actually done, and
    MSFS stops servicing SimConnect at all for the duration -- see the
    measurement comment above _SIM_RECOVERY_TIMEOUT_S. Returning as soon as
    the file exists (or the load call returns True) would still hand the
    caller a success while the sim cannot yet answer anything.

    Returns (True, elapsed) once a probe read succeeds, (False, elapsed) if
    _SIM_RECOVERY_TIMEOUT_S elapses first -- `elapsed` is how long this
    call actually waited, so a caller can report a real number ("waited
    30.1s") rather than just the configured bound. Never raises for an
    unresponsive sim -- that is reported as a plain bool for the caller to
    turn into a `warning`, not a ToolError, since the save/load itself
    already succeeded; FlightSave returning S_OK and the file landing on
    disk are not in question here. A genuine asyncio.CancelledError (a
    client cancelling this call) is a BaseException, not an Exception, so
    the `except Exception` below does not catch it -- it propagates
    normally.

    Each iteration is one complete, independent SimVarAccessor.read() call
    with its own short _SIM_RECOVERY_PROBE_TIMEOUT_S, exactly the pattern
    flightsave_stall_probe.py used to measure the stall in the first place.
    Two consequences of doing it this way, both deliberate:

    * SimConnectManager._sim_lock (taken inside run_sync) is only ever held
      for one short probe read at a time, never for the whole wait -- the
      sleep between probes (_SIM_RECOVERY_POLL_INTERVAL_S) happens after
      run_sync has already returned, with the lock released. Waiting out
      the full ~14s inside a single run_sync call would hold that lock
      continuously and queue every OTHER tool call on the server behind
      this one wait -- trading one slow tool for a frozen server. Probing
      in short bursts keeps the server usable for everything else while
      this one call waits.
    * Nothing is left registered or locked if this coroutine is cancelled
      mid-wait. Unlike tools/facilities.py's facility subscription or
      tools/lvars.py's response handler -- both registered once up front
      and only safe to tear down via a `finally` because they must survive
      several `await`s in between -- a probe read's SimConnect-side
      bookkeeping (its RequestRegistry entry, its definition's send-ID
      binding) is entirely contained inside SimVarAccessor.read() itself.
      That call always reaches its own `finally: registry.discard(...)`
      before returning, in the executor thread, regardless of whether the
      coroutine awaiting it here has since been cancelled -- run_in_executor
      cannot forcibly stop a thread already running synchronous code, so
      the read simply finishes and cleans up on its own. Nothing in this
      wait loop is held across an `await` boundary, so there is no
      resource of its own left for a `finally` at this level to release.
    """
    start = time.monotonic()
    if manager.accessor is None:
        # No SimVarAccessor on this connection (plain SimConnect fallback --
        # see connection.py's connect()). save_flight/load_flight/
        # load_flight_plan's primary action does not need the accessor
        # either (see require_connection's needs_accessor docstring), so
        # this degrades to the pre-fix behaviour of not waiting at all
        # rather than refusing the whole tool over what is only ever a
        # bonus check.
        return True, 0.0

    deadline = start + _SIM_RECOVERY_TIMEOUT_S
    while True:
        try:
            await manager.run_sync(
                lambda: manager.accessor.read(
                    _SIM_RECOVERY_PROBE_VAR, timeout=_SIM_RECOVERY_PROBE_TIMEOUT_S
                )
            )
            return True, time.monotonic() - start
        except Exception:
            pass
        if time.monotonic() >= deadline:
            return False, time.monotonic() - start
        await asyncio.sleep(_SIM_RECOVERY_POLL_INTERVAL_S)


def _recovery_warning(action: str, waited_s: float) -> str:
    """FlightResult.warning text for when the sim had not resumed answering
    SimConnect within the wait bound. `action` names the operation that
    already succeeded, e.g. "save", "load", "flight-plan load" -- this is
    never reached for a save/load that itself failed, so the wording always
    leads with the fact that the requested operation is not in doubt.
    `waited_s` is _wait_for_sim_responsive's own measured elapsed time, not
    just the configured bound -- the fix brief specifically asked for "how
    long was waited", which the bound alone does not answer (the actual
    wait can run slightly past it: the last probe/sleep before the deadline
    check is not clipped)."""
    return (
        f"MSFS had not resumed answering SimConnect requests after waiting "
        f"{waited_s:.1f}s (bounded at {_SIM_RECOVERY_TIMEOUT_S:.0f}s) for "
        f"the {action} to complete. That is not a failure of the {action} "
        f"itself -- it already succeeded -- but the sim may still be busy, "
        "so the very next tool call could be slow or fail."
    )


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

    Does not return as soon as the load call succeeds -- it waits for MSFS
    to resume answering SimConnect first (bounded; see
    _wait_for_sim_responsive and msfs_save_flight's docstring for the
    measurement behind this). FlightLoad's own stall was short in the one
    round trip measured live (~0.9s), but a heavier scenery load could take
    much longer, so this does not assume load is always cheap.
    """
    validated = _validate_path(path, ".FLT", must_exist=True)
    if isinstance(validated, ToolError):
        return validated

    manager = SimConnectManager()
    start = time.monotonic()
    ok = await manager.run_sync(lambda: manager.sm.load_flight(str(validated)))
    if not ok:
        return ToolError(
            error="LOAD_FAILED",
            message=f"MSFS refused to load '{path}'.",
            suggestion="Check the file is a valid .FLT for this MSFS version, "
                       "and that the sim is not mid-load.",
        )
    responsive, waited = await _wait_for_sim_responsive(manager)
    return FlightResult(
        action="msfs_load_flight", path=str(validated),
        message=f"Loaded flight '{validated.name}'",
        duration_s=round(time.monotonic() - start, 2),
        warning=None if responsive else _recovery_warning("load", waited),
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

    Does not return once the file appears, either. MSFS keeps SimConnect
    entirely unresponsive for a further ~14s (measured live) while it
    actually finishes the save; this waits that out too, bounded, so a
    caller never gets `status: "ok"` while the sim still cannot answer
    anything -- see _wait_for_sim_responsive's docstring for the full
    measurement and why the wait is structured the way it is.
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
    start = time.monotonic()

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

    # The file existing is not the same as the sim being usable again -- see
    # _wait_for_sim_responsive's docstring and the measurement comment above
    # _SIM_RECOVERY_TIMEOUT_S. Do NOT treat a False (unresponsive) result as
    # a failure here: FlightSave already returned S_OK and the file is
    # confirmed on disk, so the save itself is not in doubt -- only whether
    # the sim has finished being busy about it.
    responsive, waited = await _wait_for_sim_responsive(manager)
    return FlightResult(
        action="msfs_save_flight",
        path=str(validated),
        message=(
            f"Saved flight to '{validated.name}'"
            + (" (replaced an existing file)" if existed_before else "")
        ),
        duration_s=round(time.monotonic() - start, 2),
        warning=None if responsive else _recovery_warning("save", waited),
    )


@handle_simconnect_errors
@require_connection
async def load_flight_plan(
    path: Annotated[str, Field(description="Absolute path to a .PLN flight plan",
                               min_length=4)],
) -> FlightResult | ToolError:
    """Load a .PLN flight plan into the aircraft's GPS or FMS.

    The aircraft is not repositioned; only the plan is loaded.

    Does not return as soon as the load call succeeds -- see
    msfs_save_flight's docstring and _wait_for_sim_responsive for why this
    waits (bounded) for MSFS to resume answering SimConnect first.
    """
    validated = _validate_path(path, ".PLN", must_exist=True)
    if isinstance(validated, ToolError):
        return validated

    manager = SimConnectManager()
    start = time.monotonic()
    ok = await manager.run_sync(lambda: manager.sm.load_flight_plan(str(validated)))
    if not ok:
        return ToolError(
            error="LOAD_FAILED",
            message=f"MSFS refused to load flight plan '{path}'.",
            suggestion="Check the file is a valid .PLN for this MSFS version.",
        )
    responsive, waited = await _wait_for_sim_responsive(manager)
    return FlightResult(
        action="msfs_load_flight_plan", path=str(validated),
        message=f"Loaded flight plan '{validated.name}'",
        duration_s=round(time.monotonic() - start, 2),
        warning=None if responsive else _recovery_warning("flight-plan load", waited),
    )


# How long create_ai_object waits for SimConnect's
# SIMCONNECT_RECV_ID_ASSIGNED_OBJECT_ID reply before giving up and leaving
# object_id as None. Measured live (MSFS 2024, Cessna Citation Longitude at
# KATL): a title matching an installed aircraft got its reply in ~0.11s: a
# bogus title never gets one at all, so that call always pays the full
# timeout -- confirmed live at 5.108s elapsed against this exact bound.
# 5s is generous relative to the ~0.11s measured success case (comparable
# margin to tools/facilities.py's _COLLECT_TIMEOUT, chosen the same way),
# without making a bogus-title call hang for long.
_AI_OBJECT_REPLY_TIMEOUT_S = 5.0
_AI_OBJECT_POLL_INTERVAL_S = 0.1


async def _wait_for_assigned_object_id(pending: PendingRequest) -> int | None:
    """Poll for dispatch.py's ASSIGNED_OBJECT_ID branch to resolve `pending`.

    Bounded by _AI_OBJECT_REPLY_TIMEOUT_S. Polls with asyncio.sleep rather
    than blocking synchronously (e.g. `pending.done.wait(...)` inside
    run_sync): the reply is delivered by SimConnect's own dispatch thread,
    not by anything this coroutine calls, so making the wait itself
    synchronous would hold SimConnectManager._sim_lock for the whole
    duration (see run_sync's docstring) and queue every other tool call on
    the server behind it. Same shape, and the same reasoning, as
    tools/facilities.py's _collect() poll loop and this module's own
    _wait_for_sim_responsive.

    Returns the object id once resolve_data() (dispatch.py's
    ASSIGNED_OBJECT_ID branch) has actually run for this request; None if
    the deadline passes first. `pending.resolved` is set only when the DLL
    side is confirmed finished with this request's id (see PendingRequest's
    docstring in dispatch.py), so this never invents an id from a
    still-in-flight guess.
    """
    deadline = time.monotonic() + _AI_OBJECT_REPLY_TIMEOUT_S
    while not pending.done.is_set():
        if time.monotonic() >= deadline:
            return None
        await asyncio.sleep(_AI_OBJECT_POLL_INTERVAL_S)
    return int(pending.value) if pending.resolved else None


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
    title must match an installed aircraft exactly. When it does, SimConnect
    confirms the object was actually created with an ASSIGNED_OBJECT_ID
    reply, and `object_id` on the result carries it -- also the id
    SimConnect_AIRemoveObject would need to remove it again. When the title
    matches nothing installed, MSFS ignores the request silently: no error,
    no reply, so `object_id` stays null. Treat that null as "not confirmed
    to exist," not as a definite failure of this call -- a request registry
    being briefly slow to answer, or a connection with none at all (the
    plain SimConnect fallback), leaves the same null for a different reason;
    see `message` for which applies.
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

    # Serializes register -> create -> wait -> discard end to end: this
    # correlates on the single request id reserved_request_id() reserves for
    # the "ai_object" key (ring=1), so two overlapping creations must not
    # both be registered against it at once -- see ai_object_lock's
    # docstring (connection.py).
    async with manager.ai_object_lock():

        def _create() -> tuple[bool, PendingRequest | None]:
            from SimConnect.Enum import SIMCONNECT_DATA_INITPOSITION

            # Not manager.sm.createSimulatedObject(): that wrapper
            # (SimConnect.py in the installed library) builds this exact DLL
            # call but discards the HRESULT
            # SimConnect_AICreateSimulatedObject returns -- so a call MSFS
            # rejected outright (stale handle, E_INVALIDARG, a connection
            # dropped between ensure_connected and here) looked identical to
            # one it accepted, and this tool reported success for a request
            # that never left. SimConnect_AICreateSimulatedObject's restype
            # is HRESULT (Attributes.py in the same package) and IsHR is the
            # library's own helper for reading one. Same wrapper defect,
            # same fix, as tools/utilities.py's send_sim_text -- see the
            # extended comment there for the general rule.
            init_pos = SIMCONNECT_DATA_INITPOSITION()
            init_pos.Altitude = altitude_ft
            init_pos.Latitude = latitude
            init_pos.Longitude = longitude
            init_pos.Pitch = 0
            init_pos.Bank = 0
            init_pos.Heading = heading
            init_pos.OnGround = 1 if on_ground else 0
            init_pos.Airspeed = airspeed

            # Reserved once per connection rather than allocated per call --
            # see connection.py's reserved_request_id. Registered against
            # the dispatcher's request registry *before* the DLL call, the
            # same ordering tools/facilities.py's _subscribe uses for its
            # own collector, so a reply that somehow arrived before this
            # function returned would still be correlated correctly.
            request_id = manager.reserved_request_id("ai_object")
            registry = getattr(manager.sm, "registry", None)
            pending = (
                PendingRequest(request_id=request_id) if registry is not None else None
            )
            if pending is not None:
                registry.register(pending)

            hr = manager.sm.dll.AICreateSimulatedObject(
                manager.sm.hSimConnect, title.encode(), init_pos, request_id,
            )
            return bool(manager.sm.IsHR(hr, 0)), pending

        accepted, pending = await manager.run_sync(_create)

        if not accepted:
            if pending is not None:
                # Nothing is coming: SimConnect rejected the call outright,
                # so dispatch.py's ASSIGNED_OBJECT_ID branch will never fire
                # for this request id. recycle=False -- see
                # RequestRegistry.discard's docstring -- keeps this id
                # reserved for "ai_object" rather than leaking it into the
                # general SimVarAccessor pool.
                manager.sm.registry.discard(pending, recycle=False)
            return ToolError(
                error="AI_OBJECT_FAILED",
                message=f"MSFS rejected the request to create AI object '{title}'.",
                suggestion="Check the sim is running and not mid-load, then "
                           "reconnect with msfs_connect and try again.",
            )

        object_id: int | None = None
        if pending is not None:
            try:
                object_id = await _wait_for_assigned_object_id(pending)
            finally:
                manager.sm.registry.discard(pending, recycle=False)

    if object_id is not None:
        message = (
            f"Created AI object '{title}' -- MSFS assigned it object ID "
            f"{object_id}, confirming the object actually exists, not just "
            "that SimConnect accepted the request."
        )
    elif pending is not None:
        message = (
            f"Requested AI object '{title}'. SimConnect accepted the "
            f"request, but no confirmation arrived within "
            f"{_AI_OBJECT_REPLY_TIMEOUT_S:.0f}s. MSFS ignores this request "
            "silently if the title does not match an installed aircraft, so "
            "this is not confirmation the object exists."
        )
    else:
        message = (
            f"Requested AI object '{title}'. SimConnect accepted the "
            "request, but MSFS ignores it silently if the title does not "
            "match an installed aircraft, and this connection has no "
            "request registry to confirm creation either way."
        )

    return AiObjectResult(
        title=title, latitude=latitude, longitude=longitude,
        object_id=object_id, message=message,
    )
