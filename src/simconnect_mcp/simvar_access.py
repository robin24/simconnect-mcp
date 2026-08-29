"""Generic SimVar access via SimConnect data definitions.

Replaces SimConnect.AircraftRequests, which holds a hardcoded table of 828
variables each bound to one fixed unit, and signals failure by returning
None/False.  That design makes four things impossible: honouring a requested
unit, reading variables outside the table, reading string variables, and
telling a failed write from a successful one.

This layer builds definitions directly -- the same native pattern already
used by SimConnectManager.set_lvar -- so all four work.
"""
from __future__ import annotations

import ctypes
import logging
from collections import OrderedDict
from ctypes.wintypes import DWORD

from SimConnect.Constants import SIMCONNECT_OBJECT_ID_USER, SIMCONNECT_UNUSED
from SimConnect.Enum import (
    SIMCONNECT_DATA_SET_FLAG,
    SIMCONNECT_DATATYPE,
    SIMCONNECT_PERIOD,
)

from simconnect_mcp.data.simvar_catalog import is_string_var, resolve_unit
from simconnect_mcp.dispatch import PendingRequest

log = logging.getLogger(__name__)

STRING_SIZE = 256
DEFINITION_CACHE_SIZE = 256
DEFAULT_TIMEOUT = 2.0


class SimVarError(Exception):
    """Base class for SimVar access failures."""


class SimVarNotFoundError(SimVarError):
    """SimConnect did not recognise the variable name."""


class SimVarNotSettableError(SimVarError):
    """The variable exists but cannot be written."""


class UnitMismatchError(SimVarError):
    """The requested unit is not valid for this variable."""


class SimVarTimeoutError(SimVarError):
    """No data and no exception arrived before the timeout."""


# SimConnect reports the same exception codes for different underlying
# causes depending on the operation.  A bad unit on a read and a write to a
# read-only variable both surface as DATA_ERROR, so the mapping is chosen by
# operation rather than from one global table.  (There is no
# SIMCONNECT_EXCEPTION_ILLEGAL_OPERATION -- verify any code you add against
# SimConnect.Enum.SIMCONNECT_EXCEPTION before using it.)
_NOT_FOUND = frozenset({
    "SIMCONNECT_EXCEPTION_NAME_UNRECOGNIZED",
    # RequestDataOnSimObject reports this when its definition was never
    # successfully built -- in practice, because the variable name was bad
    # and AddToDataDefinition already raised NAME_UNRECOGNIZED for it. Once
    # both packets are bound, either exception can arrive first, so both
    # must map to the same typed error for the result to be deterministic.
    "SIMCONNECT_EXCEPTION_UNRECOGNIZED_ID",
})

# Ambiguous codes: on a read these mean the unit or datatype was wrong; on a
# write they usually mean the variable is not settable.
_DATA_CODES = frozenset({
    "SIMCONNECT_EXCEPTION_DATA_ERROR",
    "SIMCONNECT_EXCEPTION_INVALID_DATA_TYPE",
    "SIMCONNECT_EXCEPTION_INVALID_DATA_SIZE",
    "SIMCONNECT_EXCEPTION_DEFINITION_ERROR",
    "SIMCONNECT_EXCEPTION_OUT_OF_BOUNDS",
})


def simconnect_name(name: str, index: int | None = None) -> bytes:
    """Convert an MCP-style SimVar name to the form SimConnect expects.

    'PLANE_ALTITUDE'      -> b'PLANE ALTITUDE'
    'ENG_N1_RPM', index=2 -> b'ENG N1 RPM:2'

    `index` is compared against None, not truthiness: index 0 is valid.
    """
    base = name.strip().upper().split(":", 1)[0].replace("_", " ")
    if index is not None:
        base = f"{base}:{index}"
    return base.encode("ascii")


