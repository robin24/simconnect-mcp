import threading
from unittest.mock import MagicMock

import pytest

from simconnect_mcp.dispatch import RequestRegistry
from simconnect_mcp.simvar_access import (
    SimVarAccessor,
    SimVarNotFoundError,
    SimVarTimeoutError,
    UnitMismatchError,
)


class FakeSM:
    """Minimal stand-in for SimConnectDispatcher.

    Resolves each read on a background thread, the way the real dispatch
    thread would, so the accessor's blocking wait is exercised.

    Packet IDs increment on every simulated "send" (AddToDataDefinition as
    well as RequestDataOnSimObject), the way real SimConnect packet IDs do.
    A fake that returned the same fixed ID for every send (as this harness
    originally did) cannot distinguish the two calls, which hid a real bug:
    an exception from AddToDataDefinition was never bound and so degraded
    to a timeout instead of the correct typed error.

    `exception=` models an exception delivered against the *request*
    packet (RequestDataOnSimObject); `definition_exception=` models one
    against the *definition* packet (AddToDataDefinition) -- the case that
    was previously untested. The two are mutually exclusive in practice
    (a request built on a definition SimConnect just rejected does not also
    get a real response), so when `definition_exception` is set,
    `_respond_async` does not schedule a competing delivery.
    """

    def __init__(self, value=None, exception=None, respond=True, definition_exception=None):
        self.registry = RequestRegistry()
        self.hSimConnect = 1
        self.dll = MagicMock()
        self._next_id = 100
        self._value = value
        self._exception = exception
        self._definition_exception = definition_exception
        self._respond = respond
        self.definitions = []
        self._packet_id = 500
        self.packet_ids_issued = []
        self.dll.AddToDataDefinition.side_effect = self._record_definition
        self.dll.RequestDataOnSimObject.side_effect = self._respond_async
        self.dll.GetLastSentPacketID.side_effect = self._set_packet_id

    def _record_definition(self, handle, def_id, name, unit, datatype, epsilon, datum):
        self.definitions.append((def_id, name, unit, datatype))
        self._packet_id += 1
        self.packet_ids_issued.append(self._packet_id)
        if self._definition_exception is not None:
            packet_id = self._packet_id
            threading.Timer(
                0.01,
                lambda: self.registry.resolve_exception(packet_id, self._definition_exception),
            ).start()

    def _set_packet_id(self, handle, out):
        out.value = self._packet_id

    def _respond_async(self, handle, req_id, def_id, obj, period, flags, origin, interval, limit):
        self._packet_id += 1
        self.packet_ids_issued.append(self._packet_id)
        if self._definition_exception is not None:
            return  # the definition already failed; no request response follows
        if not self._respond:
            return
        request_packet_id = self._packet_id
        def deliver():
            if self._exception is not None:
                self.registry.resolve_exception(request_packet_id, self._exception)
            else:
                self.registry.resolve_data(req_id, self._value)
        threading.Timer(0.01, deliver).start()

    def new_def_id(self):
        self._next_id += 1
        return MagicMock(value=self._next_id)

    def new_request_id(self):
        self._next_id += 1
        return MagicMock(value=self._next_id)


def test_read_returns_the_dispatched_value():
    sm = FakeSM(value=35000.0)
    accessor = SimVarAccessor(sm)
    assert accessor.read("PLANE_ALTITUDE") == 35000.0


def test_read_passes_the_explicit_unit_to_the_data_definition():
    """The whole point of the new layer: the requested unit reaches SimConnect."""
    sm = FakeSM(value=10668.0)
    accessor = SimVarAccessor(sm)
    accessor.read("PLANE_ALTITUDE", unit="meters")

    _, name, unit, _ = sm.definitions[0]
    assert name == b"PLANE ALTITUDE"
    assert unit == b"meters"


def test_read_converts_underscores_to_spaces_for_simconnect():
    sm = FakeSM(value=1.0)
    SimVarAccessor(sm).read("AIRSPEED_INDICATED")
    assert sm.definitions[0][1] == b"AIRSPEED INDICATED"


def test_read_appends_index_to_the_variable_name():
    sm = FakeSM(value=1.0)
    SimVarAccessor(sm).read("ENG_N1_RPM", index=2)
    assert sm.definitions[0][1] == b"ENG N1 RPM:2"


def test_index_zero_is_honoured_not_dropped():
    sm = FakeSM(value=1.0)
    SimVarAccessor(sm).read("GENERAL_ENG_THROTTLE_LEVER_POSITION", index=0)
    assert sm.definitions[0][1].endswith(b":0")


