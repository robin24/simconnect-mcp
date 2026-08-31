import ctypes
import logging
import struct
import threading
from ctypes.wintypes import DWORD

from SimConnect.Enum import (
    SIMCONNECT_RECV_ASSIGNED_OBJECT_ID,
    SIMCONNECT_RECV_FACILITIES_LIST,
    SIMCONNECT_RECV_ID,
)

from simconnect_mcp.dispatch import (
    STRING256_SIZE,
    PendingRequest,
    RecvException,
    RequestRegistry,
    SimConnectDispatcher,
    _MobiFlightCrossTalkFilter,
)
from simconnect_mcp.facilities import FacilityCollector, FacilityKind


def test_resolve_data_sets_value_and_signals_waiter():
    registry = RequestRegistry()
    req = PendingRequest(request_id=7)
    registry.register(req)

    assert registry.resolve_data(7, 1234.5) is True
    assert req.done.wait(0.1)
    assert req.value == 1234.5
    assert req.exception is None


def test_resolve_data_for_unknown_request_is_ignored():
    registry = RequestRegistry()
    assert registry.resolve_data(999, 1.0) is False


def test_resolve_exception_matches_on_send_id():
    registry = RequestRegistry()
    req = PendingRequest(request_id=3)
    registry.register(req)
    registry.bind_send_id(req, send_id=42)

    assert registry.resolve_exception(42, "SIMCONNECT_EXCEPTION_NAME_UNRECOGNIZED") is True
    assert req.done.wait(0.1)
    assert req.exception == "SIMCONNECT_EXCEPTION_NAME_UNRECOGNIZED"
    assert req.value is None


def test_resolve_exception_for_unrelated_send_id_is_ignored():
    registry = RequestRegistry()
    req = PendingRequest(request_id=3)
    registry.register(req)
    registry.bind_send_id(req, send_id=42)

    assert registry.resolve_exception(99, "SIMCONNECT_EXCEPTION_UNRECOGNIZED_ID") is False
    assert not req.done.is_set()


def test_discard_removes_both_index_entries():
    registry = RequestRegistry()
    req = PendingRequest(request_id=5)
    registry.register(req)
    registry.bind_send_id(req, send_id=11)
    registry.discard(req)

    assert registry.resolve_data(5, 1.0) is False
    assert registry.resolve_exception(11, "X") is False


def test_discard_removes_every_bound_send_id():
    """A read binds two send IDs (AddToDataDefinition and
    RequestDataOnSimObject); discard must not leak either one."""
    registry = RequestRegistry()
    req = PendingRequest(request_id=5)
    registry.register(req)
    registry.bind_send_id(req, send_id=11)
    registry.bind_send_id(req, send_id=12)
    registry.discard(req)

    assert registry.resolve_exception(11, "X") is False
    assert registry.resolve_exception(12, "X") is False


def test_acquire_request_id_allocates_fresh_when_the_pool_is_empty():
    """Finding 1: fails against current code because acquire_request_id does
    not exist yet -- read() called self._sm.new_request_id() directly."""
    registry = RequestRegistry()
    calls = []

    def allocate():
        calls.append(1)
        return 100 + len(calls)

    id1 = registry.acquire_request_id(allocate)
    id2 = registry.acquire_request_id(allocate)

    assert id1 != id2
    assert len(calls) == 2, "allocate must be called once per fresh id"


def test_acquire_request_id_reuses_an_id_a_discard_just_freed():
    """The whole point of the pool: a freed id is handed back out before the
    (expensive) allocator is ever called again -- but only once the request
    is actually resolved (see the race-condition tests below for why a
    timed-out request's id must NOT be recycled this way)."""
    registry = RequestRegistry()
    calls = []

    def allocate():
        calls.append(1)
        return 100 + len(calls)

    first_id = registry.acquire_request_id(allocate)
    req = PendingRequest(request_id=first_id)
    registry.register(req)
    registry.resolve_data(first_id, 1.0)  # the sim answered -- safe to recycle
    registry.discard(req)

    second_id = registry.acquire_request_id(allocate)

    assert second_id == first_id, "must reuse the freed id rather than allocating fresh"
    assert len(calls) == 1, "allocate must not be called again while the pool has an entry"


def test_acquire_request_id_does_not_reuse_an_id_still_in_flight():
    """An id must only enter the free-list via discard() -- never handed
    out again while its owning request is still pending."""
    registry = RequestRegistry()
    calls = []

    def allocate():
        calls.append(1)
        return 100 + len(calls)

    first_id = registry.acquire_request_id(allocate)
    registry.register(PendingRequest(request_id=first_id))  # still pending, not discarded

    second_id = registry.acquire_request_id(allocate)

    assert second_id != first_id
    assert len(calls) == 2


