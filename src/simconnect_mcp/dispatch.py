"""Ownership of the SimConnect dispatch loop.

The vendored SimConnectMobiFlight intercepts only CLIENT_DATA messages and
delegates everything else to SimConnect.my_dispatch_proc.  Two of those
delegated branches write to stdout, which on a stdio MCP server corrupts the
JSON-RPC stream:

  * handle_state_event  -- bare print() on every SYSTEM_STATE message
  * the *_LIST branch   -- facilitie.dump() and parent.dump(), both print()

SimConnectDispatcher takes over the loop so those branches are unreachable,
and so SimVar reads and SimConnect exceptions can be correlated back to the
call that caused them.

Exception correlation matches the exception's send ID against the value
returned by GetLastSentPacketID at send time.

The installed package's SIMCONNECT_RECV_EXCEPTION binding cannot be used for
this.  The SDK declares UNKNOWN_SENDID and UNKNOWN_INDEX as *static
constants* alongside three wire fields (dwException, dwSendID, dwIndex); the
package wrongly puts both constants inside _fields_, producing a 32-byte
struct where the wire format is 24.  Its names are therefore shifted by one
slot: package `UNKNOWN_SENDID` (offset 16) is the real dwSendID, and package
`dwSendID` (offset 20) is the real dwIndex.  The same package models the
identical pattern correctly for SIMCONNECT_RECV_EVENT.UNKNOWN_GROUP (a class
constant outside _fields_), which confirms this is a packaging mistake.

We therefore declare our own correctly-shaped struct below and cast to it,
rather than trusting either misnamed attribute.
"""
from __future__ import annotations

import ctypes
import logging
import threading
from collections.abc import Callable
from ctypes.wintypes import DWORD
from dataclasses import dataclass, field
from typing import Any

from SimConnect.Enum import (
    SIMCONNECT_EXCEPTION,
    SIMCONNECT_RECV,
    SIMCONNECT_RECV_ASSIGNED_OBJECT_ID,
    SIMCONNECT_RECV_ID,
    SIMCONNECT_RECV_SIMOBJECT_DATA,
)

from simconnect_mcp.facilities import FacilityCollector, parse_facility_message
from simconnect_mcp.vendor.simconnect_mobiflight import SimConnectMobiFlight

log = logging.getLogger(__name__)

# SIMCONNECT_DATATYPE_STRING256's fixed wire size. Every is_string request
# resolved by _on_simobject_data was registered at that datatype by
# SimVarAccessor.definition_id (see simvar_access.STRING_SIZE, which this
# must match) -- so 256 is the declared field width, not a guess.
STRING256_SIZE = 256


class RecvException(SIMCONNECT_RECV):
    """SIMCONNECT_RECV_EXCEPTION with the layout the SDK actually defines.

    Three wire fields after the 12-byte SIMCONNECT_RECV header, for a total
    of 24 bytes.  UNKNOWN_SENDID and UNKNOWN_INDEX are static constants in
    the SDK, not fields -- see the module docstring for why the installed
    package's binding cannot be used here.
    """

    _fields_ = [
        ("dwException", DWORD),
        ("dwSendID", DWORD),
        ("dwIndex", DWORD),
    ]


FACILITY_RECV_IDS = frozenset({
    SIMCONNECT_RECV_ID.SIMCONNECT_RECV_ID_AIRPORT_LIST,
    SIMCONNECT_RECV_ID.SIMCONNECT_RECV_ID_WAYPOINT_LIST,
    SIMCONNECT_RECV_ID.SIMCONNECT_RECV_ID_NDB_LIST,
    SIMCONNECT_RECV_ID.SIMCONNECT_RECV_ID_VOR_LIST,
})


@dataclass
class PendingRequest:
    """One in-flight SimVar read or write.

    Reads carry a `request_id` and are resolved by SIMOBJECT_DATA.  Writes
    produce no reply, so `request_id` is None and they are only ever resolved
    by an exception (or by timing out successfully).

    A single read can itself issue two DLL calls -- AddToDataDefinition and
    RequestDataOnSimObject -- and SimConnect can raise an exception
    correlated to either one's packet ID (a bad variable name or a bad unit
    is typically reported against the AddToDataDefinition send, not the
    RequestDataOnSimObject that follows it). `send_ids` accumulates every
    packet ID this request is waiting on, so an exception against any of
    them resolves it.

    `resolved` is distinct from `done`: `done` is also set when the caller
    gives up locally on a timeout (see SimVarAccessor.read()), but a local
    timeout cancels nothing on the DLL side -- SimConnect may still deliver
    SIMOBJECT_DATA for this request's ID later. `resolved` is set only by
    resolve_data()/resolve_exception(), i.e. only when the DLL side is
    actually known to be finished with this request's ID (data arrived, or
    SimConnect rejected it and nothing is coming). RequestRegistry.discard()
    uses this to decide whether the ID is safe to recycle.
    """

    request_id: int | None
    is_string: bool = False
    send_ids: list[int] = field(default_factory=list)
    value: Any = None
    exception: str | None = None
    done: threading.Event = field(default_factory=threading.Event)
    resolved: bool = False


