import threading
import time
from unittest.mock import MagicMock

import pytest

from simconnect_mcp.dispatch import RequestRegistry
from simconnect_mcp.simvar_access import (
    SimVarAccessor,
    SimVarNotFoundError,
    SimVarNotSettableError,
    SimVarTimeoutError,
    UnitMismatchError,
    simconnect_name,
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

    `respond_after=` overrides the fixed 0.01s delivery delay -- set it
    longer than a caller's `read(timeout=...)` to model a late arrival: the
    DLL answers only *after* the caller has already given up locally.
    Nothing in real SimConnect cancels an outstanding request just because
    the caller stopped waiting, so this is not a contrived edge case.
    """

    def __init__(
        self, value=None, exception=None, respond=True, definition_exception=None,
        respond_after: float = 0.01,
    ):
        self.registry = RequestRegistry()
        self.hSimConnect = 1
        self.dll = MagicMock()
        self._next_id = 100
        self._value = value
        self._exception = exception
        self._definition_exception = definition_exception
        self._respond = respond
        self._respond_after = respond_after
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
                self._respond_after,
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
        # Snapshot _value/_exception now, at send time -- not when the timer
        # fires. A real SimConnect response's payload is fixed by whatever
        # was true when the sim processed the request, not by whatever this
        # test object's attributes happen to hold later. Reading self._value
        # lazily inside deliver() would let a value change made *after* this
        # send (to set up a later, different request) silently retarget an
        # earlier request's already-scheduled delivery.
        scheduled_value = self._value
        scheduled_exception = self._exception
        def deliver():
            if scheduled_exception is not None:
                self.registry.resolve_exception(request_packet_id, scheduled_exception)
            else:
                self.registry.resolve_data(req_id, scheduled_value)
        threading.Timer(self._respond_after, deliver).start()

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


def test_two_hundred_sequential_reads_allocate_far_fewer_than_two_hundred_ids():
    """Finding 1: new_request_id() must not be called once per read -- it
    rebuilds an Enum from every prior member plus one, on every call, so a
    per-read allocation makes read cost grow with cumulative read count
    rather than with the number of distinct variables in flight. Each
    sequential read here discards its id (freeing it for reuse) before the
    next one starts, so after the very first read, new_request_id() should
    never be called again.

    Fails against the current code, which calls
    self._sm.new_request_id().value unconditionally on every read -- 200
    reads would allocate 200 fresh ids."""
    sm = FakeSM(value=1.0)
    accessor = SimVarAccessor(sm)
    allocate_calls = {"n": 0}
    real_new_request_id = sm.new_request_id

    def counting_new_request_id():
        allocate_calls["n"] += 1
        return real_new_request_id()

    sm.new_request_id = counting_new_request_id

    for _ in range(200):
        accessor.read("PLANE_ALTITUDE")

    assert allocate_calls["n"] == 1, (
        f"expected exactly one fresh request id allocation across 200 sequential "
        f"reads (every later read should reuse the freed id), got {allocate_calls['n']}"
    )


def test_late_response_after_a_timeout_does_not_corrupt_the_next_read():
    """The pool-recycling race, end to end through the real read() path.

    A's local timeout does not cancel the DLL request, so SimConnect can
    still deliver SIMOBJECT_DATA for A's id after read() already raised
    SimVarTimeoutError and discarded A. If discard() had recycled A's id
    (as the first version of the pool did), the very next read (B) would be
    handed that exact id, and A's late, stale delivery would resolve B with
    A's value instead of B's own -- silently returning the wrong SimVar's
    value with no error at all.

    Fails against the pre-fix code: B's read() returns 999.999 (A's stale
    value) instead of B's own 42.0.
    """
    sm = FakeSM(value=999.999, respond=True, respond_after=0.2)
    accessor = SimVarAccessor(sm)

    with pytest.raises(SimVarTimeoutError):
        accessor.read("PLANE_ALTITUDE", timeout=0.05)  # gives up well before 0.2s

    # A's real (stale) response is still in flight, timed to land ~0.2s
    # after A's own send. Change the value before B sends, so a delivery of
    # A's stale value to B is unambiguous rather than a coincidental match.
    sm._value = 42.0
    result = accessor.read("AIRSPEED_INDICATED", timeout=0.5)

    assert result == 42.0, (
        f"expected B's own value (42.0), got {result!r} -- looks like A's "
        "late, stale delivery landed on B instead of hitting the dead-letter branch"
    )


def test_non_ascii_unit_does_not_leak_the_pending_registry_entry():
    """Finding 2: register() used to run before the try/finally that
    discards it. unit.encode('ascii') -- fed straight from the caller, e.g.
    get_simvar('PLANE_ALTITUDE', unit='°') -- raises UnicodeEncodeError
    inside that unprotected window, so the registry entry survived forever
    instead of being discarded.

    Fails against the current code two ways: the wrong exception type
    escapes (UnicodeEncodeError, not UnitMismatchError) and, even ignoring
    that, the request id is never freed."""
    sm = FakeSM(value=1.0)
    accessor = SimVarAccessor(sm)
    captured = {}
    real_new_request_id = sm.new_request_id

    def spy():
        result = real_new_request_id()
        captured["req_id"] = result.value
        return result

    sm.new_request_id = spy

    with pytest.raises(UnitMismatchError):
        accessor.read("PLANE_ALTITUDE", unit="°")  # degree sign, not ASCII

    assert "req_id" in captured
    assert sm.registry.resolve_data(captured["req_id"], 999.0) is False


def test_non_ascii_name_raises_not_found_not_a_bare_unicode_error():
    """Same shape as the unit case, for the name instead: a non-ASCII name
    must surface as the typed SimVarNotFoundError the tool layer already
    knows how to report, not a raw UnicodeEncodeError that
    handle_simconnect_errors turns into an opaque UNEXPECTED envelope."""
    sm = FakeSM(value=1.0)
    with pytest.raises(SimVarNotFoundError):
        SimVarAccessor(sm).read("PLANE_ALTITUDE_°")


def test_read_request_id_and_send_id_are_not_leaked_if_request_data_raises():
    """Same leak shape as the non-ASCII case, triggered differently: an
    exception from the SECOND DLL call (RequestDataOnSimObject) used to
    leak both the request id and the send id bound by the FIRST call
    (AddToDataDefinition), because the try/finally that discards them did
    not open until after both calls had already been made."""
    sm = FakeSM(value=1.0)
    sm.dll.RequestDataOnSimObject.side_effect = RuntimeError("boom")
    accessor = SimVarAccessor(sm)
    captured = {}
    real_new_request_id = sm.new_request_id

    def spy():
        result = real_new_request_id()
        captured["req_id"] = result.value
        return result

    sm.new_request_id = spy

    with pytest.raises(RuntimeError):
        accessor.read("PLANE_ALTITUDE")

    definition_send_id = sm.packet_ids_issued[0]
    assert "req_id" in captured
    assert sm.registry.resolve_data(captured["req_id"], 1.0) is False
    assert sm.registry.resolve_exception(definition_send_id, "X") is False


def test_string_definitions_are_registered_with_a_null_unit():
    """STRING256 definitions must take a NULL unit. Passing "string" makes
    SimConnect raise NAME_UNRECOGNIZED -- verified against a live sim, where
    it made every string SimVar including TITLE unreadable."""
    sm = FakeSM(value="Boeing 747-8i")
    SimVarAccessor(sm).read("TITLE")
    _, name, unit, datatype = sm.definitions[0]
    assert name == b"TITLE"
    assert unit is None, f"STRING256 unit must be None, got {unit!r}"


def test_numeric_definitions_still_get_their_unit():
    sm = FakeSM(value=1.0)
    SimVarAccessor(sm).read("PLANE_ALTITUDE", unit="feet")
    assert sm.definitions[0][2] == b"feet"


def test_string_var_uses_string256_datatype():
    from SimConnect.Enum import SIMCONNECT_DATATYPE

    sm = FakeSM(value="Boeing 747-8i")
    SimVarAccessor(sm).read("TITLE")
    _, _, _, datatype = sm.definitions[0]
    assert datatype == SIMCONNECT_DATATYPE.SIMCONNECT_DATATYPE_STRING256


def test_string_var_returns_str_not_bytes():
    sm = FakeSM(value="Boeing 747-8i")
    result = SimVarAccessor(sm).read("TITLE")
    assert result == "Boeing 747-8i"
    assert isinstance(result, str)


def test_read_many_returns_a_result_per_variable():
    sm = FakeSM(value=42.0)
    results = SimVarAccessor(sm).read_many(
        [("PLANE_ALTITUDE", None, None), ("AIRSPEED_INDICATED", "knots", None)]
    )
    assert results["PLANE_ALTITUDE"]["value"] == 42.0
    assert results["AIRSPEED_INDICATED"]["value"] == 42.0


def test_read_many_isolates_a_failing_variable():
    """One bad name must not abort the whole batch."""
    sm = FakeSM(respond=False)
    results = SimVarAccessor(sm).read_many([("NOT_A_REAL_VAR", None, None)], timeout=0.05)
    assert "error" in results["NOT_A_REAL_VAR"]
    assert results["NOT_A_REAL_VAR"]["error_type"] == "SimVarTimeoutError"


def test_read_many_keys_indexed_vars_distinctly():
    sm = FakeSM(value=1.0)
    results = SimVarAccessor(sm).read_many(
        [("ENG_N1_RPM", None, 1), ("ENG_N1_RPM", None, 2)]
    )
    assert set(results) == {"ENG_N1_RPM:1", "ENG_N1_RPM:2"}


def test_read_many_timeout_is_a_total_budget_not_a_per_item_timeout():
    """Finding 3: read_many used to pass the full `timeout` to EVERY item
    independently, so a hung sim (every item timing out) held _sim_lock for
    len(requests) * timeout seconds -- and since get_simvar_bulk passes a
    caller-supplied list straight through, that made the lock-hold time
    unbounded from the caller's side. `timeout` is now a total budget for
    the whole batch.

    Fails against the current code: 5 items at a 0.15s per-item timeout
    take ~0.75s total; this asserts well under that, using real wall-clock
    waiting (FakeSM's timeout path genuinely blocks) rather than mocking
    time.monotonic, so the measurement reflects actual blocking behaviour."""
    sm = FakeSM(respond=False)
    requests = [(f"VAR_{i}", None, None) for i in range(5)]

    start = time.monotonic()
    results = SimVarAccessor(sm).read_many(requests, timeout=0.15)
    elapsed = time.monotonic() - start

    assert elapsed < 0.4, (
        f"expected the whole batch to respect one ~0.15s budget, took {elapsed:.2f}s "
        "-- looks like each item is still waiting the full timeout independently"
    )
    assert len(results) == 5
    for key in results:
        assert results[key]["error_type"] == "SimVarTimeoutError"


def test_read_many_reports_a_timeout_for_entries_past_the_deadline_without_calling_read():
    """Once the total deadline has passed, remaining entries must be
    reported as a timeout without even attempting SimConnect -- not just
    with a very short per-item timeout, which would still send the DLL
    calls and consume a request id and a definition slot for no benefit."""
    sm = FakeSM(value=1.0)
    accessor = SimVarAccessor(sm)
    real_read = accessor.read
    read_calls = []

    def spy_read(name, unit=None, index=None, timeout=2.0):
        read_calls.append(name)
        return real_read(name, unit=unit, index=index, timeout=timeout)

    accessor.read = spy_read

    requests = [("PLANE_ALTITUDE", None, None), ("AIRSPEED_INDICATED", None, None)]
    # A deadline already in the past when the loop reaches the second item.
    results = accessor.read_many(requests, timeout=-1)

    assert read_calls == [], "no item should be attempted once the deadline has passed"
    assert results["PLANE_ALTITUDE"]["error_type"] == "SimVarTimeoutError"
    assert results["AIRSPEED_INDICATED"]["error_type"] == "SimVarTimeoutError"


def test_simconnect_name_helper_is_index_zero_safe():
    assert simconnect_name("ENG_N1_RPM", 0) == b"ENG N1 RPM:0"
    assert simconnect_name("ENG_N1_RPM", None) == b"ENG N1 RPM"


class FakeWriteSM(FakeSM):
    """FakeSM whose SetDataOnSimObject optionally raises a SimConnect exception.

    SetDataOnSimObject gets its own distinct, incrementing packet ID -- just
    like AddToDataDefinition does in the base class -- and a write exception
    is correlated to that exact ID rather than a fixed placeholder. A fake
    that reused one ID for both sends, or resolved the exception against an
    ID nothing is bound to, would let a write() that binds only one of the
    two packets pass anyway: precisely the harness defect the previous task
    had to correct on the read side.

    `definition_exception=` (inherited from FakeSM) models an exception
    against the *definition* packet, exercised the same way it is for reads.
    """

    def __init__(self, exception=None, definition_exception=None):
        super().__init__(respond=False, definition_exception=definition_exception)
        self._write_exception = exception
        self.writes = []
        self.dll.SetDataOnSimObject.side_effect = self._on_write

    def _on_write(self, handle, def_id, obj, flags, count, size, data):
        self.writes.append((def_id, size))
        self._packet_id += 1
        self.packet_ids_issued.append(self._packet_id)
        if self._definition_exception is not None:
            return  # the definition already failed; no write exception follows
        if self._write_exception is not None:
            packet_id = self._packet_id
            threading.Timer(
                0.01, lambda: self.registry.resolve_exception(packet_id, self._write_exception)
            ).start()

    def set_readback(self, value: float) -> None:
        """Make the verifying read() that write(verify=True) issues return `value`.

        Reuses FakeSM's own RequestDataOnSimObject machinery (inherited,
        untouched here) -- it only delivers a response when `_respond` is
        True, which the base class sets to False for FakeWriteSM by default
        since a plain write never issues a read.
        """
        self._value = value
        self._respond = True


def test_write_sends_the_value_and_returns_none():
    sm = FakeWriteSM()
    assert SimVarAccessor(sm).write("AUTOPILOT_ALTITUDE_LOCK_VAR", 12000.0, grace=0.05) is None
    assert len(sm.writes) == 1


def test_write_to_non_settable_var_raises():
    """The bug this replaces: aq.set() returned False and the tool said ok."""
    sm = FakeWriteSM(exception="SIMCONNECT_EXCEPTION_DATA_ERROR")
    with pytest.raises(SimVarNotSettableError):
        SimVarAccessor(sm).write("AIRSPEED_INDICATED", 250.0, grace=0.2)


def test_write_to_unknown_var_raises_not_found():
    sm = FakeWriteSM(exception="SIMCONNECT_EXCEPTION_NAME_UNRECOGNIZED")
    with pytest.raises(SimVarNotFoundError):
        SimVarAccessor(sm).write("NOT_A_REAL_VAR", 1.0, grace=0.2)


def test_write_honours_index_zero():
    sm = FakeWriteSM()
    SimVarAccessor(sm).write("GENERAL_ENG_THROTTLE_LEVER_POSITION", 50.0, index=0, grace=0.05)
    assert sm.definitions[0][1].endswith(b":0")


def test_write_bad_name_on_add_to_data_definition_is_correlated():
    """Verified against a live sim: AddToDataDefinition raises
    NAME_UNRECOGNIZED for a bad variable name, correlated to its own packet,
    not to the SetDataOnSimObject that follows. Binding only the second
    packet would make bad-name writes silently time out instead of raising."""
    sm = FakeWriteSM(definition_exception="SIMCONNECT_EXCEPTION_NAME_UNRECOGNIZED")
    with pytest.raises(SimVarNotFoundError):
        SimVarAccessor(sm).write("NOT_A_REAL_VAR", 1.0, grace=0.2)


def test_write_non_ascii_unit_raises_unit_mismatch_not_a_bare_unicode_error():
    """Finding 2, write() side: same defect shape as read() -- a non-ASCII
    unit must surface as UnitMismatchError, not a raw UnicodeEncodeError."""
    sm = FakeWriteSM()
    with pytest.raises(UnitMismatchError):
        SimVarAccessor(sm).write("AUTOPILOT_ALTITUDE_LOCK_VAR", 1.0, unit="°")


def test_write_send_id_is_not_leaked_if_set_data_on_sim_object_raises():
    """Finding 2, write() side: write()'s try/finally used to open only
    after both DLL sends, so an exception from the SECOND call
    (SetDataOnSimObject) left the send id bound by the FIRST call
    (AddToDataDefinition) stuck in the registry forever."""
    sm = FakeWriteSM()
    sm.dll.SetDataOnSimObject.side_effect = RuntimeError("boom")
    accessor = SimVarAccessor(sm)

    with pytest.raises(RuntimeError):
        accessor.write("AUTOPILOT_ALTITUDE_LOCK_VAR", 1.0)

    definition_send_id = sm.packet_ids_issued[0]
    assert sm.registry.resolve_exception(definition_send_id, "X") is False


def test_failed_write_is_not_left_in_the_cache():
    """A poisoned cache entry would make every retry skip AddToDataDefinition,
    even though SimConnect just told us the write was rejected."""
    sm = FakeWriteSM(exception="SIMCONNECT_EXCEPTION_DATA_ERROR")
    accessor = SimVarAccessor(sm)
    for _ in range(2):
        with pytest.raises(SimVarNotSettableError):
            accessor.write("AIRSPEED_INDICATED", 250.0, grace=0.2)
    assert len(sm.definitions) == 2, "second attempt must re-send AddToDataDefinition"


def test_verify_returns_true_when_the_value_lands():
    sm = FakeWriteSM()
    sm.set_readback(42.0)
    assert SimVarAccessor(sm).write("AUTOPILOT_ALTITUDE_LOCK_VAR", 42.0, verify=True) is True


def test_verify_returns_false_when_the_write_was_ignored():
    """A read-only variable: SimConnect raises nothing, the value just does
    not change. Read-back is the only way to detect it."""
    sm = FakeWriteSM()
    sm.set_readback(0.0)
    assert SimVarAccessor(sm).write("AIRSPEED_TRUE", 250.0, verify=True) is False


def test_verify_false_by_default_returns_none():
    sm = FakeWriteSM()
    assert SimVarAccessor(sm).write("AUTOPILOT_ALTITUDE_LOCK_VAR", 1.0) is None


def test_verify_tolerates_float_round_trip_error():
    sm = FakeWriteSM()
    sm.set_readback(12000.000000001)
    assert SimVarAccessor(sm).write("AUTOPILOT_ALTITUDE_LOCK_VAR", 12000.0, verify=True) is True


def test_verify_does_not_raise_on_mismatch():
    """Reporting, not judging -- the sim overriding a value is legitimate."""
    sm = FakeWriteSM()
    sm.set_readback(0.0)
    SimVarAccessor(sm).write("PLANE_ALTITUDE", 9999.0, verify=True)  # must not raise


def test_name_errors_still_raise_even_with_verify():
    sm = FakeWriteSM(definition_exception="SIMCONNECT_EXCEPTION_NAME_UNRECOGNIZED")
    with pytest.raises(SimVarNotFoundError):
        SimVarAccessor(sm).write("NOT_A_REAL_VAR", 1.0, verify=True, grace=0.3)