def test_concurrent_acquire_never_hands_out_the_same_freed_id():
    """Two threads racing to acquire when exactly one id is free must not
    both receive it: acquire_request_id runs under pending_lock end to end,
    so only one of them may reuse it -- the other must allocate fresh."""
    registry = RequestRegistry()
    freed_req = PendingRequest(request_id=42)
    registry.register(freed_req)
    registry.resolve_data(42, 1.0)  # the sim answered -- safe to recycle
    registry.discard(freed_req)  # frees id 42

    alloc_lock = threading.Lock()
    allocate_calls = []

    def allocate():
        with alloc_lock:
            allocate_calls.append(1)
            return 1000 + len(allocate_calls)

    results = []
    results_lock = threading.Lock()
    barrier = threading.Barrier(2)

    def worker():
        barrier.wait(timeout=1)
        rid = registry.acquire_request_id(allocate)
        with results_lock:
            results.append(rid)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=2)

    assert len(results) == 2
    assert len(set(results)) == 2, "two concurrent acquires must not share an id"
    assert 42 in results, "the freed id must be handed out to exactly one caller"


def test_discard_without_a_request_id_does_not_touch_the_free_list():
    """Writes register with request_id=None; discard must not add None to
    the pool (acquire_request_id would then hand back None as a "fresh" id
    to a later read)."""
    registry = RequestRegistry()
    req = PendingRequest(request_id=None)
    registry.register(req)
    registry.bind_send_id(req, send_id=5)
    registry.discard(req)

    assert registry.acquire_request_id(lambda: 999) == 999


def test_a_timed_out_request_does_not_return_its_id_to_the_pool():
    """A local timeout does not cancel the DLL request, so the sim may still
    deliver for that id. Recycling it lets that late response land on
    whoever holds the id next -- a real race, reproduced end to end below.
    Fails against the pre-fix code, which recycled on every discard()
    regardless of whether resolve_data()/resolve_exception() ever ran."""
    registry = RequestRegistry()
    allocated = []

    def allocate():
        allocated.append(len(allocated) + 1)
        return allocated[-1]

    first = registry.acquire_request_id(allocate)
    req = PendingRequest(request_id=first)
    registry.register(req)
    registry.discard(req)  # timed out: never resolved

    second = registry.acquire_request_id(allocate)
    assert second != first, "a timed-out id must not be recycled"


def test_a_resolved_request_does_return_its_id_to_the_pool():
    registry = RequestRegistry()
    counter = iter(range(1, 100))

    def allocate():
        return next(counter)

    first = registry.acquire_request_id(allocate)
    req = PendingRequest(request_id=first)
    registry.register(req)
    registry.resolve_data(first, 42.0)  # the sim answered
    registry.discard(req)

    assert registry.acquire_request_id(allocate) == first


def test_an_exception_resolved_request_returns_its_id():
    """A rejected request gets no data, so its id is safe to reuse."""
    registry = RequestRegistry()
    counter = iter(range(1, 100))

    def allocate():
        return next(counter)

    first = registry.acquire_request_id(allocate)
    req = PendingRequest(request_id=first)
    registry.register(req)
    registry.bind_send_id(req, send_id=7)
    registry.resolve_exception(7, "SIMCONNECT_EXCEPTION_NAME_UNRECOGNIZED")
    registry.discard(req)

    assert registry.acquire_request_id(allocate) == first


def test_discard_recycle_false_never_returns_a_resolved_id_to_the_pool():
    """tools/flight.py's create_ai_object correlates on a request id that
    SimConnectManager.reserved_request_id() has permanently reserved for the
    "ai_object" key and rotates through for the rest of the connection's
    life -- unlike SimVarAccessor's per-call ids, which this free-list
    exists to recycle. Letting a resolved ai_object request recycle its id
    here anyway would let some unrelated SimVarAccessor read claim that
    exact number via acquire_request_id() while "ai_object" is still
    handing out the very same number on its own next call. recycle=False is
    how a caller in that situation opts out, regardless of `resolved`."""
    registry = RequestRegistry()
    counter = iter(range(1, 100))

    def allocate():
        return next(counter)

    reserved = registry.acquire_request_id(allocate)  # stands in for reserved_request_id
    req = PendingRequest(request_id=reserved)
    registry.register(req)
    registry.resolve_data(reserved, 42.0)  # the sim answered -- would normally recycle
    registry.discard(req, recycle=False)

    assert registry.acquire_request_id(allocate) != reserved