class RequestRegistry:
    """Thread-safe index of in-flight requests, by request ID and send IDs.

    `pending_lock` is public on purpose: the accessor holds it across the DLL
    send and the subsequent bind_send_id() so the dispatch thread cannot
    deliver an exception for a request whose send ID is not yet recorded.
    """

    def __init__(self) -> None:
        self.pending_lock = threading.Lock()
        self._by_request: dict[int, PendingRequest] = {}
        self._by_send: dict[int, PendingRequest] = {}
        self._free_request_ids: list[int] = []

    def acquire_request_id(self, allocate: Callable[[], int]) -> int:
        """Get a SimConnect request ID, reusing one `discard()` just freed.

        SimConnect.new_request_id() rebuilds an Enum from every prior member
        plus one, on *every* call, so allocating fresh per read makes cost
        grow with the cumulative number of reads ever issued rather than
        with the number of distinct variables in flight -- unbounded over a
        long-running server (measured on real hardware: single-call cost
        rising from ~4.5ms at 600 cumulative allocations to ~31ms at 2000,
        cumulative time into the tens of seconds).

        This pool bounds the real allocator to (at most) the number of
        requests concurrently in flight: a freed id is handed back out
        before `allocate` is ever called. `allocate` is supplied by the
        caller -- SimVarAccessor holds the SimConnect instance the
        registry does not -- and is invoked only while the free-list is
        empty. The whole operation runs under `pending_lock` so two
        concurrent callers can never be handed the same id, whether that id
        comes from the free-list or from `allocate`.
        """
        with self.pending_lock:
            if self._free_request_ids:
                return self._free_request_ids.pop()
            return allocate()

    def register(self, req: PendingRequest) -> None:
        with self.pending_lock:
            if req.request_id is not None:
                self._by_request[req.request_id] = req

    def peek(self, request_id: int) -> PendingRequest | None:
        """Look up a pending request by ID without resolving it."""
        with self.pending_lock:
            return self._by_request.get(request_id)

    def bind_send_id(self, req: PendingRequest, send_id: int, _locked: bool = False) -> None:
        """Record another packet ID this request is waiting on.

        A request can accumulate more than one send ID -- e.g. a read binds
        both its AddToDataDefinition and RequestDataOnSimObject packets,
        since either can independently raise an exception. Pass
        _locked=True when pending_lock is already held by this thread.
        """
        if _locked:
            req.send_ids.append(send_id)
            self._by_send[send_id] = req
            return
        with self.pending_lock:
            req.send_ids.append(send_id)
            self._by_send[send_id] = req

    def resolve_data(self, request_id: int, value: Any) -> bool:
        with self.pending_lock:
            req = self._by_request.get(request_id)
        if req is None:
            return False
        req.value = value
        # Set before done: a waiter that wakes because done is set must
        # also observe resolved=True, since discard() (running on the
        # waiter's thread right after it wakes) uses resolved to decide
        # whether this request's ID is safe to recycle.
        req.resolved = True
        req.done.set()
        return True

    def resolve_exception(self, send_id: int, name: str) -> bool:
        with self.pending_lock:
            req = self._by_send.get(send_id)
        if req is None:
            return False
        req.exception = name
        req.resolved = True
        req.done.set()
        return True

    def discard(self, req: PendingRequest, *, recycle: bool = True) -> None:
        """Stop tracking a request, freeing its ID for reuse IF SAFE.

        A request's ID is only added back to the free-list when `resolved`
        is set -- i.e. resolve_data()/resolve_exception() actually ran for
        it, so the DLL side is known to be finished with that ID. A local
        timeout sets neither: it cancels nothing on the DLL side, so
        SimConnect may still deliver SIMOBJECT_DATA for that ID later. If
        that ID had already been recycled to a new, unrelated request, the
        late delivery would resolve the WRONG request with someone else's
        data (dwRequestID is all the wire format carries -- there is no
        generation token the dispatch handler could use to detect this).
        A timed-out ID is therefore retired rather than recycled: removed
        from _by_request (so a late delivery hits the dead-letter branch in
        _on_simobject_data, exactly as it always has) but never handed to
        another request. This costs one permanently-unused ID per timeout,
        bounded by how often timeouts occur (rare: a paused/hung sim), not
        by read count -- the hot, successful-read path is unaffected.

        `recycle=False` is for a caller whose `request_id` was never drawn
        from *this* free-list to begin with -- e.g. tools/flight.py's
        create_ai_object, which correlates on a number
        SimConnectManager.reserved_request_id() has permanently reserved for
        the "ai_object" key and rotates through for the rest of the
        connection's life (see that method's docstring). If a resolved
        request like that were allowed to recycle here, the ID would enter
        the *general* pool that acquire_request_id() hands out to ordinary
        SimVarAccessor reads -- while "ai_object" goes on handing out that
        exact same number on its own next call. Two independent waiters
        would then both be keyed on one ID, with SimConnect's replies
        landing on whichever happens to be registered at delivery time: the
        same class of race this whole recycle-only-if-resolved scheme
        exists to prevent, just entered through a different door. Default
        True preserves today's behaviour for every other caller.
        """
        with self.pending_lock:
            if req.request_id is not None:
                self._by_request.pop(req.request_id, None)
                if req.resolved and recycle:
                    self._free_request_ids.append(req.request_id)
            for send_id in req.send_ids:
                self._by_send.pop(send_id, None)


