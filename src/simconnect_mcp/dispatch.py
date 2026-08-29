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
    SIMCONNECT_RECV_ID,
    SIMCONNECT_RECV_SIMOBJECT_DATA,
)

from simconnect_mcp.vendor.simconnect_mobiflight import SimConnectMobiFlight

log = logging.getLogger(__name__)


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
    """

    request_id: int | None
    is_string: bool = False
    send_ids: list[int] = field(default_factory=list)
    value: Any = None
    exception: str | None = None
    done: threading.Event = field(default_factory=threading.Event)


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
        req.done.set()
        return True

    def resolve_exception(self, send_id: int, name: str) -> bool:
        with self.pending_lock:
            req = self._by_send.get(send_id)
        if req is None:
            return False
        req.exception = name
        req.done.set()
        return True

    def discard(self, req: PendingRequest) -> None:
        with self.pending_lock:
            if req.request_id is not None:
                self._by_request.pop(req.request_id, None)
                self._free_request_ids.append(req.request_id)
            for send_id in req.send_ids:
                self._by_send.pop(send_id, None)


class SimConnectDispatcher(SimConnectMobiFlight):
    """SimConnectMobiFlight that owns the whole dispatch loop."""

    def __init__(self, auto_connect: bool = True, library_path: str | None = None) -> None:
        # Must exist before super().__init__ starts the dispatch thread.
        self.registry = RequestRegistry()
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

        if dwID == SIMCONNECT_RECV_ID.SIMCONNECT_RECV_ID_SYSTEM_STATE:
            # SimConnect.handle_state_event print()s this straight to stdout.
            log.debug("SYSTEM_STATE message swallowed to protect the stdio stream")
            return

        if dwID in FACILITY_RECV_IDS:
            # The library's branch calls dump(), which print()s.  Phase 2
            # installs real handlers here.
            for handler in self.facility_handlers:
                handler(pData)
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
            raw = ctypes.cast(address, ctypes.c_char_p).value or b""
            value: Any = raw.decode("ascii", errors="replace").strip()
        else:
            value = ctypes.cast(address, ctypes.POINTER(ctypes.c_double)).contents.value
        self.registry.resolve_data(request_id, value)