def test_late_delivery_after_a_timeout_cannot_reach_a_later_request():
    """The end-to-end race, as reproduced against real code: A times out, B
    acquires next, a late response for A's id must not land on B."""
    registry = RequestRegistry()
    counter = iter(range(1, 100))

    def allocate():
        return next(counter)

    id_a = registry.acquire_request_id(allocate)
    a = PendingRequest(request_id=id_a)
    registry.register(a)
    registry.discard(a)  # timeout

    id_b = registry.acquire_request_id(allocate)
    b = PendingRequest(request_id=id_b)
    registry.register(b)

    registry.resolve_data(id_a, 999.999)  # the sim finally answers A
    assert b.value is None, "B must not receive A's data"
    assert not b.done.is_set()


def test_write_request_has_no_request_id_but_still_matches_exceptions():
    """Writes get no SIMOBJECT_DATA reply, so they are matched by send_id only."""
    registry = RequestRegistry()
    req = PendingRequest(request_id=None)
    registry.register(req)
    registry.bind_send_id(req, send_id=77)

    assert registry.resolve_exception(77, "SIMCONNECT_EXCEPTION_UNRECOGNIZED_ID") is True
    assert req.exception == "SIMCONNECT_EXCEPTION_UNRECOGNIZED_ID"


def test_registry_lock_is_reentrant_safe_across_threads():
    """The accessor holds pending_lock across send + bind_send_id so the
    dispatch thread cannot deliver an exception before the send_id is known."""
    registry = RequestRegistry()
    req = PendingRequest(request_id=1)
    registry.register(req)
    seen = []

    with registry.pending_lock:
        t = threading.Thread(
            target=lambda: seen.append(registry.resolve_exception(50, "BOOM"))
        )
        t.start()
        t.join(0.2)
        assert seen == [], "dispatch thread must block until send_id is bound"
        registry.bind_send_id(req, send_id=50, _locked=True)

    t.join(1.0)
    assert seen == [True]


def test_recv_exception_struct_matches_the_sdk_wire_layout():
    """The installed package's binding has two static constants wrongly
    inside _fields_, shifting dwSendID by one slot. Ours must match the SDK:
    three DWORDs after the 12-byte SIMCONNECT_RECV header."""
    assert ctypes.sizeof(RecvException) == 24
    assert RecvException.dwException.offset == 12
    assert RecvException.dwSendID.offset == 16
    assert RecvException.dwIndex.offset == 20


class _FakeSimObjectData(ctypes.Structure):
    """Just enough of SIMCONNECT_RECV_SIMOBJECT_DATA for _on_simobject_data:
    a request id and a data buffer. dwData is DWORD-typed here, exactly as
    in the real struct (and unlike a c_char array, a DWORD array field
    stays a real ctypes object on attribute access, so ctypes.addressof()
    works on it the same way it does on the genuine SIMCONNECT_RECV_
    SIMOBJECT_DATA.dwData). The real buffer is DWORD * 8192 (32KB); 128
    DWORDs (512 bytes) is plenty to exercise the STRING256 boundary safely,
    entirely within memory we allocated ourselves."""

    _fields_ = [
        ("dwRequestID", DWORD),
        ("dwData", DWORD * 128),
    ]


def _make_simobject_data(request_id: int, payload: bytes) -> _FakeSimObjectData:
    """Build a _FakeSimObjectData with `payload` copied into dwData's memory."""
    data = _FakeSimObjectData(dwRequestID=request_id)
    ctypes.memmove(data.dwData, payload, len(payload))
    return data


def _bare_dispatcher() -> SimConnectDispatcher:
    """A SimConnectDispatcher with none of __init__'s work done.

    __init__ connects to the real SimConnect DLL, which needs neither exist
    nor matter for _on_simobject_data -- it only ever touches self.registry.
    object.__new__ allocates the instance without running __init__.
    """
    dispatcher = object.__new__(SimConnectDispatcher)
    dispatcher.registry = RequestRegistry()
    dispatcher.facilities = FacilityCollector()
    dispatcher.facility_handlers = []
    return dispatcher


