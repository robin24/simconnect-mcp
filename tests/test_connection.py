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


def test_get_status_disconnected():
    """Status reports disconnected state."""
    manager = SimConnectManager()
    status = manager.get_status()
    assert status["state"] == "disconnected"
    assert status["connected"] is False


def test_get_status_connected(mock_simconnect):
    """Status reports connected state."""
    manager = mock_simconnect["manager"]
    status = manager.get_status()
    assert status["state"] == "connected"
    assert status["connected"] is True


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