def test_definitions_are_cached_per_name_unit_index():
    sm = FakeSM(value=1.0)
    accessor = SimVarAccessor(sm)
    accessor.read("PLANE_ALTITUDE", unit="feet")
    accessor.read("PLANE_ALTITUDE", unit="feet")
    assert len(sm.definitions) == 1, "definition IDs are a finite resource"


def test_different_units_get_different_definitions():
    sm = FakeSM(value=1.0)
    accessor = SimVarAccessor(sm)
    accessor.read("PLANE_ALTITUDE", unit="feet")
    accessor.read("PLANE_ALTITUDE", unit="meters")
    assert len(sm.definitions) == 2


def test_unrecognised_name_exception_raises_not_found():
    sm = FakeSM(exception="SIMCONNECT_EXCEPTION_NAME_UNRECOGNIZED")
    with pytest.raises(SimVarNotFoundError) as excinfo:
        SimVarAccessor(sm).read("NOT_A_REAL_VAR")
    assert "NOT_A_REAL_VAR" in str(excinfo.value)


def test_silent_sim_raises_timeout():
    sm = FakeSM(respond=False)
    with pytest.raises(SimVarTimeoutError):
        SimVarAccessor(sm).read("PLANE_ALTITUDE", timeout=0.05)


def test_data_error_exception_raises_unit_mismatch_on_read():
    """DATA_ERROR is ambiguous; on a read it means the unit doesn't apply."""
    sm = FakeSM(exception="SIMCONNECT_EXCEPTION_DATA_ERROR")
    with pytest.raises(UnitMismatchError):
        SimVarAccessor(sm).read("PLANE_ALTITUDE", unit="bogus_unit")


def test_bad_name_exception_on_add_to_data_definition_is_correlated():
    """SimConnect raises NAME_UNRECOGNIZED from AddToDataDefinition, not from
    RequestDataOnSimObject. Binding only the request packet made this a timeout."""
    sm = FakeSM(definition_exception="SIMCONNECT_EXCEPTION_NAME_UNRECOGNIZED")
    with pytest.raises(SimVarNotFoundError):
        SimVarAccessor(sm).read("NOT_A_REAL_VAR", timeout=1.0)


def test_unrecognised_id_on_the_request_also_raises_not_found():
    """Live behaviour: a bad name makes AddToDataDefinition raise
    NAME_UNRECOGNIZED and the following request raise UNRECOGNIZED_ID.
    Whichever arrives first must produce the same typed error."""
    sm = FakeSM(exception="SIMCONNECT_EXCEPTION_UNRECOGNIZED_ID")
    with pytest.raises(SimVarNotFoundError):
        SimVarAccessor(sm).read("NOT_A_REAL_VAR", timeout=1.0)


def test_definition_and_request_packets_get_distinct_send_ids():
    """Guards the harness itself: if the fake returned one id for every send,
    the test above would pass even with the defect present."""
    sm = FakeSM(value=1.0)
    SimVarAccessor(sm).read("PLANE_ALTITUDE")
    assert len(set(sm.packet_ids_issued)) == len(sm.packet_ids_issued) >= 2


def test_failed_definition_is_not_left_in_the_cache():
    """A poisoned cache entry would make every retry skip AddToDataDefinition."""
    sm = FakeSM(definition_exception="SIMCONNECT_EXCEPTION_NAME_UNRECOGNIZED")
    accessor = SimVarAccessor(sm)
    for _ in range(2):
        with pytest.raises(SimVarNotFoundError):
            accessor.read("NOT_A_REAL_VAR", timeout=1.0)
    assert len(sm.definitions) == 2, "second attempt must re-send AddToDataDefinition"


def test_cache_hit_binds_only_the_request_packet():
    sm = FakeSM(value=1.0)
    accessor = SimVarAccessor(sm)
    accessor.read("PLANE_ALTITUDE")
    before = len(sm.definitions)
    accessor.read("PLANE_ALTITUDE")
    assert len(sm.definitions) == before, "cache hit must not re-send the definition"


def test_read_discards_the_pending_request_after_success():
    """No leaked registry entries after a normal read -- otherwise the
    registry grows unbounded over a long-running server session."""
    sm = FakeSM(value=1.0)
    accessor = SimVarAccessor(sm)
    captured = {}
    real_new_request_id = sm.new_request_id

    def spy():
        result = real_new_request_id()
        captured["req_id"] = result.value
        return result

    sm.new_request_id = spy
    accessor.read("PLANE_ALTITUDE")

    assert "req_id" in captured
    assert sm.registry.resolve_data(captured["req_id"], 999.0) is False
