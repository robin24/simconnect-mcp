import threading

from simconnect_mcp.dispatch import PendingRequest, RequestRegistry


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
    (expensive) allocator is ever called again."""
    registry = RequestRegistry()
    calls = []

    def allocate():
        calls.append(1)
        return 100 + len(calls)

    first_id = registry.acquire_request_id(allocate)
    req = PendingRequest(request_id=first_id)
    registry.register(req)
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
    import ctypes

    from simconnect_mcp.dispatch import RecvException

    assert ctypes.sizeof(RecvException) == 24
    assert RecvException.dwException.offset == 12
    assert RecvException.dwSendID.offset == 16
    assert RecvException.dwIndex.offset == 20