class SimVarAccessor:
    """Reads and writes SimVars through SimConnect data definitions."""

    def __init__(self, sm) -> None:
        self._sm = sm
        self._definitions: OrderedDict[tuple[str, str, int | None, bool], int] = OrderedDict()

    @staticmethod
    def _cache_key(
        name: str, unit: str, index: int | None, is_string: bool
    ) -> tuple[str, str, int | None, bool]:
        return (name.strip().upper(), unit, index, is_string)

    def definition_id(
        self, name: str, unit: str, index: int | None, is_string: bool
    ) -> tuple[int, int | None]:
        """Definition ID for this (name, unit, index), creating it once.

        Returns (definition_id, send_id). send_id is the packet ID of the
        AddToDataDefinition call when one was actually issued (cache miss);
        it is None on a cache hit. AddToDataDefinition can itself raise
        NAME_UNRECOGNIZED for a bad variable name -- or a DATA_ERROR-style
        code for a bad unit -- and that exception correlates to its own
        packet, not to the RequestDataOnSimObject that follows, so callers
        must bind this send_id too or the exception is never matched.

        Definition IDs are a finite SimConnect resource and new_def_id()
        rebuilds an Enum on every call, so they must not be created per read.
        Eviction drops only our mapping; SimConnect definitions are not
        reclaimable, so the bound caps growth rather than recycling IDs.
        """
        key = self._cache_key(name, unit, index, is_string)
        cached = self._definitions.get(key)
        if cached is not None:
            self._definitions.move_to_end(key)
            return cached, None

        def_id = self._sm.new_def_id().value
        datatype = (
            SIMCONNECT_DATATYPE.SIMCONNECT_DATATYPE_STRING256
            if is_string
            else SIMCONNECT_DATATYPE.SIMCONNECT_DATATYPE_FLOAT64
        )
        self._sm.dll.AddToDataDefinition(
            self._sm.hSimConnect,
            def_id,
            simconnect_name(name, index),
            unit.encode("ascii"),
            datatype,
            ctypes.c_float(0.0),
            SIMCONNECT_UNUSED,
        )
        send_id = self._last_packet_id()
        self._definitions[key] = def_id
        if len(self._definitions) > DEFINITION_CACHE_SIZE:
            self._definitions.popitem(last=False)
        return def_id, send_id

    def _evict(self, key: tuple[str, str, int | None, bool]) -> None:
        """Drop a cached definition after SimConnect rejects it.

        Without this, a definition that failed (bad name, bad unit) stays
        cached and every retry silently reuses the same broken definition
        instead of re-registering -- so a fixed unit or a renamed variable
        could never succeed on retry.
        """
        self._definitions.pop(key, None)

    def _last_packet_id(self) -> int:
        """Packet ID of the call just sent, for exception correlation."""
        out = DWORD(0)
        self._sm.dll.GetLastSentPacketID(self._sm.hSimConnect, out)
        return out.value

    def _raise_for(
        self, exception_name: str, name: str, unit: str, writing: bool = False
    ) -> None:
        """Translate a SimConnect exception into a typed error."""
        if exception_name in _NOT_FOUND:
            raise SimVarNotFoundError(f"SimConnect does not recognise SimVar '{name}'")
        if exception_name in _DATA_CODES:
            if writing:
                raise SimVarNotSettableError(
                    f"SimConnect rejected the write to '{name}'. It is most likely "
                    f"read-only; the unit '{unit}' or the value may also be invalid "
                    f"({exception_name})."
                )
            raise UnitMismatchError(
                f"Unit '{unit}' is not valid for SimVar '{name}' ({exception_name})"
            )
        raise SimVarError(f"SimConnect rejected '{name}': {exception_name}")

    def read(
        self,
        name: str,
        unit: str | None = None,
        index: int | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        """Read one SimVar. Returns float, or str for string variables.

        Raises SimVarNotFoundError, UnitMismatchError, SimVarTimeoutError.
        """
        as_string = is_string_var(name)
        resolved_unit = "string" if as_string else resolve_unit(name, unit)
        key = self._cache_key(name, resolved_unit, index, as_string)

        req_id = self._sm.new_request_id().value
        pending = PendingRequest(request_id=req_id, is_string=as_string)
        self._sm.registry.register(pending)

        # The lock spans BOTH sends: on a cache miss, AddToDataDefinition can
        # itself raise NAME_UNRECOGNIZED (bad variable) or a DATA_ERROR-style
        # code (bad unit), and that exception correlates to its own packet,
        # not to the RequestDataOnSimObject that follows. Binding only the
        # request's send ID would leave a definition-level exception
        # unmatched, and the caller would see a timeout instead of the
        # correct typed error.
        with self._sm.registry.pending_lock:
            def_id, def_send_id = self.definition_id(name, resolved_unit, index, as_string)
            if def_send_id is not None:
                self._sm.registry.bind_send_id(pending, def_send_id, _locked=True)

            self._sm.dll.RequestDataOnSimObject(
                self._sm.hSimConnect,
                req_id,
                def_id,
                SIMCONNECT_OBJECT_ID_USER,
                SIMCONNECT_PERIOD.SIMCONNECT_PERIOD_ONCE,
                0,
                0,
                0,
                0,
            )
            self._sm.registry.bind_send_id(pending, self._last_packet_id(), _locked=True)

        try:
            if not pending.done.wait(timeout):
                raise SimVarTimeoutError(
                    f"No response for SimVar '{name}' within {timeout}s. "
                    "The sim may be paused, loading, or not running."
                )
            if pending.exception is not None:
                # A definition that SimConnect just rejected must not
                # survive in the cache, or every retry reuses the same
                # broken definition and can never succeed.
                self._evict(key)
                self._raise_for(pending.exception, name, resolved_unit)
            return pending.value
        finally:
            self._sm.registry.discard(pending)

    def write(
        self,
        name: str,
        value: float,
        unit: str | None = None,
        index: int | None = None,
        grace: float = 0.15,
    ) -> None:
        """Write one numeric SimVar.

        SimConnect sends no acknowledgement for a successful write, so success
        is defined as "no exception arrived within `grace`".  This is the only
        way to distinguish a real write from a rejected one -- AircraftRequests
        simply returned False and callers ignored it.
        """
        resolved_unit = resolve_unit(name, unit)
        key = self._cache_key(name, resolved_unit, index, False)

        pending = PendingRequest(request_id=None)
        self._sm.registry.register(pending)

        payload = (ctypes.c_double * 1)(float(value))

        # The lock spans BOTH sends, exactly as in read(): AddToDataDefinition
        # can itself raise NAME_UNRECOGNIZED for a bad variable, correlated to
        # its own packet rather than the SetDataOnSimObject that follows.
        # `definition_id` returns (def_id, send_id_or_None) -- None on a cache hit.
        with self._sm.registry.pending_lock:
            def_id, def_send_id = self.definition_id(name, resolved_unit, index, False)
            if def_send_id is not None:
                self._sm.registry.bind_send_id(pending, def_send_id, _locked=True)
            self._sm.dll.SetDataOnSimObject(
                self._sm.hSimConnect,
                def_id,
                SIMCONNECT_OBJECT_ID_USER,
                SIMCONNECT_DATA_SET_FLAG.SIMCONNECT_DATA_SET_FLAG_DEFAULT,
                0,
                ctypes.sizeof(ctypes.c_double),
                ctypes.cast(payload, ctypes.c_void_p),
            )
            self._sm.registry.bind_send_id(pending, self._last_packet_id(), _locked=True)

        try:
            # An exception, if any, arrives within one dispatch round trip.
            if pending.done.wait(grace) and pending.exception is not None:
                self._evict(key)
                self._raise_for(pending.exception, name, resolved_unit, writing=True)
        finally:
            self._sm.registry.discard(pending)