class _MobiFlightCrossTalkFilter(logging.Filter):
    """Silences one confirmed-benign warning from the vendored
    MobiFlightVariableRequests.client_data_callback_handler.

    vendor/simconnect_mobiflight.py's dispatch loop calls *every* registered
    client-data handler for *every* incoming CLIENT_DATA message, with no
    per-definition-ID routing (see SimConnectMobiFlight.my_dispatch_proc).
    tools/pmdg.py's data managers register their own handlers on this same
    loop for the PMDG_777X_Data / PMDG_NG3_Data client data areas -- so
    whenever a PMDG message arrives, MobiFlightVariableRequests's handler
    also receives it, does not recognise the definition ID (it only tracks
    the dynamically-assigned IDs of L-vars it was asked to watch), and logs
    a warning. That is a no-op for that handler, correctly handled by
    PMDG's own -- live-verified: probing a PMDG 737-600's data area logs
    "client_data_callback_handler DefinitionID 1313289010 not found!"
    (1313289010 == PMDG_NG3_DATA_DEFINITION) even though the probe and the
    subsequent read both succeed.

    Deliberately narrow, and does not touch vendor/: matches only this
    exact message shape, logged via the bare `logging.warning(...)` module
    function in vendor/mobiflight_variable_requests.py (which uses no named
    logger of its own, so the record's logger name is the root logger's,
    "root"). Every other message on the root logger passes through
    unaffected.
    """

    _PREFIX = "client_data_callback_handler DefinitionID"
    _SUFFIX = "not found!"

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name != "root":
            return True
        message = record.getMessage()
        return not (message.startswith(self._PREFIX) and message.endswith(self._SUFFIX))


def _suppress_mobiflight_cross_talk_warning() -> None:
    """Install _MobiFlightCrossTalkFilter on the root logger, once.

    Idempotent so importing this module more than once (or constructing more
    than one SimConnectDispatcher) never stacks duplicate filters.
    """
    root_logger = logging.getLogger()
    if any(isinstance(f, _MobiFlightCrossTalkFilter) for f in root_logger.filters):
        return
    root_logger.addFilter(_MobiFlightCrossTalkFilter())


_suppress_mobiflight_cross_talk_warning()


