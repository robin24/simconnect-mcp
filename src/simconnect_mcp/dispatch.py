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

Exception correlation uses SIMCONNECT_RECV_EXCEPTION.dwSendID against the
value returned by GetLastSentPacketID at send time.  (SimConnect.py's own
handle_exception_event compares against the constant field UNKNOWN_SENDID
instead of dwSendID, which is why its correlation never matches.)
"""
from __future__ import annotations

import ctypes
import logging
import threading
from dataclasses import dataclass, field
from typing import Any

from SimConnect.Enum import (
    SIMCONNECT_EXCEPTION,
    SIMCONNECT_RECV_EXCEPTION,
    SIMCONNECT_RECV_ID,
    SIMCONNECT_RECV_SIMOBJECT_DATA,
)

from simconnect_mcp.vendor.simconnect_mobiflight import SimConnectMobiFlight

log = logging.getLogger(__name__)

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
    """

    request_id: int | None
    is_string: bool = False
    send_id: int | None = None
    value: Any = None
    exception: str | None = None
    done: threading.Event = field(default_factory=threading.Event)


class RequestRegistry:
    """Thread-safe index of in-flight requests, by request ID and send ID.

    `pending_lock` is public on purpose: the accessor holds it across the DLL
    send and the subsequent bind_send_id() so the dispatch thread cannot
    deliver an exception for a request whose send ID is not yet recorded.
    """

    def __init__(self) -> None:
        self.pending_lock = threading.Lock()
        self._by_request: dict[int, PendingRequest] = {}
        self._by_send: dict[int, PendingRequest] = {}

    def register(self, req: PendingRequest) -> None:
        with self.pending_lock:
            if req.request_id is not None:
                self._by_request[req.request_id] = req

    def bind_send_id(self, req: PendingRequest, send_id: int, _locked: bool = False) -> None:
        """Record the packet ID returned by GetLastSentPacketID.

        Pass _locked=True when pending_lock is already held by this thread.
        """
        if _locked:
            req.send_id = send_id
            self._by_send[send_id] = req
            return
        with self.pending_lock:
            req.send_id = send_id
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
            if req.send_id is not None:
                self._by_send.pop(req.send_id, None)


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
            exc = ctypes.cast(pData, ctypes.POINTER(SIMCONNECT_RECV_EXCEPTION)).contents
            try:
                name = SIMCONNECT_EXCEPTION(exc.dwException).name
            except ValueError:
                name = f"SIMCONNECT_EXCEPTION_{exc.dwException}"
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
        with self.registry.pending_lock:
            req = self.registry._by_request.get(request_id)
        if req is None:
            log.debug("SIMOBJECT_DATA for unknown request %s", request_id)
            return

        address = ctypes.addressof(data.dwData)
        if req.is_string:
            raw = ctypes.cast(address, ctypes.c_char_p).value or b""
            value: Any = raw.decode("ascii", errors="replace").rstrip("\x00").strip()
        else:
            value = ctypes.cast(address, ctypes.POINTER(ctypes.c_double)).contents.value
        self.registry.resolve_data(request_id, value)
