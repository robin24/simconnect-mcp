"""Tests for SimConnectManager."""

from __future__ import annotations

from simconnect_mcp.connection import ConnectionState, SimConnectManager


def test_singleton():
    """SimConnectManager is a singleton."""
    a = SimConnectManager()
    b = SimConnectManager()
    assert a is b


def test_initial_state():
    """Manager starts disconnected."""
    manager = SimConnectManager()
    assert manager.state == ConnectionState.DISCONNECTED
    assert not manager.is_connected
    assert not manager.mobiflight_available


async def test_get_status_disconnected():
    """Status reports disconnected state."""
    manager = SimConnectManager()
    status = await manager.get_status()
    assert status["state"] == "disconnected"
    assert status["connected"] is False


async def test_get_status_connected(mock_simconnect):
    """Status reports connected state."""
    manager = mock_simconnect["manager"]
    status = await manager.get_status()
    assert status["state"] == "connected"
    assert status["connected"] is True


async def test_get_status_reports_sim_paused_and_running(mock_simconnect):
    manager = mock_simconnect["manager"]
    status = await manager.get_status()
    assert status["sim_paused"] is False
    assert status["sim_running"] is True


async def test_get_status_fetches_sim_state_via_run_sync(mock_simconnect):
    """Finding 3: get_status() used to acquire _sim_lock directly on the
    event loop thread. If some other call already held that lock for a
    while (e.g. a large get_simvar_bulk against a hung sim), a direct
    acquisition there would block the event loop itself until it freed --
    freezing every other tool call on the server, not just this one.
    Routing the sim_paused/sim_running read through run_sync moves the wait
    into a background thread instead. This fails against the current code
    because get_status() is a plain `def`, not a coroutine: awaiting it
    raises TypeError."""
    manager = mock_simconnect["manager"]
    calls = []
    real_run_sync = manager.run_sync

    async def spy_run_sync(fn, *args):
        calls.append(fn)
        return await real_run_sync(fn, *args)

    manager.run_sync = spy_run_sync

    status = await manager.get_status()

    assert calls, "get_status() must fetch sim_paused/sim_running through run_sync"
    assert status["sim_paused"] is False
    assert status["sim_running"] is True


def test_disconnect(mock_simconnect):
    """Disconnect resets state."""
    manager = mock_simconnect["manager"]
    result = manager.disconnect()
    assert result["status"] == "ok"
    assert manager.state == ConnectionState.DISCONNECTED
    assert manager.sm is None


def test_disconnect_when_already_disconnected():
    """Disconnecting when already disconnected is a no-op."""
    manager = SimConnectManager()
    result = manager.disconnect()
    assert result["status"] == "ok"


def test_manager_exposes_an_accessor_when_connected(mock_simconnect):
    manager = mock_simconnect["manager"]
    assert manager.accessor is not None


def test_disconnect_clears_the_accessor(mock_simconnect):
    manager = mock_simconnect["manager"]
    manager.disconnect()
    assert manager.accessor is None


def test_disconnect_clears_the_facility_cache(mock_simconnect):
    """tools/facilities.py caches the (large, expensive-to-rebuild) world
    facility list per connection; a stale cache must not survive into a
    fresh connection the way _title_cache/_pmdg_variant_cache already
    don't."""
    manager = mock_simconnect["manager"]
    manager.set_cached_facilities("airport", [{"icao": "KSEA"}])
    assert manager.get_cached_facilities("airport") is not None

    manager.disconnect()

    assert manager.get_cached_facilities("airport") is None


def test_facility_lock_is_stable_per_kind(mock_simconnect):
    """The lock tools/facilities.py uses to guard concurrent collection must
    be the same object across calls for one kind (so it actually
    serializes) and distinct across kinds (so airport/waypoint/ndb/vor
    collection can proceed independently)."""
    import asyncio

    manager = mock_simconnect["manager"]
    airport_lock_1 = manager.facility_lock("airport")
    airport_lock_2 = manager.facility_lock("airport")
    waypoint_lock = manager.facility_lock("waypoint")

    assert isinstance(airport_lock_1, asyncio.Lock)
    assert airport_lock_1 is airport_lock_2
    assert airport_lock_1 is not waypoint_lock