def test_string_decode_never_reads_past_the_string256_declared_size():
    """Minor: dwData is DWORD * 8192 (32KB); a STRING256 value only ever
    occupies its first 256 bytes. The old
    ctypes.cast(address, c_char_p).value scans for the first NUL *anywhere*
    in memory, so a value with no NUL inside its own 256 bytes reads into
    whatever data follows it. Simulated safely here with a NUL placed well
    past byte 256 inside our own over-sized fake buffer -- still entirely
    within memory we own, but past the field the old code was supposed to
    respect.

    Fails against the current code: c_char_p reads through to that later
    NUL and returns 300 'A's, not 256."""
    dispatcher = _bare_dispatcher()
    pending = PendingRequest(request_id=7, is_string=True)
    dispatcher.registry.register(pending)

    buf = bytearray(b"A" * 512)
    buf[300] = 0  # NUL well past the 256-byte STRING256 field
    data = _make_simobject_data(7, bytes(buf))

    dispatcher._on_simobject_data(data)

    assert pending.value == "A" * STRING256_SIZE, (
        f"expected exactly {STRING256_SIZE} 'A's (the STRING256 field width), got "
        f"{len(pending.value)} characters -- the decode read past the declared field"
    )


def test_string_decode_stops_at_a_null_within_the_field():
    """Common case, must still work: SimConnect pads STRING256 with NULs
    after the value."""
    dispatcher = _bare_dispatcher()
    pending = PendingRequest(request_id=9, is_string=True)
    dispatcher.registry.register(pending)

    payload = b"Boeing 747-8i" + b"\x00" * (512 - len(b"Boeing 747-8i"))
    data = _make_simobject_data(9, payload)

    dispatcher._on_simobject_data(data)

    assert pending.value == "Boeing 747-8i"


def test_numeric_decode_is_unaffected_by_the_string_decode_change():
    dispatcher = _bare_dispatcher()
    pending = PendingRequest(request_id=11, is_string=False)
    dispatcher.registry.register(pending)

    class _FakeNumericData(ctypes.Structure):
        _fields_ = [
            ("dwRequestID", DWORD),
            ("dwData", ctypes.c_double * 4096),
        ]

    data = _FakeNumericData(dwRequestID=11)
    data.dwData[0] = 35000.0

    dispatcher._on_simobject_data(data)

    assert pending.value == 35000.0


# ---------------------------------------------------------------------------
# my_dispatch_proc -- ASSIGNED_OBJECT_ID routing
#
# L1 (live-follow-up): create_ai_object needs SimConnect's own confirmation
# that an AI object was actually created, not just accepted -- that
# confirmation is this message, correlated back to the request id
# tools/flight.py registered. The library's own branch (SimConnect.py)
# instead stashes dwObjectID in os.environ["SIMCONNECT_OBJECT_ID"], a
# process-global with no per-call correlation, so falling through to it
# must never happen here.
# ---------------------------------------------------------------------------


def _assigned_object_id_message(request_id: int, object_id: int):
    """Build a raw byte buffer shaped like a real ASSIGNED_OBJECT_ID
    message: the 12-byte SIMCONNECT_RECV header (dwSize, dwVersion, dwID)
    followed by dwRequestID + dwObjectID -- 20 bytes total, matching the
    real SDK (see dispatch.py's inline comment on this branch)."""
    raw = struct.pack(
        "<IIIII",
        0,  # dwSize -- unused by the code under test
        0,  # dwVersion
        SIMCONNECT_RECV_ID.SIMCONNECT_RECV_ID_ASSIGNED_OBJECT_ID,
        request_id,
        object_id,
    )
    buf = ctypes.create_string_buffer(raw, len(raw))
    ptr = ctypes.cast(
        ctypes.addressof(buf), ctypes.POINTER(SIMCONNECT_RECV_ASSIGNED_OBJECT_ID)
    )
    return buf, ptr  # caller must keep `buf` alive as long as `ptr` is used


def test_my_dispatch_proc_resolves_assigned_object_id():
    """The actual integration point of the L1 fix: a real ASSIGNED_OBJECT_ID
    message reaching my_dispatch_proc must resolve the matching
    PendingRequest by request id, and must not fall through to the
    library's own os.environ-stashing branch."""
    import os

    os.environ.pop("SIMCONNECT_OBJECT_ID", None)
    dispatcher = _bare_dispatcher()
    pending = PendingRequest(request_id=55)
    dispatcher.registry.register(pending)
    buf, ptr = _assigned_object_id_message(request_id=55, object_id=999)

    dispatcher.my_dispatch_proc(ptr, len(buf.raw), None)

    assert pending.done.wait(0.1)
    assert pending.value == 999
    assert pending.resolved is True
    assert "SIMCONNECT_OBJECT_ID" not in os.environ, (
        "fell through to the library's own branch instead of being resolved here"
    )