class SimConnectDispatcher(SimConnectMobiFlight):
    """SimConnectMobiFlight that owns the whole dispatch loop."""

    def __init__(self, auto_connect: bool = True, library_path: str | None = None) -> None:
        # Must exist before super().__init__ starts the dispatch thread.
        self.registry = RequestRegistry()
        self.facilities = FacilityCollector()
        # Extension point with no in-tree consumer: Phase 0 built this hook
        # for Phase 2's facility work, which then took `self.facilities`
        # (the collector above) instead. Nothing in src/ appends to it --
        # only tests do -- so an empty list here is the expected state, not
        # a wiring bug to hunt down. Kept because an out-of-tree consumer
        # wanting the raw pData costs nothing to support.
        self.facility_handlers: list = []
        if library_path:
            super().__init__(auto_connect, library_path)
        else:
            super().__init__(auto_connect)

    def my_dispatch_proc(self, pData, cbData, pContext):  # noqa: N802 (library name)
        dwID = pData.contents.dwID

        if dwID == SIMCONNECT_RECV_ID.SIMCONNECT_RECV_ID_SIMOBJECT_DATA:
            data = ctypes.cast(
                pData, ctypes.POINTER(SIMCONNECT_RECV_SIMOBJECT_DATA)
            ).contents
            self._on_simobject_data(data)
            return

        if dwID == SIMCONNECT_RECV_ID.SIMCONNECT_RECV_ID_EXCEPTION:
            exc = ctypes.cast(pData, ctypes.POINTER(RecvException)).contents
            try:
                name = SIMCONNECT_EXCEPTION(exc.dwException).name
            except ValueError:
                name = f"SIMCONNECT_EXCEPTION_{exc.dwException}"
            # dwSize is the sim's own view of the message length. If it is not
            # 24, our struct does not match the wire format and correlation
            # would silently read the wrong offset -- worth knowing loudly.
            if exc.dwSize != ctypes.sizeof(RecvException):
                log.warning(
                    "SIMCONNECT_RECV_EXCEPTION size %s, expected %s -- send-ID "
                    "correlation may be reading the wrong offset",
                    exc.dwSize, ctypes.sizeof(RecvException),
                )
            if not self.registry.resolve_exception(exc.dwSendID, name):
                log.debug("Unmatched SimConnect exception: %s (sendID=%s)", name, exc.dwSendID)
            return

        if dwID == SIMCONNECT_RECV_ID.SIMCONNECT_RECV_ID_ASSIGNED_OBJECT_ID:
            # The library's own branch (SimConnect.py) stashes dwObjectID in
            # os.environ["SIMCONNECT_OBJECT_ID"] -- process-global mutable
            # state with no per-call correlation, so a second concurrent
            # AICreateSimulatedObject would clobber the first's answer. Not
            # a stdout print like the branches above, but still not
            # something to let run: resolve it through the registry instead,
            # exactly like SIMOBJECT_DATA, so tools/flight.py's
            # create_ai_object can correlate the reply to its own call.
            #
            # Unlike RecvException (see this module's docstring),
            # SIMCONNECT_RECV_ASSIGNED_OBJECT_ID's installed binding is
            # correctly shaped -- verified against the SDK: the base
            # SIMCONNECT_RECV header is 3 DWORDs (12 bytes) and this adds
            # exactly dwRequestID + dwObjectID (2 more), with no bogus extra
            # constants folded into _fields_ the way the exception struct
            # had. 20 bytes total, matching the real wire format, so it is
            # used directly rather than redeclared.
            assigned = ctypes.cast(
                pData, ctypes.POINTER(SIMCONNECT_RECV_ASSIGNED_OBJECT_ID)
            ).contents
            if not self.registry.resolve_data(assigned.dwRequestID, assigned.dwObjectID):
                log.debug(
                    "Unmatched ASSIGNED_OBJECT_ID for request %s (objectID=%s)",
                    assigned.dwRequestID, assigned.dwObjectID,
                )
            return

        if dwID == SIMCONNECT_RECV_ID.SIMCONNECT_RECV_ID_SYSTEM_STATE:
            # SimConnect.handle_state_event print()s this straight to stdout.
            log.debug("SYSTEM_STATE message swallowed to protect the stdio stream")
            return

        if dwID in FACILITY_RECV_IDS:
            # The library's branch calls dump(), which print()s -- never let
            # that run. Parse into the collector ourselves; a parse failure
            # is swallowed (logged at debug) rather than raised, since this
            # runs on SimConnect's own callback thread, not the event loop.
            try:
                kind, header, entries = parse_facility_message(pData)
                self.facilities.handle(kind, header, entries)
            except Exception:
                log.debug("Could not parse facility message", exc_info=True)
            # Each registered handler gets its own try/except: one raising
            # callback must not stop the others from running, nor propagate
            # out of this native callback.
            for handler in self.facility_handlers:
                try:
                    handler(pData)
                except Exception:
                    log.warning("Facility handler raised an exception", exc_info=True)
            return

        super().my_dispatch_proc(pData, cbData, pContext)

    def _on_simobject_data(self, data) -> None:
        """Decode one SIMOBJECT_DATA payload and resolve its request."""
        request_id = data.dwRequestID
        req = self.registry.peek(request_id)
        if req is None:
            log.debug("SIMOBJECT_DATA for unknown request %s", request_id)
            return

        address = ctypes.addressof(data.dwData)
        if req.is_string:
            # dwData is DWORD * 8192 (32KB); a STRING256 value only ever
            # occupies its first 256 bytes. ctypes.cast(..., c_char_p).value
            # scans for the first NUL *anywhere* in memory, so a value with
            # no NUL inside its own 256 bytes would read past the declared
            # field into whatever data follows. string_at with an explicit
            # length can never read past that bound.
            raw = ctypes.string_at(address, STRING256_SIZE).split(b"\0", 1)[0]
            value: Any = raw.decode("ascii", errors="replace").strip()
        else:
            value = ctypes.cast(address, ctypes.POINTER(ctypes.c_double)).contents.value
        self.registry.resolve_data(request_id, value)
