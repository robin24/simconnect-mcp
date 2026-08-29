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