def test_my_dispatch_proc_ignores_an_unmatched_assigned_object_id():
    """No PendingRequest registered for this request id (e.g. it already
    timed out and was discarded) -- must not raise, and must not resolve
    some other, unrelated request."""
    dispatcher = _bare_dispatcher()
    other = PendingRequest(request_id=1)
    dispatcher.registry.register(other)
    buf, ptr = _assigned_object_id_message(request_id=999, object_id=1)

    dispatcher.my_dispatch_proc(ptr, len(buf.raw), None)  # must not raise

    assert not other.done.is_set()


# ---------------------------------------------------------------------------
# my_dispatch_proc -- facility list routing (FACILITY_RECV_IDS branch)
#
# Phase 0 made this branch a no-op loop specifically so the library's own
# handler (which print()s via dump()) could never run. Phase 2 Task 1 fills
# it in: parse into self.facilities, then run any registered
# facility_handlers. See dispatch.py's inline comment on this branch for why
# each handler gets its own try/except.
# ---------------------------------------------------------------------------


def _facility_message(array_size: int, entry_number: int, out_of: int, body: bytes = b""):
    """Build a raw byte buffer shaped like a real AIRPORT_LIST message: the
    28-byte SIMCONNECT_RECV_FACILITIES_LIST header (dwSize, dwVersion, dwID,
    dwRequestID, dwArraySize, dwEntryNumber, dwOutOf) followed by `body`."""
    header = struct.pack(
        "<IIIIIII",
        0,  # dwSize -- unused by the code under test
        0,  # dwVersion
        SIMCONNECT_RECV_ID.SIMCONNECT_RECV_ID_AIRPORT_LIST,
        0,  # dwRequestID
        array_size,
        entry_number,
        out_of,
    )
    raw = header + body
    buf = ctypes.create_string_buffer(raw, len(raw))
    ptr = ctypes.cast(ctypes.addressof(buf), ctypes.POINTER(SIMCONNECT_RECV_FACILITIES_LIST))
    return buf, ptr  # caller must keep `buf` alive as long as `ptr` is used


def _airport_record(ident: bytes, region: bytes, lat: float, lon: float, alt: float) -> bytes:
    return ident.ljust(6, b"\0") + region.ljust(3, b"\0") + struct.pack("<ddd", lat, lon, alt)


def test_my_dispatch_proc_routes_facility_messages_into_the_collector():
    """The actual integration point of this task: a real *_LIST message
    reaching my_dispatch_proc ends up parsed in self.facilities, not
    dropped on the floor and not handed to the library's dump()."""
    dispatcher = _bare_dispatcher()
    record = _airport_record(b"KATL", b"K6", 33.6367, -84.4281, 313.0)
    buf, ptr = _facility_message(array_size=1, entry_number=0, out_of=1, body=record)

    dispatcher.my_dispatch_proc(ptr, len(buf.raw), None)

    assert dispatcher.facilities.is_complete(FacilityKind.AIRPORT) is True
    results = dispatcher.facilities.results(FacilityKind.AIRPORT)
    assert [r["icao"] for r in results] == ["KATL"]


def test_a_raising_facility_handler_does_not_stop_the_others_or_propagate():
    """Finding from an earlier review: facility_handlers was invoked with no
    try/except, deferred on the grounds that the list was empty until this
    task. It is no longer empty-by-construction (callers may register
    handlers), so one handler raising must not prevent the rest from
    running, and must not escape my_dispatch_proc -- this runs on
    SimConnect's own callback thread, not the event loop."""
    dispatcher = _bare_dispatcher()
    calls: list[str] = []

    def bad_handler(pData):
        calls.append("bad")
        raise RuntimeError("boom")

    def good_handler(pData):
        calls.append("good")

    dispatcher.facility_handlers = [bad_handler, good_handler]
    record = _airport_record(b"KATL", b"K6", 33.6367, -84.4281, 313.0)
    buf, ptr = _facility_message(array_size=1, entry_number=0, out_of=1, body=record)

    dispatcher.my_dispatch_proc(ptr, len(buf.raw), None)  # must not raise

    assert calls == ["bad", "good"]


def test_facility_chunk_accumulation_survives_through_my_dispatch_proc():
    """dwOutOf > 1 chunks arriving as separate dispatch calls must still
    accumulate into one result, exercised through the real dispatch entry
    point rather than by calling FacilityCollector.handle() directly."""
    dispatcher = _bare_dispatcher()
    first = _airport_record(b"KATL", b"K6", 33.6367, -84.4281, 313.0)
    second = _airport_record(b"KPDK", b"K6", 33.8756, -84.3020, 316.0)

    buf1, ptr1 = _facility_message(array_size=1, entry_number=0, out_of=2, body=first)
    dispatcher.my_dispatch_proc(ptr1, len(buf1.raw), None)
    assert dispatcher.facilities.is_complete(FacilityKind.AIRPORT) is False

    buf2, ptr2 = _facility_message(array_size=1, entry_number=1, out_of=2, body=second)
    dispatcher.my_dispatch_proc(ptr2, len(buf2.raw), None)

    assert dispatcher.facilities.is_complete(FacilityKind.AIRPORT) is True
    results = dispatcher.facilities.results(FacilityKind.AIRPORT)
    assert [r["icao"] for r in results] == ["KATL", "KPDK"]


# ---------------------------------------------------------------------------
# _MobiFlightCrossTalkFilter
#
# Task 7's PMDG variant probe (tools/pmdg.py) subscribes to both PMDG data
# areas, which triggers a confirmed-benign WARNING from the vendored
# MobiFlightVariableRequests.client_data_callback_handler for every PMDG
# client-data message (it does not recognise PMDG's definition IDs -- see
# the filter's own docstring in dispatch.py). Live-verified message:
# "client_data_callback_handler DefinitionID 1313289010 not found!".
# ---------------------------------------------------------------------------


def _make_record(name: str, msg: str) -> logging.LogRecord:
    return logging.LogRecord(
        name=name, level=logging.WARNING, pathname=__file__, lineno=1,
        msg=msg, args=(), exc_info=None,
    )


def test_crosstalk_filter_drops_the_exact_mobiflight_warning():
    """Fails against a filter that doesn't match the real message shape --
    this is the literal live-verified text (module name and DefinitionID
    value observed against a real PMDG 737-600 in this session)."""
    record = _make_record(
        "root", "client_data_callback_handler DefinitionID 1313289010 not found!"
    )
    assert _MobiFlightCrossTalkFilter().filter(record) is False


def test_crosstalk_filter_keeps_other_root_warnings():
    """Must not become a blanket root-logger suppressor -- only this one
    message shape is silenced."""
    record = _make_record("root", "something else entirely went wrong")
    assert _MobiFlightCrossTalkFilter().filter(record) is True


def test_crosstalk_filter_keeps_warnings_from_named_loggers():
    """The noisy call is bare `logging.warning(...)` (root logger, name
    "root"); a similarly-worded message from a real named logger (e.g. this
    project's own modules) must not be caught by the same filter."""
    record = _make_record(
        "simconnect_mcp.tools.pmdg",
        "client_data_callback_handler DefinitionID 123 not found!",
    )
    assert _MobiFlightCrossTalkFilter().filter(record) is True


def test_suppress_mobiflight_cross_talk_warning_does_not_stack_duplicates():
    """_suppress_mobiflight_cross_talk_warning() must be idempotent -- each
    additional filter instance is one more no-op scan per log record
    forever. Calls the install function directly, twice, rather than
    reloading the module.

    A prior version of this test used importlib.reload(dispatch_module) to
    simulate a second install. That does not work: reload executes the
    module body in a fresh namespace, so it creates a NEW
    _MobiFlightCrossTalkFilter *class* object -- the isinstance() guard
    (correctly) does not recognise the old instance as one of the new
    class, so reload demonstrably added a second filter (measured: 1
    filter before, 2 after), while the reload-based test's own before/after
    comparison used the stale pre-reload class throughout and so still saw
    "1 == 1" and passed. A test that cannot fail against the bug it names
    is worse than none; calling the function twice directly, with no
    reload, actually exercises the guard the production code relies on.
    """
    from simconnect_mcp.dispatch import _suppress_mobiflight_cross_talk_warning

    root_logger = logging.getLogger()
    before = sum(
        isinstance(f, _MobiFlightCrossTalkFilter) for f in root_logger.filters
    )
    assert before >= 1, "expected the filter installed by dispatch.py's own import"

    _suppress_mobiflight_cross_talk_warning()
    _suppress_mobiflight_cross_talk_warning()

    after = sum(
        isinstance(f, _MobiFlightCrossTalkFilter) for f in root_logger.filters
    )
    assert after == before
